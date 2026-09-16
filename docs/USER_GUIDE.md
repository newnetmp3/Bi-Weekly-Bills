# Bi-Weekly Bills 1.0 User Guide

Bi-Weekly Bills is a Linux desktop application for running a recurring 1st/15th bill-pay workflow. It stores bill history in SQLite, reads balances and transactions through Plaid, helps verify payments against the correct bank account, plans the amount that needs to be moved into Bills Checking, and exports monthly reports.

The application never sends a bank transfer or pays a bill. Any actual movement of money remains a user action at the financial institution.

## 1. Install on Arch Linux

### Recommended: install the release wheel

Install the required system packages:

```bash
sudo pacman -S --needed python python-pip libglvnd libxkbcommon libxkbcommon-x11 libxcb fontconfig
```

Download the latest `.whl` file from the [GitHub Releases page](https://github.com/newnetmp3/Bi-Weekly-Bills/releases). For v1.0.0 the file is:

```text
bi_weekly_bills-1.0.0-py3-none-any.whl
```

Create a dedicated environment and install the downloaded wheel:

```bash
mkdir -p ~/.local/opt/bi-weekly-bills
python -m venv ~/.local/opt/bi-weekly-bills
~/.local/opt/bi-weekly-bills/bin/python -m pip install --upgrade pip
~/.local/opt/bi-weekly-bills/bin/python -m pip install ~/Downloads/bi_weekly_bills-1.0.0-py3-none-any.whl
```

Launch the app:

```bash
~/.local/opt/bi-weekly-bills/bin/biweekly-bills-app
```

The first launch installs or refreshes the desktop launcher and icon. Future launches can normally be made from the desktop application menu.

To update later, download the newer wheel and install it into the same environment with `pip install --upgrade`.

### Alternate: install from a Git clone

```bash
sudo pacman -S --needed git python python-pip libglvnd libxkbcommon libxkbcommon-x11 libxcb fontconfig

git clone https://github.com/newnetmp3/Bi-Weekly-Bills.git
cd Bi-Weekly-Bills

python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
.venv/bin/biweekly-bills-app
```

For normal users, `git clone` is only a way to download the application. No commits, pushes, branches, or other Git workflow are required.

## 2. First run and bank connection

An unconfigured installation opens **Settings** automatically.

In **Settings → Bank connection**:

1. Enter the Plaid **Client ID** and **Secret**. Enter a Redirect URI only if the Plaid configuration requires one.
2. Choose **Save & continue**.
3. When prompted, complete the financial-institution sign-in in **Plaid Link in the browser**. Do not type a bank username or password into the Bi-Weekly Bills settings fields.
4. Allow the application to synchronize accounts, balances, and transactions.
5. In **Accounts & bill funding**, select the checking account used as the destination for the bill-funding workflow and choose **Use selected as Bills Checking**.
6. Select the ordinary checking account that funds Bills Checking and choose **Use selected as Transfer Source**.

The selected roles are shown directly in the account table. A saved role remains selected after refresh and is marked **YES**.

### Existing bank connection

If a connection already exists, use **Sync Accounts** for routine refreshes. If the bank requires reauthentication, use **Repair bank connection**. Repair uses Plaid Update Mode and reuses the existing connection.

If the application reports a recovery state, use the recovery path it presents. Do not work around a repair/recovery message by creating another Production Plaid Item.

## 3. Configure recurring bills

Open **Bills**.

The left side is the master recurring-bill list. Select an existing bill or choose **New bill**.

The bill editor contains:

- **Bill name**
- **Cycle** — 1st, 15th, or Both
- **Latest due / typical amount**
- **Default method**
- **Merchant aliases** — comma-separated alternate names used when matching bank transactions
- **Payment account** — the real account the payment leaves from
- **Notes**

Choose **Save** after editing.

### Payment Account and funding behavior

The Payment Account drives the funding rule automatically:

- If **Payment Account = Bills Checking**, the bill is included in the Bills Checking funding requirement and the configured Transfer Source is used.
- If the payment leaves another checking account, the bill is not included in the Bills Checking transfer requirement.

Bills Checking cannot be its own Transfer Source. If a bill requires Bills Checking but no valid Transfer Source is configured, the application blocks the save instead of guessing.

Use **Deactivate** when a recurring bill should stop. Deactivation preserves historical bill instances.

## 4. Overview

**Overview** is the monthly dashboard.

Use the month/year selectors to review:

- total scheduled
- total paid
- remaining or over-scheduled amount
- 1st vs 15th scheduled split
- paid vs still due
- workflow handled/verified counts
- month closeout status
- bill-level activity

The **1st Pay Period** and **15th Pay Period** cards include **Open checklist** buttons that jump directly to the corresponding work list.

## 5. Pay Periods / bill checklist

Open **Pay Periods** and select:

- **1st Pay Period**
- **15th Pay Period**
- **Whole Month**

Use **Show unfinished only** when you only want items that still need attention.

Selecting a bill row exposes the supported inline fields for that bill instance. The checklist tracks manual Paid checkpoints separately from bank verification, so a manual “I handled this” action does not fabricate a bank transaction.

For a prior month, a manually paid item without bank evidence is shown as paid but unverified. Current-month items can remain waiting for a bank posting.

### Bills Checking funding

The page includes Bills Checking funding information based on recurring bills whose Payment Account is Bills Checking. For the current month, the synced available Bills Checking balance is applied to the 1st requirement first, then to the 15th. Historical/future months do not use today’s live balance.

## 6. Transactions

Open **Transactions** after synchronizing bank data.

Use the filters for:

- date/month
- account
- posted/pending state
- reconciliation state
- search text

The merchant column distinguishes real merchants from connected-account transfers. Where Plaid supplies a logo, the app caches and displays it.

### Transaction Detail

Select a transaction to populate the **Transaction Detail** card at the bottom. When Plaid supplied the data, it can show:

- Merchant
- Bank description
- Website
- Plaid merchant/entity ID
- Personal-finance category and confidence
- Payment channel
- Counterparty
- Location/store information

This is especially useful for short or unfamiliar descriptors such as a merchant name that is not immediately recognizable.

### Reconcile a payment

For an unresolved outgoing payment, the reconciliation panel shows candidate bills and the bank amount versus expected amount. Automatic matching is deliberately conservative; ambiguous evidence remains for review.

Use:

- **Accept Match** when the selected bill is correct.
- **Ignore** when the transaction is unrelated to a tracked bill.
- **Undo Match / Ignore** to reverse a prior decision.

The configured Payment Account is authoritative evidence. A matching amount from the wrong checking account is not treated as verification.

### Validate a Bills funding transfer

A posted incoming transfer to Bills Checking can be validated against:

- 1st pay period
- 15th pay period
- whole month

The application compares the bank transfer with the scheduled aggregate funding requirement and records the exact/short/over difference. This validation does **not** mark individual bills paid.

## 7. Reconciliation

The **Reconciliation** page is for historical review and integrity checks.

Use it to:

- review verified, needs-review, paid-unverified, and unpaid historical bills
- re-scan completed months using stored bank history
- inspect why a payment did or did not match
- diagnose missing Payment Accounts, incomplete funding routing, duplicate aliases, orphaned history, and unpaired credit-account evidence

Historical re-scans operate on already stored transactions; they do not create a new Plaid Item.

## 8. Reports

Open **Reports**, select a month/year, then choose:

- **Export Excel**
- **Export PDF**
- **Export ODS**
- **Export all**

The spreadsheet formats contain Summary, Bills, Funding, Transactions, and Accounts sections. Reports are read-only exports and do not modify SQLite.

By default reports are written under:

```text
<database directory>/reports/
```

The page shows the exact report folder and can open it directly.

## 9. Settings, backups, and restore

**Settings** contains bank setup, account roles, and database backup/restore.

Automatic verified SQLite backups are created before consequential changes such as bill edits, account-role changes, reconciliation operations, imports, and schema migrations.

Use the Settings backup controls for an extra manual snapshot before major maintenance.

Restore verifies the selected backup, creates a safety snapshot of the current database, restores the chosen copy, verifies the result, and refreshes the app.

Plaid credentials are intentionally stored outside SQLite, so database backups do not contain the bank access token.

## 10. Customize the sidebar

The left navigation is normally locked.

Choose the small **cog** to enter sidebar edit mode. While editing:

- double-click a section name or press **F2** to rename it
- drag only from the three-line grip on the right side of a row to reorder it
- drop between rows to choose the new position

Names and order save automatically. Turn the cog off to return to normal navigation; edit hints and drag grips are hidden when editing is disabled.

Sidebar preferences are stored separately from financial data.

## 11. Local data and privacy

Default SQLite location:

```text
~/.local/share/bi-weekly-bills/biweekly-bills.sqlite3
```

This honors `XDG_DATA_HOME`.

Private application configuration and Plaid credentials live under:

```text
~/.config/bi-weekly-bills/
```

The application uses private filesystem permissions for credential/config files. Bank credentials are not stored in the Git repository or SQLite database.

## 12. Import an older ODS workbook

The desktop database is the source of truth for 1.0, but existing ODS history can be imported read-only.

Example:

```bash
biweekly-bills-app --import-ods "workbook/Bi-Weekly Bills - 2026.ods" --legacy-year 2026
```

To import without opening the GUI:

```bash
biweekly-bills-app --import-ods "workbook/Bi-Weekly Bills - 2026.ods" --legacy-year 2026 --import-only
```

The importer verifies that the source workbook bytes were not changed.

## 13. Maintenance commands

Normal use should happen in the desktop app. Useful maintenance commands include:

```bash
biweekly-bills doctor
biweekly-bills production-readiness
biweekly-bills update-link
biweekly-bills recover-production
biweekly-bills self-test
biweekly-bills install-desktop
```

Use `update-link` for bank reauthentication. Use `recover-production` only when the readiness/doctor output reports a pending Production credential recovery.

## 14. Troubleshooting

### A transaction did not auto-match

Open **Transactions → Needs review** or **Reconciliation**. Check the merchant/description, amount, date, and especially the bill’s configured Payment Account. Add a merchant alias on the Bills page only when the identity is genuinely the same merchant.

### An unfamiliar merchant appears

Select the transaction and read the **Transaction Detail** card. Plaid metadata can include the website, category, counterparty, payment channel, and location even when the short statement description is vague.

### A bill paid from another checking account is not verifying

Open **Bills** and confirm that bill’s **Payment Account** is the account where the outgoing transaction actually posts.

### Bills Checking funding is wrong

Confirm the Bills Checking and Transfer Source roles in **Settings**, then verify that every bill meant to be funded into Bills Checking has **Payment Account = Bills Checking**.

### Bank sign-in broke

Use **Repair bank connection** in Settings or `biweekly-bills update-link`. Do not create a replacement connection to fix reauthentication.

### Need to undo a reconciliation

Select the transaction and use **Undo Match / Ignore**, or use the historical reconciliation tools when reviewing older periods.

## 15. Safety model

Bi-Weekly Bills intentionally favors “leave it for review” over a risky automatic match.

Important guarantees include:

- no automatic bill payment or money transfer
- bank reauthentication reuses the existing Plaid Item
- stable account IDs are stored internally instead of trusting names/masks as identity
- ambiguous payment matches remain unresolved
- wrong-account evidence cannot verify a bill merely because the amount matches
- backup/restore uses SQLite’s online backup mechanism and integrity checks
- Plaid credentials remain outside SQLite

For deeper implementation and maintenance details, see [DESKTOP_APP.md](DESKTOP_APP.md).
