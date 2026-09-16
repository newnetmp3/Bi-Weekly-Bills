# Desktop application foundation

The LibreOffice workbook remains a supported import/export artifact, but the desktop
application is the long-term primary UI and SQLite is the authoritative data store.

## Design direction

The PySide6 shell takes visual cues from the referenced Salesforce invoice-management
dashboard: dark navy surfaces, compact rounded cards, high-contrast summary metrics,
lime status accents, restrained borders, and dense finance-oriented tables.

It is intentionally adapted to the 1st/15th household bill workflow rather than copied
screen-for-screen.

## Safety boundaries

- The desktop foundation does not create, replace, or relink a Production Plaid Item.
- Existing `plaid_sdk.py`, `secure_store.py`, `production_guard.py`, Link Update Mode,
  and recovery logic remain authoritative and will be reused.
- Plaid credentials are never stored in SQLite.
- ODS migration is read-only and verifies that the source workbook bytes are identical
  before and after import.
- Bills-transfer behavior is derived from the selected Payment Account. Payment method is not used
  as a proxy for whether Bills Checking funding is required.

## Local database

By default:

```
~/.local/share/bi-weekly-bills/biweekly-bills.sqlite3
```

The location honors `XDG_DATA_HOME`. Local SQLite files and sidecars are ignored by Git.

The initial schema contains:

- `bills`
- `bill_aliases`
- `pay_periods`
- `bill_instances`
- `bank_accounts`
- `bank_transactions`
- `sync_state`
- `transaction_reconciliations`
- `funding_transfer_validations`
- `internal_transfer_matches`
- `workbook_imports`
- `metadata`

Money is stored as integer cents.

## Install / launch on Arch Linux

From the existing virtual environment:

```bash
cd ~/git/bi-weekly-bills
git pull
source .venv/bin/activate
pip install -e .
biweekly-bills-app
```

## First-run desktop setup

A new or incomplete installation opens **Settings** automatically instead of
showing an empty Overview.

1. Open **Settings → Bank connection**.
2. Enter the Plaid Client ID and Secret once. The app stores them outside
   SQLite in the user's private configuration directory.
3. Click **Save & continue**. When local safeguards permit the first bank
   connection, the same primary button opens Plaid Link in the browser.
4. After Link succeeds, the app synchronizes accounts and transactions.
5. In **Accounts & bill funding**, confirm **Bills Checking** and **Default
   Transfer Source**. When each role has only one plausible checking account,
   the app selects that row but still requires explicit confirmation.
6. Add recurring bills on **Bills**. New active bills appear in the current
   Pay Periods checklist without rewriting historical months.

An existing bank Item never routes back to Initial Link. The same Settings
section offers Sync, Update Mode repair, or pending-credential recovery based on
the persisted state.

See [FIRST_RUN_QA.md](FIRST_RUN_QA.md) for the ten-pass usability review that
produced this workflow.

## Read-only workbook migration

Import the current ODS history and then open the desktop application:

```bash
biweekly-bills-app --import-ods workbook/YOUR_WORKBOOK.ods --legacy-year 2026
```

Import without opening the GUI:

```bash
biweekly-bills-app --import-ods workbook/YOUR_WORKBOOK.ods --legacy-year 2026 --import-only
```

Year-qualified tabs such as `September 2026` use their explicit year. Legacy month-only
tabs use `--legacy-year`.

## Current UI

The first milestone includes:

