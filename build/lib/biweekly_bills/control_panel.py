from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import shutil
import tempfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from .ods_workbook import ODSWorkbook


CONTROL_SHEET = "Bi-Weekly Bills Control"
MACRO_LIBRARY = "BiWeeklyBills"
MACRO_MODULE = "Module1"

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
    "library": "http://openoffice.org/2000/library",
    "of": "urn:oasis:names:tc:opendocument:xmlns:of:1.2",
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


def _q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


TABLE = _q("table", "table")
TABLE_NAME = _q("table", "name")
STYLE_NAME = _q("table", "style-name")
CELL = _q("table", "table-cell")
COVERED_CELL = _q("table", "covered-table-cell")
ROW = _q("table", "table-row")
COLUMN = _q("table", "table-column")
COL_SPAN = _q("table", "number-columns-spanned")
OFFICE_VALUE_TYPE = _q("office", "value-type")
TEXT_P = _q("text", "p")
TEXT_A = _q("text", "a")
XLINK_HREF = _q("xlink", "href")
XLINK_TYPE = _q("xlink", "type")
STYLE_STYLE = _q("style", "style")
STYLE_FAMILY = _q("style", "family")
STYLE_STYLE_NAME = _q("style", "name")


BASIC_CODE = r'''Option Explicit

Const CONTROL_SHEET As String = "Bi-Weekly Bills Control"
Const STATUS_CELL As String = "A23"

Sub RunSelfTest()
    RunAction "self-test", "ODS Self-Test"
End Sub

Sub RunUnitTests()
    RunAction "unit-tests", "Full Unit Tests"
End Sub

Sub RunDrySync()
    RunAction "dry-sync", "Plaid Dry-Run Sync"
End Sub

Sub ShowAccounts()
    RunAction "accounts", "Plaid Accounts"
End Sub

Sub RepairConnection()
    RunAction "update-link", "Repair Bank Connection"
End Sub

Sub SandboxReauthTest()
    RunAction "sandbox-reauth", "Sandbox Reauthentication Test"
End Sub

Sub ApplyWorkbookTheme()
    On Error GoTo Handler
    Dim backupPath As String
    backupPath = BackupBeforeTheme()
    SetStatus "Applying workbook theme..."
    StyleEverySheet
    CleanupKnownSetupArtifactsFromFutureMonths
    ThisComponent.calculateAll()
    RefreshWorkbookHealth
    ThisComponent.store()
    SetStatus "Workbook theme applied"
    MsgBox "Workbook theme applied to all sheets." & Chr(10) & Chr(10) & _
           "Backup created:" & Chr(10) & backupPath, 64, "Bi-Weekly Bills — Appearance"
    Exit Sub

Handler:
    SetStatus "Workbook theme: ERROR"
    MsgBox "Theme error " & Err & ": " & Error$, 16, "Bi-Weekly Bills"
End Sub

Sub CreateNextYear()
    On Error GoTo Handler

    Dim baseYear As Integer
    Dim newYear As Integer
    Dim backupPath As String
    Dim hadLegacy As Boolean
    Dim m As Integer
    Dim sourceName As String
    Dim targetName As String
    Dim targetSheet As Object

    baseYear = HighestWorkbookYear()
    newYear = baseYear + 1

    For m = 1 To 12
        sourceName = ExistingMonthSheetName(m, baseYear)
        If sourceName = "" Then
            MsgBox "Cannot create " & newYear & ": missing source tab for " & _
                   MonthNameByNumber(m) & " " & baseYear & ".", 16, "Bi-Weekly Bills — New Year"
            Exit Sub
        End If
        If Not HasSheetGutter(ThisComponent.Sheets.getByName(sourceName)) Then
            MsgBox "Apply / Refresh Workbook Theme before creating a new year. " & _
                   sourceName & " is not yet in the supported perpetual layout.", _
                   48, "Bi-Weekly Bills — New Year"
            Exit Sub
        End If

        targetName = MonthNameByNumber(m) & " " & newYear
        If ThisComponent.Sheets.hasByName(targetName) Then
            MsgBox targetName & " already exists. No year was created.", 48, "Bi-Weekly Bills — New Year"
            Exit Sub
        End If
    Next m

    If MsgBox( _
        "Create a complete set of monthly tabs for " & newYear & "?" & Chr(10) & Chr(10) & _
        "The " & baseYear & " tabs will remain as historical records." & Chr(10) & _
        "New tabs will keep formulas and formatting, clear old numeric/payment data, and repopulate active bills from Setup.", _
        1 + 32, _
        "Bi-Weekly Bills — Create " & newYear _
    ) <> 1 Then Exit Sub

    ThisComponent.store()
    backupPath = BackupBeforeNewYear(newYear)
    hadLegacy = HasLegacyMonthTabs()

    For m = 1 To 12
        sourceName = ExistingMonthSheetName(m, baseYear)
        targetName = MonthNameByNumber(m) & " " & newYear

        ThisComponent.Sheets.copyByName(sourceName, targetName, ThisComponent.Sheets.Count)
        targetSheet = ThisComponent.Sheets.getByName(targetName)
        If targetSheet.isProtected() Then targetSheet.unprotect("")
        ResetMonthForNewYear targetSheet, m, newYear
    Next m

    PopulateYearFromSetup newYear

    ' Once a second year exists, make the original history explicit too.
    If hadLegacy Then MigrateLegacyMonthTabsToYear baseYear

    StyleEverySheet
    ThisComponent.calculateAll()
    RefreshWorkbookHealth
    ThisComponent.store()

    MsgBox "Created January through December " & newYear & "." & Chr(10) & Chr(10) & _
           "All " & baseYear & " history was preserved." & Chr(10) & _
           "Backup created:" & Chr(10) & backupPath, _
           64, "Bi-Weekly Bills — " & newYear
    Exit Sub

Handler:
    Dim failureNumber As Long
    Dim failureText As String
    failureNumber = Err
    failureText = Error$
    On Error Resume Next
    DeleteYearTabs newYear
    If hadLegacy Then RollbackLegacyMigration baseYear
    On Error GoTo 0
    MsgBox "New-year creation failed and partial " & newYear & " tabs were rolled back." & Chr(10) & _
           "Error " & failureNumber & ": " & failureText & Chr(10) & Chr(10) & _
           "The pre-year backup remains available at:" & Chr(10) & backupPath, _
           16, "Bi-Weekly Bills — New Year"
End Sub

Sub RunPreProductionCheck()
    RefreshWorkbookHealth
    RunAction "pre-production", "Pre-Production Check"
End Sub

Sub RefreshWorkbookHealth()
    On Error GoTo Handler

    Dim formulaErrors As Long
    Dim duplicateSetup As Long
    Dim invalidCycles As Long
    Dim syncIssues As Long
    Dim monthlyDuplicates As Long
    Dim missingSetup As Long

    ThisComponent.calculateAll()

    formulaErrors = CountWorkbookFormulaErrors()
    duplicateSetup = CountDuplicateSetupBills()
    invalidCycles = CountInvalidSetupCycles()
    syncIssues = CountCurrentMonthSyncIssues()
    monthlyDuplicates = CountCurrentMonthDuplicateBills()
    missingSetup = CountCurrentMonthBillsMissingSetup()

    SetHealthLine "Formula health:", _
        IIf(formulaErrors = 0, "Formula health: PASS", "Formula health: " & formulaErrors & " error cell(s)"), _
        (formulaErrors = 0)

    SetHealthLine "Setup health:", _
        IIf(duplicateSetup + invalidCycles = 0, _
            "Setup health: PASS", _
            "Setup health: " & duplicateSetup & " duplicate(s), " & invalidCycles & " invalid cycle(s)"), _
        (duplicateSetup + invalidCycles = 0)

    SetHealthLine "Current month sync:", _
        IIf(syncIssues = 0, "Current month sync: PASS", "Current month sync: " & syncIssues & " issue(s)"), _
        (syncIssues = 0)

    SetHealthLine "Monthly consistency:", _
        IIf(monthlyDuplicates + missingSetup = 0, _
            "Monthly consistency: PASS", _
            "Monthly consistency: " & monthlyDuplicates & " duplicate(s), " & missingSetup & " bill(s) missing Setup"), _
        (monthlyDuplicates + missingSetup = 0)

    RefreshExceptionHighlights
    SetStatus "Workbook health refreshed"
    Exit Sub

Handler:
    SetStatus "Workbook health: ERROR"
    MsgBox "Workbook health error " & Err & ": " & Error$, 16, "Bi-Weekly Bills"
End Sub

Sub RunAction(actionName As String, titleText As String)
    On Error GoTo Handler

    Dim projectRoot As String
    Dim wrapperPath As String
    Dim outputPath As String
    Dim shellArgs As String
    Dim resultText As String
    Dim resultStatus As String

    projectRoot = GetProjectRoot()
    wrapperPath = projectRoot & "/scripts/libreoffice_gui_action.sh"
    outputPath = "/tmp/biweekly-bills-gui-" & actionName & ".txt"

    If Dir(wrapperPath) = "" Then
        SetStatus titleText & ": helper not found"
        MsgBox "GUI helper not found:" & Chr(10) & wrapperPath & Chr(10) & Chr(10) & _
               "Run git pull and pip install -e . from the project once.", 16, "Bi-Weekly Bills"
        Exit Sub
    End If

    SetStatus "Running " & titleText & "..."
    shellArgs = QuoteArg(wrapperPath) & " " & QuoteArg(actionName) & " " & QuoteArg(outputPath)
    Shell "/bin/bash", 0, shellArgs, True

    resultText = ReadAllText(outputPath)
    If InStr(resultText, "__BWB_STATUS__:PASS") > 0 Then
        resultStatus = "PASS"
        resultText = Replace(resultText, "__BWB_STATUS__:PASS", "")
    Else
        resultStatus = "FAIL"
        resultText = Replace(resultText, "__BWB_STATUS__:FAIL", "")
    End If

    SetStatus titleText & ": " & resultStatus
    If Len(resultText) > 3800 Then
        resultText = "... last 3800 characters ..." & Chr(10) & Right(resultText, 3800)
    End If

    If resultStatus = "PASS" Then
        MsgBox Trim(resultText), 64, "Bi-Weekly Bills — " & titleText & " — PASS"
    Else
        MsgBox Trim(resultText), 16, "Bi-Weekly Bills — " & titleText & " — FAIL"
    End If
    Exit Sub

Handler:
    SetStatus titleText & ": ERROR"
    MsgBox "LibreOffice macro error " & Err & ": " & Error$, 16, "Bi-Weekly Bills"
End Sub

Sub ManageMonthlyBills()
    On Error Resume Next
    Dim sheet As Object
    sheet = ThisComponent.CurrentController.ActiveSheet
    If Not IsMonthSheet(sheet.Name) Then Exit Sub
    SetBillEditorStatus sheet, "N12:P12", _
        "Use the editor above: choose Action / Bill / Cycle, then APPLY.", "info"
    ThisComponent.CurrentController.select(sheet.getCellRangeByName("O5"))
    On Error GoTo 0
End Sub

Sub ManageSetupBills()
    On Error Resume Next
    Dim sheet As Object
    sheet = ThisComponent.Sheets.getByName("Setup")
    ThisComponent.CurrentController.setActiveSheet(sheet)
    SetBillEditorStatus sheet, "K13:O13", _
        "Use the Bill Editor: choose an action, select/type a bill, then APPLY.", "info"
    ThisComponent.CurrentController.select(sheet.getCellRangeByName("L3"))
    On Error GoTo 0
End Sub

Sub LoadMonthlyBillEditor()
    On Error GoTo Handler

    Dim sheet As Object
    Dim setupSheet As Object
    Dim setupBillCol As Integer
    Dim setupRow As Long
    Dim billName As String

    sheet = ThisComponent.CurrentController.ActiveSheet
    If Not IsMonthSheet(sheet.Name) Then Exit Sub

    billName = Trim(sheet.getCellRangeByName("O6").String)
    If billName = "" Then
        SetBillEditorStatus sheet, "N12:P12", "Select or type a bill first.", "warning"
        Exit Sub
    End If

    If Not ThisComponent.Sheets.hasByName("Setup") Then
        SetBillEditorStatus sheet, "N12:P12", "Setup sheet was not found.", "error"
        Exit Sub
    End If

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    setupBillCol = SetupBillColumn(setupSheet)
    setupRow = FindSetupBillRow(setupSheet, setupBillCol, billName)

    If setupRow < 0 Then
        sheet.getCellRangeByName("O5").String = "Add"
        sheet.getCellRangeByName("O7").String = "Both"
        sheet.getCellRangeByName("O8").String = ""
        sheet.getCellRangeByName("O9").Formula = ""
        sheet.getCellRangeByName("O10").String = ""
        SetBillEditorStatus sheet, "N12:P12", _
            billName & " is new. Fill the fields and choose APPLY.", "info"
        Exit Sub
    End If

    sheet.getCellRangeByName("O5").String = "Edit"
    sheet.getCellRangeByName("O7").String = _
        NormalizeCycleLabel(setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String)
    CopyCellDisplayValue setupSheet.getCellByPosition(setupBillCol + 2, setupRow), sheet.getCellRangeByName("O8")
    CopyCellDisplayValue setupSheet.getCellByPosition(setupBillCol + 3, setupRow), sheet.getCellRangeByName("O9")
    CopyCellDisplayValue setupSheet.getCellByPosition(setupBillCol + 4, setupRow), sheet.getCellRangeByName("O10")
    SetBillEditorStatus sheet, "N12:P12", _
        "Loaded " & billName & " from Setup. Edit fields, then APPLY.", "good"
    Exit Sub

Handler:
    SetBillEditorStatus sheet, "N12:P12", _
        "Load failed: " & Error$, "error"
End Sub

Sub ApplyMonthlyBillEditor()
    On Error GoTo Handler

    Dim sheet As Object
    Dim setupSheet As Object
    Dim setupBillCol As Integer
    Dim setupRow As Long
    Dim actionName As String
    Dim billName As String
    Dim cycleName As String
    Dim whenText As String
    Dim dueText As String
    Dim methodText As String
    Dim backupPath As String
    Dim resultText As String
    Dim startMonth As Integer
    Dim existingCycle As String

    sheet = ThisComponent.CurrentController.ActiveSheet
    If Not IsMonthSheet(sheet.Name) Then Exit Sub

    actionName = LCase(Trim(sheet.getCellRangeByName("O5").String))
    billName = Trim(sheet.getCellRangeByName("O6").String)
    cycleName = Trim(sheet.getCellRangeByName("O7").String)
    whenText = Trim(sheet.getCellRangeByName("O8").String)
    dueText = Trim(CellPromptValue(sheet.getCellRangeByName("O9")))
    methodText = Trim(sheet.getCellRangeByName("O10").String)

    If actionName <> "add" And actionName <> "edit" And actionName <> "remove" Then
        SetBillEditorStatus sheet, "N12:P12", _
            "Choose Add, Edit, or Remove in Action.", "warning"
        Exit Sub
    End If
    If billName = "" Then
        SetBillEditorStatus sheet, "N12:P12", "Bill is required.", "warning"
        Exit Sub
    End If
    If Not IsValidCycleText(cycleName) Then
        SetBillEditorStatus sheet, "N12:P12", _
            "Cycle must be 1st, 15th, or Both.", "warning"
        Exit Sub
    End If

    If Not ThisComponent.Sheets.hasByName("Setup") Then
        SetBillEditorStatus sheet, "N12:P12", "Setup sheet was not found.", "error"
        Exit Sub
    End If

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    If setupSheet.isProtected() Then setupSheet.unprotect("")
    setupBillCol = SetupBillColumn(setupSheet)
    setupRow = FindSetupBillRow(setupSheet, setupBillCol, billName)
    startMonth = Month(Date)
    backupPath = BackupBeforeBillChange(sheet.Name)

    If actionName = "add" Then
        If setupRow < 0 Then
            setupRow = FindEmptySetupRow(setupSheet, setupBillCol)
            If setupRow < 0 Then
                SetBillEditorStatus sheet, "N12:P12", _
                    "No empty Setup row is available.", "error"
                Exit Sub
            End If
            setupSheet.getCellByPosition(setupBillCol, setupRow).String = billName
            setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String = _
                NormalizeCycleLabel(cycleName)
        Else
            existingCycle = setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String
            setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String = _
                MergeCycle(existingCycle, cycleName)
        End If
        WriteSetupFieldsFromEditor setupSheet, setupRow, setupBillCol, _
            whenText, dueText, methodText, "", "", False
        SetSetupActive setupSheet, setupRow, setupBillCol, True

    ElseIf actionName = "edit" Then
        If setupRow < 0 Then
            SetBillEditorStatus sheet, "N12:P12", _
                billName & " is not on Setup. Use Add instead.", "warning"
            Exit Sub
        End If
        setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String = _
            NormalizeCycleLabel(cycleName)
        WriteSetupFieldsFromEditor setupSheet, setupRow, setupBillCol, _
            whenText, dueText, methodText, "", "", False
        SetSetupActive setupSheet, setupRow, setupBillCol, True

    Else
        If setupRow < 0 Then
            SetBillEditorStatus sheet, "N12:P12", _
                billName & " was not found on Setup.", "warning"
            Exit Sub
        End If
    End If

    If actionName = "remove" Then
        resultText = UpdateSetupForMonthlyRemoval(billName, cycleName) & "  " & _
                     RemoveBillFromFutureMonths(billName, cycleName, startMonth)
    Else
        resultText = PropagateSetupBillForward(billName, billName, startMonth)
    End If

    RefreshBillEditorLists
    ThisComponent.calculateAll()
    ProtectWorkbookFormulaCells
    ThisComponent.store()

    SetBillEditorStatus sheet, "N12:P12", _
        UCase(Left(actionName, 1)) & Mid(actionName, 2) & " applied. " & _
        resultText & " Backup: " & backupPath, "good"
    Exit Sub

Handler:
    SetBillEditorStatus sheet, "N12:P12", _
        "Apply failed: " & Error$, "error"
End Sub

Sub ResetMonthlyBillEditor()
    On Error Resume Next
    Dim sheet As Object
    sheet = ThisComponent.CurrentController.ActiveSheet
    If Not IsMonthSheet(sheet.Name) Then Exit Sub

    sheet.getCellRangeByName("O5").String = "Add"
    sheet.getCellRangeByName("O6").String = ""
    sheet.getCellRangeByName("O7").String = "Both"
    sheet.getCellRangeByName("O8").String = ""
    sheet.getCellRangeByName("O9").Formula = ""
    sheet.getCellRangeByName("O10").String = ""
    SetBillEditorStatus sheet, "N12:P12", "Editor reset.", "info"
    On Error GoTo 0
End Sub

Sub LoadSetupBillEditor()
    On Error GoTo Handler

    Dim sheet As Object
    Dim setupBillCol As Integer
    Dim setupRow As Long
    Dim billName As String

    sheet = ThisComponent.Sheets.getByName("Setup")
    billName = Trim(sheet.getCellRangeByName("L4").String)

    If billName = "" Then
        SetBillEditorStatus sheet, "K13:O13", "Select or type a bill first.", "warning"
        Exit Sub
    End If

    setupBillCol = SetupBillColumn(sheet)
    setupRow = FindSetupBillRow(sheet, setupBillCol, billName)

    If setupRow < 0 Then
        sheet.getCellRangeByName("L3").String = "Add"
        sheet.getCellRangeByName("L5").String = "Both"
        sheet.getCellRangeByName("L6").String = ""
        sheet.getCellRangeByName("L7").Formula = ""
        sheet.getCellRangeByName("L8").String = ""
        sheet.getCellRangeByName("L9").String = ""
        sheet.getCellRangeByName("L10").String = ""
        SetBillEditorStatus sheet, "K13:O13", _
            billName & " is new. Fill the fields and choose APPLY.", "info"
        Exit Sub
    End If

    sheet.getCellRangeByName("L3").String = "Edit"
    sheet.getCellRangeByName("L5").String = _
        NormalizeCycleLabel(sheet.getCellByPosition(setupBillCol + 1, setupRow).String)
    CopyCellDisplayValue sheet.getCellByPosition(setupBillCol + 2, setupRow), sheet.getCellRangeByName("L6")
    CopyCellDisplayValue sheet.getCellByPosition(setupBillCol + 3, setupRow), sheet.getCellRangeByName("L7")
    CopyCellDisplayValue sheet.getCellByPosition(setupBillCol + 4, setupRow), sheet.getCellRangeByName("L8")
    CopyCellDisplayValue sheet.getCellByPosition(setupBillCol + 5, setupRow), sheet.getCellRangeByName("L9")
    CopyCellDisplayValue sheet.getCellByPosition(setupBillCol + 7, setupRow), sheet.getCellRangeByName("L10")
    SetBillEditorStatus sheet, "K13:O13", _
        "Loaded " & billName & ". Edit fields, then APPLY.", "good"
    Exit Sub

Handler:
    SetBillEditorStatus sheet, "K13:O13", "Load failed: " & Error$, "error"
End Sub

Sub ApplySetupBillEditor()
    On Error GoTo Handler

    Dim sheet As Object
    Dim setupBillCol As Integer
    Dim setupRow As Long
    Dim actionName As String
    Dim billName As String
    Dim cycleName As String
    Dim whenText As String
    Dim dueText As String
    Dim methodText As String
    Dim accountText As String
    Dim notesText As String
    Dim backupPath As String
    Dim resultText As String
    Dim startMonth As Integer

    sheet = ThisComponent.Sheets.getByName("Setup")
    If sheet.isProtected() Then sheet.unprotect("")

    actionName = LCase(Trim(sheet.getCellRangeByName("L3").String))
    billName = Trim(sheet.getCellRangeByName("L4").String)
    cycleName = Trim(sheet.getCellRangeByName("L5").String)
    whenText = Trim(sheet.getCellRangeByName("L6").String)
    dueText = Trim(CellPromptValue(sheet.getCellRangeByName("L7")))
    methodText = Trim(sheet.getCellRangeByName("L8").String)
    accountText = Trim(sheet.getCellRangeByName("L9").String)
    notesText = Trim(sheet.getCellRangeByName("L10").String)

    If actionName <> "add" And actionName <> "edit" And actionName <> "remove" Then
        SetBillEditorStatus sheet, "K13:O13", _
            "Choose Add, Edit, or Remove in Action.", "warning"
        Exit Sub
    End If
    If billName = "" Then
        SetBillEditorStatus sheet, "K13:O13", "Bill is required.", "warning"
        Exit Sub
    End If

    setupBillCol = SetupBillColumn(sheet)
    setupRow = FindSetupBillRow(sheet, setupBillCol, billName)
    startMonth = Month(Date)
    backupPath = BackupBeforeBillChange("Setup")

    If actionName = "add" Or actionName = "edit" Then
        If Not IsValidCycleText(cycleName) Then
            SetBillEditorStatus sheet, "K13:O13", _
                "Cycle must be 1st, 15th, or Both.", "warning"
            Exit Sub
        End If

        If actionName = "add" And setupRow < 0 Then
            setupRow = FindEmptySetupRow(sheet, setupBillCol)
            If setupRow < 0 Then
                SetBillEditorStatus sheet, "K13:O13", _
                    "No empty Setup row is available.", "error"
                Exit Sub
            End If
            sheet.getCellByPosition(setupBillCol, setupRow).String = billName
        ElseIf actionName = "edit" And setupRow < 0 Then
            SetBillEditorStatus sheet, "K13:O13", _
                billName & " was not found. Use Add instead.", "warning"
            Exit Sub
        End If

        sheet.getCellByPosition(setupBillCol + 1, setupRow).String = _
            NormalizeCycleLabel(cycleName)
        WriteSetupFieldsFromEditor sheet, setupRow, setupBillCol, _
            whenText, dueText, methodText, accountText, notesText, True
        SetSetupActive sheet, setupRow, setupBillCol, True
    Else
        If setupRow < 0 Then
            SetBillEditorStatus sheet, "K13:O13", _
                billName & " was not found on Setup.", "warning"
            Exit Sub
        End If
    End If

    If actionName = "remove" Then
        SetSetupActive sheet, setupRow, setupBillCol, False
        resultText = "Marked inactive. " & _
            RemoveBillFromFutureMonths(billName, "both", startMonth)
    Else
        resultText = PropagateSetupBillForward(billName, billName, startMonth)
    End If

    RefreshBillEditorLists
    ThisComponent.calculateAll()
    ProtectWorkbookFormulaCells
    ThisComponent.store()

    SetBillEditorStatus sheet, "K13:O13", _
        UCase(Left(actionName, 1)) & Mid(actionName, 2) & " applied. " & _
        resultText & " Backup: " & backupPath, "good"
    Exit Sub

Handler:
    SetBillEditorStatus sheet, "K13:O13", "Apply failed: " & Error$, "error"
End Sub

Sub ResetSetupBillEditor()
    On Error Resume Next
    Dim sheet As Object
    sheet = ThisComponent.Sheets.getByName("Setup")

    sheet.getCellRangeByName("L3").String = "Add"
    sheet.getCellRangeByName("L4").String = ""
    sheet.getCellRangeByName("L5").String = "Both"
    sheet.getCellRangeByName("L6").String = ""
    sheet.getCellRangeByName("L7").Formula = ""
    sheet.getCellRangeByName("L8").String = ""
    sheet.getCellRangeByName("L9").String = ""
    sheet.getCellRangeByName("L10").String = ""
    SetBillEditorStatus sheet, "K13:O13", "Editor reset.", "info"
    On Error GoTo 0
End Sub

Sub CleanupKnownSetupArtifactsFromFutureMonths()
    On Error Resume Next
    Dim names
    Dim i As Integer
    Dim sheetName As String
    Dim sheetMonth As Integer
    Dim sheetYear As Integer
    Dim startMonth As Integer
    Dim startYear As Integer
    Dim monthSheet As Object
    Dim cycleIndex As Integer
    Dim baseCol As Integer
    Dim r As Long
    Dim billName As String

    startMonth = Month(Date)
    startYear = Year(Date)
    names = ThisComponent.Sheets.ElementNames

    For i = LBound(names) To UBound(names)
        sheetName = names(i)
        If IsMonthSheet(sheetName) Then
            sheetMonth = MonthNumberByName(sheetName)
            sheetYear = MonthSheetYear(sheetName)
            If sheetYear = 0 Then sheetYear = LegacyMonthYear()

            If sheetYear > startYear Or (sheetYear = startYear And sheetMonth >= startMonth) Then
                monthSheet = ThisComponent.Sheets.getByName(sheetName)

                For cycleIndex = 0 To 1
                    If cycleIndex = 0 Then
                        baseCol = CycleBaseColumn("1st")
                    Else
                        baseCol = CycleBaseColumn("15th")
                    End If

                    For r = 13 To 26
                        billName = Trim(monthSheet.getCellByPosition(baseCol, r).String)
                        If IsNonBillSetupLabel(billName) Then
                            If billName <> "" Then
                                If Not HasRecordedPayment(monthSheet, baseCol, r) Then
                                    If cycleIndex = 0 Then
                                        RemoveBillFromCycle monthSheet, billName, "1st"
                                    Else
                                        RemoveBillFromCycle monthSheet, billName, "15th"
                                    End If
                                End If
                            End If
                        End If
                    Next r
                Next cycleIndex
            End If
        End If
    Next i
    On Error GoTo 0
End Sub

Function CopyMonthBillDetailsToSetup( _
    monthSheet As Object, baseCol As Integer, monthRow As Long, _
    setupSheet As Object, setupBillCol As Integer, setupRow As Long) As Boolean

    On Error Resume Next
    Dim changed As Boolean

    If Trim(setupSheet.getCellByPosition(setupBillCol + 2, setupRow).String) = "" Then
        CopyCellDisplayValue monthSheet.getCellByPosition(baseCol + 1, monthRow), _
            setupSheet.getCellByPosition(setupBillCol + 2, setupRow)
        changed = True
    End If

    If setupSheet.getCellByPosition(setupBillCol + 3, setupRow).Type = 0 Then
        CopyCellDisplayValue monthSheet.getCellByPosition(baseCol + 2, monthRow), _
            setupSheet.getCellByPosition(setupBillCol + 3, setupRow)
        changed = True
    End If

    If Trim(setupSheet.getCellByPosition(setupBillCol + 4, setupRow).String) = "" Then
        CopyCellDisplayValue monthSheet.getCellByPosition(baseCol + 4, monthRow), _
            setupSheet.getCellByPosition(setupBillCol + 4, setupRow)
        changed = True
    End If

    CopyMonthBillDetailsToSetup = changed
    On Error GoTo 0
End Function

Function ReconcileSetupCyclesFromCurrentMonth() As String
    On Error GoTo Handler

    Dim setupSheet As Object
    Dim monthSheet As Object
    Dim setupBillCol As Integer
    Dim monthName As String
    Dim cycleIndex As Integer
    Dim baseCol As Integer
    Dim r As Long
    Dim billName As String
    Dim setupRow As Long
    Dim firstRow As Long
    Dim fifteenthRow As Long
    Dim inferredCycle As String
    Dim updatedCount As Long
    Dim createdCount As Long

    monthName = ExistingMonthSheetName(Month(Date), Year(Date))
    If monthName = "" Then
        ReconcileSetupCyclesFromCurrentMonth = "No current-month sheet found; Setup cycles were not migrated."
        Exit Function
    End If
    If Not ThisComponent.Sheets.hasByName("Setup") Then
        ReconcileSetupCyclesFromCurrentMonth = "Setup sheet not found."
        Exit Function
    End If

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    monthSheet = ThisComponent.Sheets.getByName(monthName)
    If setupSheet.isProtected() Then setupSheet.unprotect("")
    setupBillCol = SetupBillColumn(setupSheet)

    ' Walk both current-month bill lists. Each unique bill is reconciled to
    ' the placement already present in the user's current working month.
    For cycleIndex = 0 To 1
        If cycleIndex = 0 Then
            baseCol = CycleBaseColumn("1st")
        Else
            baseCol = CycleBaseColumn("15th")
        End If

        For r = 13 To 26
            billName = Trim(monthSheet.getCellByPosition(baseCol, r).String)
            If Not IsNonBillSetupLabel(billName) Then
                setupRow = FindSetupBillRow(setupSheet, setupBillCol, billName)

                If setupRow < 0 Then
                    setupRow = FindEmptySetupRow(setupSheet, setupBillCol)
                    If setupRow >= 0 Then
                        setupSheet.getCellByPosition(setupBillCol, setupRow).String = billName
                        createdCount = createdCount + 1
                    End If
                End If

                If setupRow >= 0 Then
                    firstRow = FindBillRow(monthSheet, CycleBaseColumn("1st"), billName)
                    fifteenthRow = FindBillRow(monthSheet, CycleBaseColumn("15th"), billName)

                    If firstRow >= 0 And fifteenthRow >= 0 Then
                        inferredCycle = "Both"
                    ElseIf fifteenthRow >= 0 Then
                        inferredCycle = "15th"
                    Else
                        inferredCycle = "1st"
                    End If

                    If setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String <> inferredCycle Then
                        setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String = inferredCycle
                        updatedCount = updatedCount + 1
                    End If

                    SetSetupActive setupSheet, setupRow, setupBillCol, True

                    If firstRow >= 0 Then
                        CopyMonthBillDetailsToSetup monthSheet, CycleBaseColumn("1st"), firstRow, _
                            setupSheet, setupBillCol, setupRow
                    ElseIf fifteenthRow >= 0 Then
                        CopyMonthBillDetailsToSetup monthSheet, CycleBaseColumn("15th"), fifteenthRow, _
                            setupSheet, setupBillCol, setupRow
                    End If
                End If
            End If
        Next r
    Next cycleIndex

    RefreshBillEditorLists
    ReconcileSetupCyclesFromCurrentMonth = _
        "Current-month placement reconciled to Setup. Cycle updates: " & updatedCount & _
        "; Setup rows created: " & createdCount & "."
    Exit Function

Handler:
    ReconcileSetupCyclesFromCurrentMonth = "Setup cycle reconciliation failed: " & Error$
End Function

Sub SyncSetupBillsToFutureMonths()
    On Error GoTo Handler

    Dim setupSheet As Object
    Dim setupBillCol As Integer
    Dim r As Long
    Dim billName As String
    Dim backupPath As String
    Dim startMonth As Integer
    Dim activeCount As Long
    Dim inactiveCount As Long
    Dim reconciliationText As String

    If Not ThisComponent.Sheets.hasByName("Setup") Then Exit Sub
    setupSheet = ThisComponent.Sheets.getByName("Setup")
    If setupSheet.isProtected() Then setupSheet.unprotect("")
    setupBillCol = SetupBillColumn(setupSheet)
    startMonth = Month(Date)

    backupPath = BackupBeforeBillChange("Setup-Sync")
    CleanupKnownSetupArtifactsFromFutureMonths
    reconciliationText = ReconcileSetupCyclesFromCurrentMonth()

    For r = 2 To 200
        billName = Trim(setupSheet.getCellByPosition(setupBillCol, r).String)
        If IsSetupBillRecord(setupSheet, r, setupBillCol) Then
            If IsSetupRowActive(setupSheet, r, setupBillCol) Then
                PropagateSetupBillForward billName, billName, startMonth
                activeCount = activeCount + 1
            Else
                RemoveBillFromFutureMonths billName, "both", startMonth
                inactiveCount = inactiveCount + 1
            End If
        End If
    Next r

    RefreshBillEditorLists
    ThisComponent.calculateAll()
    ProtectWorkbookFormulaCells
    ThisComponent.store()
    SetBillEditorStatus setupSheet, "K13:O13", _
        reconciliationText & " Synchronized current/future months. Active: " & activeCount & _
        "; inactive checked: " & inactiveCount & ". Backup: " & backupPath, "good"
    Exit Sub

Handler:
    SetBillEditorStatus setupSheet, "K13:O13", _
        "Sync failed: " & Error$, "error"
End Sub

Sub SetBillEditorStatus(sheet As Object, address As String, statusText As String, level As String)
    On Error Resume Next
    Dim area As Object
    Dim cell As Object
    area = sheet.getCellRangeByName(address)
    cell = area.getCellByPosition(0,0)
    cell.String = statusText
    area.CharWeight = 150
    area.CharColor = RGB(31,41,55)

    Select Case LCase(level)
        Case "good"
            area.CellBackColor = RGB(226,240,217)
            area.CharColor = RGB(55,86,35)
        Case "warning"
            area.CellBackColor = RGB(255,242,204)
            area.CharColor = RGB(127,96,0)
        Case "error"
            area.CellBackColor = RGB(244,204,204)
            area.CharColor = RGB(156,0,6)
        Case Else
            area.CellBackColor = RGB(221,235,247)
            area.CharColor = RGB(23,54,93)
    End Select
    On Error GoTo 0
End Sub

Sub WriteSetupFieldsFromEditor( _
    setupSheet As Object, setupRow As Long, setupBillCol As Integer, _
    whenText As String, dueText As String, methodText As String, _
    accountText As String, notesText As String, writeAccountAndNotes As Boolean)

    setupSheet.getCellByPosition(setupBillCol + 2, setupRow).String = whenText
    SetCellFromInput setupSheet.getCellByPosition(setupBillCol + 3, setupRow), dueText
    setupSheet.getCellByPosition(setupBillCol + 4, setupRow).String = methodText

    If writeAccountAndNotes Then
        setupSheet.getCellByPosition(setupBillCol + 5, setupRow).String = accountText
        setupSheet.getCellByPosition(setupBillCol + 7, setupRow).String = notesText
    End If
End Sub

Function SetupBillColumn(setupSheet As Object) As Integer
    If HasSheetGutter(setupSheet) Then
        SetupBillColumn = 1
    Else
        SetupBillColumn = 0
    End If
End Function

Function FindEmptySetupRow(setupSheet As Object, setupBillCol As Integer) As Long
    Dim r As Long
    For r = 2 To 200
        If Trim(setupSheet.getCellByPosition(setupBillCol, r).String) = "" Then
            FindEmptySetupRow = r
            Exit Function
        End If
    Next r
    FindEmptySetupRow = -1
End Function

Function NormalizeCycleLabel(cycleName As String) As String
    Dim valueText As String
    valueText = LCase(Trim(cycleName))
    If valueText = "both" Or valueText = "1st & 15th" Or valueText = "1st+15th" Then
        NormalizeCycleLabel = "Both"
    ElseIf InStr(valueText, "15") > 0 Then
        NormalizeCycleLabel = "15th"
    Else
        NormalizeCycleLabel = "1st"
    End If
End Function

Function CycleIncludes(cycleName As String, targetCycle As String) As Boolean
    Dim normalized As String
    normalized = NormalizeCycleLabel(cycleName)
    If normalized = "Both" Then
        CycleIncludes = True
    Else
        CycleIncludes = (LCase(normalized) = LCase(targetCycle))
    End If
End Function

Function MergeCycle(existingCycle As String, requestedCycle As String) As String
    If LCase(requestedCycle) = "both" Then
        MergeCycle = "Both"
    ElseIf Trim(existingCycle) = "" Then
        MergeCycle = NormalizeCycleLabel(requestedCycle)
    ElseIf CycleIncludes(existingCycle, requestedCycle) Then
        MergeCycle = NormalizeCycleLabel(existingCycle)
    Else
        MergeCycle = "Both"
    End If
End Function

Function RemoveCycle(existingCycle As String, removedCycle As String) As String
    Dim normalized As String
    normalized = NormalizeCycleLabel(existingCycle)

    If LCase(removedCycle) = "both" Then
        RemoveCycle = "None"
    ElseIf normalized = "Both" Then
        If LCase(removedCycle) = "1st" Then
            RemoveCycle = "15th"
        Else
            RemoveCycle = "1st"
        End If
    ElseIf LCase(normalized) = LCase(removedCycle) Then
        RemoveCycle = "None"
    Else
        RemoveCycle = normalized
    End If
End Function

Sub SetCellFromInput(cell As Object, valueText As String)
    On Error Resume Next
    If Trim(valueText) = "" Then
        cell.Formula = ""
    ElseIf IsNumeric(valueText) Then
        cell.Value = CDbl(valueText)
    Else
        cell.String = valueText
    End If
    On Error GoTo 0
End Sub

Sub SetSetupActive(setupSheet As Object, setupRow As Long, setupBillCol As Integer, isActive As Boolean)
    On Error Resume Next
    If isActive Then
        setupSheet.getCellByPosition(setupBillCol + 6, setupRow).String = "Yes"
    Else
        setupSheet.getCellByPosition(setupBillCol + 6, setupRow).String = "No"
    End If
    On Error GoTo 0
End Sub

Function IsSetupRowActive(setupSheet As Object, setupRow As Long, setupBillCol As Integer) As Boolean
    Dim textValue As String
    textValue = LCase(Trim(setupSheet.getCellByPosition(setupBillCol + 6, setupRow).String))
    IsSetupRowActive = Not (textValue = "no" Or textValue = "false" Or textValue = "inactive" Or textValue = "0")
End Function

Function CellPromptValue(cell As Object) As String
    On Error Resume Next
    If cell.Type = 1 Then
        CellPromptValue = CStr(cell.Value)
    Else
        CellPromptValue = cell.String
    End If
    On Error GoTo 0
End Function

Function UpdateSetupForMonthlyRemoval(billName As String, periodChoice As String) As String
    Dim setupSheet As Object
    Dim setupBillCol As Integer
    Dim setupRow As Long
    Dim currentCycle As String
    Dim newCycle As String

    If Not ThisComponent.Sheets.hasByName("Setup") Then
        UpdateSetupForMonthlyRemoval = "Setup: sheet not found."
        Exit Function
    End If

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    setupBillCol = SetupBillColumn(setupSheet)
    setupRow = FindSetupBillRow(setupSheet, setupBillCol, billName)

    If setupRow < 0 Then
        UpdateSetupForMonthlyRemoval = "Setup: " & billName & " was not found."
        Exit Function
    End If

    currentCycle = setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String
    newCycle = RemoveCycle(currentCycle, periodChoice)

    If newCycle = "None" Then
        SetSetupActive setupSheet, setupRow, setupBillCol, False
        UpdateSetupForMonthlyRemoval = "Setup: " & billName & " marked inactive."
    Else
        setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String = newCycle
        SetSetupActive setupSheet, setupRow, setupBillCol, True
        UpdateSetupForMonthlyRemoval = "Setup: " & billName & " now applies to " & newCycle & "."
    End If
End Function

Function HasRecordedPayment(monthSheet As Object, baseCol As Integer, targetRow As Long) As Boolean
    On Error Resume Next
    HasRecordedPayment = (monthSheet.getCellByPosition(baseCol + 3, targetRow).Type <> 0)
    On Error GoTo 0
End Function

Function RemoveBillFromCycleSafe(monthSheet As Object, billName As String, cycleName As String) As String
    Dim baseCol As Integer
    Dim targetRow As Long

    baseCol = CycleBaseColumn(cycleName)
    targetRow = FindBillRow(monthSheet, baseCol, billName)
    If targetRow < 0 Then
        RemoveBillFromCycleSafe = cycleName & ": not listed."
        Exit Function
    End If

    If HasRecordedPayment(monthSheet, baseCol, targetRow) Then
        RemoveBillFromCycleSafe = cycleName & ": preserved because Paid data exists."
        Exit Function
    End If

    RemoveBillFromCycleSafe = RemoveBillFromCycle(monthSheet, billName, cycleName)
End Function

Function EnsureBillOnMonth(monthSheet As Object, oldName As String, newName As String, cycleName As String) As String
    Dim baseCol As Integer
    Dim targetRow As Long

    baseCol = CycleBaseColumn(cycleName)
    targetRow = FindBillRow(monthSheet, baseCol, newName)
    If targetRow < 0 And LCase(oldName) <> LCase(newName) Then
        targetRow = FindBillRow(monthSheet, baseCol, oldName)
        If targetRow >= 0 Then
            monthSheet.getCellByPosition(baseCol, targetRow).String = newName
        End If
    End If

    If targetRow < 0 Then
        AddBillToCycle monthSheet, newName, cycleName
        targetRow = FindBillRow(monthSheet, baseCol, newName)
    End If

    If targetRow >= 0 Then
        monthSheet.getCellByPosition(baseCol, targetRow).String = newName
        PopulateFromSetup monthSheet, targetRow, baseCol, newName
    End If

    EnsureBillOnMonth = cycleName & ": synchronized."
End Function

Function PropagateSetupBillForward(oldName As String, newName As String, startMonth As Integer) As String
    Dim setupSheet As Object
    Dim setupBillCol As Integer
    Dim setupRow As Long
    Dim cycleName As String
    Dim names
    Dim i As Integer
    Dim sheetName As String
    Dim sheetMonth As Integer
    Dim sheetYear As Integer
    Dim startYear As Integer
    Dim monthSheet As Object

    If Not ThisComponent.Sheets.hasByName("Setup") Then
        PropagateSetupBillForward = "Monthly tabs: Setup sheet not found."
        Exit Function
    End If

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    setupBillCol = SetupBillColumn(setupSheet)
    setupRow = FindSetupBillRow(setupSheet, setupBillCol, newName)

    If setupRow < 0 Then
        PropagateSetupBillForward = "Monthly tabs: Setup entry was not found."
        Exit Function
    End If

    If Not IsSetupRowActive(setupSheet, setupRow, setupBillCol) Then
        PropagateSetupBillForward = RemoveBillFromFutureMonths(newName, "both", startMonth)
        Exit Function
    End If

    cycleName = setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String
    startYear = Year(Date)
    names = ThisComponent.Sheets.ElementNames

    For i = LBound(names) To UBound(names)
        sheetName = names(i)
        If IsMonthSheet(sheetName) Then
            sheetMonth = MonthNumberByName(sheetName)
            sheetYear = MonthSheetYear(sheetName)
            If sheetYear = 0 Then sheetYear = LegacyMonthYear()

            If sheetYear > startYear Or (sheetYear = startYear And sheetMonth >= startMonth) Then
                monthSheet = ThisComponent.Sheets.getByName(sheetName)

                If CycleIncludes(cycleName, "1st") Then
                    EnsureBillOnMonth monthSheet, oldName, newName, "1st"
                Else
                    RemoveBillFromCycleSafe monthSheet, oldName, "1st"
                    If LCase(oldName) <> LCase(newName) Then RemoveBillFromCycleSafe monthSheet, newName, "1st"
                End If

                If CycleIncludes(cycleName, "15th") Then
                    EnsureBillOnMonth monthSheet, oldName, newName, "15th"
                Else
                    RemoveBillFromCycleSafe monthSheet, oldName, "15th"
                    If LCase(oldName) <> LCase(newName) Then RemoveBillFromCycleSafe monthSheet, newName, "15th"
                End If
            End If
        End If
    Next i

    PropagateSetupBillForward = "Monthly tabs: synchronized from " & _
        MonthNameByNumber(startMonth) & " " & startYear & " through all existing future tabs."
End Function

Function RemoveBillFromFutureMonths(billName As String, periodChoice As String, startMonth As Integer) As String
    Dim names
    Dim i As Integer
    Dim sheetName As String
    Dim sheetMonth As Integer
    Dim sheetYear As Integer
    Dim startYear As Integer
    Dim monthSheet As Object
    Dim preservedCount As Long
    Dim resultText As String

    startYear = Year(Date)
    names = ThisComponent.Sheets.ElementNames

    For i = LBound(names) To UBound(names)
        sheetName = names(i)
        If IsMonthSheet(sheetName) Then
            sheetMonth = MonthNumberByName(sheetName)
            sheetYear = MonthSheetYear(sheetName)
            If sheetYear = 0 Then sheetYear = LegacyMonthYear()

            If sheetYear > startYear Or (sheetYear = startYear And sheetMonth >= startMonth) Then
                monthSheet = ThisComponent.Sheets.getByName(sheetName)

                If LCase(periodChoice) = "both" Or LCase(periodChoice) = "1st" Then
                    resultText = RemoveBillFromCycleSafe(monthSheet, billName, "1st")
                    If InStr(resultText, "preserved") > 0 Then preservedCount = preservedCount + 1
                End If

                If LCase(periodChoice) = "both" Or LCase(periodChoice) = "15th" Then
                    resultText = RemoveBillFromCycleSafe(monthSheet, billName, "15th")
                    If InStr(resultText, "preserved") > 0 Then preservedCount = preservedCount + 1
                End If
            End If
        End If
    Next i

    RemoveBillFromFutureMonths = "Monthly tabs: removed from empty current/future slots; preserved Paid rows: " & preservedCount & "."
End Function

Function MonthNumberByName(monthName As String) As Integer
    Dim valueText As String
    valueText = Trim(monthName)

    If valueText = "January" Or Left(valueText, 8) = "January " Then MonthNumberByName = 1: Exit Function
    If valueText = "February" Or Left(valueText, 9) = "February " Then MonthNumberByName = 2: Exit Function
    If valueText = "March" Or Left(valueText, 6) = "March " Then MonthNumberByName = 3: Exit Function
    If valueText = "April" Or Left(valueText, 6) = "April " Then MonthNumberByName = 4: Exit Function
    If valueText = "May" Or Left(valueText, 4) = "May " Then MonthNumberByName = 5: Exit Function
    If valueText = "June" Or Left(valueText, 5) = "June " Then MonthNumberByName = 6: Exit Function
    If valueText = "July" Or Left(valueText, 5) = "July " Then MonthNumberByName = 7: Exit Function
    If valueText = "August" Or Left(valueText, 7) = "August " Then MonthNumberByName = 8: Exit Function
    If valueText = "September" Or Left(valueText, 10) = "September " Then MonthNumberByName = 9: Exit Function
    If valueText = "October" Or Left(valueText, 8) = "October " Then MonthNumberByName = 10: Exit Function
    If valueText = "November" Or Left(valueText, 9) = "November " Then MonthNumberByName = 11: Exit Function
    If valueText = "December" Or Left(valueText, 9) = "December " Then MonthNumberByName = 12: Exit Function

    MonthNumberByName = 0
End Function

Function MonthNameByNumber(monthNumber As Integer) As String
    Select Case monthNumber
        Case 1: MonthNameByNumber = "January"
        Case 2: MonthNameByNumber = "February"
        Case 3: MonthNameByNumber = "March"
        Case 4: MonthNameByNumber = "April"
        Case 5: MonthNameByNumber = "May"
        Case 6: MonthNameByNumber = "June"
        Case 7: MonthNameByNumber = "July"
        Case 8: MonthNameByNumber = "August"
        Case 9: MonthNameByNumber = "September"
        Case 10: MonthNameByNumber = "October"
        Case 11: MonthNameByNumber = "November"
        Case 12: MonthNameByNumber = "December"
        Case Else: MonthNameByNumber = ""
    End Select
End Function

Function MonthSheetYear(sheetName As String) As Integer
    Dim monthNumber As Integer
    Dim bareMonth As String
    Dim suffixText As String

    monthNumber = MonthNumberByName(sheetName)
    If monthNumber = 0 Then
        MonthSheetYear = 0
        Exit Function
    End If

    bareMonth = MonthNameByNumber(monthNumber)
    If Len(Trim(sheetName)) <= Len(bareMonth) Then
        MonthSheetYear = 0
        Exit Function
    End If

    suffixText = Trim(Mid(Trim(sheetName), Len(bareMonth) + 1))
    If Len(suffixText) = 4 And IsNumeric(suffixText) Then
        MonthSheetYear = CInt(suffixText)
    Else
        MonthSheetYear = 0
    End If
End Function

Function ExtractYearFromText(valueText As String) As Integer
    Dim y As Integer
    For y = 2000 To 2200
        If InStr(valueText, CStr(y)) > 0 Then
            ExtractYearFromText = y
            Exit Function
        End If
    Next y
    ExtractYearFromText = 0
End Function

Function LegacyMonthYear() As Integer
    Dim m As Integer
    Dim sheetName As String
    Dim sheet As Object
    Dim r As Integer
    Dim c As Integer
    Dim detected As Integer

    For m = 1 To 12
        sheetName = MonthNameByNumber(m)
        If ThisComponent.Sheets.hasByName(sheetName) Then
            sheet = ThisComponent.Sheets.getByName(sheetName)
            For r = 0 To 4
                For c = 0 To 5
                    detected = ExtractYearFromText(sheet.getCellByPosition(c, r).String)
                    If detected > 0 Then
                        LegacyMonthYear = detected
                        Exit Function
                    End If
                Next c
            Next r
        End If
    Next m

    LegacyMonthYear = Year(Date)
End Function

Function ExistingMonthSheetName(monthNumber As Integer, yearNumber As Integer) As String
    Dim monthName As String
    Dim qualifiedName As String

    monthName = MonthNameByNumber(monthNumber)
    qualifiedName = monthName & " " & yearNumber

    If ThisComponent.Sheets.hasByName(qualifiedName) Then
        ExistingMonthSheetName = qualifiedName
        Exit Function
    End If

    If ThisComponent.Sheets.hasByName(monthName) And LegacyMonthYear() = yearNumber Then
        ExistingMonthSheetName = monthName
        Exit Function
    End If

    ExistingMonthSheetName = ""
End Function

Function HighestWorkbookYear() As Integer
    Dim names
    Dim i As Integer
    Dim sheetName As String
    Dim sheetYear As Integer
    Dim highest As Integer

    names = ThisComponent.Sheets.ElementNames
    For i = LBound(names) To UBound(names)
        sheetName = names(i)
        If IsMonthSheet(sheetName) Then
            sheetYear = MonthSheetYear(sheetName)
            If sheetYear = 0 Then sheetYear = LegacyMonthYear()
            If sheetYear > highest Then highest = sheetYear
        End If
    Next i

    If highest = 0 Then highest = Year(Date)
    HighestWorkbookYear = highest
End Function


Function AddBillToCycle(sheet As Object, billName As String, cycleName As String) As String
    On Error GoTo Handler
    Dim baseCol As Integer
    Dim existingRow As Long
    Dim targetRow As Long

    baseCol = CycleBaseColumn(cycleName)
    existingRow = FindBillRow(sheet, baseCol, billName)
    If existingRow >= 0 Then
        AddBillToCycle = cycleName & ": " & billName & " is already listed."
        Exit Function
    End If

    targetRow = FindEmptyBillRow(sheet, baseCol)
    If targetRow < 0 Then
        AddBillToCycle = cycleName & ": no empty bill slot is available."
        Exit Function
    End If

    sheet.getCellByPosition(baseCol, targetRow).String = billName
    ClearInputCell sheet.getCellByPosition(baseCol + 1, targetRow)
    ClearInputCell sheet.getCellByPosition(baseCol + 2, targetRow)
    ClearInputCell sheet.getCellByPosition(baseCol + 3, targetRow)
    ClearInputCell sheet.getCellByPosition(baseCol + 4, targetRow)

    PopulateFromSetup sheet, targetRow, baseCol, billName

    AddBillToCycle = cycleName & ": added " & billName & " on row " & (targetRow + 1) & "."
    Exit Function

Handler:
    AddBillToCycle = cycleName & ": could not add " & billName & " (" & Error$ & ")."
End Function

Function RemoveBillFromCycle(sheet As Object, billName As String, cycleName As String) As String
    On Error GoTo Handler
    Dim baseCol As Integer
    Dim targetRow As Long
    Dim i As Integer

    baseCol = CycleBaseColumn(cycleName)
    targetRow = FindBillRow(sheet, baseCol, billName)
    If targetRow < 0 Then
        RemoveBillFromCycle = cycleName & ": " & billName & " was not found."
        Exit Function
    End If

    ' Name / When / Due / Paid / Method are user-input fields.
    For i = 0 To 4
        ClearInputCell sheet.getCellByPosition(baseCol + i, targetRow)
    Next i

    ' Status and Extra may be formulas. Clear them only when they are literals.
    ClearLiteralOnly sheet.getCellByPosition(baseCol + 5, targetRow)
    ClearLiteralOnly sheet.getCellByPosition(baseCol + 6, targetRow)

    RemoveBillFromCycle = cycleName & ": removed " & billName & "."
    Exit Function

Handler:
    RemoveBillFromCycle = cycleName & ": could not remove " & billName & " (" & Error$ & ")."
End Function

Function CycleBaseColumn(cycleName As String) As Integer
    If cycleName = "1st" Then
        CycleBaseColumn = 1   ' B after the left gutter
    Else
        CycleBaseColumn = 9   ' J after the left gutter
    End If
End Function

Function FindBillRow(sheet As Object, baseCol As Integer, billName As String) As Long
    Dim r As Long
    Dim candidate As String

    For r = 13 To 26   ' worksheet rows 14 through 27
        candidate = Trim(sheet.getCellByPosition(baseCol, r).String)
        If LCase(candidate) = LCase(Trim(billName)) Then
            FindBillRow = r
            Exit Function
        End If
    Next r

    FindBillRow = -1
End Function

Function FindEmptyBillRow(sheet As Object, baseCol As Integer) As Long
    Dim r As Long

    For r = 13 To 26
        If Trim(sheet.getCellByPosition(baseCol, r).String) = "" Then
            FindEmptyBillRow = r
            Exit Function
        End If
    Next r

    FindEmptyBillRow = -1
End Function

Sub PopulateFromSetup(monthSheet As Object, targetRow As Long, baseCol As Integer, billName As String)
    On Error Resume Next
    Dim setupSheet As Object
    Dim setupBillCol As Integer
    Dim setupRow As Long

    If Not ThisComponent.Sheets.hasByName("Setup") Then Exit Sub
    setupSheet = ThisComponent.Sheets.getByName("Setup")

    If HasSheetGutter(setupSheet) Then
        setupBillCol = 1
    Else
        setupBillCol = 0
    End If

    setupRow = FindSetupBillRow(setupSheet, setupBillCol, billName)
    If setupRow < 0 Then Exit Sub

    ' Setup columns: Bill, Cycle, When, Latest Due, Default Method...
    CopyCellDisplayValue setupSheet.getCellByPosition(setupBillCol + 2, setupRow), monthSheet.getCellByPosition(baseCol + 1, targetRow)
    CopyCellDisplayValue setupSheet.getCellByPosition(setupBillCol + 3, setupRow), monthSheet.getCellByPosition(baseCol + 2, targetRow)
    CopyCellDisplayValue setupSheet.getCellByPosition(setupBillCol + 4, setupRow), monthSheet.getCellByPosition(baseCol + 4, targetRow)
    On Error GoTo 0
End Sub

Function FindSetupBillRow(setupSheet As Object, setupBillCol As Integer, billName As String) As Long
    Dim r As Long
    Dim candidate As String

    For r = 2 To 200
        candidate = Trim(setupSheet.getCellByPosition(setupBillCol, r).String)
        If IsSetupBillRecord(setupSheet, r, setupBillCol) And _
           LCase(candidate) = LCase(Trim(billName)) Then
            FindSetupBillRow = r
            Exit Function
        End If
    Next r

    FindSetupBillRow = -1
End Function

Sub CopyCellDisplayValue(sourceCell As Object, targetCell As Object)
    On Error Resume Next
    If sourceCell.Type = 1 Then
        targetCell.Value = sourceCell.Value
        targetCell.NumberFormat = sourceCell.NumberFormat
    Else
        targetCell.String = sourceCell.String
    End If
    On Error GoTo 0
End Sub

Sub ClearInputCell(cell As Object)
    On Error Resume Next
    cell.Formula = ""
    On Error GoTo 0
End Sub

Sub ClearLiteralOnly(cell As Object)
    On Error Resume Next
    If Left(cell.Formula, 1) <> "=" Then cell.Formula = ""
    On Error GoTo 0
End Sub

Function HasLegacyMonthTabs() As Boolean
    Dim m As Integer
    For m = 1 To 12
        If ThisComponent.Sheets.hasByName(MonthNameByNumber(m)) Then
            HasLegacyMonthTabs = True
            Exit Function
        End If
    Next m
    HasLegacyMonthTabs = False
End Function

Sub MigrateLegacyMonthTabsToYear(yearNumber As Integer)
    Dim m As Integer
    Dim oldName As String
    Dim newName As String
    Dim sheet As Object

    For m = 1 To 12
        oldName = MonthNameByNumber(m)
        newName = oldName & " " & yearNumber

        If ThisComponent.Sheets.hasByName(oldName) And Not ThisComponent.Sheets.hasByName(newName) Then
            sheet = ThisComponent.Sheets.getByName(oldName)
            sheet.Name = newName
        End If
    Next m
End Sub

Sub RollbackLegacyMigration(yearNumber As Integer)
    Dim m As Integer
    Dim oldName As String
    Dim qualifiedName As String
    Dim sheet As Object

    For m = 1 To 12
        oldName = MonthNameByNumber(m)
        qualifiedName = oldName & " " & yearNumber

        If Not ThisComponent.Sheets.hasByName(oldName) And ThisComponent.Sheets.hasByName(qualifiedName) Then
            sheet = ThisComponent.Sheets.getByName(qualifiedName)
            sheet.Name = oldName
        End If
    Next m
End Sub

Sub DeleteYearTabs(yearNumber As Integer)
    On Error Resume Next
    Dim m As Integer
    Dim sheetName As String

    For m = 1 To 12
        sheetName = MonthNameByNumber(m) & " " & yearNumber
        If ThisComponent.Sheets.hasByName(sheetName) Then
            ThisComponent.Sheets.removeByName(sheetName)
        End If
    Next m
    On Error GoTo 0
End Sub

Sub ClearNonFormulaNumericValues(sheet As Object)
    On Error Resume Next
    Dim cursor As Object
    Dim addr As Object
    Dim r As Long
    Dim c As Long
    Dim cell As Object

    cursor = sheet.createCursor()
    cursor.gotoEndOfUsedArea(True)
    addr = cursor.RangeAddress

    For r = addr.StartRow To addr.EndRow
        For c = addr.StartColumn To addr.EndColumn
            cell = sheet.getCellByPosition(c, r)
            If cell.Type = 1 And Left(cell.Formula, 1) <> "=" Then
                cell.Formula = ""
            End If
        Next c
    Next r
    On Error GoTo 0
End Sub

Sub ResetMonthForNewYear(sheet As Object, monthNumber As Integer, yearNumber As Integer)
    On Error Resume Next
    Dim cycleIndex As Integer
    Dim baseCol As Integer
    Dim r As Long
    Dim i As Integer

    If sheet.isProtected() Then sheet.unprotect("")

    ' Remove prior-year literal numeric values anywhere on the copied month.
    ' Formula cells are preserved and will recalculate for the new sheet.
    ClearNonFormulaNumericValues sheet

    ' Rebuild the main bill slots from Setup; never carry Paid values forward.
    For cycleIndex = 0 To 1
        If cycleIndex = 0 Then
            baseCol = CycleBaseColumn("1st")
        Else
            baseCol = CycleBaseColumn("15th")
        End If

        For r = 13 To 26
            For i = 0 To 4
                ClearInputCell sheet.getCellByPosition(baseCol + i, r)
            Next i
            ClearLiteralOnly sheet.getCellByPosition(baseCol + 5, r)
            ClearLiteralOnly sheet.getCellByPosition(baseCol + 6, r)
        Next r
    Next cycleIndex

    sheet.getCellByPosition(1, 1).String = MonthNameByNumber(monthNumber) & " " & yearNumber & " — Bill Pay"
    On Error GoTo 0
End Sub

Sub PopulateYearFromSetup(yearNumber As Integer)
    On Error Resume Next
    Dim setupSheet As Object
    Dim setupBillCol As Integer
    Dim setupRow As Long
    Dim billName As String
    Dim cycleText As String
    Dim m As Integer
    Dim sheetName As String
    Dim monthSheet As Object

    If Not ThisComponent.Sheets.hasByName("Setup") Then Exit Sub

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    setupBillCol = SetupBillColumn(setupSheet)

    For setupRow = 2 To 200
        billName = Trim(setupSheet.getCellByPosition(setupBillCol, setupRow).String)

        If IsSetupBillRecord(setupSheet, setupRow, setupBillCol) And _
           IsSetupRowActive(setupSheet, setupRow, setupBillCol) Then
            cycleText = setupSheet.getCellByPosition(setupBillCol + 1, setupRow).String

            For m = 1 To 12
                sheetName = MonthNameByNumber(m) & " " & yearNumber
                If ThisComponent.Sheets.hasByName(sheetName) Then
                    monthSheet = ThisComponent.Sheets.getByName(sheetName)

                    If CycleIncludes(cycleText, "1st") Then
                        EnsureBillOnMonth monthSheet, billName, billName, "1st"
                    End If
                    If CycleIncludes(cycleText, "15th") Then
                        EnsureBillOnMonth monthSheet, billName, billName, "15th"
                    End If
                End If
            Next m
        End If
    Next setupRow
    On Error GoTo 0
End Sub

Function BackupBeforeNewYear(newYear As Integer) As String
    Dim documentPath As String
    Dim workbookDir As String
    Dim backupDir As String
    Dim backupPath As String
    Dim fileName As String

    If ThisComponent.URL = "" Then
        Err.Raise 1004, , "Save the workbook before creating a new year."
    End If

    documentPath = ConvertFromURL(ThisComponent.URL)
    workbookDir = ParentPath(documentPath)
    backupDir = workbookDir & "/backups"
    If Dir(backupDir, 16) = "" Then MkDir backupDir

    fileName = FileNameFromPath(documentPath)
    backupPath = backupDir & "/" & Left(fileName, Len(fileName) - 4) & "." & _
                 Format(Now, "YYYYMMDD-HHMMSS") & ".pre-" & newYear & ".bak.ods"
    FileCopy documentPath, backupPath
    BackupBeforeNewYear = backupPath
End Function

Sub SetSimpleListValidation(cell As Object, valuesText As String)
    On Error Resume Next
    Dim validity As Object
    Dim values
    Dim i As Integer
    Dim formulaText As String

    values = Split(valuesText, "|")
    For i = LBound(values) To UBound(values)
        If formulaText <> "" Then formulaText = formulaText & ";"
        formulaText = formulaText & Chr(34) & values(i) & Chr(34)
    Next i

    validity = cell.Validation
    validity.Type = com.sun.star.sheet.ValidationType.LIST
    validity.Formula1 = formulaText
    validity.IgnoreBlank = True
    validity.ShowList = 1
    validity.ShowErrorMessage = False
    cell.Validation = validity
    On Error GoTo 0
End Sub

Function IsNonBillSetupLabel(valueText As String) As Boolean
    Dim normalized As String
    normalized = LCase(Trim(valueText))

    IsNonBillSetupLabel = ( _
        normalized = "" Or _
        normalized = "bill" Or _
        normalized = "nfc u" Or _
        normalized = "nfcu" Or _
        InStr(normalized, "nfcu workflow") = 1 Or _
        Left(normalized, 5) = "note:" _
    )
End Function

Function IsSetupBillRecord(setupSheet As Object, setupRow As Long, setupBillCol As Integer) As Boolean
    On Error Resume Next
    Dim billName As String

    billName = Trim(setupSheet.getCellByPosition(setupBillCol, setupRow).String)
    IsSetupBillRecord = Not IsNonBillSetupLabel(billName)
    On Error GoTo 0
End Function

Sub RebuildSetupBillDropdownSource()
    On Error Resume Next
    Dim setupSheet As Object
    Dim setupBillCol As Integer
    Dim r As Long
    Dim outRow As Long
    Dim helperCol As Integer

    If Not ThisComponent.Sheets.hasByName("Setup") Then Exit Sub

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    If setupSheet.isProtected() Then setupSheet.unprotect("")
    setupBillCol = SetupBillColumn(setupSheet)

    helperCol = 16 ' Q
    setupSheet.getCellRangeByName("Q2:Q202").Formula = ""
    setupSheet.getCellRangeByName("Q2").String = "Bill Dropdown Source"
    outRow = 2

    For r = 2 To 200
        If IsSetupBillRecord(setupSheet, r, setupBillCol) Then
            setupSheet.getCellByPosition(helperCol, outRow).String = _
                setupSheet.getCellByPosition(setupBillCol, r).String
            outRow = outRow + 1
        End If
    Next r

    setupSheet.Columns.getByIndex(helperCol).IsVisible = False
    On Error GoTo 0
End Sub

Sub SetBillListValidation(cell As Object)
    On Error Resume Next
    Dim validity As Object
    validity = cell.Validation
    validity.Type = com.sun.star.sheet.ValidationType.LIST
    RebuildSetupBillDropdownSource
    validity.Formula1 = "$Setup.$Q$3:$Q$202"
    validity.IgnoreBlank = True
    validity.ShowList = 1
    validity.ShowErrorMessage = False
    cell.Validation = validity
    On Error GoTo 0
End Sub

Sub PrepareEditorInput(sheet As Object, address As String, defaultText As String)
    On Error Resume Next
    Dim area As Object
    Dim cell As Object

    area = sheet.getCellRangeByName(address)
    area.merge(True)
    area.CellBackColor = RGB(255,255,255)
    area.CharColor = RGB(31,41,55)
    area.CharWeight = 100
    area.CharHeight = 10
    area.HoriJustify = 1
    area.VertJustify = 2
    area.IsTextWrapped = False

    cell = area.getCellByPosition(0,0)
    If Trim(cell.String) = "" And defaultText <> "" Then cell.String = defaultText
    On Error GoTo 0
End Sub

Sub InstallEditorLink(sheet As Object, address As String, labelText As String, macroName As String, backColor As Long)
    On Error Resume Next
    Dim area As Object
    Dim cell As Object
    Dim scriptUrl As String

    area = sheet.getCellRangeByName(address)
    area.merge(True)
    cell = area.getCellByPosition(0,0)
    scriptUrl = "vnd.sun.star.script:BiWeeklyBills.Module1." & macroName & "?language=Basic&location=document"
    cell.FormulaLocal = "=HYPERLINK(""" & scriptUrl & """;""" & labelText & """)"
    If InStr(cell.String, labelText) = 0 Then
        cell.Formula = ""
        cell.String = labelText
        cell.HyperLinkURL = scriptUrl
    End If

    area.CellBackColor = backColor
    area.CharColor = RGB(255,255,255)
    area.CharWeight = 150
    area.CharHeight = 10
    area.HoriJustify = 3
    area.VertJustify = 2
    area.IsTextWrapped = False
    On Error GoTo 0
End Sub

Sub RefreshBillEditorLists()
    RebuildSetupBillDropdownSource
    ThisComponent.calculateAll()
End Sub

Sub InstallMonthlyBillManagerButton(sheet As Object)
    On Error Resume Next
    Dim actionCell As Object
    Dim billCell As Object
    Dim cycleCell As Object

    ' Compact in-sheet editor in the unused upper-right portion of each month.
    StyleRange sheet, "N4:P4", RGB(31,78,120), RGB(255,255,255), 10.5, True, False
    sheet.getCellRangeByName("N4:P4").merge(True)
    sheet.getCellRangeByName("N4").String = "BILL EDITOR"

    sheet.getCellRangeByName("N5").String = "Action"
    sheet.getCellRangeByName("N6").String = "Bill"
    sheet.getCellRangeByName("N7").String = "Cycle"
    sheet.getCellRangeByName("N8").String = "When"
    sheet.getCellRangeByName("N9").String = "Due"
    sheet.getCellRangeByName("N10").String = "Method"
    StyleRange sheet, "N5:N10", RGB(239,244,249), RGB(23,54,93), 9.5, True, False

    PrepareEditorInput sheet, "O5:P5", "Add"
    PrepareEditorInput sheet, "O6:P6", ""
    PrepareEditorInput sheet, "O7:P7", "Both"
    PrepareEditorInput sheet, "O8:P8", ""
    PrepareEditorInput sheet, "O9:P9", ""
    PrepareEditorInput sheet, "O10:P10", ""

    actionCell = sheet.getCellRangeByName("O5")
    billCell = sheet.getCellRangeByName("O6")
    cycleCell = sheet.getCellRangeByName("O7")
    SetSimpleListValidation actionCell, "Add|Edit|Remove"
    SetBillListValidation billCell
    SetSimpleListValidation cycleCell, "1st|15th|Both"

    InstallEditorLink sheet, "N11", "LOAD", "LoadMonthlyBillEditor", RGB(91,101,115)
    InstallEditorLink sheet, "O11", "APPLY", "ApplyMonthlyBillEditor", RGB(84,130,53)
    InstallEditorLink sheet, "P11", "RESET", "ResetMonthlyBillEditor", RGB(91,101,115)

    sheet.getCellRangeByName("N12:P12").merge(True)
    SetBillEditorStatus sheet, "N12:P12", _
        "Choose Action / Bill / Cycle. LOAD fills saved details; APPLY commits.", "info"

    ApplyNumberFormat sheet, "O9:P9", "$#,##0.00;-$#,##0.00;-"
    On Error GoTo 0
End Sub

Sub InstallSetupBillManagerButtons(sheet As Object)
    On Error Resume Next
    Dim actionCell As Object
    Dim billCell As Object
    Dim cycleCell As Object

    SetColumnWidth sheet, 9, 700
    SetColumnWidth sheet, 10, 2350
    SetColumnWidth sheet, 11, 2600
    SetColumnWidth sheet, 12, 2600
    SetColumnWidth sheet, 13, 2600
    SetColumnWidth sheet, 14, 3000

    StyleRange sheet, "K2:O2", RGB(31,78,120), RGB(255,255,255), 11, True, False
    sheet.getCellRangeByName("K2:O2").merge(True)
    sheet.getCellRangeByName("K2").String = "BILL EDITOR"

    sheet.getCellRangeByName("K3").String = "Action"
    sheet.getCellRangeByName("K4").String = "Bill"
    sheet.getCellRangeByName("K5").String = "Cycle"
    sheet.getCellRangeByName("K6").String = "When"
    sheet.getCellRangeByName("K7").String = "Latest Due"
    sheet.getCellRangeByName("K8").String = "Method"
    sheet.getCellRangeByName("K9").String = "Payment Account"
    sheet.getCellRangeByName("K10").String = "Notes"
    StyleRange sheet, "K3:K10", RGB(239,244,249), RGB(23,54,93), 9.5, True, False

    PrepareEditorInput sheet, "L3:O3", "Add"
    PrepareEditorInput sheet, "L4:O4", ""
    PrepareEditorInput sheet, "L5:O5", "Both"
    PrepareEditorInput sheet, "L6:O6", ""
    PrepareEditorInput sheet, "L7:O7", ""
    PrepareEditorInput sheet, "L8:O8", ""
    PrepareEditorInput sheet, "L9:O9", ""
    PrepareEditorInput sheet, "L10:O10", ""

    actionCell = sheet.getCellRangeByName("L3")
    billCell = sheet.getCellRangeByName("L4")
    cycleCell = sheet.getCellRangeByName("L5")
    SetSimpleListValidation actionCell, "Add|Edit|Remove"
    SetBillListValidation billCell
    SetSimpleListValidation cycleCell, "1st|15th|Both"

    InstallEditorLink sheet, "K11:L11", "LOAD", "LoadSetupBillEditor", RGB(91,101,115)
    InstallEditorLink sheet, "M11:N11", "APPLY", "ApplySetupBillEditor", RGB(84,130,53)
    InstallEditorLink sheet, "O11", "RESET", "ResetSetupBillEditor", RGB(91,101,115)
    InstallEditorLink sheet, "K12:O12", "SYNC CURRENT + FUTURE MONTHS", "SyncSetupBillsToFutureMonths", RGB(46,117,182)

    sheet.getCellRangeByName("K13:O13").merge(True)
    SetBillEditorStatus sheet, "K13:O13", _
        "Use dropdowns and fields above. APPLY updates Setup and current/future months.", "info"

    ApplyNumberFormat sheet, "L7:O7", "$#,##0.00;-$#,##0.00;-"
    On Error GoTo 0
End Sub

Sub StyleEverySheet()
    Dim names
    Dim i As Integer
    Dim sheet As Object
    Dim nameText As String

    names = ThisComponent.Sheets.ElementNames
    For i = LBound(names) To UBound(names)
        nameText = names(i)
        sheet = ThisComponent.Sheets.getByName(nameText)
        PrepareSheetBase sheet

        If nameText = CONTROL_SHEET Then
            StyleControlSheet sheet
        ElseIf IsMonthSheet(nameText) Then
            StyleMonthSheet sheet
        ElseIf nameText = "Setup" Then
            StyleSetupSheet sheet
        ElseIf nameText = "Debt Tracker" Then
            StyleDebtSheet sheet
        ElseIf nameText = "Dashboard" Then
            StyleDashboardSheet sheet
        Else
            StyleGenericSheet sheet
        End If
    Next i

    ApplyWorkbookNumberFormats
    ApplyExceptionConditionalFormatting
    ProtectWorkbookFormulaCells
    FreezeWorkbookHeaders
End Sub

Sub PrepareSheetBase(sheet As Object)
    On Error Resume Next
    If sheet.isProtected() Then sheet.unprotect("")
    EnsureSheetGutter sheet

    Dim cursor As Object
    cursor = sheet.createCursor()
    cursor.gotoEndOfUsedArea(True)
    cursor.CharFontName = "Liberation Sans"
    cursor.CharHeight = 10.5
    cursor.CharColor = RGB(31, 41, 55)
    cursor.VertJustify = 2
    sheet.TabColor = RGB(31, 78, 120)
    On Error GoTo 0
End Sub

Sub EnsureSheetGutter(sheet As Object)
    On Error Resume Next

    If Not HasSheetGutter(sheet) Then
        ' Insert through LibreOffice so all formulas/ranges are adjusted by Calc.
        sheet.Columns.insertByIndex(0, 1)
        sheet.Rows.insertByIndex(0, 1)
    End If

    sheet.Columns.getByIndex(0).Width = 900
    sheet.Rows.getByIndex(0).Height = 550
    sheet.getCellRangeByPosition(0, 0, 0, 200).CellBackColor = RGB(248,250,252)
    sheet.getCellRangeByPosition(0, 0, 30, 0).CellBackColor = RGB(248,250,252)
    On Error GoTo 0
End Sub

Function HasSheetGutter(sheet As Object) As Boolean
    On Error GoTo NoGutter
    Dim colWidth As Long
    Dim rowHeight As Long

    colWidth = sheet.Columns.getByIndex(0).Width
    rowHeight = sheet.Rows.getByIndex(0).Height

    If colWidth >= 700 And colWidth <= 1300 And rowHeight >= 400 And rowHeight <= 800 Then
        HasSheetGutter = True
        Exit Function
    End If

NoGutter:
    HasSheetGutter = False
End Function

Function EnsureNumberFormatKey(formatCode As String) As Long
    Dim locale As New com.sun.star.lang.Locale
    Dim formats As Object
    Dim key As Long

    locale.Language = "en"
    locale.Country = "US"
    formats = ThisComponent.NumberFormats
    key = formats.queryKey(formatCode, locale, False)
    If key = -1 Then key = formats.addNew(formatCode, locale)
    EnsureNumberFormatKey = key
End Function

Sub ApplyNumberFormat(sheet As Object, address As String, formatCode As String)
    On Error Resume Next
    sheet.getCellRangeByName(address).NumberFormat = EnsureNumberFormatKey(formatCode)
    On Error GoTo 0
End Sub

Sub ApplyWorkbookNumberFormats()
    Dim names
    Dim i As Integer
    Dim sheet As Object
    Dim nameText As String

    names = ThisComponent.Sheets.ElementNames
    For i = LBound(names) To UBound(names)
        nameText = names(i)
        sheet = ThisComponent.Sheets.getByName(nameText)

        If IsMonthSheet(nameText) Then
            ApplyNumberFormat sheet, "D4:E11", "$#,##0.00;-$#,##0.00;-"
            ApplyNumberFormat sheet, "L4:M11", "$#,##0.00;-$#,##0.00;-"
            ApplyNumberFormat sheet, "D14:E27", "$#,##0.00;-$#,##0.00;-"
            ApplyNumberFormat sheet, "L14:M27", "$#,##0.00;-$#,##0.00;-"
            ApplyNumberFormat sheet, "H14:H27", "$#,##0.00;-$#,##0.00;-"
            ApplyNumberFormat sheet, "P14:P27", "$#,##0.00;-$#,##0.00;-"
            ApplyNumberFormat sheet, "D28:H61", "$#,##0.00;-$#,##0.00;-"
            ApplyNumberFormat sheet, "L28:P61", "$#,##0.00;-$#,##0.00;-"
        ElseIf nameText = "Setup" Then
            ApplyNumberFormat sheet, "E3:E201", "$#,##0.00;-$#,##0.00;-"
        ElseIf nameText = "Debt Tracker" Then
            ApplyDebtTrackerNumberFormats sheet
        End If
    Next i
End Sub

Sub ApplyDebtTrackerNumberFormats(sheet As Object)
    On Error Resume Next
    Dim c As Integer
    Dim headerText As String
    Dim columnRange As Object

    For c = 1 To 10
        headerText = LCase(Trim(sheet.getCellByPosition(c, 1).String))
        columnRange = sheet.getCellRangeByPosition(c, 2, c, 101)

        If InStr(headerText, "apr") > 0 Or InStr(headerText, "rate") > 0 Or InStr(headerText, "%") > 0 Then
            columnRange.NumberFormat = EnsureNumberFormatKey("0.0%")
        ElseIf InStr(headerText, "balance") > 0 Or InStr(headerText, "payment") > 0 Or _
               InStr(headerText, "amount") > 0 Or InStr(headerText, "limit") > 0 Or _
               InStr(headerText, "debt") > 0 Or InStr(headerText, "paid") > 0 Or _
               InStr(headerText, "due") > 0 Then
            columnRange.NumberFormat = EnsureNumberFormatKey("$#,##0.00;-$#,##0.00;-")
        End If
    Next c
    On Error GoTo 0
End Sub

Sub EnsureExceptionStyles()
    On Error Resume Next
    Dim styles As Object
    Dim style As Object

    styles = ThisComponent.StyleFamilies.getByName("CellStyles")

    If Not styles.hasByName("BWB Exception Red") Then
        style = ThisComponent.createInstance("com.sun.star.style.CellStyle")
        styles.insertByName("BWB Exception Red", style)
        style = styles.getByName("BWB Exception Red")
        style.CellBackColor = RGB(244,204,204)
        style.CharColor = RGB(156,0,6)
        style.CharWeight = 150
    End If

    If Not styles.hasByName("BWB Exception Amber") Then
        style = ThisComponent.createInstance("com.sun.star.style.CellStyle")
        styles.insertByName("BWB Exception Amber", style)
        style = styles.getByName("BWB Exception Amber")
        style.CellBackColor = RGB(255,235,156)
        style.CharColor = RGB(156,101,0)
        style.CharWeight = 150
    End If
    On Error GoTo 0
End Sub

Sub ClearConditionalFormats(rangeObj As Object)
    On Error Resume Next
    Dim fmt As Object
    fmt = rangeObj.ConditionalFormat
    fmt.clear()
    rangeObj.ConditionalFormat = fmt
    On Error GoTo 0
End Sub

Sub AddCellValueCondition(rangeObj As Object, operatorValue As Variant, formulaText As String, styleName As String)
    On Error Resume Next
    Dim fmt As Object
    Dim condition(2) As New com.sun.star.beans.PropertyValue

    fmt = rangeObj.ConditionalFormat

    condition(0).Name = "Operator"
    condition(0).Value = operatorValue
    condition(1).Name = "Formula1"
    condition(1).Value = formulaText
    condition(2).Name = "StyleName"
    condition(2).Value = styleName

    fmt.addNew(condition())
    rangeObj.ConditionalFormat = fmt
    On Error GoTo 0
End Sub

Sub ApplyExceptionConditionalFormatting()
    Dim names
    Dim i As Integer
    Dim sheet As Object
    Dim nameText As String
    Dim statusRange As Object
    Dim extraRange As Object

    EnsureExceptionStyles
    names = ThisComponent.Sheets.ElementNames

    For i = LBound(names) To UBound(names)
        nameText = names(i)
        If IsMonthSheet(nameText) Then
            sheet = ThisComponent.Sheets.getByName(nameText)

            statusRange = sheet.getCellRangeByName("G14:G27")
            ClearConditionalFormats statusRange
            AddCellValueCondition statusRange, com.sun.star.sheet.ConditionOperator.EQUAL, """SHORT""", "BWB Exception Red"
            AddCellValueCondition statusRange, com.sun.star.sheet.ConditionOperator.EQUAL, """OVERDUE""", "BWB Exception Red"
            AddCellValueCondition statusRange, com.sun.star.sheet.ConditionOperator.EQUAL, """DUE""", "BWB Exception Amber"
            AddCellValueCondition statusRange, com.sun.star.sheet.ConditionOperator.EQUAL, """NEEDS ACTION""", "BWB Exception Amber"

            statusRange = sheet.getCellRangeByName("O14:O27")
            ClearConditionalFormats statusRange
            AddCellValueCondition statusRange, com.sun.star.sheet.ConditionOperator.EQUAL, """SHORT""", "BWB Exception Red"
            AddCellValueCondition statusRange, com.sun.star.sheet.ConditionOperator.EQUAL, """OVERDUE""", "BWB Exception Red"
            AddCellValueCondition statusRange, com.sun.star.sheet.ConditionOperator.EQUAL, """DUE""", "BWB Exception Amber"
            AddCellValueCondition statusRange, com.sun.star.sheet.ConditionOperator.EQUAL, """NEEDS ACTION""", "BWB Exception Amber"

            extraRange = sheet.getCellRangeByName("H14:H27")
            ClearConditionalFormats extraRange
            AddCellValueCondition extraRange, com.sun.star.sheet.ConditionOperator.LESS, "0", "BWB Exception Red"

            extraRange = sheet.getCellRangeByName("P14:P27")
            ClearConditionalFormats extraRange
            AddCellValueCondition extraRange, com.sun.star.sheet.ConditionOperator.LESS, "0", "BWB Exception Red"
        End If
    Next i
End Sub

Sub RefreshExceptionHighlights()
    ' Conditional formats recalculate with Calc; this forces an immediate refresh.
    ThisComponent.calculateAll()
End Sub

Sub ProtectWorkbookFormulaCells()
    Dim names
    Dim i As Integer
    Dim sheet As Object
    Dim nameText As String

    names = ThisComponent.Sheets.ElementNames
    For i = LBound(names) To UBound(names)
        nameText = names(i)
        If nameText <> CONTROL_SHEET Then
            sheet = ThisComponent.Sheets.getByName(nameText)
            ProtectFormulaCellsOnSheet sheet
        End If
    Next i
End Sub

Sub ProtectFormulaCellsOnSheet(sheet As Object)
    On Error Resume Next
    Dim cursor As Object
    Dim addr As Object
    Dim r As Long
    Dim c As Long
    Dim cell As Object
    Dim protection As Object
    Dim isFormula As Boolean

    If sheet.isProtected() Then sheet.unprotect("")

    cursor = sheet.createCursor()
    cursor.gotoEndOfUsedArea(True)
    addr = cursor.RangeAddress

    For r = addr.StartRow To addr.EndRow
        For c = addr.StartColumn To addr.EndColumn
            cell = sheet.getCellByPosition(c, r)
            isFormula = (Left(cell.Formula, 1) = "=")
            protection = cell.CellProtection
            protection.IsLocked = isFormula
            protection.IsFormulaHidden = False
            cell.CellProtection = protection
        Next c
    Next r

    sheet.protect("")
    On Error GoTo 0
End Sub

Sub FreezeWorkbookHeaders()
    On Error Resume Next
    Dim controller As Object
    Dim originalSheet As Object
    Dim names
    Dim i As Integer
    Dim sheet As Object
    Dim nameText As String
    Dim rowsToFreeze As Integer

    controller = ThisComponent.CurrentController
    originalSheet = controller.ActiveSheet
    names = ThisComponent.Sheets.ElementNames

    For i = LBound(names) To UBound(names)
        nameText = names(i)
        sheet = ThisComponent.Sheets.getByName(nameText)
        controller.setActiveSheet(sheet)
        controller.freezeAtPosition(0, 0)

        If IsMonthSheet(nameText) Then
            rowsToFreeze = 13
        ElseIf nameText = "Setup" Or nameText = "Debt Tracker" Then
            rowsToFreeze = 2
        ElseIf nameText = "Dashboard" Then
            rowsToFreeze = 4
        Else
            rowsToFreeze = 0
        End If

        If rowsToFreeze > 0 Then controller.freezeAtPosition(0, rowsToFreeze)
    Next i

    controller.setActiveSheet(originalSheet)
    On Error GoTo 0
End Sub

Function CountWorkbookFormulaErrors() As Long
    On Error Resume Next
    Dim names
    Dim i As Integer
    Dim sheet As Object
    Dim cursor As Object
    Dim addr As Object
    Dim r As Long
    Dim c As Long
    Dim textValue As String

    names = ThisComponent.Sheets.ElementNames
    For i = LBound(names) To UBound(names)
        sheet = ThisComponent.Sheets.getByName(names(i))
        cursor = sheet.createCursor()
        cursor.gotoEndOfUsedArea(True)
        addr = cursor.RangeAddress

        For r = addr.StartRow To addr.EndRow
            For c = addr.StartColumn To addr.EndColumn
                textValue = UCase(Trim(sheet.getCellByPosition(c, r).String))
                If InStr(textValue, "ERR:") > 0 Or InStr(textValue, "#REF!") > 0 Or _
                   InStr(textValue, "#VALUE!") > 0 Or InStr(textValue, "#NAME?") > 0 Or _
                   InStr(textValue, "#DIV/0!") > 0 Then
                    CountWorkbookFormulaErrors = CountWorkbookFormulaErrors + 1
                End If
            Next c
        Next r
    Next i
    On Error GoTo 0
End Function

Function CountDuplicateSetupBills() As Long
    On Error Resume Next
    Dim setupSheet As Object
    Dim setupBillCol As Integer
    Dim r As Long
    Dim s As Long
    Dim nameR As String
    Dim nameS As String

    If Not ThisComponent.Sheets.hasByName("Setup") Then
        CountDuplicateSetupBills = 1
        Exit Function
    End If

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    setupBillCol = SetupBillColumn(setupSheet)

    For r = 2 To 199
        nameR = LCase(Trim(setupSheet.getCellByPosition(setupBillCol, r).String))
        If IsSetupBillRecord(setupSheet, r, setupBillCol) Then
            For s = r + 1 To 200
                nameS = LCase(Trim(setupSheet.getCellByPosition(setupBillCol, s).String))
                If IsSetupBillRecord(setupSheet, s, setupBillCol) And nameR = nameS Then
                    CountDuplicateSetupBills = CountDuplicateSetupBills + 1
                    Exit For
                End If
            Next s
        End If
    Next r
    On Error GoTo 0
End Function

Function IsValidCycleText(valueText As String) As Boolean
    Dim normalized As String
    normalized = LCase(Trim(valueText))
    IsValidCycleText = (normalized = "1st" Or normalized = "15th" Or normalized = "both" Or _
                        normalized = "1st & 15th" Or normalized = "1st+15th")
End Function

Function CountInvalidSetupCycles() As Long
    On Error Resume Next
    Dim setupSheet As Object
    Dim setupBillCol As Integer
    Dim r As Long
    Dim billName As String
    Dim cycleText As String

    If Not ThisComponent.Sheets.hasByName("Setup") Then
        CountInvalidSetupCycles = 1
        Exit Function
    End If

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    setupBillCol = SetupBillColumn(setupSheet)

    For r = 2 To 200
        billName = Trim(setupSheet.getCellByPosition(setupBillCol, r).String)
        If IsSetupBillRecord(setupSheet, r, setupBillCol) And _
           IsSetupRowActive(setupSheet, r, setupBillCol) Then
            cycleText = setupSheet.getCellByPosition(setupBillCol + 1, r).String
            If Not IsValidCycleText(cycleText) Then CountInvalidSetupCycles = CountInvalidSetupCycles + 1
        End If
    Next r
    On Error GoTo 0
End Function

Function CountCurrentMonthSyncIssues() As Long
    On Error Resume Next
    Dim setupSheet As Object
    Dim monthSheet As Object
    Dim setupBillCol As Integer
    Dim r As Long
    Dim billName As String
    Dim cycleText As String
    Dim monthName As String

    monthName = ExistingMonthSheetName(Month(Date), Year(Date))
    If monthName = "" Or Not ThisComponent.Sheets.hasByName("Setup") Then
        CountCurrentMonthSyncIssues = 1
        Exit Function
    End If

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    monthSheet = ThisComponent.Sheets.getByName(monthName)
    setupBillCol = SetupBillColumn(setupSheet)

    For r = 2 To 200
        billName = Trim(setupSheet.getCellByPosition(setupBillCol, r).String)
        If IsSetupBillRecord(setupSheet, r, setupBillCol) And _
           IsSetupRowActive(setupSheet, r, setupBillCol) Then
            cycleText = setupSheet.getCellByPosition(setupBillCol + 1, r).String
            If CycleIncludes(cycleText, "1st") Then
                If FindBillRow(monthSheet, CycleBaseColumn("1st"), billName) < 0 Then CountCurrentMonthSyncIssues = CountCurrentMonthSyncIssues + 1
            End If
            If CycleIncludes(cycleText, "15th") Then
                If FindBillRow(monthSheet, CycleBaseColumn("15th"), billName) < 0 Then CountCurrentMonthSyncIssues = CountCurrentMonthSyncIssues + 1
            End If
        End If
    Next r
    On Error GoTo 0
End Function

Function CountCurrentMonthDuplicateBills() As Long
    Dim monthName As String
    Dim monthSheet As Object

    monthName = ExistingMonthSheetName(Month(Date), Year(Date))
    If monthName = "" Then
        CountCurrentMonthDuplicateBills = 1
        Exit Function
    End If

    monthSheet = ThisComponent.Sheets.getByName(monthName)
    CountCurrentMonthDuplicateBills = CountDuplicateBillsInCycle(monthSheet, "1st") + _
                                     CountDuplicateBillsInCycle(monthSheet, "15th")
End Function

Function CountDuplicateBillsInCycle(monthSheet As Object, cycleName As String) As Long
    Dim baseCol As Integer
    Dim r As Long
    Dim s As Long
    Dim nameR As String
    Dim nameS As String

    baseCol = CycleBaseColumn(cycleName)
    For r = 13 To 25
        nameR = LCase(Trim(monthSheet.getCellByPosition(baseCol, r).String))
        If nameR <> "" Then
            For s = r + 1 To 26
                nameS = LCase(Trim(monthSheet.getCellByPosition(baseCol, s).String))
                If nameR = nameS Then
                    CountDuplicateBillsInCycle = CountDuplicateBillsInCycle + 1
                    Exit For
                End If
            Next s
        End If
    Next r
End Function

Function CountCurrentMonthBillsMissingSetup() As Long
    On Error Resume Next
    Dim setupSheet As Object
    Dim monthSheet As Object
    Dim setupBillCol As Integer
    Dim cycleName As String
    Dim baseCol As Integer
    Dim r As Long
    Dim billName As String
    Dim monthName As String
    Dim cycleIndex As Integer

    monthName = ExistingMonthSheetName(Month(Date), Year(Date))
    If monthName = "" Or Not ThisComponent.Sheets.hasByName("Setup") Then
        CountCurrentMonthBillsMissingSetup = 1
        Exit Function
    End If

    setupSheet = ThisComponent.Sheets.getByName("Setup")
    monthSheet = ThisComponent.Sheets.getByName(monthName)
    setupBillCol = SetupBillColumn(setupSheet)

    For cycleIndex = 0 To 1
        If cycleIndex = 0 Then
            cycleName = "1st"
        Else
            cycleName = "15th"
        End If
        baseCol = CycleBaseColumn(cycleName)

        For r = 13 To 26
            billName = Trim(monthSheet.getCellByPosition(baseCol, r).String)
            If billName <> "" Then
                If FindSetupBillRow(setupSheet, setupBillCol, billName) < 0 Then
                    CountCurrentMonthBillsMissingSetup = CountCurrentMonthBillsMissingSetup + 1
                End If
            End If
        Next r
    Next cycleIndex
    On Error GoTo 0
End Function

Sub SetHealthLine(prefixText As String, fullText As String, isGood As Boolean)
    On Error Resume Next
    Dim sheet As Object
    Dim cursor As Object
    Dim addr As Object
    Dim r As Long
    Dim c As Long
    Dim cell As Object

    sheet = ThisComponent.Sheets.getByName(CONTROL_SHEET)
    cursor = sheet.createCursor()
    cursor.gotoEndOfUsedArea(True)
    addr = cursor.RangeAddress

    For r = addr.StartRow To addr.EndRow
        For c = addr.StartColumn To addr.EndColumn
            cell = sheet.getCellByPosition(c, r)
            If Left(cell.String, Len(prefixText)) = prefixText Then
                cell.String = fullText
                If isGood Then
                    cell.CellBackColor = RGB(226,240,217)
                    cell.CharColor = RGB(55,86,35)
                Else
                    cell.CellBackColor = RGB(255,235,156)
                    cell.CharColor = RGB(127,96,0)
                End If
                cell.CharWeight = 150
                Exit Sub
            End If
        Next c
    Next r
    On Error GoTo 0
End Sub

Sub StyleMonthSheet(sheet As Object)
    On Error Resume Next
    Dim r As Integer

    ' A / row 1 are the visual gutters.
    ' B:H = 1st cycle, I = spacer, J:P = 15th cycle.
    SetColumnWidth sheet, 0, 900
    SetColumnWidth sheet, 1, 3300
    SetColumnWidth sheet, 2, 1800
    SetColumnWidth sheet, 3, 2300
    SetColumnWidth sheet, 4, 2300
    SetColumnWidth sheet, 5, 2100
    SetColumnWidth sheet, 6, 1800
    SetColumnWidth sheet, 7, 2100
    SetColumnWidth sheet, 8, 450
    SetColumnWidth sheet, 9, 3300
    SetColumnWidth sheet, 10, 1800
    SetColumnWidth sheet, 11, 2300
    SetColumnWidth sheet, 12, 2300
    SetColumnWidth sheet, 13, 2100
    SetColumnWidth sheet, 14, 2200
    SetColumnWidth sheet, 15, 2100

    StyleRange sheet, "B2:P2", RGB(23,54,93), RGB(255,255,255), 15, True, False
    StyleRange sheet, "B3:H3", RGB(31,65,104), RGB(255,255,255), 12, True, False
    StyleRange sheet, "J3:P3", RGB(31,65,104), RGB(255,255,255), 12, True, False

    StyleRange sheet, "B4:E11", RGB(248,250,252), RGB(31,41,55), 10.5, False, False
    StyleRange sheet, "J4:M11", RGB(248,250,252), RGB(31,41,55), 10.5, False, False
    StyleRange sheet, "B4:B8", RGB(89,89,89), RGB(255,255,255), 10.5, True, False
    StyleRange sheet, "J4:J8", RGB(89,89,89), RGB(255,255,255), 10.5, True, False
    StyleRange sheet, "B9:E11", RGB(234,242,248), RGB(31,41,55), 10.5, False, True
    StyleRange sheet, "J9:M11", RGB(234,242,248), RGB(31,41,55), 10.5, False, True
    StyleRange sheet, "D10", RGB(221,235,247), RGB(23,54,93), 12, True, True
    StyleRange sheet, "L10", RGB(221,235,247), RGB(23,54,93), 12, True, True

    StyleRange sheet, "B13:H13", RGB(31,78,120), RGB(255,255,255), 10.5, True, True
    StyleRange sheet, "J13:P13", RGB(31,78,120), RGB(255,255,255), 10.5, True, True
    StyleRange sheet, "B14:H27", RGB(255,255,255), RGB(31,41,55), 10.5, False, False
    StyleRange sheet, "J14:P27", RGB(255,255,255), RGB(31,41,55), 10.5, False, False
    StyleRange sheet, "B14:B27", RGB(242,246,250), RGB(23,54,93), 10.5, True, False
    StyleRange sheet, "J14:J27", RGB(242,246,250), RGB(23,54,93), 10.5, True, False
    StyleRange sheet, "E14:E27", RGB(226,240,217), RGB(31,41,55), 10.5, True, False
    StyleRange sheet, "M14:M27", RGB(226,240,217), RGB(31,41,55), 10.5, True, False
    StyleRange sheet, "F14:F27", RGB(239,244,249), RGB(31,41,55), 10, False, False
    StyleRange sheet, "N14:N27", RGB(239,244,249), RGB(31,41,55), 10, False, False
    StyleRange sheet, "G14:G27", RGB(226,240,217), RGB(55,86,35), 10, True, False
    StyleRange sheet, "O14:O27", RGB(226,240,217), RGB(55,86,35), 10, True, False
    StyleRange sheet, "H14:H27", RGB(248,250,252), RGB(31,41,55), 10, False, False
    StyleRange sheet, "P14:P27", RGB(248,250,252), RGB(31,41,55), 10, False, False

    For r = 13 To 26
        SetRowHeight sheet, r, 500
    Next r
    SetNoWrap sheet, "B14:H27"
    SetNoWrap sheet, "J14:P27"

    StyleRange sheet, "B28:H28", RGB(221,235,247), RGB(23,54,93), 10.5, True, True
    StyleRange sheet, "J28:P28", RGB(221,235,247), RGB(23,54,93), 10.5, True, True
    StyleRange sheet, "B31:H31", RGB(217,234,247), RGB(31,78,120), 10.5, True, False
    StyleRange sheet, "J31:P31", RGB(217,234,247), RGB(31,78,120), 10.5, True, False
    StyleRange sheet, "B32:H32", RGB(31,65,104), RGB(255,255,255), 10.5, True, False
    StyleRange sheet, "J32:P32", RGB(31,65,104), RGB(255,255,255), 10.5, True, False
    StyleRange sheet, "B33:H61", RGB(255,255,255), RGB(31,41,55), 10, False, False
    StyleRange sheet, "J33:P61", RGB(255,255,255), RGB(31,41,55), 10, False, False
    SetNoWrap sheet, "B32:H61"
    SetNoWrap sheet, "J32:P61"

    SetRowHeight sheet, 0, 550
    SetRowHeight sheet, 1, 950
    SetRowHeight sheet, 2, 650
    For r = 3 To 11
        SetRowHeight sheet, r, 430
    Next r
    SetRowHeight sheet, 12, 520
    SetRowHeight sheet, 27, 520
    SetRowHeight sheet, 30, 700
    SetRowHeight sheet, 31, 500
    For r = 32 To 60
        SetRowHeight sheet, r, 430
    Next r

    InstallMonthlyBillManagerButton sheet

    ' Center compact table-like information; keep bill names left-aligned.
    AlignLeft sheet, "B14:B27"
    AlignCenter sheet, "C14:H27"
    AlignLeft sheet, "J14:J27"
    AlignCenter sheet, "K14:P27"
    AlignCenter sheet, "C4:E11"
    AlignCenter sheet, "K4:M11"
    AlignCenter sheet, "D28:H28"
    AlignCenter sheet, "L28:P28"
    AlignCenter sheet, "H33:H61"
    AlignCenter sheet, "P33:P61"
    AlignCenter sheet, "C46:E52"

    FixDarkCellContrast sheet
    sheet.TabColor = RGB(46,117,182)
    On Error GoTo 0
End Sub

Sub StyleSetupSheet(sheet As Object)
    On Error Resume Next
    Dim i As Integer
    StyleRange sheet, "B2:I2", RGB(23,54,93), RGB(255,255,255), 11, True, True
    SetColumnWidth sheet, 0, 900
    SetColumnWidth sheet, 1, 4300
    SetColumnWidth sheet, 2, 2100
    SetColumnWidth sheet, 3, 2600
    SetColumnWidth sheet, 4, 2600
    SetColumnWidth sheet, 5, 3000
    SetColumnWidth sheet, 6, 3400
    SetColumnWidth sheet, 7, 1800
    SetColumnWidth sheet, 8, 6200

    For i = 1 To 80
        If (i Mod 2) = 0 Then
            StyleRange sheet, "B" & (i + 2) & ":I" & (i + 2), RGB(247,250,252), RGB(31,41,55), 10.5, False, False
        End If
    Next i
    StyleRange sheet, "C3:H81", RGB(250,252,254), RGB(31,41,55), 10.5, False, False
    InstallSetupBillManagerButtons sheet
    AlignLeft sheet, "B3:B201"
    AlignCenter sheet, "C3:H201"
    AlignLeft sheet, "I3:I201"
    FixDarkCellContrast sheet
    sheet.TabColor = RGB(91,101,115)
    On Error GoTo 0
End Sub

Sub StyleDebtSheet(sheet As Object)
    On Error Resume Next
    Dim i As Integer
    StyleRange sheet, "B2:K2", RGB(23,54,93), RGB(255,255,255), 11, True, True
    SetColumnWidth sheet, 0, 900
    SetColumnWidth sheet, 1, 4200
    For i = 2 To 10
        SetColumnWidth sheet, i, 2700
    Next i
    For i = 1 To 100
        If (i Mod 2) = 0 Then
            StyleRange sheet, "B" & (i + 2) & ":K" & (i + 2), RGB(247,250,252), RGB(31,41,55), 10.5, False, False
        End If
    Next i
    HighlightKeywordRows sheet, 1, 101, 1
    AlignLeft sheet, "B3:B101"
    AlignCenter sheet, "C3:K101"
    FixDarkCellContrast sheet
    sheet.TabColor = RGB(112,88,156)
    On Error GoTo 0
End Sub

Sub StyleDashboardSheet(sheet As Object)
    On Error Resume Next
    SetColumnWidth sheet, 0, 900
    SetColumnWidth sheet, 1, 3600
    SetColumnWidth sheet, 2, 3000
    SetColumnWidth sheet, 3, 3000
    SetColumnWidth sheet, 4, 900
    SetColumnWidth sheet, 5, 3600
    SetColumnWidth sheet, 6, 3000
    SetColumnWidth sheet, 7, 3000
    SetColumnWidth sheet, 8, 3000
    StyleRange sheet, "B2:I3", RGB(23,54,93), RGB(255,255,255), 17, True, False
    StyleRange sheet, "B5:I5", RGB(217,234,247), RGB(31,78,120), 11, True, True
    HighlightDashboardLabels sheet
    AlignCenter sheet, "C2:I60"
    AlignLeft sheet, "B2:B60"
    AlignLeft sheet, "F2:F60"
    FixDarkCellContrast sheet
    sheet.TabColor = RGB(84,130,53)
    On Error GoTo 0
End Sub

Sub StyleControlSheet(sheet As Object)
    On Error Resume Next
    SetColumnWidth sheet, 0, 900
    SetColumnWidth sheet, 1, 4700
    SetColumnWidth sheet, 2, 4700
    SetColumnWidth sheet, 3, 4700
    SetColumnWidth sheet, 4, 4700
    SetRowHeight sheet, 0, 550
    SetRowHeight sheet, 1, 1050
    SetRowHeight sheet, 4, 900
    SetRowHeight sheet, 5, 900
    SetRowHeight sheet, 8, 900
    SetRowHeight sheet, 9, 900
    SetRowHeight sheet, 12, 900
    SetRowHeight sheet, 13, 900
    SetRowHeight sheet, 16, 900
    FixDarkCellContrast sheet
    sheet.TabColor = RGB(23,54,93)
    On Error GoTo 0
End Sub

Sub StyleGenericSheet(sheet As Object)
    On Error Resume Next
    StyleRange sheet, "B2:I2", RGB(23,54,93), RGB(255,255,255), 11, True, True
    sheet.TabColor = RGB(91,101,115)
    On Error GoTo 0
End Sub

Sub HighlightDashboardLabels(sheet As Object)
    On Error Resume Next
    Dim cursor As Object
    Dim addr As Object
    Dim r As Long
    Dim c As Long
    Dim cell As Object
    Dim txt As String

    cursor = sheet.createCursor()
    cursor.gotoEndOfUsedArea(True)
    addr = cursor.RangeAddress

    For r = addr.StartRow To addr.EndRow
        For c = addr.StartColumn To addr.EndColumn
            cell = sheet.getCellByPosition(c, r)
            txt = LCase(Trim(cell.String))
            If txt <> "" Then
                If InStr(txt, "paid") > 0 Or InStr(txt, "income") > 0 Then
                    cell.CellBackColor = RGB(226,240,217)
                    cell.CharColor = RGB(55,86,35)
                    cell.CharWeight = 150
                ElseIf InStr(txt, "due") > 0 Or InStr(txt, "remaining") > 0 Or InStr(txt, "difference") > 0 Then
                    cell.CellBackColor = RGB(255,242,204)
                    cell.CharColor = RGB(127,96,0)
                    cell.CharWeight = 150
                ElseIf InStr(txt, "balance") > 0 Or InStr(txt, "total") > 0 Then
                    cell.CellBackColor = RGB(221,235,247)
                    cell.CharColor = RGB(23,54,93)
                    cell.CharWeight = 150
                End If
            End If
        Next c
    Next r
    On Error GoTo 0
End Sub

Sub HighlightKeywordRows(sheet As Object, firstRow As Long, lastRow As Long, labelColumn As Long)
    On Error Resume Next
    Dim r As Long
    Dim txt As String
    Dim rowRange As Object

    For r = firstRow To lastRow
        txt = LCase(Trim(sheet.getCellByPosition(labelColumn, r).String))
        If InStr(txt, "total") > 0 Or InStr(txt, "summary") > 0 Then
            rowRange = sheet.getCellRangeByPosition(0, r, 9, r)
            rowRange.CellBackColor = RGB(221,235,247)
            rowRange.CharColor = RGB(23,54,93)
            rowRange.CharWeight = 150
        End If
    Next r
    On Error GoTo 0
End Sub

Sub StyleRange(sheet As Object, address As String, backColor As Long, fontColor As Long, fontSize As Double, boldText As Boolean, addBorder As Boolean)
    On Error Resume Next
    Dim area As Object
    Dim border As New com.sun.star.table.BorderLine2

    area = sheet.getCellRangeByName(address)
    area.CellBackColor = backColor
    area.CharColor = fontColor
    area.CharFontName = "Liberation Sans"
    area.CharHeight = fontSize
    If boldText Then
        area.CharWeight = 150
    Else
        area.CharWeight = 100
    End If
    area.IsTextWrapped = True
    area.VertJustify = 2

    If addBorder Then
        border.Color = RGB(218,226,234)
        border.OuterLineWidth = 18
        area.TopBorder = border
        area.BottomBorder = border
        area.LeftBorder = border
        area.RightBorder = border
    End If
    On Error GoTo 0
End Sub

Sub SetNoWrap(sheet As Object, address As String)
    On Error Resume Next
    sheet.getCellRangeByName(address).IsTextWrapped = False
    On Error GoTo 0
End Sub

Sub FixDarkCellContrast(sheet As Object)
    On Error Resume Next
    Dim cursor As Object
    Dim addr As Object
    Dim r As Long
    Dim c As Long
    Dim cell As Object
    Dim fillColor As Long
    Dim redPart As Long
    Dim greenPart As Long
    Dim bluePart As Long
    Dim brightness As Double

    cursor = sheet.createCursor()
    cursor.gotoEndOfUsedArea(True)
    addr = cursor.RangeAddress

    For r = addr.StartRow To addr.EndRow
        For c = addr.StartColumn To addr.EndColumn
            cell = sheet.getCellByPosition(c, r)
            fillColor = cell.CellBackColor
            If fillColor >= 0 Then
                redPart = Int(fillColor / 65536) Mod 256
                greenPart = Int(fillColor / 256) Mod 256
                bluePart = fillColor Mod 256
                brightness = (0.299 * redPart) + (0.587 * greenPart) + (0.114 * bluePart)

                If brightness < 135 And Trim(cell.String) <> "" Then
                    cell.CharColor = RGB(255,255,255)
                    cell.CharWeight = 150
                End If
            End If
        Next c
    Next r
    On Error GoTo 0
End Sub

Sub AlignCenter(sheet As Object, address As String)
    On Error Resume Next
    Dim area As Object
    area = sheet.getCellRangeByName(address)
    area.HoriJustify = 2
    area.VertJustify = 2
    On Error GoTo 0
End Sub

Sub AlignLeft(sheet As Object, address As String)
    On Error Resume Next
    Dim area As Object
    area = sheet.getCellRangeByName(address)
    area.HoriJustify = 1
    area.VertJustify = 2
    On Error GoTo 0
End Sub

Sub SetColumnWidth(sheet As Object, columnIndex As Integer, widthValue As Long)
    On Error Resume Next
    sheet.Columns.getByIndex(columnIndex).Width = widthValue
    On Error GoTo 0
End Sub

Sub SetRowHeight(sheet As Object, rowIndex As Integer, heightValue As Long)
    On Error Resume Next
    sheet.Rows.getByIndex(rowIndex).Height = heightValue
    On Error GoTo 0
End Sub

Function IsMonthSheet(nameText As String) As Boolean
    Dim monthNumber As Integer
    Dim yearNumber As Integer
    Dim bareMonth As String

    monthNumber = MonthNumberByName(nameText)
    If monthNumber = 0 Then
        IsMonthSheet = False
        Exit Function
    End If

    bareMonth = MonthNameByNumber(monthNumber)
    If Trim(nameText) = bareMonth Then
        IsMonthSheet = True
        Exit Function
    End If

    yearNumber = MonthSheetYear(nameText)
    IsMonthSheet = (yearNumber >= 2000 And yearNumber <= 2200)
End Function

Function BackupBeforeBillChange(sheetName As String) As String
    Dim documentPath As String
    Dim workbookDir As String
    Dim backupDir As String
    Dim backupPath As String
    Dim fileName As String
    Dim safeSheetName As String

    If ThisComponent.URL = "" Then
        Err.Raise 1003, , "Save the workbook before changing bills."
    End If

    documentPath = ConvertFromURL(ThisComponent.URL)
    workbookDir = ParentPath(documentPath)
    backupDir = workbookDir & "/backups"
    If Dir(backupDir, 16) = "" Then MkDir backupDir

    fileName = FileNameFromPath(documentPath)
    safeSheetName = Replace(sheetName, " ", "-")
    backupPath = backupDir & "/" & Left(fileName, Len(fileName) - 4) & "." & _
                 Format(Now, "YYYYMMDD-HHMMSS") & "." & safeSheetName & ".pre-bill-change.bak.ods"
    FileCopy documentPath, backupPath
    BackupBeforeBillChange = backupPath
End Function

Function BackupBeforeTheme() As String
    Dim documentPath As String
    Dim workbookDir As String
    Dim backupDir As String
    Dim backupPath As String
    Dim fileName As String

    If ThisComponent.URL = "" Then
        Err.Raise 1002, , "Save the workbook before applying the theme."
    End If

    documentPath = ConvertFromURL(ThisComponent.URL)
    workbookDir = ParentPath(documentPath)
    backupDir = workbookDir & "/backups"
    If Dir(backupDir, 16) = "" Then MkDir backupDir

    fileName = FileNameFromPath(documentPath)
    backupPath = backupDir & "/" & Left(fileName, Len(fileName) - 4) & "." & _
                 Format(Now, "YYYYMMDD-HHMMSS") & ".pre-theme.bak.ods"
    FileCopy documentPath, backupPath
    BackupBeforeTheme = backupPath
End Function

Function FileNameFromPath(pathText As String) As String
    Dim i As Long
    For i = Len(pathText) To 1 Step -1
        If Mid(pathText, i, 1) = "/" Then
            FileNameFromPath = Mid(pathText, i + 1)
            Exit Function
        End If
    Next i
    FileNameFromPath = pathText
End Function

Function GetProjectRoot() As String
    Dim documentPath As String
    Dim workbookDir As String

    If ThisComponent.URL = "" Then
        Err.Raise 1001, , "Save the ODS workbook before using the Control Panel."
    End If

    documentPath = ConvertFromURL(ThisComponent.URL)
    workbookDir = ParentPath(documentPath)
    GetProjectRoot = ParentPath(workbookDir)
End Function

Function ParentPath(pathText As String) As String
    Dim i As Long
    For i = Len(pathText) To 1 Step -1
        If Mid(pathText, i, 1) = "/" Then
            ParentPath = Left(pathText, i - 1)
            Exit Function
        End If
    Next i
    ParentPath = pathText
End Function

Function QuoteArg(valueText As String) As String
    QuoteArg = Chr(34) & valueText & Chr(34)
End Function

Function ReadAllText(filePath As String) As String
    Dim fileNo As Integer
    Dim lineText As String
    Dim allText As String

    If Dir(filePath) = "" Then
        ReadAllText = "No result file was produced."
        Exit Function
    End If

    fileNo = FreeFile
    Open filePath For Input As #fileNo
    Do While Not EOF(fileNo)
        Line Input #fileNo, lineText
        allText = allText & lineText & Chr(10)
    Loop
    Close #fileNo
    ReadAllText = allText
End Function

Sub SetStatus(statusText As String)
    On Error Resume Next
    Dim sheet As Object
    Dim cursor As Object
    Dim addr As Object
    Dim r As Long
    Dim c As Long
    Dim cell As Object

    sheet = ThisComponent.Sheets.getByName(CONTROL_SHEET)
    cursor = sheet.createCursor()
    cursor.gotoEndOfUsedArea(True)
    addr = cursor.RangeAddress

    For r = addr.StartRow To addr.EndRow
        For c = addr.StartColumn To addr.EndColumn
            cell = sheet.getCellByPosition(c, r)
            If Left(cell.String, 12) = "Last status:" Then
                cell.String = "Last status: " & statusText
                Exit Sub
            End If
        Next c
    Next r
    On Error GoTo 0
End Sub
'''

