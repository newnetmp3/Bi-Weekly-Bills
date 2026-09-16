# Version 1.0 Release Notes

Bi-Weekly Bills 1.0 is the first stable PySide6 + SQLite desktop release.

## Highlights

- Desktop-first 1st/15th bill workflow.
- SQLite as the source of truth.
- Safe Plaid connection, synchronization, repair/update mode, and recovery handling.
- Overview dashboard with scheduled/paid summaries.
- Recurring Bills editor with merchant aliases and Payment Account assignments.
- Pay Period checklists with manual checkpoints and bank-verification provenance.
- Transactions browser with merchant logos and Plaid merchant metadata.
- Historical reconciliation audit and diagnostics.
- Bills Checking funding planner with explicit Bills Checking and Transfer Source roles.
- Excel, PDF, and ODS reports.
- Verified SQLite backup/restore.
- Wayland-compatible launcher, icon, and branding.
- Editable/reorderable persistent sidebar.
- Read-only ODS history migration.

## Release safety

The application reads bank data but does not initiate transfers or payments.

Automatic reconciliation requires strong evidence and leaves ambiguous cases for review.

Production repair uses the existing Plaid Item through Update Mode rather than silently creating a replacement.

## Merchant detail

Transaction Detail can display Plaid-provided merchant website, entity ID, personal-finance category/confidence, payment channel, counterparties, and location/store metadata.

## Release artifacts

The GitHub Release contains both a Python wheel and source archive for v1.0.0.

For current usage instructions, start at [[Home]].
