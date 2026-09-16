import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from biweekly_bills.self_test import run_self_test


MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"

CONTENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0"
 xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2"
 office:version="1.3">
 <office:body>
  <office:spreadsheet>
   <table:table table:name="September">
    <table:table-row table:number-rows-repeated="8">
     <table:table-cell table:number-columns-repeated="12"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:number-columns-repeated="12"/>
    </table:table-row>
    <table:table-row table:number-rows-repeated="5">
     <table:table-cell table:number-columns-repeated="12"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:number-columns-repeated="8"/>
     <table:table-cell office:value-type="string"><text:p>Verizon</text:p></table:table-cell>
     <table:table-cell table:number-columns-repeated="3"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:number-columns-repeated="8"/>
     <table:table-cell office:value-type="string"><text:p>Cox</text:p></table:table-cell>
     <table:table-cell table:number-columns-repeated="3"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:number-columns-repeated="8"/>
     <table:table-cell office:value-type="string"><text:p>USAA</text:p></table:table-cell>
     <table:table-cell table:number-columns-repeated="3"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:number-columns-repeated="8"/>
     <table:table-cell office:value-type="string"><text:p>Acellus Academy</text:p></table:table-cell>
     <table:table-cell table:number-columns-repeated="3"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:number-columns-repeated="8"/>
     <table:table-cell office:value-type="string"><text:p>Star Card</text:p></table:table-cell>
     <table:table-cell table:number-columns-repeated="3"/>
    </table:table-row>
    <table:table-row>
     <table:table-cell table:formula="of:=SUM([.L15:.L19])" office:value-type="float" office:value="0"><text:p>0</text:p></table:table-cell>
     <table:table-cell table:number-columns-repeated="11"/>
    </table:table-row>
   </table:table>
  </office:spreadsheet>
 </office:body>
</office:document-content>
"""

MANIFEST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest
 xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
 manifest:version="1.3">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
"""


def make_ods(path: Path, *, year_qualified: bool = False) -> None:
    body = CONTENT_XML
    if year_qualified:
        body = body.replace('table:name="September"', 'table:name="September 2026"')
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", MIMETYPE, compress_type=ZIP_STORED)
        archive.writestr("content.xml", body, compress_type=ZIP_DEFLATED)
        archive.writestr("META-INF/manifest.xml", MANIFEST_XML, compress_type=ZIP_DEFLATED)


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


class SelfTestTests(unittest.TestCase):
    def test_self_test_preserves_original_and_exercises_ods_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bills.ods"
            make_ods(path)
            before = file_hash(path)

            checks = run_self_test(path)

            self.assertEqual(file_hash(path), before)
            names = {check.name for check in checks}
            self.assertEqual(
                names,
                {
                    "ODS package",
                    "Sheets",
                    "Bill matching",
                    "Backup",
                    "ODS write",
                    "ODS members",
                    "Sheet preservation",
                    "Formula preservation",
                    "Original safety",
                },
            )



    def test_self_test_accepts_year_qualified_month_tabs(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bills-year.ods"
            make_ods(path, year_qualified=True)

            checks = run_self_test(path)

            self.assertIn("Bill matching", {check.name for check in checks})
            self.assertIn("Original safety", {check.name for check in checks})

if __name__ == "__main__":
    unittest.main()