def _basic_module_xml() -> bytes:
    content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">
<script:module xmlns:script="http://openoffice.org/2000/script" script:name="{MACRO_MODULE}" script:language="StarBasic" script:moduleType="normal">{escape(BASIC_CODE)}</script:module>'''
    return content.encode("utf-8")


def _basic_library_xml() -> bytes:
    content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE library:library PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "library.dtd">
<library:library xmlns:library="http://openoffice.org/2000/library" library:name="{MACRO_LIBRARY}" library:readonly="false" library:passwordprotected="false"><library:element library:name="{MACRO_MODULE}"/></library:library>'''
    return content.encode("utf-8")


def _libraries_xml(existing: bytes | None) -> bytes:
    if existing:
        root = ET.fromstring(existing)
    else:
        root = ET.Element(_q("library", "libraries"))

    for child in list(root):
        if child.tag == _q("library", "library") and child.get(_q("library", "name")) == MACRO_LIBRARY:
            root.remove(child)

    entry = ET.SubElement(root, _q("library", "library"))
    entry.set(_q("library", "name"), MACRO_LIBRARY)
    entry.set(_q("library", "link"), "false")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _ensure_control_styles(root: ET.Element) -> None:
    automatic = root.find("office:automatic-styles", NS)
    if automatic is None:
        body = root.find("office:body", NS)
        automatic = ET.Element(_q("office", "automatic-styles"))
        if body is not None:
            index = list(root).index(body)
            root.insert(index, automatic)
        else:
            root.insert(0, automatic)

    style_names = {
        "BWBTitle",
        "BWBSubtitle",
        "BWBSection",
        "BWBButtonPrimary",
        "BWBButtonGreen",
        "BWBButtonSlate",
        "BWBButtonOrange",
        "BWBInfo",
        "BWBStatus",
        "BWBCard",
        "BWBLegendWhite",
        "BWBLegendBlue",
        "BWBLegendGreen",
        "BWBLegendAlert",
        "BWBColumnWide",
    }
    for child in list(automatic):
        if child.tag == STYLE_STYLE and child.get(STYLE_STYLE_NAME) in style_names:
            automatic.remove(child)

    def cell_style(
        name: str,
        background: str,
        color: str,
        *,
        bold: bool = False,
        size: str = "11pt",
        align: str = "left",
        border: str = "#D7E1EA",
        padding: str = "0.14cm",
    ):
        style = ET.SubElement(automatic, STYLE_STYLE)
        style.set(STYLE_STYLE_NAME, name)
        style.set(STYLE_FAMILY, "table-cell")
        props = ET.SubElement(style, _q("style", "table-cell-properties"))
        props.set(_q("fo", "background-color"), background)
        props.set(_q("fo", "border"), f"0.02cm solid {border}")
        props.set(_q("fo", "padding"), padding)
        text_props = ET.SubElement(style, _q("style", "text-properties"))
        text_props.set(_q("fo", "color"), color)
        text_props.set(_q("fo", "font-size"), size)
        text_props.set(_q("fo", "font-family"), "Liberation Sans")
        if bold:
            text_props.set(_q("fo", "font-weight"), "bold")
        para = ET.SubElement(style, _q("style", "paragraph-properties"))
        para.set(_q("fo", "text-align"), align)

    cell_style("BWBTitle", "#17365D", "#FFFFFF", bold=True, size="18pt", align="center", border="#17365D", padding="0.22cm")
    cell_style("BWBSubtitle", "#EAF2F8", "#36556F", size="10.5pt", align="center", border="#EAF2F8")
    cell_style("BWBSection", "#D9EAF7", "#1F4E78", bold=True, size="11pt", border="#B4C7DC")
    cell_style("BWBButtonPrimary", "#2E75B6", "#FFFFFF", bold=True, size="12pt", align="center", border="#2E75B6", padding="0.24cm")
    cell_style("BWBButtonGreen", "#548235", "#FFFFFF", bold=True, size="12pt", align="center", border="#548235", padding="0.24cm")
    cell_style("BWBButtonSlate", "#5B6573", "#FFFFFF", bold=True, size="12pt", align="center", border="#5B6573", padding="0.24cm")
    cell_style("BWBButtonOrange", "#C55A11", "#FFFFFF", bold=True, size="12pt", align="center", border="#C55A11", padding="0.24cm")
    cell_style("BWBInfo", "#F8FAFC", "#475569", size="10pt", border="#E5EBF0")
    cell_style("BWBCard", "#FFFFFF", "#1F2937", size="10pt", border="#D7E1EA")
    cell_style("BWBStatus", "#FFF2CC", "#7F6000", bold=True, size="10.5pt", border="#E6D58A")
    cell_style("BWBLegendWhite", "#FFFFFF", "#374151", bold=True, size="9.5pt", align="center", border="#D7E1EA")
    cell_style("BWBLegendBlue", "#DDEBF7", "#17365D", bold=True, size="9.5pt", align="center", border="#B4C7DC")
    cell_style("BWBLegendGreen", "#E2F0D9", "#375623", bold=True, size="9.5pt", align="center", border="#C6E0B4")
    cell_style("BWBLegendAlert", "#FFF2CC", "#7F6000", bold=True, size="9.5pt", align="center", border="#E6D58A")

    style = ET.SubElement(automatic, STYLE_STYLE)
    style.set(STYLE_STYLE_NAME, "BWBColumnWide")
    style.set(STYLE_FAMILY, "table-column")
    props = ET.SubElement(style, _q("style", "table-column-properties"))
    props.set(_q("style", "column-width"), "5.0cm")


