# ODS Migration

Version 1.0 uses SQLite as the source of truth, but older OpenDocument Spreadsheet history can be imported read-only.

## Import and open the app

```bash
biweekly-bills-app \
  --import-ods "workbook/Bi-Weekly Bills - 2026.ods" \
  --legacy-year 2026
```

## Import without opening the GUI

```bash
biweekly-bills-app \
  --import-ods "workbook/Bi-Weekly Bills - 2026.ods" \
  --legacy-year 2026 \
  --import-only
```

The importer verifies that the source workbook bytes are unchanged.

## Important distinction

The ODS workbook is migration/history input. New day-to-day bill management should happen in the desktop application and SQLite database.

The repository still contains maintenance code for the older workbook workflow, but normal 1.0 use should start with the desktop application.
