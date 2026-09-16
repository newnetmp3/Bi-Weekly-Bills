import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from biweekly_bills.control_panel import CONTROL_SHEET, install_control_panel
from biweekly_bills.ods_workbook import ODSWorkbook


MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"

CONTENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2"
 office:version="1.3">
 <office:automatic-styles/>
 <office:body>
  <office:spreadsheet>
   <table:table table:name="September">
    <table:table-row>
     <table:table-cell table:formula="of:=1+1" office:value-type="float" office:value="2"><text:p>2</text:p></table:table-cell>
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


def make_ods(path: Path) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", MIMETYPE, compress_type=ZIP_STORED)
        archive.writestr("content.xml", CONTENT_XML, compress_type=ZIP_DEFLATED)
        archive.writestr("META-INF/manifest.xml", MANIFEST_XML, compress_type=ZIP_DEFLATED)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


class ControlPanelTests(unittest.TestCase):
    def test_install_control_panel_preserves_original_and_adds_macros(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "bills.ods"
            backups = root / "backups"
            make_ods(path)
            before_hash = digest(path)

            backup = install_control_panel(path, backup_dir=backups)

            self.assertTrue(backup.exists())
            self.assertEqual(digest(backup), before_hash)

            workbook = ODSWorkbook(path)
            self.assertEqual(workbook.sheet_names[0], CONTROL_SHEET)
            self.assertEqual(workbook.sheet_names[1:], ["September"])

            with ZipFile(path, "r") as archive:
                names = set(archive.namelist())
                self.assertIn("Basic/script-lc.xml", names)
                self.assertIn("Basic/BiWeeklyBills/script-lb.xml", names)
                self.assertIn("Basic/BiWeeklyBills/Module1.xml", names)

                macro = archive.read("Basic/BiWeeklyBills/Module1.xml").decode("utf-8")
                self.assertIn("Sub RunSelfTest()", macro)
                self.assertIn("Sub RunUnitTests()", macro)
                self.assertIn("Sub RunDrySync()", macro)
                self.assertIn("Sub ShowAccounts()", macro)
                self.assertIn("Sub RepairConnection()", macro)
                self.assertIn("Sub SandboxReauthTest()", macro)
                self.assertIn("Sub ApplyWorkbookTheme()", macro)
                self.assertIn("Sub RefreshWorkbookHealth()", macro)
                self.assertIn("Sub RunPreProductionCheck()", macro)
                self.assertIn("Sub CreateNextYear()", macro)
                self.assertIn("Sub ResetMonthForNewYear(", macro)
                self.assertIn("Sub PopulateYearFromSetup(", macro)
                self.assertIn("Function HighestWorkbookYear()", macro)
                self.assertIn("Function ExistingMonthSheetName(", macro)
                self.assertIn("Sub MigrateLegacyMonthTabsToYear(", macro)
                self.assertIn("Sub DeleteYearTabs(", macro)
                self.assertIn("Sub ApplyWorkbookNumberFormats()", macro)
                self.assertIn("Sub ApplyExceptionConditionalFormatting()", macro)
                self.assertIn("Sub ProtectWorkbookFormulaCells()", macro)
                self.assertIn("Sub FreezeWorkbookHeaders()", macro)
                self.assertIn("Function CountWorkbookFormulaErrors()", macro)
                self.assertIn("Function CountDuplicateSetupBills()", macro)
                self.assertIn("Function CountCurrentMonthSyncIssues()", macro)
                self.assertIn("Sub ManageMonthlyBills()", macro)
                self.assertIn("Sub ManageSetupBills()", macro)
                self.assertIn("Sub LoadMonthlyBillEditor()", macro)
                self.assertIn("Sub ApplyMonthlyBillEditor()", macro)
                self.assertIn("Sub ResetMonthlyBillEditor()", macro)
                self.assertIn("Sub LoadSetupBillEditor()", macro)
                self.assertIn("Sub ApplySetupBillEditor()", macro)
                self.assertIn("Sub ResetSetupBillEditor()", macro)
                self.assertIn("Sub SyncSetupBillsToFutureMonths()", macro)
                self.assertIn("Function UpdateSetupForMonthlyRemoval(", macro)
                self.assertIn("Function PropagateSetupBillForward(", macro)
                self.assertIn("Function RemoveBillFromFutureMonths(", macro)
                self.assertIn("Function HasRecordedPayment(", macro)
                self.assertIn("Sub InstallSetupBillManagerButtons(", macro)
                self.assertIn("Sub SetSimpleListValidation(", macro)
                self.assertIn("Sub SetBillListValidation(", macro)
                self.assertIn("Function IsSetupBillRecord(", macro)
                self.assertIn("Sub RebuildSetupBillDropdownSource()", macro)
                self.assertIn('InStr(normalized, "nfcu workflow") = 1', macro)
                self.assertIn('normalized = "bill"', macro)
                self.assertIn("$Setup.$Q$3:$Q$202", macro)
                self.assertIn("IsValidCycleText(cycleText)", macro)
                self.assertIn("ValidationType.LIST", macro)
                self.assertIn("ShowList = 1", macro)
                self.assertIn("Sub InstallMonthlyBillManagerButton(", macro)
                self.assertIn("Function BackupBeforeBillChange(", macro)
                self.assertIn("Sub StyleMonthSheet(", macro)
                self.assertIn("Sub EnsureSheetGutter(", macro)
                self.assertIn("Function HasSheetGutter(", macro)
                self.assertIn("Sub FixDarkCellContrast(", macro)
                self.assertIn("Sub StyleSetupSheet(", macro)
                self.assertIn("Sub StyleDebtSheet(", macro)
                self.assertIn("Sub StyleDashboardSheet(", macro)
                self.assertNotIn("PLAID_SECRET", macro)
                self.assertNotIn("access_token", macro)
                self.assertNotIn("InputBox(", macro)
                self.assertNotIn("ChoosePayPeriod", macro)
                self.assertIn("preserved because Paid data exists", macro)
                self.assertIn("SetBillEditorStatus", macro)
                self.assertNotIn("$Setup.$B$3:$B$201", macro)
                self.assertIn("Month(Date)", macro)

                content = archive.read("content.xml").decode("utf-8")
                self.assertIn("vnd.sun.star.script:BiWeeklyBills.Module1.RunSelfTest", content)
                self.assertIn("vnd.sun.star.script:BiWeeklyBills.Module1.RunUnitTests", content)
                self.assertIn("vnd.sun.star.script:BiWeeklyBills.Module1.RunDrySync", content)
                self.assertIn("vnd.sun.star.script:BiWeeklyBills.Module1.ShowAccounts", content)
                self.assertIn("vnd.sun.star.script:BiWeeklyBills.Module1.RepairConnection", content)
                self.assertIn("vnd.sun.star.script:BiWeeklyBills.Module1.SandboxReauthTest", content)
                self.assertIn("vnd.sun.star.script:BiWeeklyBills.Module1.ApplyWorkbookTheme", content)
                self.assertIn("vnd.sun.star.script:BiWeeklyBills.Module1.RefreshWorkbookHealth", content)
                self.assertIn("vnd.sun.star.script:BiWeeklyBills.Module1.RunPreProductionCheck", content)
                self.assertIn("vnd.sun.star.script:BiWeeklyBills.Module1.CreateNextYear", content)
                self.assertIn("CREATE NEXT YEAR", content)
                self.assertIn("Formula health: not checked", content)
                self.assertIn("Current month sync: not checked", content)
                self.assertIn("White = editable", content)
                self.assertIn("Amber / red = attention", content)
                self.assertIn("Family Budget Control Center", content)
                self.assertIn('table:formula="of:=1+1"', content)
                self.assertIn('xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2"', content)

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "bills.ods"
            make_ods(path)

            install_control_panel(path, backup_dir=root / "backups")
            install_control_panel(path, backup_dir=root / "backups")

            workbook = ODSWorkbook(path)
            self.assertEqual(workbook.sheet_names.count(CONTROL_SHEET), 1)

            with ZipFile(path, "r") as archive:
                content = archive.read("content.xml").decode("utf-8")
                self.assertEqual(content.count("Family Budget Control Center"), 1)

    def test_refuses_open_libreoffice_workbook(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "bills.ods"
            make_ods(path)
            lock = root / f".~lock.{path.name}#"
            lock.write_text("locked", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Close the workbook"):
                install_control_panel(path, backup_dir=root / "backups")


if __name__ == "__main__":
    unittest.main()