def _text_cell(text: str, style: str = "BWBInfo", span: int = 1) -> list[ET.Element]:
    cell = ET.Element(CELL)
    cell.set(STYLE_NAME, style)
    cell.set(OFFICE_VALUE_TYPE, "string")
    if span > 1:
        cell.set(COL_SPAN, str(span))
    p = ET.SubElement(cell, TEXT_P)
    p.text = text
    result = [cell]
    result.extend(ET.Element(COVERED_CELL) for _ in range(span - 1))
    return result


def _link_cell(
    label: str,
    macro_name: str,
    *,
    style: str = "BWBButtonPrimary",
    span: int = 2,
) -> list[ET.Element]:
    cell = ET.Element(CELL)
    cell.set(STYLE_NAME, style)
    cell.set(OFFICE_VALUE_TYPE, "string")
    if span > 1:
        cell.set(COL_SPAN, str(span))
    p = ET.SubElement(cell, TEXT_P)
    link = ET.SubElement(p, TEXT_A)
    link.set(
        XLINK_HREF,
        f"vnd.sun.star.script:{MACRO_LIBRARY}.{MACRO_MODULE}.{macro_name}?language=Basic&location=document",
    )
    link.set(XLINK_TYPE, "simple")
    link.text = label
    result = [cell]
    result.extend(ET.Element(COVERED_CELL) for _ in range(span - 1))
    return result


