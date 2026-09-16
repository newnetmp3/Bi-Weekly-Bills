# Bi-Weekly Bills Wiki

**Bi-Weekly Bills 1.0** is a PySide6 + SQLite desktop application for a 1st/15th household bill workflow. It synchronizes balances and transactions through Plaid, tracks recurring bills, reconciles outgoing payments, plans Bills Checking funding, and exports monthly reports.

> **Safety boundary:** Bi-Weekly Bills reads bank data and records bill activity. It does **not** initiate bank transfers or bill payments.

## Start here

1. [[Installation]]
2. [[First-Run-and-Bank-Connection]]
3. [[Bills-and-Pay-Periods]]
4. [[Transactions-and-Reconciliation]]
5. [[Reports-and-Backups]]

For problems or maintenance, use [[Troubleshooting]] and [[CLI-and-Maintenance]].

## Core concepts

### SQLite is the source of truth
Version 1.0 uses SQLite as the primary application database. The older ODS/LibreOffice workflow is supported for read-only history migration; see [[ODS-Migration]].

### Payment Account drives funding
Each recurring bill has a **Payment Account**. When that account is Bills Checking, the bill is included in Bills Checking funding. When the payment leaves another checking account, it is not.

### Reconciliation is conservative
Automatic matching requires strong evidence. Ambiguous transactions, wrong-account evidence, and competing candidate bills are left for review rather than guessed.

### Plaid credentials stay outside SQLite
Plaid credentials are stored separately in the user's private configuration directory. Database backups therefore do not contain the bank access token.

## Main application areas

- **Overview** — monthly scheduled/paid status and 1st/15th summaries.
- **Bills** — recurring bill definitions, payment accounts, aliases, and notes.
- **Pay Periods** — 1st/15th checklists and funding requirements.
- **Transactions** — synced bank activity, merchant metadata, reconciliation, and funding-transfer validation.
- **Reconciliation** — historical audit and diagnostics.
- **Reports** — Excel, PDF, and ODS exports.
- **Settings** — bank connection, account roles, backup, and restore.

## Documentation map

- [[Installation]]
- [[First-Run-and-Bank-Connection]]
- [[Bills-and-Pay-Periods]]
- [[Transactions-and-Reconciliation]]
- [[Reports-and-Backups]]
- [[Sidebar-Customization]]
- [[ODS-Migration]]
- [[CLI-and-Maintenance]]
- [[Troubleshooting]]
- [[Security-and-Privacy]]
- [[Version-1.0-Release-Notes]]

The repository documentation under `docs/` remains the version-controlled technical source; this Wiki is the user-friendly navigation layer.
