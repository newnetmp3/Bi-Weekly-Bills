from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
import shutil
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

from .ods_workbook import ODSWorkbook
from .workbook_sync import (
    WorkbookChange,
    apply_workbook_changes,
    inspect_workbook_matches,
    month_sheet_name,
)


ODS_MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"
FORMULA_ATTR = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}formula"


@dataclass(frozen=True)
class SelfTestCheck:
    name: str
    detail: str


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ods_snapshot(path: Path) -> dict:
    with ZipFile(path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if not infos or infos[0].filename != "mimetype":
            raise RuntimeError("ODS package does not have mimetype as its first ZIP member.")
        if infos[0].compress_type != ZIP_STORED:
            raise RuntimeError("ODS mimetype entry must be stored uncompressed.")
        mimetype = archive.read("mimetype").decode("utf-8")
        if mimetype != ODS_MIMETYPE:
            raise RuntimeError(f"Unexpected ODS mimetype: {mimetype!r}")
        for required in ("content.xml", "META-INF/manifest.xml"):
            if required not in names:
                raise RuntimeError(f"ODS package is missing {required}.")
        content = archive.read("content.xml")

    if b"of:=" in content and b"xmlns:of=" not in content:
        raise RuntimeError(
            "ODS formulas use the OpenFormula 'of:' prefix but content.xml is missing xmlns:of."
        )

    root = ET.fromstring(content)
    formulas = tuple(
        element.attrib[FORMULA_ATTR]
        for element in root.iter()
        if FORMULA_ATTR in element.attrib
    )
    workbook = ODSWorkbook(path)
    return {
        "members": tuple(names),
        "sheets": tuple(workbook.sheet_names),
        "formulas": formulas,
    }


def _fake_bill_transactions() -> list[dict]:
    account_id = "self-test-bills-account"
    return [
        {
            "transaction_id": "self-verizon",
            "account_id": account_id,
            "date": "2026-09-15",
            "merchant_name": "VERIZON WIRELESS",
            "amount": 213.07,
            "pending": False,
        },
        {
            "transaction_id": "self-cox",
            "account_id": account_id,
            "date": "2026-09-15",
            "merchant_name": "COX COMMUNICATIONS",
            "amount": 120.60,
            "pending": False,
        },
        {
            "transaction_id": "self-usaa",
            "account_id": account_id,
            "date": "2026-09-15",
            "merchant_name": "USAA INSURANCE",
            "amount": 272.20,
            "pending": False,
        },
        {
            "transaction_id": "self-acellus",
            "account_id": account_id,
            "date": "2026-09-15",
            "merchant_name": "ACELLUS ACADEMY",
            "amount": 158.00,
            "pending": False,
        },
        {
            "transaction_id": "self-star",
            "account_id": account_id,
            "date": "2026-09-15",
            "merchant_name": "MILITARY STAR CARD",
            "amount": 126.28,
            "pending": False,
        },
    ]


def run_self_test(workbook_path: Path) -> list[SelfTestCheck]:
    workbook_path = Path(workbook_path).expanduser().resolve()
    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")
    if workbook_path.suffix.casefold() != ".ods":
        raise ValueError("Self-test requires an .ods workbook.")

    checks: list[SelfTestCheck] = []
    original_hash = _file_hash(workbook_path)
    before = _ods_snapshot(workbook_path)
    checks.append(SelfTestCheck("ODS package", "valid mimetype, manifest, and content.xml"))
    checks.append(SelfTestCheck("Sheets", f"{len(before['sheets'])} sheet(s) readable"))

    source_book = ODSWorkbook(workbook_path)
    september_sheet = month_sheet_name(source_book, date(2026, 9, 15))

    matches = inspect_workbook_matches(
        workbook_path,
        _fake_bill_transactions(),
        as_of=date(2026, 9, 15),
        cycle="15th",
        bills_account_id="self-test-bills-account",
    )
    expected = {"Verizon", "Cox", "USAA", "Acellus Academy", "Star Card"}
    matched = {match.bill for match in matches}
    missing = sorted(expected - matched)
    if missing:
        raise RuntimeError("Bill matching self-test failed; missing: " + ", ".join(missing))
    checks.append(SelfTestCheck("Bill matching", "all 5 known 15th-cycle bills mapped to workbook rows"))

    with tempfile.TemporaryDirectory(prefix="biweekly-bills-self-test-") as temp:
        temp_root = Path(temp)
        test_copy = temp_root / workbook_path.name
        backup_dir = temp_root / "backups"
        shutil.copy2(workbook_path, test_copy)

        test_book = ODSWorkbook(test_copy)
        test_sheet = month_sheet_name(test_book, date(2026, 9, 15))
        guttered = (
            test_book.get_cell_value(test_sheet, "A1") in (None, "")
            and "bill pay" in str(test_book.get_cell_value(test_sheet, "B2") or "").casefold()
        )
        test_cell = "L10" if guttered else "K9"
        old_value = test_book.get_cell_value(test_sheet, test_cell)
        marker = 9876.54
        try:
            if round(float(old_value), 2) == marker:
                marker = 9876.55
        except (TypeError, ValueError):
            pass

        change = WorkbookChange(
            sheet=test_sheet,
            cell=test_cell,
            old=old_value,
            new=marker,
            reason="self-test temporary ODS write",
        )
        backup = apply_workbook_changes(test_copy, [change], backup_dir=backup_dir)
        if backup is None or not backup.exists():
            raise RuntimeError("ODS write self-test did not create a backup.")
        if _file_hash(backup) != original_hash:
            raise RuntimeError("Backup is not byte-for-byte identical to the original workbook.")
        checks.append(SelfTestCheck("Backup", "timestamped .bak.ods is byte-identical to original"))

        written = ODSWorkbook(test_copy).get_cell_value(test_sheet, test_cell)
        if round(float(written), 2) != marker:
            raise RuntimeError("ODS write self-test could not read back the temporary value.")
        checks.append(SelfTestCheck("ODS write", "temporary numeric write was read back successfully"))

        after = _ods_snapshot(test_copy)
        if after["members"] != before["members"]:
            raise RuntimeError("ODS package member list changed during the temporary write.")
        checks.append(SelfTestCheck("ODS members", "package members preserved"))

        if after["sheets"] != before["sheets"]:
            raise RuntimeError("Sheet names/order changed during the temporary write.")
        checks.append(SelfTestCheck("Sheet preservation", "sheet names and order preserved"))

        if after["formulas"] != before["formulas"]:
            raise RuntimeError("Workbook formulas changed during the temporary write.")
        checks.append(SelfTestCheck("Formula preservation", f"{len(before['formulas'])} formula(s) preserved"))

    if _file_hash(workbook_path) != original_hash:
        raise RuntimeError("CRITICAL: original workbook changed during self-test.")
    checks.append(SelfTestCheck("Original safety", "original workbook SHA-256 unchanged"))

    return checks