def _row(*cells: ET.Element) -> ET.Element:
    row = ET.Element(ROW)
    for cell in cells:
        row.append(cell)
    return row


def _build_control_sheet() -> ET.Element:
    sheet = ET.Element(TABLE)
    sheet.set(TABLE_NAME, CONTROL_SHEET)

    for _ in range(4):
        column = ET.SubElement(sheet, COLUMN)
        column.set(STYLE_NAME, "BWBColumnWide")

    sheet.append(_row(*_text_cell("Bi-Weekly Bills", "BWBTitle", 4)))
    sheet.append(_row(*_text_cell("Family Budget Control Center  •  Navy Federal + Plaid  •  ODS-native", "BWBSubtitle", 4)))
    sheet.append(_row(*_text_cell("", "BWBInfo", 4)))

    sheet.append(_row(*_text_cell("EVERYDAY", "BWBSection", 4)))
    sheet.append(_row(
        *_link_cell("↻  DRY-RUN SYNC", "RunDrySync", style="BWBButtonPrimary", span=2),
        *_link_cell("▤  SHOW ACCOUNTS", "ShowAccounts", style="BWBButtonPrimary", span=2),
    ))
    sheet.append(_row(
        *_text_cell("Preview bill reconciliation without writing the workbook.", "BWBCard", 2),
        *_text_cell("View linked balances and the selected Bills Checking account.", "BWBCard", 2),
    ))

    sheet.append(_row(*_text_cell("CONNECTION", "BWBSection", 4)))
    sheet.append(_row(
        *_link_cell("⚙  REPAIR BANK CONNECTION", "RepairConnection", style="BWBButtonGreen", span=2),
        *_link_cell("✓  SANDBOX REAUTH TEST", "SandboxReauthTest", style="BWBButtonOrange", span=2),
    ))
    sheet.append(_row(
        *_text_cell("Uses Plaid Update Mode on the existing Item—no replacement token.", "BWBCard", 2),
        *_text_cell("Sandbox only: force ITEM_LOGIN_REQUIRED, repair it, and verify the same token.", "BWBCard", 2),
    ))

    sheet.append(_row(*_text_cell("MAINTENANCE", "BWBSection", 4)))
    sheet.append(_row(
        *_link_cell("✓  ODS SELF-TEST", "RunSelfTest", style="BWBButtonSlate", span=2),
        *_link_cell("⌘  FULL UNIT TESTS", "RunUnitTests", style="BWBButtonSlate", span=2),
    ))
    sheet.append(_row(
        *_text_cell("Checks ODS package integrity, formulas, backups, and transaction matching.", "BWBCard", 2),
        *_text_cell("Runs the project test suite locally on this Arch machine.", "BWBCard", 2),
    ))

    sheet.append(_row(*_text_cell("APPEARANCE", "BWBSection", 4)))
    sheet.append(_row(*_link_cell("✦  APPLY / REFRESH WORKBOOK THEME", "ApplyWorkbookTheme", style="BWBButtonGreen", span=4)))
    sheet.append(_row(*_text_cell(
        "Applies the polished navy theme to every sheet—months, Setup, Debt Tracker, Dashboard, and this Control Center. "
        "A timestamped pre-theme ODS backup is created first.",
        "BWBCard",
        4,
    )))

    sheet.append(_row(*_text_cell("YEAR MANAGEMENT", "BWBSection", 4)))
    sheet.append(_row(*_link_cell("＋  CREATE NEXT YEAR", "CreateNextYear", style="BWBButtonPrimary", span=4)))
    sheet.append(_row(*_text_cell(
        "Creates a full new year of month tabs without overwriting history. "
        "The prior year is preserved, new payment data starts blank, and active Setup bills are carried forward.",
        "BWBCard",
        4,
    )))

    sheet.append(_row(*_text_cell("HEALTH & READINESS", "BWBSection", 4)))
    sheet.append(_row(
        *_link_cell("↻  REFRESH WORKBOOK HEALTH", "RefreshWorkbookHealth", style="BWBButtonPrimary", span=2),
        *_link_cell("✓  PRE-PRODUCTION CHECK", "RunPreProductionCheck", style="BWBButtonGreen", span=2),
    ))
    sheet.append(_row(
        *_text_cell("Formula health: not checked", "BWBCard", 2),
        *_text_cell("Setup health: not checked", "BWBCard", 2),
    ))
    sheet.append(_row(
        *_text_cell("Current month sync: not checked", "BWBCard", 2),
        *_text_cell("Monthly consistency: not checked", "BWBCard", 2),
    ))

    sheet.append(_row(*_text_cell("LEGEND", "BWBSection", 4)))
    sheet.append(_row(
        *_text_cell("White = editable", "BWBLegendWhite", 1),
        *_text_cell("Blue = bank / calculated", "BWBLegendBlue", 1),
        *_text_cell("Green = reconciled / healthy", "BWBLegendGreen", 1),
        *_text_cell("Amber / red = attention", "BWBLegendAlert", 1),
    ))

    sheet.append(_row(*_text_cell("SAFETY", "BWBSection", 4)))
    sheet.append(_row(*_text_cell(
        "GUI actions are fixed and do not accept arbitrary shell commands. Plaid secrets, bank credentials, and access tokens are never stored in worksheet cells or macros.",
        "BWBInfo",
        4,
    )))
    sheet.append(_row(*_text_cell(
        "LibreOffice may ask you to enable document macros. Trust only this project's workbook folder—not your entire home or Downloads directory.",
        "BWBInfo",
        4,
    )))

    while len(sheet.findall("table:table-row", NS)) < 30:
        sheet.append(_row(*_text_cell("", "BWBInfo", 4)))
    sheet.append(_row(*_text_cell("Last status: ready", "BWBStatus", 4)))
    return sheet


