# Bi-Weekly Bills

## Why this app exists: the two-checking-account system

**Bi-Weekly Bills is built around using two separate checking accounts on purpose.** That distinction is the core idea behind the application.

1. **Primary Checking / Transfer Source** — the normal checking account where income is received and everyday money is kept.
2. **Bills Checking** — a **separate checking account used only to hold money for scheduled automatic bill payments**.

Instead of leaving money for upcoming bills mixed in with everyday spending money, Bi-Weekly Bills calculates how much needs to be moved into Bills Checking for the **1st** and **15th** pay-period cycles. You make that transfer between your own accounts, and the transferred money then stays in Bills Checking until the scheduled automatic payments post.

The app exists to make that system practical: it tracks which bills are paid from Bills Checking, calculates the amount that should be transferred into it, compares that requirement with the available Bills Checking balance, watches the outgoing automatic payments, and reconciles those payments against the bills they were meant to cover.

**Bills Checking is not another name for your normal checking account. It is intentionally a second checking account whose balance is reserved for bills.** If this budgeting method is unfamiliar, think of Bills Checking as a dedicated holding account for money that has already been set aside for upcoming automatic payments.

Bi-Weekly Bills never moves the money itself. It tells you what should be funded and verifies what actually happened after the bank transactions post.

The desktop application is built with PySide6 + SQLite and can synchronize balances and transactions through Plaid, reconcile payments, plan Bills Checking funding, and export monthly reports.

## Version 1.0

Version 1.0 makes the desktop application the primary interface and SQLite the source of truth. The legacy ODS/LibreOffice workflow remains available for migration and maintenance, but normal day-to-day use should begin with the desktop app.

