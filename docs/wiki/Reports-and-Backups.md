# Reports and Backups

## Reports

Open **Reports**, select a month/year, then choose:

- **Export Excel**
- **Export PDF**
- **Export ODS**
- **Export all**

Reports are read-only exports and do not modify SQLite.

Spreadsheet exports contain logical sections for Summary, Bills, Funding, Transactions, and Accounts.

The default report directory is:

```text
<database directory>/reports/
```

The Reports page displays the actual destination and can open it directly.

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
