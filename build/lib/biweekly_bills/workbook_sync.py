from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import shutil
from typing import Any

from .bank_data import find_bill_matches
from .ods_workbook import ODSWorkbook


@dataclass(frozen=True)
class WorkbookChange:
    sheet: str
    cell: str
    old: Any
    new: Any
    reason: str


@dataclass(frozen=True)
class WorkbookMatch:
    bill: str
    sheet: str
    cell: str
    existing: Any
    bank_amount: float
    merchant: str
    tx_date: str
    status: str


CYCLE_LAYOUT = {
    "1st": {
        "name_col": "A",
        "paid_col": "D",
        "posted_balance": "C9",
        "row_start": 13,
        "row_end": 26,
    },
    "15th": {
        "name_col": "I",
        "paid_col": "L",
        "posted_balance": "K9",
        "row_start": 13,
        "row_end": 26,
    },
}

GUTTER_CYCLE_LAYOUT = {
    "1st": {
        "name_col": "B",
        "paid_col": "E",
        "posted_balance": "D10",
        "row_start": 14,
        "row_end": 27,
    },
    "15th": {
        "name_col": "J",
        "paid_col": "M",
        "posted_balance": "L10",
        "row_start": 14,
        "row_end": 27,
    },
}


def month_sheet_name(workbook: ODSWorkbook, as_of: date) -> str:
    month = as_of.strftime("%B")
    year_qualified = f"{month} {as_of.year}"
    if year_qualified in workbook.sheet_names:
        return year_qualified
    if month in workbook.sheet_names:
        return month
    raise RuntimeError(
        f"Workbook has no sheet for {month} {as_of.year!s}. "
        f"Expected {year_qualified!r} or legacy {month!r}."
    )


def _has_theme_gutter(workbook: ODSWorkbook, sheet_name: str) -> bool:
    a1 = workbook.get_cell_value(sheet_name, "A1")
    b2 = workbook.get_cell_value(sheet_name, "B2")
    return (
        a1 in (None, "")
        and isinstance(b2, str)
        and "bill pay" in b2.casefold()
    )


def _cycle_layout(workbook: ODSWorkbook, sheet_name: str, cycle: str) -> dict[str, Any]:
    return (GUTTER_CYCLE_LAYOUT if _has_theme_gutter(workbook, sheet_name) else CYCLE_LAYOUT)[cycle]


def _find_bill_row(
    workbook: ODSWorkbook,
    sheet_name: str,
    name_col: str,
    bill_name: str,
    *,
    row_start: int,
    row_end: int,
) -> int | None:
    for row in range(row_start, row_end + 1):
        value = workbook.get_cell_value(sheet_name, f"{name_col}{row}")
        if str(value or "").strip().casefold() == bill_name.casefold():
            return row
    return None


def _transaction_amount(tx: dict[str, Any]) -> float:
    return round(float(tx.get("amount") or 0.0), 2)


def inspect_workbook_matches(
    workbook_path: Path,
    transactions: list[dict[str, Any]],
    *,
    as_of: date,
    cycle: str,
    bills_account_id: str | None,
) -> list[WorkbookMatch]:
    if cycle not in CYCLE_LAYOUT:
        raise ValueError("cycle must be 1st or 15th")

    workbook = ODSWorkbook(workbook_path)
    sheet_name = month_sheet_name(workbook, as_of)
    layout = _cycle_layout(workbook, sheet_name, cycle)
    matches = find_bill_matches(
        transactions,
        year=as_of.year,
        month=as_of.month,
        cycle=cycle,
        account_id=bills_account_id,
    )

    results: list[WorkbookMatch] = []
    for bill_name, tx in matches.items():
        row = _find_bill_row(
            workbook,
            sheet_name,
            layout["name_col"],
            bill_name,
            row_start=int(layout["row_start"]),
            row_end=int(layout["row_end"]),
        )
        if row is None:
            results.append(
                WorkbookMatch(
                    bill=bill_name,
                    sheet=sheet_name,
                    cell="(bill row not found)",
                    existing=None,
                    bank_amount=_transaction_amount(tx),
                    merchant=str(tx.get("merchant_name") or tx.get("name") or bill_name),
                    tx_date=str(tx.get("date") or tx.get("authorized_date") or ""),
                    status="matched transaction; workbook bill row not found",
                )
            )
            continue

        cell = f"{layout['paid_col']}{row}"
        existing = workbook.get_cell_value(sheet_name, cell)
        bank_amount = _transaction_amount(tx)
        if existing in (None, "", 0, 0.0):
            status = "ready to fill Paid"
        else:
            try:
                same = round(float(existing), 2) == bank_amount
            except (TypeError, ValueError):
                same = False
            status = "already recorded" if same else "existing Paid differs; preserved"

        results.append(
            WorkbookMatch(
                bill=bill_name,
                sheet=sheet_name,
                cell=cell,
                existing=existing,
                bank_amount=bank_amount,
                merchant=str(tx.get("merchant_name") or tx.get("name") or bill_name),
                tx_date=str(tx.get("date") or tx.get("authorized_date") or ""),
                status=status,
            )
        )

    return results


