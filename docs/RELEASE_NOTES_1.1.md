# Bi-Weekly Bills 1.1.0

## Updates keep your bank connection

- All connection state now lives in your home folder. Version 1.0 kept the one-bank-connection lock, the transaction sync cursor, and the Bills Checking choice inside the app's install folder, where a reinstall or an Arch Python upgrade could lose them. They now live in `~/.config/bi-weekly-bills/state/` and are moved there automatically the first time 1.1 starts. Nothing is deleted from the old location.
- **New one-command updater:** `scripts/update-bi-weekly-bills.sh` backs up your database and settings, installs the new version (rebuilding the app environment if a Python upgrade broke it), and confirms, without contacting your bank, that the same connection is still in place.
- New commands: `biweekly-bills verify-update`, `migrate-state`, `backup`, and `connection-item`.

## Easier day-to-day use

- **Sync bank** button and a "Last synced 2 days ago" note on every page, so you no longer have to open Settings to sync.
- **Settings → About & updates** shows the app version and where your data, backups, and bank connection are stored, with buttons to open each folder.
- Sync times appear in plain words instead of raw timestamps.
- The app version is shown in the sidebar.

## Fixes

- An empty `XDG_DATA_HOME` no longer points the database at a relative path.