def _install_control_sheet(content: bytes) -> bytes:
    root = ET.fromstring(content)
    _ensure_control_styles(root)
    spreadsheet = root.find(".//office:spreadsheet", NS)
    if spreadsheet is None:
        raise ValueError("ODS package has no spreadsheet element.")

    for table in list(spreadsheet.findall("table:table", NS)):
        if table.get(TABLE_NAME) == CONTROL_SHEET:
            spreadsheet.remove(table)

    spreadsheet.insert(0, _build_control_sheet())

    # Formula values use the lexical OpenFormula prefix "of:=". ElementTree
    # drops namespace declarations used only inside attribute values, so add
    # this declaration explicitly before serialization.
    root.set("xmlns:of", NS["of"])
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _install_manifest(manifest: bytes) -> bytes:
    root = ET.fromstring(manifest)
    full_path_attr = _q("manifest", "full-path")
    media_type_attr = _q("manifest", "media-type")
    paths = {
        f"Basic/{MACRO_LIBRARY}/{MACRO_MODULE}.xml",
        f"Basic/{MACRO_LIBRARY}/script-lb.xml",
        "Basic/script-lc.xml",
    }
    for entry in list(root):
        if entry.get(full_path_attr) in paths:
            root.remove(entry)
    for path in sorted(paths):
        entry = ET.SubElement(root, _q("manifest", "file-entry"))
        entry.set(full_path_attr, path)
        entry.set(media_type_attr, "text/xml")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _formula_snapshot(path: Path) -> tuple[str, ...]:
    with ZipFile(path, "r") as archive:
        root = ET.fromstring(archive.read("content.xml"))
    formula_attr = _q("table", "formula")
    return tuple(element.attrib[formula_attr] for element in root.iter() if formula_attr in element.attrib)


