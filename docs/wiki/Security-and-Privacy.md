# Security and Privacy

Bi-Weekly Bills handles sensitive financial data locally.

## What the app does not do

The application does **not** initiate bank transfers or bill payments.

It reads/synchronizes bank data and records/reconciles bill workflow state.

## Credential storage

Plaid credentials live in the user's private configuration directory:

```text
~/.config/bi-weekly-bills/
```

They are intentionally stored outside SQLite.

## Local database

The default SQLite database is:

```text
~/.local/share/bi-weekly-bills/biweekly-bills.sqlite3
```

Database backups contain application financial history but not the Plaid access token.

## Never commit personal financial data

Do not commit:

- Plaid secrets or access tokens
- `.env` files
- SQLite databases or sidecars
- backups
- personal ODS workbooks
- exported financial reports
- real account numbers or masks
- real Plaid Item/account/transaction IDs
- private keys or API/session tokens
- workstation-specific absolute paths containing personal identifiers

Use synthetic fixture data in tests and documentation.

## Matching safety

The app favors review over risky automation:

- ambiguous matches remain unresolved
- wrong-account evidence cannot verify a bill
- connected-account transfers are kept distinct from ordinary merchants
- bank repair reuses the existing Plaid Item
- consequential database changes create verified backups

## Public repository policy

The public source repository is intentionally separate from the old private history archive. Sensitive runtime data belongs only in local application storage, never in source control.

For contributor-facing details, see the repository's `SECURITY.md`.
