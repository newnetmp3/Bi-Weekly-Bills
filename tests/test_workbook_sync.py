import tempfile
import unittest
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from biweekly_bills.ods_workbook import ODSWorkbook
from biweekly_bills.workbook_sync import (
    apply_workbook_changes,
    choose_workbook,
    plan_workbook_changes,
    month_sheet_name,
)


MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"

CONTENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0"
 office:version="1.3">
 <office:body>
  <office:spreadsheet>
   <table:table table:name="September">
    <table:table-row table:number-rows-repeated="8">
     <table:table-cell table:number-columns-repeated="12"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:number-columns-repeated="11"/>
     <table:table-cell/>
    </table:table-row>
    <table:table-row table:number-rows-repeated="5">
     <table:table-cell table:number-columns-repeated="12"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:number-columns-repeated="8"/>
     <table:table-cell office:value-type="string"><text:p>Verizon</text:p></table:table-cell>
     <table:table-cell table:number-columns-repeated="3"/>
    </table:table-row>
   </table:table>
  </office:spreadsheet>
 </office:body>
</office:document-content>
"""

GUTTER_CONTENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0"
 office:version="1.3">
 <office:body>
  <office:spreadsheet>
   <table:table table:name="September">
    <table:table-row>
     <table:table-cell table:number-columns-repeated="16"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell/>
     <table:table-cell office:value-type="string"><text:p>September 2026 — Bill Pay</text:p></table:table-cell>
     <table:table-cell table:number-columns-repeated="14"/>
    </table:table-row>
    <table:table-row table:number-rows-repeated="13">
     <table:table-cell table:number-columns-repeated="16"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:number-columns-repeated="9"/>
     <table:table-cell office:value-type="string"><text:p>Verizon</text:p></table:table-cell>
     <table:table-cell table:number-columns-repeated="6"/>
    </table:table-row>
   </table:table>
  </office:spreadsheet>
 </office:body>
</office:document-content>
"""

YEAR_GUTTER_CONTENT_XML = GUTTER_CONTENT_XML.replace(
    'table:name="September"',
    'table:name="September 2026"',
)

MANIFEST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest
 xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
 manifest:version="1.3">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
"""


def make_ods(path: Path, *, guttered: bool = False, year_qualified: bool = False) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", MIMETYPE, compress_type=ZIP_STORED)
        archive.writestr(
            "content.xml",
            YEAR_GUTTER_CONTENT_XML if year_qualified else (GUTTER_CONTENT_XML if guttered else CONTENT_XML),
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr("META-INF/manifest.xml", MANIFEST_XML, compress_type=ZIP_DEFLATED)


class WorkbookSyncTests(unittest.TestCase):
    def test_plan_and_apply_ods_with_backup_and_repeated_cells(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            path = temp_path / "bills.ods"
            make_ods(path)

            txs = [{
                "transaction_id": "x",
                "account_id": "bills",
                "date": "2026-09-20",
                "merchant_name": "VERIZON",
                "amount": 213.07,
                "pending": False,
            }]

            changes = plan_workbook_changes(
                path,
                txs,
                as_of=date(2026, 9, 20),
                cycle="15th",
                bills_account_id="bills",
                posted_balance=900.25,
            )

            cells = {change.cell: change.new for change in changes}
            self.assertEqual(cells["L15"], 213.07)
            self.assertEqual(cells["K9"], 900.25)

            backup = apply_workbook_changes(path, changes)
            self.assertIsNotNone(backup)
            self.assertTrue(backup.exists())
            self.assertTrue(backup.name.endswith(".bak.ods"))

            check = ODSWorkbook(path)
            self.assertEqual(check.get_cell_value("September", "L15"), 213.07)
            self.assertEqual(check.get_cell_value("September", "K9"), 900.25)
            self.assertEqual(check.get_cell_value("September", "I15"), "Verizon")

            with ZipFile(path, "r") as archive:
                self.assertEqual(archive.read("mimetype").decode(), MIMETYPE)
                self.assertIn("META-INF/manifest.xml", archive.namelist())


    def test_guttered_theme_uses_shifted_monthly_coordinates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bills-guttered.ods"
            make_ods(path, guttered=True)

            txs = [{
                "transaction_id": "x",
                "account_id": "bills",
                "date": "2026-09-20",
                "merchant_name": "VERIZON",
                "amount": 213.07,
                "pending": False,
            }]

            changes = plan_workbook_changes(
                path,
                txs,
                as_of=date(2026, 9, 20),
                cycle="15th",
                bills_account_id="bills",
                posted_balance=900.25,
            )

            cells = {change.cell: change.new for change in changes}
            self.assertEqual(cells["M16"], 213.07)
            self.assertEqual(cells["L10"], 900.25)


    def test_year_qualified_month_sheet_is_preferred(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bills-year.ods"
            make_ods(path, year_qualified=True)
            workbook = ODSWorkbook(path)

            self.assertEqual(
                month_sheet_name(workbook, date(2026, 9, 20)),
                "September 2026",
            )

            txs = [{
                "transaction_id": "x",
                "account_id": "bills",
                "date": "2026-09-20",
                "merchant_name": "VERIZON",
                "amount": 213.07,
                "pending": False,
            }]
            changes = plan_workbook_changes(
                path,
                txs,
                as_of=date(2026, 9, 20),
                cycle="15th",
                bills_account_id="bills",
                posted_balance=900.25,
            )
            self.assertTrue(all(change.sheet == "September 2026" for change in changes))

    def test_choose_workbook_requires_ods(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "workbook"
            folder.mkdir()
            make_ods(folder / "current.ODS")
            (folder / "ignored.xlsx").write_bytes(b"not used")

            self.assertEqual(choose_workbook(root).name, "current.ODS")
            with self.assertRaises(ValueError):
                choose_workbook(root, str(folder / "ignored.xlsx"))


if __name__ == "__main__":
    unittest.main()
