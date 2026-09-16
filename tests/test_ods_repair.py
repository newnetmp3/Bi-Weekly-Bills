import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from biweekly_bills.ods_repair import OPENFORMULA_NS, repair_openformula_namespace


MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"
MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
"""


def content(formula: str, *, include_of: bool) -> bytes:
    of_decl = f' xmlns:of="{OPENFORMULA_NS}"' if include_of else ""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"{of_decl}
 office:version="1.3">
 <office:body>
  <office:spreadsheet>
   <table:table table:name="September">
    <table:table-row>
     <table:table-cell table:formula="{formula}" office:value-type="float" office:value="2"><text:p>2</text:p></table:table-cell>
    </table:table-row>
   </table:table>
  </office:spreadsheet>
 </office:body>
</office:document-content>
"""
    return xml.encode("utf-8")


def make_ods(path: Path, body: bytes) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", MIMETYPE, compress_type=ZIP_STORED)
        archive.writestr("content.xml", body, compress_type=ZIP_DEFLATED)
        archive.writestr("META-INF/manifest.xml", MANIFEST, compress_type=ZIP_DEFLATED)


class ODSRepairTests(unittest.TestCase):
    def test_repairs_namespace_when_formulas_match_pre_gui_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workbook = root / "bills.ods"
            backups = root / "backups"
            backups.mkdir()

            make_ods(workbook, content("of:=1+1", include_of=False))
            pre_gui = backups / "bills.20260915-160000-000000.pre-gui.bak.ods"
            make_ods(pre_gui, content("of:=1+1", include_of=True))

            repair_backup, detected_pre_gui = repair_openformula_namespace(workbook)

            self.assertIsNotNone(repair_backup)
            self.assertTrue(repair_backup.exists())
            self.assertEqual(detected_pre_gui, pre_gui)
            with ZipFile(workbook, "r") as archive:
                repaired = archive.read("content.xml")
            self.assertIn(f'xmlns:of="{OPENFORMULA_NS}"'.encode(), repaired)
            self.assertIn(b'table:formula="of:=1+1"', repaired)

    def test_refuses_if_formula_strings_changed_since_pre_gui_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workbook = root / "bills.ods"
            backups = root / "backups"
            backups.mkdir()

            make_ods(workbook, content("of:=1+2", include_of=False))
            pre_gui = backups / "bills.20260915-160000-000000.pre-gui.bak.ods"
            make_ods(pre_gui, content("of:=1+1", include_of=True))

            with self.assertRaisesRegex(RuntimeError, "formula strings differ"):
                repair_openformula_namespace(workbook)

    def test_noop_when_namespace_already_present(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workbook = root / "bills.ods"
            make_ods(workbook, content("of:=1+1", include_of=True))

            repair_backup, _ = repair_openformula_namespace(workbook)

            self.assertIsNone(repair_backup)


if __name__ == "__main__":
    unittest.main()
