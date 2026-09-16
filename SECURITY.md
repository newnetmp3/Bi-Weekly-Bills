# Security and Privacy

Bi-Weekly Bills handles sensitive financial data locally. The source repository must remain free of personal financial records and credentials.

## Never commit

Do not commit any of the following:

- Plaid client secrets, access tokens, public tokens, or other credentials.
- `.env` files or other local credential/configuration files.
- SQLite databases, database sidecars, or application backups.
- Personal ODS workbooks or other bill/account source documents.
- Exported reports that contain balances, transactions, account names, masks, or merchant history.
- Real account numbers, account masks/last-four digits, personally named accounts, transaction IDs, Plaid account/item IDs, or merchant records copied from a real installation.
- Local absolute paths that identify a workstation user.
- Private keys, API keys, session tokens, or authentication cookies.

Use synthetic fixture data in tests and documentation. Account masks in examples should be obviously fake (for example, `1111` and `2222`).

## Local storage

Runtime financial data is designed to live outside the repository in the user's local application data/configuration directories. The repository `.gitignore` also excludes common local database, backup, workbook, and report paths as a second line of defense.

## Before publishing a change

Review the staged diff for personal data and credentials. If sensitive data was ever committed, deleting it in a later commit is not enough: rewrite Git history before publishing the repository and rotate any exposed credential.

## Reporting a security issue

If you discover a credential or sensitive financial record in repository history, keep the repository private until the data is removed from all reachable refs/history and any affected credential has been rotated.
