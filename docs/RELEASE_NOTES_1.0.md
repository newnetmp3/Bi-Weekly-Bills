# Bi-Weekly Bills 1.0.0

Bi-Weekly Bills 1.0 is the first stable release of the PySide6 + SQLite desktop application.

## Highlights

- Desktop-first 1st/15th bill workflow with SQLite as the source of truth.
- Safe Plaid connection setup, synchronization, repair/update mode, and recovery handling.
- Overview dashboard with monthly scheduled/paid summaries and pay-period drill-down.
- Recurring Bills editor with merchant aliases and stable Payment Account assignments.
- Pay Period checklists with inline editing, manual workflow checkpoints, and bank-verification provenance.
- Transactions browser with merchant logos, Plaid merchant metadata, filtering, reconciliation, and funding-transfer validation.
- Historical Reconciliation audit and integrity diagnostics.
- Bills Checking funding planner with explicit Bills Checking and Transfer Source roles.
- Excel, PDF, and ODS monthly reports.
- Verified SQLite backup/restore.
- Wayland-compatible Linux launcher, icon, and in-app branding.
- Editable/reorderable sidebar with explicit edit mode and persistent preferences.
- Read-only ODS history migration for users coming from the workbook workflow.

## Release audit

Before the 1.0 version bump, the complete source tree was reviewed for:

- unresolved TODO/FIXME/debug stubs
- missing Qt signal/callback targets
- invalid private method references
- obsolete duplicate UI implementations
- import/package inconsistencies
- syntax errors
- unused imports and undefined names
- dependency conflicts
- build/install failures

Two confirmed UI orphans were removed:

1. an obsolete early `TransactionsPage` implementation that remained in `ui/bank_pages.py`
2. an unused `PlaceholderPage` class in `ui/main_window.py`

The release gate runs:

- Python `compileall`
- Ruff static checks
- `pip check`
- high-confidence Vulture dead-code reporting
- the full unit/UI test suite
- CLI entry-point smoke tests
- desktop launcher/icon smoke tests
- wheel build verification

## Bank and reconciliation safety

The app reads bank data but never initiates a transfer or bill payment.

Automatic reconciliation requires strong evidence and leaves ambiguous cases for review. The configured Payment Account is authoritative account evidence, and connected credit/loan transfers are kept distinct from ordinary merchants.

Production Plaid safety logic prevents a routine repair from silently creating a replacement Item. Existing connections use Update Mode; interrupted final credential persistence has an explicit recovery path.

## Merchant information

Transactions retain Plaid merchant/entity metadata and cache supported merchant logos. The Transaction Detail card can show the merchant website, entity ID, Plaid personal-finance category/confidence, payment channel, counterparties, and location/store information when provided by Plaid.

## Documentation

Start with [USER_GUIDE.md](USER_GUIDE.md).

The repository Wiki is enabled and provides a user-friendly documentation layer synchronized from `docs/wiki/`. The version-controlled technical source remains under `docs/`, including the full user guide and QA/implementation documentation.