- **Start here:** [Bi-Weekly Bills 1.0 User Guide](docs/USER_GUIDE.md)
- **What shipped:** [Version 1.0 Release Notes](docs/RELEASE_NOTES_1.0.md)
- **Technical desktop reference:** [Desktop Application](docs/DESKTOP_APP.md)
- **User Wiki:** [GitHub Wiki](https://github.com/newnetmp3/Bi-Weekly-Bills/wiki)

The application reads bank data and records/reconciles bill activity. It **does not initiate bank transfers or bill payments**.

## Free-Trial design

This project is deliberately designed around Plaid's Trial-plan rules:

- Trial supports real Production data for free.
- A Trial team can create at most 10 Production Items.
- Deleting an Item does **not** restore the slot.
- Every Production access token created counts against the lifetime Trial Item limit.
- The Production access token must therefore be persisted and never casually discarded.
- Routine use is limited to Trial-supported **Transactions** and **Balance**.
- The program never initiates transfers or payments.

Source of truth: https://plaid.com/docs/account/billing/#trial-plans

## Arch Linux architecture

This project does **not** require Homebrew or Plaid's CLI.

Plaid's official CLI currently documents Homebrew as its installation method. On Arch, this project instead uses Plaid's official Python SDK (`plaid-python`) and the same Link/API flow demonstrated by Plaid's official Quickstart.

Sandbox and Production credentials are isolated from each other:

```
~/.config/bi-weekly-bills/plaid_item_sandbox.json
~/.config/bi-weekly-bills/plaid_item_production.json
```

The directory is mode `0700` and credential files are mode `0600`. They are never committed to Git. Sandbox and Production also use separate selected-account state, transaction cursors, and transaction caches under `data/`.

After the first successful Production link, the program also creates:

```
PRODUCTION_ITEM_CREATED.lock
```

That file contains no credential. Commit it to the private repository so a fresh clone still knows that a lifetime Trial slot has already been consumed.

## Safety rules

1. Link the financial institution **once** in Production.
2. If a persisted credential already exists, initial Link refuses to run again.
3. If `PRODUCTION_ITEM_CREATED.lock` exists but the local credential is missing, Link refuses to run. Recover the existing credential instead of creating another Item.
4. Before opening Link, the program verifies that the secure credential directory is writable.
5. Immediately after the first Production token exchange, the app writes a secure pending-recovery credential and records the Production Item guard before final persistence.
6. If final persistence fails or the process is interrupted, run `biweekly-bills recover-production` to finish saving the same Item. Do **not** run Production Link again.
7. The project uses **OpenDocument Spreadsheet (`.ods`) only**. It reads and writes the ODS package directly; there is no XLSX conversion step.
8. Workbook sync is a dry run unless `--apply` is supplied.
9. `--apply` creates a timestamped `.bak.ods` workbook backup before writing.
10. Transaction matching is conservative: posted outflows must come from the configured Payment Account when one is set. An already-Paid bill can be verified from a unique matching outgoing amount on that account even if the bank description is generic; ambiguous same-account/same-amount cases stay in review. Internal credit receipt evidence is still paired when available.
11. Reauthentication will use Plaid **update mode**, not a replacement Item.

## Local checkout

Recommended path:

```
~/git/bi-weekly-bills/
├── src/
├── data/          # ignored local cache/config
├── workbook/      # ignored personal workbook
├── tests/
└── .env           # ignored Plaid API keys
```

Put the current **`.ods`** bills workbook in `workbook/`. `.xlsx` and `.xls` are intentionally not supported by this project.

## Arch installation

```bash
cd ~/git/bi-weekly-bills
git pull

sudo pacman -S --needed python python-pip

python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .

# Optional explicit launcher/icon install; the desktop app also does this
# automatically on first launch.
biweekly-bills install-desktop
```

As of September 2026, the current official `plaid-python` release is 43.x; the project pins that major version.

### First desktop run

Launch `biweekly-bills-app`. An unconfigured installation opens **Settings**
automatically.

Enter the Plaid Client ID and Secret in **Settings → Bank connection**, then
click **Save & continue**. The app stores these API values in the user's private
configuration directory rather than SQLite. The primary setup button then
changes to the next safe action: connect, recover, or sync.

Plaid Link handles the actual bank sign-in. Do not enter a bank username or
password into the Bi-Weekly Bills settings fields.

After the first successful sync, use **Accounts & bill funding** on the same
Settings page to confirm **Bills Checking** and **Transfer Source**.
The standalone Accounts navigation page has been removed.

Project-root `.env` files and the CLI remain supported for development and
maintenance, but they are no longer required for the normal desktop first-run
workflow.

### Linux desktop / Wayland launcher

The packaged desktop app includes a scalable launcher icon and wide Bi-Weekly Bills logo. Running `biweekly-bills-app` automatically keeps the current user's XDG launcher files current, including:

```text
~/.local/share/applications/biweekly-bills.desktop
~/.local/share/icons/hicolor/scalable/apps/biweekly-bills.svg
```

Qt advertises the matching Wayland desktop ID `biweekly-bills`, so GNOME/KDE can associate the running window with the installed launcher/icon. Use `biweekly-bills install-desktop` to refresh the integration manually.

### First-run QA

The ten-pass first-run QA review and the resulting workflow changes are
documented in `docs/FIRST_RUN_QA.md`.

### Merchant identity and logos

Transactions now maintain a local merchant catalog in SQLite. Plaid merchant
metadata such as the merchant entity ID, website, and HTTPS logo URL is retained,
and merchant logos are fetched asynchronously and cached in SQLite. Cached logos
are rendered beside merchant names in the Transactions table.

Connected credit/loan accounts and generic account transfers are intentionally
not treated as merchants. Transactions tied to connected accounts are labeled
as connected-account activity, while normal merchant purchases remain merchant
activity.

Reconciliation also uses this distinction: a named merchant cannot be suggested
as a connected credit/loan bill merely because the amount matches a recorded
Paid amount or the transaction came from the same checking account. Connected
credit/loan bills are resolved through connected-account/internal-transfer
evidence instead. Generic ACH/payment descriptions can still use the existing
configured Payment Account evidence.

### UI responsiveness

The desktop UI now loads pages lazily and reuses clean page state between
navigation changes. Hidden pages are marked dirty after writes and refresh only
when opened instead of rebuilding immediately.

Transactions use period-scoped SQLite reads, debounced/local filtering, and
bulk-cached reconciliation candidate analysis. Reconciliation audit/diagnostics
run off the GUI thread and reuse one historical scan. Settings backup history is
metadata-only during normal display; full integrity checks still occur when
backups are created or restored. Reports query only the selected transaction
month.

See `docs/UI_RESPONSIVENESS_QA.md` for the interaction-validation pass.

### Outgoing payment verification

Paid bills are verified from the account that actually paid them. If a bill's
Payment Account is Bills Checking, the outgoing Bills Checking transaction is
eligible. If another checking account is selected as the Payment Account, that
account is used instead. The rule is generic for all bills.

A posted outgoing transaction with the recorded Paid amount can verify an
already-Paid bill even when its bank description is generic. Wrong-account
transactions are rejected, and ambiguous same-account/same-amount candidates
are left for review.

### Reconciliation and diagnostics

The desktop app includes a dedicated **Reconciliation** page. It audits completed months, explains unresolved historical payments, runs integrity diagnostics, and can safely re-scan stored bank history without creating or relinking a Plaid Item. Bill-specific merchant aliases can be maintained from the Bills editor and are included in automatic and historical matching.

Right-click menus are page-aware across the main data tables. On Pay Periods, selecting a row makes its editable bill fields available directly in the checklist; there is no separate Bill Details panel.

The Overview is intentionally compact: summary and pay-period cards use their natural height so the bill table gets more room, while the Scheduled and Paid cards include native Qt donut charts for the 1st/15th scheduled split and paid-vs-still-due progress.


## CLI / development Sandbox workflow

Keep:

```dotenv
PLAID_ENV=sandbox
```

Then:

```bash
biweekly-bills link
```

This opens a local browser page backed by Flask and Plaid Link.

Sandbox Items do not consume the Production Trial Item limit.

## CLI / maintenance bank workflow

Do not switch to Production until the Trial plan is approved and your Production secret is available.

Change the local `.env`:

```dotenv
PLAID_SECRET=<your Production secret>
PLAID_ENV=production
```

Then check:

```bash
biweekly-bills doctor
```

If doctor reports a pending Production recovery credential, stop and run:

```bash
biweekly-bills recover-production
```

Never create another Production Item to recover from an interrupted token save.

Before the **first and only** Production link, confirm:

- no Production Item lock exists;
- no Production Plaid credential already exists;
- `~/.config/bi-weekly-bills/` is backed up by your normal secure local backup strategy;
- the workbook itself is in `workbook/`.

Then:

```bash
biweekly-bills link
```

Do not close the terminal until the browser explicitly reports:

```
Success. Item credential persisted locally.
```

Afterward:

```bash
biweekly-bills doctor
git add PRODUCTION_ITEM_CREATED.lock
git commit -m "Record existing Production Plaid Item"
git push
```

The lock file is safe to commit. The Plaid credential is not.

## CLI account-role maintenance

```bash
biweekly-bills accounts
```

This uses Plaid Balance and lists the linked accounts. Copy the `account_id` for the Bills Checking account:

```bash
biweekly-bills use-bills-account ACCOUNT_ID
```

The selected account ID is kept in ignored local config.

## ODS workbook requirement

The active workbook must be an `.ods` file. For example:

```text
~/git/bi-weekly-bills/workbook/Bi-Weekly Bills - 2026.ods
```

The sync engine preserves the ODS package and existing formatting/formulas outside the cells it explicitly updates. LibreOffice lock files such as `.~lock.*.ods#` are ignored when auto-selecting the workbook.

## Workbook Control Panel

After installation, you can operate the common tests and read-only checks from a LibreOffice sheet instead of a terminal.

Close the workbook in LibreOffice first, then run once:

```bash
biweekly-bills install-gui
```

This:

- creates a **Bi-Weekly Bills Control** sheet as the first tab;
- embeds a dedicated `BiWeeklyBills` LibreOffice Basic macro library;
- creates a timestamped `.pre-gui.bak.ods` backup before changing the workbook;
- preserves the existing sheet order and formulas and validates both after installation;
- refuses to modify a workbook while a LibreOffice lock file is present.

The Control Center exposes fixed, non-arbitrary actions:

- **DRY-RUN SYNC** — pulls Plaid data and previews reconciliation without workbook writes;
- **SHOW ACCOUNTS** — displays account/balance information;
- **REPAIR BANK CONNECTION** — launches Plaid Link Update Mode for the existing Item;
- **SANDBOX REAUTH TEST** — Sandbox-only forced `ITEM_LOGIN_REQUIRED` recovery test;
- **ODS SELF-TEST** — proves the original workbook is unchanged while testing ODS write/backup behavior;
- **FULL UNIT TESTS** — runs the Python unittest suite locally on Arch;
- **APPLY / REFRESH WORKBOOK THEME** — creates a pre-theme backup and applies a polished navy visual theme to every sheet.

The theme covers all monthly tabs plus Setup, Debt Tracker, Dashboard, and the Control Center. It changes presentation properties only; formulas, number formats, and values are not intentionally rewritten by the theme macro.

### Monthly Bill Editor

After applying the workbook theme, every monthly sheet has a persistent **BILL EDITOR** in the unused upper-right area. Bill management is non-modal: no InputBox/MsgBox workflow is used for Add/Edit/Remove.

The editor provides native Calc dropdowns for **Action** (`Add / Edit / Remove`), **Bill**, and **Cycle** (`1st / 15th / Both`), plus editable **When**, **Due**, and **Method** fields. The **LOAD**, **APPLY**, and **RESET** controls operate entirely in-sheet, and results/errors appear in the editor status strip instead of a popup.

The Bill dropdown points directly at the Setup master list but still permits typing a new name for Add. Applying a change:

- updates Setup first;
- propagates the bill to the current month and all existing future months/years;
- preserves prior historical months;
- preserves any current/future row that already contains Paid data during removal;
- reuses existing empty bill slots rather than inserting/deleting bill-table rows;
- creates a timestamped `.pre-bill-change.bak.ods` backup before changing data.

Adding a bill to a monthly sheet does not automatically add a new Plaid merchant alias. Bank matching remains conservative and continues to use the explicit rules in `bill_rules.py`.

### Setup as the master bill list

The **Setup** sheet is the authoritative source for bill information:

- Bill
- Cycle
- When
- Latest Due
- Default Method
- Payment Account
- Active
- Notes

The themed Setup sheet includes a persistent **BILL EDITOR** and **SYNC CURRENT + FUTURE MONTHS** control. The Setup editor is also fully non-modal: Action, Bill, and Cycle use native Calc dropdowns; When, Latest Due, Method, Payment Account, and Notes are regular in-sheet fields; and **LOAD / APPLY / RESET** perform the requested operation without opening bill-management dialogs.

**Add** on Setup creates or reactivates the master bill and propagates it to the current month and every later month for the selected 1st / 15th / Both cycle.

**Edit** updates Setup and propagates the bill name, cycle, When, Latest Due, and Default Method to the current month and every later month. A cycle change is reconciled automatically.

**Remove** does not delete the Setup record; it marks the bill inactive. Historical month tabs are never touched. Current/future rows are cleared only when the Paid cell is empty. If Paid data exists, that monthly row is preserved.

The monthly **MANAGE BILLS** action follows the same master-list rules: Add/Edit/Remove updates Setup first, then synchronizes the current month and later months.

If Setup cells are edited manually instead of through the GUI, use **SYNC CURRENT + FUTURE MONTHS** to push those master-list changes forward. Past months remain unchanged.

### Pre-Production workbook reliability

The workbook theme now includes a reliability pass intended to run before real Production bank data is connected:

- **Consistent number formats** — monthly money fields and Setup Latest Due use a consistent U.S. currency format; Debt Tracker uses header-driven currency / percentage formatting where applicable.
- **Exception-only conditional formatting** — warning Status values (for example `SHORT`, `DUE`, `OVERDUE`, `NEEDS ACTION`) and negative Extra/(Short) values are highlighted. Normal/reconciled rows are not color-saturated.
- **Formula protection** — formula cells are locked against accidental editing with passwordless sheet protection; ordinary non-formula inputs remain editable. This is convenience protection, not security.
- **Frozen working headers** — monthly bill-table headers, Setup/Debt headers, and Dashboard headings remain visible while scrolling.
- **Workbook Health** — the Control Center can scan for formula errors, duplicate Setup bills, invalid cycle values, current-month Setup/month synchronization issues, duplicate monthly bill placement, and current-month bills missing from Setup.
- **Pre-Production Check** — the Control Center runs the local project doctor, ODS self-test, and Plaid account-access check through the fixed GUI helper.
- **Legend** — the Control Center documents the workbook's white / blue / green / amber-red visual language.
- **Reduced body borders** — data areas use spacing and fill hierarchy instead of heavy box borders around every cell.

Run **REFRESH WORKBOOK HEALTH** after structural changes. Before switching Plaid to Production, run **PRE-PRODUCTION CHECK** and resolve any workbook-health warnings first.

### Perpetual yearly history

The Control Center includes **CREATE NEXT YEAR**. It turns the workbook into a year-qualified, perpetual history instead of reusing the same January–December tabs forever.

On the first run, the existing legacy month tabs are preserved and renamed with their detected year (for example, `September` becomes `September 2026`). The function then creates `January 2027` through `December 2027`. Future runs create the next year in sequence.

Before any new-year changes, the workbook is saved and a timestamped `.pre-YYYY.bak.ods` backup is created. The new year's sheets are cloned from the corresponding prior-year months so formulas, layout, macros, and formatting remain consistent. Literal numeric/payment data is cleared, Paid fields start blank, and active bills are rebuilt from Setup for their configured 1st / 15th / Both cycle.

Prior-year tabs are never overwritten by **CREATE NEXT YEAR**. If creation fails partway through, partial new-year tabs are removed and the original legacy naming migration is rolled back.

Plaid sync resolves the target sheet by both month and year. It prefers a sheet such as `September 2027` and remains backward-compatible with a legacy `September` sheet.

Once future-year tabs exist, Setup Add/Edit/Remove propagation also includes those future-year tabs. Historical months before the current month/year remain protected from those forward-only bill-list changes.

No Plaid client secret, Plaid access token, bank password, or arbitrary shell command is stored in workbook cells or macros.

LibreOffice can show a macro-security warning for document macros. You can enable the macros for this workbook when prompted. If you want one-click use without repeated prompts, LibreOffice also supports trusted file locations under **Tools → Options → LibreOffice → Security → Macro Security → Trusted Sources**. Only trust the project workbook folder, not a broad Downloads or home directory.

Official LibreOffice references:

- https://help.libreoffice.org/latest/en-US/text/shared/01/securitywarning.html
- https://help.libreoffice.org/latest/en-US/text/shared/optionen/macrosecurity_ts.html

### Local self-test

The same ODS safety test remains available from the CLI:

```bash
biweekly-bills self-test
```

It validates the package structure, five known bill matches, a temporary ODS write, backup integrity, sheet/formula preservation, and confirms that the original workbook SHA-256 did not change.

## Sync the workbook

Preview the appropriate cycle for today:

```bash
biweekly-bills sync
```

Or explicitly:

```bash
biweekly-bills sync --date 2026-09-15 --cycle 15th
```

Apply after reviewing:

```bash
biweekly-bills sync --date 2026-09-15 --cycle 15th --apply
```

The sync:

- incrementally pulls Transactions using Plaid's sync cursor;
- maintains a local ignored transaction cache;
- reads the Bills Checking Balance;
- matches only known posted bill transactions;
- proposes updates to the correct 1st/15th `Paid` cells;
- fills the posted-balance reconciliation field;
- preserves your planned bill amounts and transfer formulas;
- backs up the workbook as a timestamped `.bak.ods` file before writing.

## Reauthentication

If Plaid later reports `ITEM_LOGIN_REQUIRED`, do **not** run initial Link again.

Use:

```bash
biweekly-bills update-link
```

Plaid Update Mode reuses the existing Item and access token; there is no second public-token exchange.

To test the complete recovery path in Sandbox:

```bash
biweekly-bills sandbox-reauth-test
```

That command forces the Sandbox Item into `ITEM_LOGIN_REQUIRED`, launches Update Mode, verifies that the persisted access token did not change, and confirms Balance access works again.

Official reference: https://plaid.com/docs/link/update-mode/

## If the Plaid plan is ever upgraded

Review Plaid pricing **before** continuing automated use. On a paid plan, pricing rules differ from Trial. The project is intentionally optimized for the free Trial plan today.
