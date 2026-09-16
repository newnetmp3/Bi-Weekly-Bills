# Troubleshooting

## A transaction did not auto-match

Open **Transactions → Needs review** or **Reconciliation**.

Check:

- merchant/description
- amount
- posting date
- configured Payment Account

The Payment Account is especially important. Add a merchant alias only when the alias genuinely identifies the same biller.

## An unfamiliar merchant appears

Select the transaction and read **Transaction Detail**.

Plaid metadata may include the website, category, confidence, counterparty, payment channel, and location/store details.

## A bill paid from another checking account is not verifying

Open **Bills** and confirm that the bill's **Payment Account** matches the account where the outgoing transaction actually posts.

A same-amount transaction from another account is intentionally rejected.

## Bills Checking funding is wrong

Check **Settings → Accounts & bill funding**:

1. Confirm the Bills Checking role.
2. Confirm the Transfer Source role.
3. Verify every bill meant to be funded into Bills Checking has **Payment Account = Bills Checking**.

## Bank sign-in broke

Use **Repair bank connection** in Settings or:

```bash
biweekly-bills update-link
```

Do not create a replacement Plaid connection just to fix reauthentication.

## The app reports Production recovery

Run:

```bash
biweekly-bills doctor
```

If it explicitly reports a pending Production credential recovery, use:

```bash
biweekly-bills recover-production
```

## Need to undo reconciliation

Select the transaction and use **Undo Match / Ignore**. For older periods, use the historical Reconciliation page.

## Need to restore the database

Use **Settings → Backups/Restore**. The app verifies the chosen backup and creates a safety snapshot before replacing the current database.

## Launcher/icon is stale on Wayland

Run:

```bash
biweekly-bills install-desktop
```

Then restart the application if your desktop environment still shows the previous icon.