- invoice-dashboard-inspired dark shell
- left navigation
- monthly overview
- compact Overview summary that gives recovered vertical space to Bill activity, with native Qt donut charts for scheduled 1st/15th distribution and paid-vs-still-due progress
- 1st and 15th pay-period summary cards
- bill activity table
- master Bills page
- non-modal, in-page bill editor
- a single Payment Account control backed by stable Production Plaid account IDs; Bills-transfer fields are derived automatically
- inactive-bill behavior that preserves historical instances
- full-width Pay Periods checklist with 1st, 15th, and whole-month work-list selectors
- persistent manual Paid checkpoints that save immediately without changing financial Paid amounts or bank reconciliation
- workflow progress, first-unfinished resume cue, optional unfinished-only filtering, and separate bank-verified status
- clickable Overview pay-period cards that open the corresponding checklist
- compact Bills Checking funding strip with expandable details
- clickable column-header sorting on the major data tables, with numeric/date-aware ordering
- page-aware right-click context menus on the major tables, exposing only actions that make sense for the current page and selected row
- reusable busy mouse cursor for background workers such as Plaid synchronization
- Pay Periods selected-row inline editing for When, Due, Paid amount, Method, Status, and Extra/Short; Bill, Cycle, Payment Account, and Bank Status remain read-only in the checklist
- prior-month manual Paid checkpoints without a bank match display `Paid · unverified` instead of indefinitely saying `Waiting for bank`; the current month still uses `Waiting for bank` while a posting may still arrive
- Settings-owned connected-account table with balances, credit verification, explicit Bills Checking, and explicit Default Transfer Source roles; the redundant standalone Accounts navigation page has been removed
- environment-aware Transactions page backed by SQLite
- current-month/Bills-Checking transaction defaults with account/state/date filters
- automatic high-confidence transaction-to-bill reconciliation after real bank sync, using transaction names, expected payment amounts, and the configured Payment Account as authoritative account evidence
- historical reconciliation prefers an existing recorded Paid amount over Due (except explicitly Partial bills), so variable bills can be backfilled correctly from posted bank transactions
- account-level verification in Settings for every Plaid account whose type is `credit` when a posted transaction name or merchant name is exactly `NFO PAYMENT RECEIVED`; masks and credit subtypes are not required, and this rule does not verify non-credit accounts
- reversible transaction-to-bill reconciliation with Accept, Ignore, Reassign, and Undo for unresolved or ambiguous cases
- reconciliation from any linked payment account, including everyday checking
- Bills Checking funding planner with separate 1st/15th transfer targets, current-balance allocation, and Transfer Source breakdown
- Production-only bill account assignment: Sandbox accounts never populate the Payment Account dropdown
- aggregate Bills funding-transfer validation for one incoming transfer covering the 1st, 15th, or whole month
- guarded Production Balance/Transactions sync using only the existing locked persistent Item
- Settings account management and Transactions follow the configured bank environment internally while release-facing UI avoids development environment terminology
- Production sync verifies Plaid's returned Item ID matches the persistent locked Item before writing SQLite data
- Production startup purges Sandbox transaction/reconciliation/sync-cursor state after creating a verified safety backup
- Production reports never fall back to Sandbox bank data, even before the first Production account sync
- automatic verified SQLite backups before consequential edits, imports, and schema migrations
- Settings first-run hub with secure per-user Plaid API configuration, state-aware Connect/Sync/Repair/Recover actions, embedded account-role management, and protected backup/restore history
- functional Reports page with non-modal Excel, PDF, ODS, and Export All actions
- report outputs include Summary, Bills, itemized Funding, Transactions, and Accounts sections
- Sandbox report data is explicitly labeled; Production data is preferred automatically once connected
- maintenance CLI retains isolated bank-validation and connection-readiness diagnostics without exposing development environment terminology in the release UI
- Production credentials, pending recovery state, and the persistent Item lock must agree on one Item ID
- Production Initial Link is protected by an exclusive local session reservation to block concurrent Link attempts
- Existing Production Items route to Update Mode only; pending exchanges route to Recovery only
- Bills Checking treated as a funding destination, not the only valid payment source
- duplicate-match protection and discrepancy preview before Paid/Status changes
- dedicated Reconciliation page with historical Verified / Needs review / Paid · unverified / Unpaid audit states
- non-modal background `Reconcile history` pass that re-scans completed months, including historical instances whose master bill is inactive today
- reconciliation audit shows closest bank evidence and the reason a candidate was accepted, rejected, ambiguous, already matched, or unavailable
- integrity diagnostics for missing bank evidence, missing Payment Accounts, incomplete transfer routing, paid/status drift, duplicate aliases, orphaned instances, and unpaired credit receipts
- editable per-bill merchant aliases used by automatic matching, historical matching, and review suggestions
- explicit Transactions `Needs review` filter for plausible but unsafe automatic matches
- Pay Periods verification provenance showing transaction description/date/account and paired credit-account evidence when applicable
- Dashboard month closeout summary with verified, paid-unverified, unfinished, and remaining totals
- inactive master bills are excluded from both live funding plans and scheduled aggregate transfer requirements

## Current reconciliation workflow

