from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from .database import Database
from .ods_workbook import ODSWorkbook


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

MONTH_SHEET_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"(?:\s+(\d{4}))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ImportReport:
    source: Path
    source_sha256: str
    setup_bills: int
    bill_instances: int
    months_seen: int
    legacy_year: int


def _col_name(number: int) -> str:
    if number < 1:
        raise ValueError("Column number must be positive.")
    chars: list[str] = []
    while number:
        number, rem = divmod(number - 1, 26)
        chars.append(chr(ord("A") + rem))
    return "".join(reversed(chars))


def _cell(workbook: ODSWorkbook, sheet: str, row: int, col: int) -> Any:
    return workbook.get_cell_value(sheet, f"{_col_name(col)}{row}")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return " ".join(_text(value).casefold().split())


def _clean_optional(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _to_cents(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))

    raw = _text(value).replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        return int(round(float(raw) * 100))
    except ValueError:
        return None


def _parse_active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _norm(value)
    if normalized in {"false", "no", "n", "0", "inactive", "off"}:
        return False
    return True


def _is_non_bill_label(value: Any) -> bool:
    name = _norm(value)
    if not name:
        return True
    if name == "bill":
        return True
    if name.startswith("nfcu workflow:"):
        return True
    if name.startswith("navy federal workflow:"):
        return True
    return False


def _find_setup_header(workbook: ODSWorkbook) -> tuple[int, dict[str, int]] | None:
    wanted = {
        "bill",
        "cycle",
        "when",
        "latest due",
        "default method",
        "payment account",
        "active",
        "notes",
    }
    for row in range(1, 121):
        found: dict[str, int] = {}
        for col in range(1, 31):
            value = _norm(_cell(workbook, "Setup", row, col))
            if value in wanted and value not in found:
                found[value] = col
        if "bill" in found and "cycle" in found and len(found) >= 5:
            return row, found
    return None


def _normalize_cycle(value: Any) -> str | None:
    normalized = _norm(value)
    if normalized in {"1st", "1", "first"}:
        return "1st"
    if normalized in {"15th", "15", "fifteenth"}:
        return "15th"
    if normalized in {"both", "1st/15th", "1st & 15th", "1st and 15th"}:
        return "Both"
    return None


def _import_setup(workbook: ODSWorkbook, database: Database) -> int:
    if "Setup" not in workbook.sheet_names:
        return 0

    header = _find_setup_header(workbook)
    if header is None:
        return 0

    header_row, cols = header
    imported = 0
    blank_streak = 0

    for row in range(header_row + 1, header_row + 501):
        name = _clean_optional(_cell(workbook, "Setup", row, cols["bill"]))
        if _is_non_bill_label(name):
            blank_streak += 1
            if imported and blank_streak >= 30:
                break
            continue

        blank_streak = 0
        cycle = _normalize_cycle(_cell(workbook, "Setup", row, cols["cycle"]))
        if cycle is None:
            # Bad or legacy cycle metadata should not poison the database. The
            # monthly import below preserves the actual historical placement.
            cycle = "Both"

        latest_due = _clean_optional(
            _cell(workbook, "Setup", row, cols["latest due"])
            if "latest due" in cols
            else None
        )
        default_method = _clean_optional(
            _cell(workbook, "Setup", row, cols["default method"])
            if "default method" in cols
            else None
        )
        payment_account = _clean_optional(
            _cell(workbook, "Setup", row, cols["payment account"])
            if "payment account" in cols
            else None
        )
        active = _parse_active(
            _cell(workbook, "Setup", row, cols["active"])
            if "active" in cols
            else True
        )
        notes = _clean_optional(
            _cell(workbook, "Setup", row, cols["notes"])
            if "notes" in cols
            else None
        )

        database.upsert_bill(
            name=str(name),
            cycle=cycle,
            latest_due=latest_due,
            default_method=default_method,
            payment_account=payment_account,
            active=active,
            notes=notes,
        )
        imported += 1

    return imported


def _header_signature(workbook: ODSWorkbook, sheet: str, row: int, col: int) -> bool:
    expected = ["bill", "when", "due", "paid", "method", "status", "extra"]
    actual = [_norm(_cell(workbook, sheet, row, col + offset)) for offset in range(7)]
    if actual[:6] != expected[:6]:
        return False
    return actual[6] in {"extra", "extra(short)", "extra (short)", "short", ""}


