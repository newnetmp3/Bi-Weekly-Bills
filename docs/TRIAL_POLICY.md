# Plaid Trial Policy Guardrails

Source of truth: https://plaid.com/docs/account/billing/#trial-plans

## Rules that affect this project

- Trial is free and permits real Production data.
- Maximum: 10 Production Items for the lifetime of the Trial plan.
- Removing an Item does not restore a Trial Item slot.
- Every Production access token created counts against the Trial Item limit.
- Access tokens therefore must be persistently stored and tracked.
- Transactions and Balance are available on Trial.
- Transactions is subscription-priced after an upgrade to a paid plan.
- Balance is per-request-priced after an upgrade to a paid plan.
- Sandbox is always free.

## Project policy

- One Navy Federal Production Item is the goal.
- Initial Link uses only Transactions.
- Balance is queried afterward for reconciliation; Balance does not need to be selected during initial Link.
- Never create a replacement Item for ordinary credential/consent repair; use Plaid update mode.
- Never call Item removal merely to "reset" Trial usage; deleting an Item does not restore the slot.
- No automatic "force new Item" path is exposed by this project.
- The Plaid access token and Item ID are stored outside the repository in `~/.config/bi-weekly-bills/plaid_item.json` with restrictive filesystem permissions.
- Before initial Link, the secure store is preflighted for writability.
- After a successful Production Link, commit `PRODUCTION_ITEM_CREATED.lock`. It contains no credential and exists only to prevent accidental creation of another lifetime Trial Item after a reinstall/fresh clone.
- Never copy Plaid access tokens into the repository, workbook, shell history, screenshots, issue bodies, or chat messages.

## Future paid-plan warning

If this Plaid team is ever upgraded from Trial to a paid plan:

- existing Transactions-enabled Items begin incurring the applicable subscription fee;
- successful Balance calls become per-request billable;
- pricing should be reviewed again before continuing automated syncs.
