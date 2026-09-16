# Transactions and Reconciliation

## Transactions page

After syncing bank data, open **Transactions**.

Available filters include date/month, account, posted/pending state, reconciliation state, and search text.

The merchant column distinguishes ordinary merchants from connected-account transfers. Where Plaid supplies a supported logo, the app caches and displays it.

## Transaction Detail

Select a transaction to populate the **Transaction Detail** card at the bottom.

When Plaid supplies the information, the card can show:

- Merchant
- Bank description
- Website
- Plaid merchant/entity ID
- Personal-finance category and confidence
- Payment channel
- Counterparty
- Location/store information

This is useful for short or unfamiliar descriptors where the statement name alone does not make the company obvious.

## Reconcile an outgoing payment

For an unresolved outgoing payment, the reconciliation panel shows candidate bills and compares the bank amount with the expected amount.

Use:

- **Accept Match** — selected bill is correct.
- **Ignore** — transaction is unrelated to a tracked bill.
- **Undo Match / Ignore** — reverse a prior decision.

The configured **Payment Account** is authoritative evidence. A same-amount transaction from the wrong checking account does not verify a bill.

## Automatic matching behavior

Automatic reconciliation is deliberately conservative:

- wrong-account evidence is rejected
- competing plausible bills remain unresolved
- duplicate transaction claims do not auto-resolve
- connected credit/loan transfers are distinguished from normal merchants
- amount agreement and timing participate in confidence
- ambiguous cases stay in review

## Internal credit/loan payments

When a connected credit or loan account receives the matching payment while the outgoing side leaves the configured Payment Account, the app can treat the two sides as one paired internal transfer.

Undo can be initiated from either side of the pair.

## Validate a Bills funding transfer

A posted incoming transfer to Bills Checking can be validated against the 1st pay period, 15th pay period, or whole month.

The app compares the actual transfer with the scheduled aggregate requirement and records exact/short/over funding.

Funding validation does **not** mark individual bills paid.

## Historical Reconciliation page

Use **Reconciliation** to review historical verification states, re-scan completed months from stored bank history, inspect matching explanations, and diagnose missing Payment Accounts, duplicate aliases, orphaned history, or unpaired connected-account evidence.

Historical rescans use transactions already stored locally and do not create a new Plaid Item.
