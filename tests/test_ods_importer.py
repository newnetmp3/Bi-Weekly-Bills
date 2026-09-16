from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from biweekly_bills.database import Database
from biweekly_bills.ods_importer import import_ods_history


OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _cell(value):
    if value is None:
        return "<table:table-cell/>"
    if isinstance(value, (int, float)):
        return (
            f'<table:table-cell office:value-type="float" office:value="{value}">'
            f"<text:p>{value}</text:p></table:table-cell>"
        )
    return (
        '<table:table-cell office:value-type="string">'
        f"<text:p>{value}</text:p></table:table-cell>"
    )


def _row(values):
    return "<table:table-row>" + "".join(_cell(value) for value in values) + "</table:table-row>"


def _make_workbook(path: Path) -> None:
    setup_rows = [
        _row(
            [
                "Bill",
                "Cycle",
                "When",
                "Latest Due",
                "Default Method",
                "Payment Account",
                "Active",
                "Notes",
            ]
        ),
        _row(["Verizon", "1st", "1st", "213.07", "Autopay", "Bills Checking", "Yes", ""]),
        _row(
            [
                "NFCU workflow: use Payment Account to identify which Navy Federal account funds a bill.",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ]
        ),
    ]

    month_header = [None] * 16
    for start in (1, 9):
        values = ["Bill", "When", "Due", "Paid", "Method", "Status", "Extra(Short)"]
        month_header[start : start + 7] = values

    month_data = [None] * 16
    month_data[1:8] = ["Verizon", "1st", 213.07, 213.07, "Autopay", "Paid", None]
    month_data[9:16] = ["Cox", "15th", 120.60, None, "Autopay", "Due", None]

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-content xmlns:office="{OFFICE}" xmlns:table="{TABLE}" xmlns:text="{TEXT}">'
        "<office:body><office:spreadsheet>"
        '<table:table table:name="Setup">'
        + "".join(setup_rows)
        + "</table:table>"
        '<table:table table:name="September 2026">'
        + _row(month_header)
        + _row(month_data)
        + "</table:table>"
        "</office:spreadsheet></office:body></office:document-content>"
    )

    with ZipFile(path, "w") as archive:
        archive.writestr("content.xml", xml)


class ODSImporterTests(unittest.TestCase):
    def test_read_only_ods_import_preserves_source_and_history(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workbook = root / "bills.ods"
            _make_workbook(workbook)
            original = workbook.read_bytes()

            db = Database(root / "bills.sqlite3")
            db.initialize()

            report = import_ods_history(workbook, db, legacy_year=2026)

            self.assertEqual(workbook.read_bytes(), original)
            self.assertEqual(report.setup_bills, 1)
            self.assertEqual(report.bill_instances, 2)
            self.assertEqual(report.months_seen, 1)

            bills = {row["name"]: row for row in db.list_bills(active_only=False)}
            self.assertIn("Verizon", bills)
            self.assertIn("Cox", bills)
            self.assertFalse(
                any(name.casefold().startswith("nfcu workflow:") for name in bills)
            )
            self.assertEqual(bills["Cox"]["active"], 0)

            rows = db.list_month_instances(2026, 9)
            self.assertEqual(
                [(row["cycle"], row["bill_name_snapshot"]) for row in rows],
                [("1st", "Verizon"), ("15th", "Cox")],
            )
            self.assertEqual(rows[0]["paid_cents"], 21307)
            self.assertEqual(rows[1]["due_cents"], 12060)


if __name__ == "__main__":
    unittest.main()
