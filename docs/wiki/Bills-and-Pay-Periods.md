# Bills and Pay Periods

## Configure recurring bills

Open **Bills**. Select an existing bill or choose **New bill**.

The recurring-bill editor includes:

- **Bill name**
- **Cycle** — 1st, 15th, or Both
- **Latest due / typical amount**
- **Default method**
- **Merchant aliases**
- **Payment account**
- **Notes**

Choose **Save** after editing.

Use **Deactivate** when a recurring bill should stop. Deactivation preserves historical instances.

## Payment Account is authoritative

- **Payment Account = Bills Checking** → included in the Bills Checking funding requirement.
- **Payment Account = another checking account** → not included in Bills Checking funding.

If a bill needs Bills Checking but no valid Transfer Source is configured, the app blocks the save rather than guessing.

## Merchant aliases

Merchant aliases are alternate transaction descriptions that genuinely identify the same biller. Use them conservatively; do not add broad aliases just to force a match.

## Overview

The **Overview** page provides monthly scheduled/paid totals, 1st-vs-15th split, paid-vs-still-due progress, workflow verification counts, closeout status, and bill-level activity.

The 1st and 15th cards include **Open checklist** buttons.

## Pay Periods

Open **Pay Periods** and choose:

- **1st Pay Period**
- **15th Pay Period**
- **Whole Month**

Use **Show unfinished only** to focus on remaining work.

Selecting a row exposes supported inline fields for that bill instance. A manual Paid checkpoint records that you handled the bill; it does not fabricate bank evidence.

## Bills Checking funding

Funding is based on bills whose Payment Account is Bills Checking. For the current month, the live available Bills Checking balance is applied to the 1st requirement first and then the 15th. Historical/future months do not use today's live balance.

Next: [[Transactions-and-Reconciliation]].