def plan_workbook_changes(
    workbook_path: Path,
    transactions: list[dict[str, Any]],
    *,
    as_of: date,
    cycle: str,
    bills_account_id: str | None,
    posted_balance: float | None = None,
    overwrite_paid: bool = False,
) -> list[WorkbookChange]:
    if cycle not in CYCLE_LAYOUT:
        raise ValueError("cycle must be 1st or 15th")

    workbook = ODSWorkbook(workbook_path)
    sheet_name = month_sheet_name(workbook, as_of)
    layout = _cycle_layout(workbook, sheet_name, cycle)
    matches = find_bill_matches(
        transactions,
        year=as_of.year,
        month=as_of.month,
        cycle=cycle,
        account_id=bills_account_id,
    )

    changes: list[WorkbookChange] = []
    for bill_name, tx in matches.items():
        row = _find_bill_row(
            workbook,
            sheet_name,
            layout["name_col"],
            bill_name,
            row_start=int(layout["row_start"]),
            row_end=int(layout["row_end"]),
        )
        if row is None:
            continue

        cell = f"{layout['paid_col']}{row}"
        old = workbook.get_cell_value(sheet_name, cell)
        new = _transaction_amount(tx)

        if old not in (None, "", 0, 0.0) and not overwrite_paid:
            continue
        if old == new:
            continue

        tx_date = str(tx.get("date") or tx.get("authorized_date") or "")
        merchant = str(tx.get("merchant_name") or tx.get("name") or bill_name)
        changes.append(
            WorkbookChange(
                sheet=sheet_name,
                cell=cell,
                old=old,
                new=new,
                reason=f"Posted Plaid transaction: {merchant} on {tx_date}",
            )
        )

    if posted_balance is not None:
        cell = layout["posted_balance"]
        old = workbook.get_cell_value(sheet_name, cell)
        new = round(float(posted_balance), 2)
        if old != new:
            changes.append(
                WorkbookChange(
                    sheet=sheet_name,
                    cell=cell,
                    old=old,
                    new=new,
                    reason="Current Bills Checking balance from Plaid",
                )
            )

    return changes


def apply_workbook_changes(
    workbook_path: Path,
    changes: list[WorkbookChange],
    *,
    backup_dir: Path | None = None,
) -> Path | None:
    if not changes:
        return None

    workbook_path = workbook_path.resolve()
    if workbook_path.suffix.lower() != ".ods":
        raise ValueError("Bi-Weekly Bills requires an .ods workbook.")

    if backup_dir is None:
        backup_dir = workbook_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{workbook_path.stem}.{stamp}.bak.ods"
    shutil.copy2(workbook_path, backup_path)

    workbook = ODSWorkbook(workbook_path)
    for change in changes:
        workbook.set_number(change.sheet, change.cell, float(change.new))
    workbook.save()
    return backup_path


def choose_workbook(project_root: Path, explicit: str | None = None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = (project_root / path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Workbook not found: {path}")
        if path.suffix.lower() != ".ods":
            raise ValueError("Bi-Weekly Bills only supports .ods workbooks.")
        return path

    folders: list[Path] = []
    for folder in (project_root / "workbook", Path.cwd() / "workbook"):
        try:
            key = folder.resolve()
        except OSError:
            key = folder.absolute()
        if all(existing != key for existing in folders):
            folders.append(key)

    candidates: list[Path] = []
    scanned: list[str] = []
    for folder in folders:
        if not folder.is_dir():
            scanned.append(f"{folder} (missing)")
            continue
        entries = list(folder.iterdir())
        scanned.append(
            f"{folder}: " + (", ".join(sorted(entry.name for entry in entries)) or "(empty)")
        )
        candidates.extend(
            entry
            for entry in entries
            if entry.is_file()
            and entry.suffix.casefold() == ".ods"
            and not entry.name.startswith(".~lock.")
            and not entry.name.startswith("~$")
        )

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        details = "\n  ".join(scanned)
        raise FileNotFoundError(
            "No .ods workbook found. Searched:\n  "
            + details
            + "\nPut the current bills workbook in a workbook/ directory or pass --workbook."
        )
    return candidates[0]