1. Use **Settings → Bank connection → Sync now** to refresh balances and stored transactions through the existing bank connection.
2. Normal sync automatically reconciles unique high-confidence payments against active bills.
3. Use **Reconciliation → Reconcile history** to backfill completed months; historical instances remain eligible even when the master bill is inactive today.
4. Review **Needs review** items in either Reconciliation or the Transactions filter. The app shows the closest evidence and why automatic matching was withheld.
5. Use the integrity diagnostics section to resolve account-routing, alias, orphaned-history, and credit-payment evidence issues before month closeout.

## Bills Checking funding rules

The Bills editor exposes only **Payment Account**. Transfer behavior is derived automatically from that selection.

- Payment Account is the real Production depository account from which the bill is actually paid.
- Bills Checking is the designated destination account for the Bills funding workflow.
- If Payment Account is the designated Bills Checking account, the app automatically stores `transfer_required=true` and uses the **Default Transfer Source** checking account selected in Settings.
- If Payment Account is any other account, the app automatically stores `transfer_required=false` and clears Transfer Source.
- Transfer Requirement is derived rather than manually edited per bill. The reusable Default Transfer Source is selected once in Settings.
- Bills Checking cannot be its own Transfer Source.
- The live planner uses `max(due - paid, 0)` for each automatically Transfer Required bill.
- For the current month, the synced Bills Checking available balance is applied to the 1st requirement first and then to the 15th.
- Historical or future months do not apply today's live Bills balance.
- If Bills Checking is selected but no valid Default Transfer Source exists, saving is blocked instead of guessing.
- Real assignments store stable Production Plaid account IDs internally. Names and masks are display labels only.


## Merchant catalog and account-aware matching

The SQLite schema includes a `merchant_profiles` catalog keyed by environment
and normalized merchant name. Merchant profiles accumulate Plaid-provided
merchant entity IDs, websites, HTTPS logo URLs, first/last seen dates, and an
optional cached logo image.

The Transactions page:

- renders cached merchant logos beside merchant names;
- downloads missing Plaid-provided logos asynchronously with size and HTTPS
  restrictions, then stores them in SQLite;
- labels connected credit/loan activity as connected-account activity instead
  of pretending that generic payment text is a merchant;
- leaves generic descriptions as descriptions/account transfers rather than
  creating merchant records for values such as `NFO PAYMENT RECEIVED`,
  `ACH TRANSFER`, and `ONLINE PAYMENT`.

Matching treats these identities separately. A transaction with a meaningful
merchant identity is not allowed to use amount + configured checking-account
evidence to masquerade as a connected credit/loan payment. Bills that map to a
connected credit/loan account (by account name or prior internal-transfer
evidence) are excluded from named-merchant suggestions and merchant review
candidates. Generic ACH/payment movement can still use the conservative
Payment Account evidence path.

## UI responsiveness and lazy loading

The main window treats page data as a lazy cache:

- only the startup page loads data during launch;
- revisiting a clean page reuses its current view;
- writes mark dependent hidden pages dirty rather than synchronously rebuilding
  them;
- dirty pages refresh when the user navigates to them.

Transactions load the selected month directly from SQLite and perform search,
account/state, and reconciliation filters locally. Candidate analysis is bulk
cached per month. Historical Reconciliation audit and diagnostics run in a
worker thread, and diagnostics reuse the same audit result instead of scanning
history twice.

Settings backup history avoids repeated integrity checks of every retained
snapshot. Creation and restore safety checks remain mandatory. Reports use
selected-month bank queries, and Pay Period recurring-bill materialization is
batched into one SQLite transaction.

See [UI_RESPONSIVENESS_QA.md](UI_RESPONSIVENESS_QA.md) for the validated
interaction paths and retained safety invariants.

## Outgoing Payment Account verification

A bill that is already recorded as Paid can be bank-verified directly from the
outgoing side of the payment. This is generic behavior and is not tied to any
specific bill name such as Star Card.

- The transaction must be posted and must be an outflow.
- If the bill has a configured Payment Account, that exact Plaid account ID is
  authoritative. A transaction from a different account cannot verify the bill.
- Bills paid from Bills Checking use Bills Checking in exactly the same way:
  Bills Checking is simply that bill's configured Payment Account.
- For an already-Paid bill, a matching amount from the configured Payment
  Account is strong verification evidence even when the bank description is
  generic (for example, an ACH/payment/transfer description).
- Existing amount tolerance remains conservative.
- If more than one Paid bill on the same Payment Account could claim the same
  outgoing amount, the app leaves the transaction for review rather than
  guessing.
