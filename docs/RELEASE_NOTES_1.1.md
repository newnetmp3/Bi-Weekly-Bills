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

## Better payment matching

- **Payments made just before or after a month boundary now match.** A 1st-period bill paid on the 30th or 31st of the month before (or a 15th-period bill paid a few days late) is now matched to the right month. On real data this raised bank-confirmed bills for July–November from 36 to 53.
- When the same bill could match two months, the one nearest the payment date wins.
- **Transactions → Re-check matches** finds payments that older versions filed one month off (for example a card payment on Aug 31 filed under August instead of September) and bills that can now be matched. You tick the changes you agree with; a backup is made first and every change can be undone.

## Simpler bill paying

- Each bill shows **one plain status**: To pay, Autopay · waiting for bank, Paid · waiting for bank, ✓ Paid · confirmed by bank, Paid · not found at bank, or No payment due.
- A **Next to pay** box with a single **Mark paid** button walks you through the period.
- Ticking **Paid** now fills in the paid amount from the amount due (if you have not entered one) and sets the bill to Paid, so "remaining" drops straight away. Unticking reverses it.
- The checklist fits without sideways scrolling; the old spreadsheet columns are behind **Show workbook columns**.

## Plainer wording

- "Handled", "reconciled", "verified" and "unresolved" are replaced with "paid", "linked to a bill", "confirmed by bank" and "not linked" across Overview, Pay Periods and Transactions. Transactions buttons are now **Link to bill**, **Not a bill** and **Undo**.

## Fixes

- An empty `XDG_DATA_HOME` no longer points the database at a relative path.
