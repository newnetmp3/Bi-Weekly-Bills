# Reports and Backups

## Reports

The Reports tab has two kinds of exports.

### Full monthly export

Choose a month/year, then use:

- **Export Excel**
- **Export PDF**
- **Export ODS**
- **Export all**

These are the complete monthly reports and include the bill list, Bills Checking funding, bank transactions, reconciliation status, and account information.

### Financial report buttons

All focused financial reports are shown directly as buttons on the Reports tab. There is no report dropdown.

- **Bills Funding** — shows how much Bills Checking needs for the selected month, split between the 1st and 15th, plus every bill included in that funding amount.
- **Spending by Merchant** — groups posted outflows for the selected month by merchant/description, showing total spent, transaction count, average purchase, largest purchase, and accounts used. Known funding/internal transfers are excluded.
- **12-Month Bill Trend** — shows scheduled bills, recorded payments, remaining amounts, and Bills Checking funding for the trailing 12 months ending with the selected month. It also includes a scheduled-vs-paid trend chart.
- **Needs Attention** — collects bills with money still due, paid items that are not bank-verified, missing Payment Accounts, unresolved posted transactions, and funding setup issues.
- **Payment Variance** — compares scheduled and recorded payment totals by bill for the selected year.
- **Account Cash Flow** — summarizes posted inflows, outflows, and net cash flow for each connected account in the selected month.
- **Annual Bill Summary** — provides month-by-month totals and annual totals by bill for the selected year.
- **Bill Cost Changes** — compares each bill's earliest and latest scheduled amount over the trailing 12 months and shows the dollar/percentage change, minimum, maximum, and average.

Focused financial reports are generated as Excel workbooks so they can be sorted, filtered, and kept as records.

All reports are read-only exports and do not modify the application database.

The default report directory is:

```text
<database directory>/reports/
```

The Reports page shows the exact report folder and can open it directly.

## SQLite database location

The default database is:

```text
~/.local/share/bi-weekly-bills/biweekly-bills.sqlite3
```

This honors `XDG_DATA_HOME`.

## Automatic backups

The app creates verified SQLite snapshots before consequential operations such as bill edits/deactivation, account-role changes, reconciliation changes, ODS imports, and schema migrations.

Backups use SQLite's online backup API and are integrity-checked.

The default backup directory is below the database directory:

```text
<database directory>/backups/
```

## Manual backup

Use the backup controls in **Settings** before major maintenance or whenever you want an extra snapshot.

## Restore

Restore performs a guarded sequence:

1. Verify the selected backup.
2. Create a `pre-restore-safety` snapshot of the current database.
3. Restore the selected snapshot.
4. Verify the restored database.
5. Refresh the application.

Plaid credentials are stored outside SQLite and therefore are not included in database backups.

See [[Security-and-Privacy]] for data-handling rules.