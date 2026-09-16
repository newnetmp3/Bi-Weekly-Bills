# Bi-Weekly Bills Wiki

## Why Bi-Weekly Bills exists

**Bi-Weekly Bills is designed around a two-checking-account budgeting system.** Understanding that system explains the entire application.

- **Primary Checking / Transfer Source** is the normal checking account where income is received and everyday money is kept.
- **Bills Checking** is a **separate checking account used only to hold money for scheduled automatic bill payments**.

Money for upcoming bills is deliberately separated from everyday spending money. For each 1st/15th cycle, Bi-Weekly Bills calculates how much should be moved from Primary Checking into Bills Checking. You make that transfer between your own accounts, and the money remains in Bills Checking until the scheduled automatic payments post.

The application then helps answer four questions:

1. **How much should I transfer into Bills Checking?**
2. **Which bills is that money reserved for?**
3. **Did the automatic payments actually leave Bills Checking as expected?**
4. **Is enough money still reserved for the rest of the cycle?**

**Bills Checking is intentionally a second checking account, not a label for your ordinary checking account.** Think of it as a dedicated holding account for money that has already been set aside and should no longer be considered available for normal spending.

Bi-Weekly Bills does **not** initiate transfers or bill payments. It calculates the funding requirement, tracks the two-account workflow, and verifies the bank activity after transactions post.

**Bi-Weekly Bills 1.0** is a PySide6 + SQLite desktop application that can synchronize balances and transactions through Plaid, track recurring bills, reconcile outgoing payments, plan Bills Checking funding, and export monthly reports.

**What is Plaid?** Plaid is a third-party financial-data service that securely connects supported bank accounts to applications like Bi-Weekly Bills. It supplies account, balance, and transaction data to the app; your bank sign-in happens through Plaid rather than inside Bi-Weekly Bills.

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