def install_control_panel(workbook_path: Path, *, backup_dir: Path | None = None) -> Path:
    workbook_path = Path(workbook_path).expanduser().resolve()
    if workbook_path.suffix.casefold() != ".ods":
        raise ValueError("Control Panel installation requires an .ods workbook.")
    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")

    lock_file = workbook_path.parent / f".~lock.{workbook_path.name}#"
    if lock_file.exists():
        raise RuntimeError("Close the workbook in LibreOffice before installing the Control Panel.")

    before_formulas = _formula_snapshot(workbook_path)
    before_sheets = tuple(ODSWorkbook(workbook_path).sheet_names)
    existing_sheets = tuple(sheet for sheet in before_sheets if sheet != CONTROL_SHEET)

    if backup_dir is None:
        backup_dir = workbook_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"{workbook_path.stem}.{stamp}.pre-gui.bak.ods"
    shutil.copy2(workbook_path, backup_path)

    fd, temp_name = tempfile.mkstemp(prefix=f".{workbook_path.name}.", suffix=".gui.tmp", dir=workbook_path.parent)
    os.close(fd)
    try:
        with ZipFile(workbook_path, "r") as source:
            original_names = source.namelist()
            content_xml = _install_control_sheet(source.read("content.xml"))
            manifest_xml = _install_manifest(source.read("META-INF/manifest.xml"))
            libraries_xml = _libraries_xml(source.read("Basic/script-lc.xml") if "Basic/script-lc.xml" in original_names else None)

            replacements = {
                "content.xml": content_xml,
                "META-INF/manifest.xml": manifest_xml,
                "Basic/script-lc.xml": libraries_xml,
                f"Basic/{MACRO_LIBRARY}/script-lb.xml": _basic_library_xml(),
                f"Basic/{MACRO_LIBRARY}/{MACRO_MODULE}.xml": _basic_module_xml(),
            }

            with ZipFile(temp_name, "w") as target:
                written: set[str] = set()
                for info in source.infolist():
                    payload = replacements.get(info.filename)
                    if payload is None:
                        payload = source.read(info.filename)
                    target.writestr(info, payload)
                    written.add(info.filename)
                for name, payload in replacements.items():
                    if name not in written:
                        target.writestr(name, payload, compress_type=ZIP_DEFLATED)

        os.replace(temp_name, workbook_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise

    try:
        after_book = ODSWorkbook(workbook_path)
        after_sheets = tuple(after_book.sheet_names)
        if not after_sheets or after_sheets[0] != CONTROL_SHEET:
            raise RuntimeError("Control Panel installation failed validation: sheet missing.")
        if tuple(sheet for sheet in after_sheets if sheet != CONTROL_SHEET) != existing_sheets:
            raise RuntimeError("Control Panel installation changed the existing sheet list/order.")
        if _formula_snapshot(workbook_path) != before_formulas:
            raise RuntimeError("Control Panel installation changed existing workbook formulas.")

        with ZipFile(workbook_path, "r") as archive:
            for required in (
                "Basic/script-lc.xml",
                f"Basic/{MACRO_LIBRARY}/script-lb.xml",
                f"Basic/{MACRO_LIBRARY}/{MACRO_MODULE}.xml",
            ):
                if required not in archive.namelist():
                    raise RuntimeError(f"Control Panel installation is missing {required}.")
    except Exception as exc:
        shutil.copy2(backup_path, workbook_path)
        raise RuntimeError(
            f"Control Panel validation failed and the original workbook was restored: {exc}"
        ) from exc

    return backup_path