- Credit-card/loan receipt-side evidence can still form a paired internal
  transfer when available, but it is no longer required merely to verify an
  already-Paid bill from its uniquely matching outgoing Payment Account.

## Automatic bill-payment reconciliation

Real bank synchronization automatically reconciles posted bill payments when the match is high-confidence.

- Automation runs only after a guarded **Production** bank sync. Sandbox synchronization never changes real bill-instance Paid values.
- Only posted positive Plaid outflows are considered. Pending transactions, incoming transfers, ignored transactions, and already-reconciled transactions are skipped.
- Candidates come from active bill instances in the transaction's calendar month. Inactive bills remain preserved historically but are never auto-matched during normal sync.
- When a bill has a configured Payment Account, automatic verification requires the transaction to come from that exact account; this prevents an equal amount from another linked checking account from being accepted.
- Matching combines normalized merchant/transaction names with the bill name, the existing conservative known-merchant aliases, and common acronym forms such as `NFCU` versus `Navy Federal Credit Union`.
- Credit-card, loan, and line-of-credit payments can also be verified as a two-sided internal transfer. The incoming transaction must land in a credit/loan account whose account name identifies the bill, while an equal outgoing transaction must leave the exact **Payment Account** configured for that bill.
- Internal-transfer sides must have the same amount and post within three days. The destination account, configured source account, expected Due amount, and transaction timing all participate in the confidence check.
- The bank amount must agree with the expected Due amount. Automatic matching permits only small drift: at least $1 tolerance, up to 2% of the expected amount, capped at $5.
- The strongest bill candidate must clear the confidence threshold and beat the runner-up by a safety margin. Two similarly plausible bills are left unresolved.
- If multiple bank transactions independently claim the same bill instance, none are auto-selected; those transactions are left for review.
- A posting date just before or after the 1st/15th boundary does not block an otherwise unique name-and-amount match; automatic matching searches the active bills for the whole transaction month.
- Successful matches use the normal reconciliation path: the bank amount becomes Paid, Status becomes `Paid`, source becomes `bank-reconciled`, and normal Undo behavior remains available.
- For an internal-transfer match, the outgoing Payment Account transaction is the bill payment and the incoming credit/loan transaction is stored as verification evidence. Transactions shows both sides as one paired internal transfer instead of leaving the incoming side unresolved.
- Undo can be initiated from either side of the paired transfer. If Plaid later removes either side, the pairing is removed and the bill is restored rather than retaining stale verification.
- After sync, Settings reports the account state while Transactions and Reconciliation expose auto-matched, paired, and review-needed payment evidence.

## Aggregate Bills funding-transfer validation

The Transactions page supports the user's normal workflow of adding up Transfer Required/autopay bills and making one large incoming transfer into Bills Checking.

- Only a posted **incoming** transaction to the designated Bills Checking account is eligible.
- The user can validate it against the **1st pay period**, **15th pay period**, or **whole month**.
- The app suggests the scope whose scheduled transfer target is closest to the bank transaction amount; it does not assume the transaction date determines the pay period.
- The scheduled target is the sum of the configured Due amounts for Transfer Required bills in that scope.
- This validation target intentionally ignores current Bills balance and later Paid values. It represents what was scheduled to be funded when the transfer was made.
- The result stores Expected, Actual, and Difference as a historical snapshot. Exact, short, and over transfers are all visible.
- Validating the funding transfer does **not** mark any individual bill Paid and does not replace normal bill-payment reconciliation.
- Funding validation can be undone independently and is deleted automatically if Plaid later removes the underlying transaction.
- Reports identify these transactions as validated Bills funding transfers rather than unresolved bank activity.

## Backup and restore

SQLite is backed up with SQLite's online backup API so WAL-mode databases are snapshotted consistently. Each backup is verified with `PRAGMA integrity_check` before it is accepted.

Automatic snapshots are created before bill saves/deactivation, pay-period edits, reconciliation accept/ignore/undo, Bills Checking role changes, ODS imports, and schema migrations. The backup directory defaults to `<database directory>/backups` and retains the newest 30 snapshots.

Settings provides manual backup and restore. Restore verifies the selected snapshot, creates a `pre-restore-safety` snapshot of the current database, restores the selection, verifies the restored database, and refreshes the application. Plaid credentials are not stored in SQLite and are therefore never included in these backups.


## Reports and exports

The Reports page exports the selected month without modifying SQLite. Exports run on a background worker and use the application busy cursor.

The default report directory is:

```
<database directory>/reports/
```

Available formats:

- Excel (`.xlsx`) with Summary, Bills, Funding, Transactions, and Accounts sheets.
- OpenDocument Spreadsheet (`.ods`) with the same logical sections.
- PDF with monthly 1st/15th summary, bill detail, itemized Bills Checking funding detail, account summary, and reconciliation/transaction detail.
- Export All produces all three formats from the same report snapshot.

When only Sandbox bank data exists, reports clearly label it as `SANDBOX PREVIEW`. Production bank/account data is preferred automatically once Production NFCU is connected. Funding details remain deferred until Production account assignment is active.


## Sandbox end-to-end validation

Settings includes **Run full Sandbox validation**. The validation workflow is deliberately isolated from the working SQLite source of truth:

1. Fingerprint the working application database.
2. Clone SQLite to a temporary validation database with SQLite's online backup API.
3. Remove any Production bank cache rows from the temporary clone.
4. Verify the persisted Sandbox connection and run a live Sandbox Balance/Transactions sync into the clone.
5. Do not persist the validation sync cursor back to normal local configuration.
6. Verify Sandbox account/transaction cache and Bills Checking role.
7. Exercise Accept Match and Undo against a synthetic transaction in the clone.
8. Verify Sandbox data cannot activate real funding assignments.
9. Generate and reopen Excel, PDF, and ODS reports.
10. Exercise the funding calculator with a synthetic Production-like account only inside the clone.
11. Create, mutate, and restore a verified SQLite backup in the clone.
12. Re-fingerprint the working database and fail validation if anything changed.

Production Plaid APIs and Production credentials are never requested by this validator. Temporary databases, reports, and backups are deleted automatically when validation completes.


## Production readiness and one-Item guardrails

Production linking is governed by one authoritative readiness model used by Settings, the CLI, and the local Link server.

The readiness model inspects:

- the recorded full Sandbox validation pass;
- current PLAID_ENV / configured key state;
- the persistent Production credential;
- any pending Production token exchange awaiting recovery;
- the persistent Production Item lock;
- any active Production Initial-Link session reservation;
- Item-ID consistency across credentials, pending recovery, and lock state;
- Sandbox/Production Item separation;
- secure-store file permissions;
- Production SQLite cache evidence.

The model emits one next-safe-action state:

- `READY_TO_ARM`: local state is clean while PLAID_ENV remains sandbox.
- `READY_FOR_FIRST_LINK`: Production is deliberately armed and the single allowed Initial Link may begin.
- `UPDATE_MODE_ONLY`: a persistent Production Item already exists; Initial Link is permanently disabled.
- `RECOVER`: Plaid token exchange completed but final local persistence needs recovery; do not create another Item.
- `BLOCKED`: inconsistent or ambiguous state requires resolution before any Production action.

### Initial-Link reservation

Before the one allowed Production Initial Link opens, the CLI creates `PRODUCTION_LINK_IN_PROGRESS.lock` atomically. A second Initial Link cannot start while that reservation exists. The Link server also requires the reservation and re-checks readiness before creating the Link token and again before token exchange.

On successful Production credential persistence, the reservation is released. If the process crashes, the reservation intentionally remains conservative. It can only be cleared with:

```bash
biweekly-bills clear-production-link-reservation --confirm-no-item-created
```

and that command refuses to clear the reservation if any Production credential, pending exchange, or persistent Item lock exists.

### Replacement prevention

The Production secure store refuses to overwrite a credential with a different Item ID. The pending-recovery store likewise refuses conflicting Item IDs, and both are checked against the persistent Production Item lock. The lock itself cannot be rewritten for a different Item ID.

Use:

```bash
biweekly-bills production-readiness
```

to print the same checklist and next-safe-action state shown in Settings.


## Production cutover cleanup

When the desktop app starts with `PLAID_ENV=production`, it performs a one-way cleanup of test transaction state before real NFCU data is used:

- If Sandbox transactions exist, the app first creates a verified `pre-production-sandbox-transaction-purge` SQLite backup.
- Every Sandbox bank transaction is deleted through the normal reconciliation-aware delete path.
- Sandbox reconciliation rows are removed and any bill Paid/Status/source values created by Sandbox matches are restored to their pre-match values.
- Sandbox `sync_state` and the legacy Sandbox transaction cursor are cleared.
- Production accounts, transactions, reconciliation state, and cursors are never touched by this cleanup.
- Sandbox account rows may remain as inert audit/reference records, but Production UI pages do not read them.
- While `PLAID_ENV=production`, reports use Production bank data only. If Production has not yet been synchronized, the report says `No bank data` instead of falling back to Sandbox.