def _find_month_tables(workbook: ODSWorkbook, sheet: str) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int]] = []
    for row in range(1, 81):
        for col in range(1, 31):
            if _norm(_cell(workbook, sheet, row, col)) != "bill":
                continue
            if _header_signature(workbook, sheet, row, col):
                matches.append((row, col))

    # De-duplicate exact header hits, then assign cycles by left/right position.
    matches = sorted(set(matches), key=lambda item: (item[0], item[1]))
    if not matches:
        return []

    # Usually both headers share a row. Assign the left-most table to 1st and
    # the next one to 15th, regardless of whether the workbook has the gutter.
    by_row: dict[int, list[int]] = {}
    for row, col in matches:
        by_row.setdefault(row, []).append(col)

    output: list[tuple[int, int, str]] = []
    for row, cols in by_row.items():
        for index, col in enumerate(sorted(cols)[:2]):
            output.append((row, col, "1st" if index == 0 else "15th"))
    return output


def _resolve_month_year(sheet_name: str, legacy_year: int) -> tuple[int, int] | None:
    match = MONTH_SHEET_RE.fullmatch(sheet_name.strip())
    if not match:
        return None
    month = MONTHS[match.group(1).casefold()]
    year = int(match.group(2)) if match.group(2) else legacy_year
    return year, month


def _import_month_table(
    workbook: ODSWorkbook,
    database: Database,
    *,
    sheet: str,
    year: int,
    month: int,
    header_row: int,
    start_col: int,
    cycle: str,
) -> int:
    imported = 0
    blank_streak = 0

    for row in range(header_row + 1, header_row + 121):
        values = [_cell(workbook, sheet, row, start_col + offset) for offset in range(7)]
        bill_name = _clean_optional(values[0])

        if _is_non_bill_label(bill_name):
            blank_streak += 1
            if imported and blank_streak >= 12:
                break
            continue

        blank_streak = 0
        assert bill_name is not None

        # Historical rows can outlive Setup. Preserve them in the database as
        # inactive master records instead of dropping history.
        existing = None
        for bill in database.list_bills(active_only=False):
            if _norm(bill["name"]) == _norm(bill_name):
                existing = bill
                break
        if existing is None:
            bill_id = database.upsert_bill(
                name=bill_name,
                cycle=cycle,
                active=False,
                notes="Created by read-only ODS history import; not present in Setup.",
            )
        else:
            bill_id = int(existing["id"])

        database.upsert_bill_instance(
            year=year,
            month=month,
            cycle=cycle,
            bill_name=bill_name,
            bill_id=bill_id,
            when_label=_clean_optional(values[1]),
            due_cents=_to_cents(values[2]),
            paid_cents=_to_cents(values[3]),
            method=_clean_optional(values[4]),
            status=_clean_optional(values[5]),
            extra_short=_clean_optional(values[6]),
            source="ods-import",
            source_sheet=sheet,
            source_row=row,
        )
        imported += 1

    return imported


def import_ods_history(
    workbook_path: Path | str,
    database: Database,
    *,
    legacy_year: int = 2026,
) -> ImportReport:
    """Import Setup and monthly history without ever modifying the ODS file."""

    path = Path(workbook_path).expanduser().resolve()
    before = path.read_bytes()
    digest = sha256(before).hexdigest()

    workbook = ODSWorkbook(path)
    setup_bills = _import_setup(workbook, database)

    instances = 0
    months_seen = 0
    for sheet in workbook.sheet_names:
        resolved = _resolve_month_year(sheet, legacy_year)
        if resolved is None:
            continue
        year, month = resolved
        tables = _find_month_tables(workbook, sheet)
        if not tables:
            continue
        months_seen += 1
        for header_row, start_col, cycle in tables:
            instances += _import_month_table(
                workbook,
                database,
                sheet=sheet,
                year=year,
                month=month,
                header_row=header_row,
                start_col=start_col,
                cycle=cycle,
            )

    after = path.read_bytes()
    if after != before:
        raise RuntimeError("ODS import safety check failed: source workbook bytes changed.")

    database.record_workbook_import(
        source_path=path,
        source_sha256=digest,
        source_size=len(before),
        legacy_year=legacy_year,
        setup_bills=setup_bills,
        bill_instances=instances,
    )

    return ImportReport(
        source=path,
        source_sha256=digest,
        setup_bills=setup_bills,
        bill_instances=instances,
        months_seen=months_seen,
        legacy_year=legacy_year,
    )
