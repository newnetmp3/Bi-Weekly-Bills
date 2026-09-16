# First-run QA — v0.21.0

This review treats the desktop application as a new user's first exposure to
Bi-Weekly Bills. Each pass follows the app from the UI rather than assuming
knowledge of the CLI, Plaid internals, or the previous LibreOffice workflow.

## Pass 1 — Launch and first destination

**Finding:** An unconfigured install opened an empty Overview, which made the
next step unclear.

**Change:** Startup now evaluates the bank-connection state. Unconfigured,
recoverable, or blocked setups open directly to **Settings → Bank connection**.
Established healthy installations still open on Overview.

## Pass 2 — Navigation and information architecture

**Finding:** Accounts and Settings split one setup workflow across two pages.
Accounts duplicated connection/sync state that belonged with configuration.

**Change:** The standalone **Accounts** navigation page was removed. Connected
accounts, balances, Bills Checking, and Default Transfer Source management now
live in Settings. Bill-specific Payment Account assignment remains on Bills.

## Pass 3 — Plaid API setup

**Finding:** A first-time user had to edit a project-root .env file before the
desktop app could help.

**Change:** Settings now accepts Plaid Client ID, Secret, and optional Redirect
URI. Values are stored per-user in ~/.config/bi-weekly-bills/plaid.env with
directory mode 0700 and file mode 0600. Environment variables remain the
highest-priority administrator override. A button opens the Plaid dashboard.

## Pass 4 — First bank connection

**Finding:** Creating the first link depended on understanding development
readiness terminology and a prior Sandbox QA marker.

**Change:** The isolated Sandbox validation marker is now advisory for an end
user. The actual one-Item safety controls remain mandatory: credential state,
pending recovery, persistent Item lock, session reservation, permissions, and
Item-ID consistency. Settings exposes one state-aware primary action:
**Save & continue**, **Connect bank**, **Recover connection**, or **Sync now**.

## Pass 5 — Browser/link failure and cancellation

**Finding:** Closing Plaid Link could leave the desktop background worker
waiting indefinitely for Link completion.

**Change:** Link now signals the local desktop workflow when the browser flow is
closed. A successful first link is verified as persisted before it is treated
as connected. Pending exchanges route to recovery instead of relinking.

## Pass 6 — Existing connection repair/recovery

**Finding:** Repair, recovery, initial connection, and normal sync were separate
concepts a new user had to discover.

**Change:** Settings derives the safe action from persisted state. Existing
Items never route to Initial Link. Healthy Items offer **Sync now** and
**Repair bank connection**. Pending exchanges offer **Recover connection**.
Inconsistent Item state is shown as blocked rather than being treated as healthy.

## Pass 7 — Account roles and funding assumptions

**Finding:** The product logic assumed a transfer-source account named
“Primary Checking” with a specific mask. That was valid for the original installation
but not for a new user.

**Change:** Settings now exposes two explicit checking-account roles:
**Bills Checking** and **Default Transfer Source**. The old installation-specific
lookup is retained only as a migration fallback for existing installs. Role
changes update active bill funding rules while historical bill instances stay
unchanged.

## Pass 8 — First account-role selection

**Finding:** After a successful first sync, users were left with an account
table and no obvious next action.

**Change:** When exactly one checking account is available, the app selects it
and prompts the user to confirm Bills Checking. After Bills Checking is chosen,
if exactly one other checking account remains, that row is selected and the
user is prompted to confirm Default Transfer Source. Roles are never assigned
silently.

## Pass 9 — Creating the first bill

**Finding:** Saving a master bill did not guarantee that it appeared in the
current Pay Periods checklist.

**Change:** Active recurring bills are materialized into missing instances for
the current or future month. Existing instances are never overwritten, and
historical months are never synthesized. A Both bill creates a 1st and 15th
instance. A completely empty Bills page opens directly in new-bill editing
state.

## Pass 10 — Redundancy, layout, and daily workflow

**Finding:** Settings was backup-centric, the embedded account area repeated
sync controls, and several account/setup concepts were exposed in multiple
places.

**Change:** Settings is now ordered by user intent: Bank connection first,
Accounts & bill funding second, Data safety & backups last. The embedded
account section removes duplicate sync/refresh controls because connection
sync is already available from the Settings primary action. Bills owns
bill-specific Payment Account and merchant aliases; Pay Periods owns
period-specific editable values; Transactions owns transaction review;
Reconciliation remains the historical audit/diagnostics workspace.

## Safety invariants retained

The redesign does not initiate transfers or payments. SQLite remains the
financial source of truth. Plaid access tokens remain outside SQLite. Existing
bank Items are reused for sync/repair, pending Items are recovered, and the app
does not deliberately create a replacement Item when persistent evidence says
one already exists.