This cleanup is idempotent. Once no Sandbox transactions remain, future Production startups do not create another purge backup.

## Production account and transaction sync

After the one persistent Production Item exists and Production readiness is `UPDATE_MODE_ONLY`, the desktop Accounts page can synchronize real bank data with **Sync Production**.

Production synchronization:

1. Requires `PLAID_ENV=production`.
2. Requires Production readiness to be `UPDATE_MODE_ONLY`.
3. Uses the already-persisted Production access token; it never opens Initial Link.
4. Calls Plaid `/item/get` first and verifies the returned Item ID exactly matches the persistent locked Item ID.
5. If the Item reports an error, sync stops and instructs the user to use `biweekly-bills update-link` on the same Item.
6. Only after Item identity and health pass does the app write Balance accounts to SQLite.
7. Transactions sync uses the Production-specific cursor and Production-specific SQLite rows.
8. Sandbox and Production local cursors/caches remain separate.
9. Production account sync does not assign any bill to an account automatically.

After synchronization, the user explicitly selects the real Bills Checking account from the Accounts table. That selection is backed up before change and saved only for the Production environment.

The Transactions page then displays/reconciles the configured environment's transactions. High-confidence Production payments are reconciled automatically after sync; Accept/Ignore/Reassign/Undo remain available for unresolved, ambiguous, or corrected cases. Payments from non-Bills checking accounts remain valid reconciliation sources.


## Derived bill transfer settings

Transfer Source and Transfer Requirement controls were removed from the Bills editor to match the actual household workflow.

The application now enforces one deterministic rule when a bill is saved:

- **Payment Account = Bills Checking** → **Transfer Required = Yes**, **Transfer Source = Primary Checking**.
- **Payment Account != Bills Checking** → **Transfer Required = No**, **Transfer Source = none**.

The derived fields remain stored in SQLite because the funding planner, reports, and aggregate funding-transfer validation use them, but users no longer maintain them manually.


## Release UI terminology

The desktop application is environment-neutral for normal users. Development and deployment environment names are not shown in the main UI.

- The main shell no longer displays environment badges or connection-stage labels.
- Accounts uses **Bank connection**, **Sync Accounts**, and generic sync status text.
- Transactions is simply **Bank transactions**.
- Pay Periods and Reports use ordinary bank/funding language.
- Settings contains backups and restore controls only; environment validation/readiness diagnostics remain maintenance/CLI capabilities rather than release-facing UI.
- Exported reports describe bank-data availability without development-environment banners.

The internal environment separation, persistent-Item safety rules, account-ID isolation, and bank-sync guards remain unchanged.


## Linux / Wayland desktop integration

The application ships its approved Bi-Weekly Bills branding as scalable SVG package assets:

- `assets/icons/scalable/apps/biweekly-bills.svg` — square application/launcher icon.
- `assets/branding/biweekly-bills-logo.svg` — wide in-app brand logo.
- `assets/desktop/biweekly-bills.desktop.in` — launcher template.

On a normal `biweekly-bills-app` launch, desktop integration is refreshed idempotently in the current user's XDG data directory. By default this produces:

```text
~/.local/share/applications/biweekly-bills.desktop
~/.local/share/icons/hicolor/scalable/apps/biweekly-bills.svg
~/.local/share/biweekly-bills/branding/biweekly-bills-logo.svg
```

The generated launcher uses the exact installed `biweekly-bills-app` executable path, so virtual-environment installs launch correctly from GNOME/KDE without relying on a login-shell PATH.

For Wayland, Qt sets its desktop file name/app ID to `biweekly-bills`, matching the `biweekly-bills.desktop` filename and `Icon=biweekly-bills`. The same packaged icon is also assigned as the QApplication/MainWindow icon. This gives Wayland compositors a stable launcher/task-switcher identity and prevents the generic Qt/Python icon.

The wide logo is rendered from the packaged SVG in the application sidebar. SVG rendering keeps both launcher and in-app branding crisp on HiDPI displays.

Desktop integration can also be refreshed explicitly:

```bash
biweekly-bills install-desktop
```

This command is safe to run repeatedly. It only rewrites files whose contents changed and refreshes the desktop application database when that helper is available.
