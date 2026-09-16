# First Run and Bank Connection

An unconfigured installation opens **Settings** automatically.

## Configure Plaid

In **Settings → Bank connection**:

1. Enter the Plaid **Client ID** and **Secret**.
2. Enter a Redirect URI only if required by your Plaid configuration.
3. Choose **Save & continue**.
4. Complete your financial-institution sign-in in **Plaid Link in the browser**.
5. Allow the app to synchronize accounts, balances, and transactions.

Do **not** enter your bank username or password into Bi-Weekly Bills settings fields. Bank authentication happens in Plaid Link.

## Assign account roles

Under **Accounts & bill funding**:

1. Select the checking account used as the bill-funding destination.
2. Choose **Use selected as Bills Checking**.
3. Select the ordinary checking account that funds Bills Checking.
4. Choose **Use selected as Transfer Source**.

Saved roles are marked **YES** in the account table and remain visible after refresh.

Bills Checking cannot be its own Transfer Source.

## Routine synchronization

Use **Sync Accounts** for normal updates after the connection exists.

## Reauthentication

If the bank requires reauthentication, choose **Repair bank connection**. Repair uses Plaid **Update Mode** and preserves the existing Plaid Item.

Do not create another Production Item to work around a repair problem.

## Recovery state

If the app reports an interrupted Production credential-persistence recovery state, use the recovery path it presents. From the CLI:

```bash
biweekly-bills recover-production
```

Only use that command when the app or `doctor` explicitly reports a pending recovery.

## Credential location

Private configuration and Plaid credentials live under:

```text
~/.config/bi-weekly-bills/
```

The SQLite database does not contain the Plaid access token.

Next: [[Bills-and-Pay-Periods]].
