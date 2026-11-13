"""The short to-do list shown on Overview.

Each item says what needs doing in plain words and where to go to do it.
Nothing here changes data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .bill_status import bill_state_text
from .database import Database

_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)


def money_text(cents: int) -> str:
    return "$" + f"{cents / 100:,.2f}"


@dataclass(frozen=True)
class AttentionItem:
    severity: str  # "action" (do something) or "check" (worth a look)
    text: str
    button: str
    target: str  # pay_periods:<cycle> | transactions:funding | recheck | bills | settings | sync


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def attention_items(
    database: Database,
    *,
    today: date | None = None,
    connection_action: str = "sync",
) -> list[AttentionItem]:
    today = today or date.today()
    items: list[AttentionItem] = []

    # Bank connection and freshness come first: everything else depends on it.
    if connection_action != "sync":
        items.append(
            AttentionItem(
                "action",
                "Your bank connection needs attention before new payments can be downloaded.",
                "Open Settings",
                "settings",
            )
        )
    else:
        sync = database.get_sync_state("production")
        last = sync["last_sync_at"] if sync is not None else None
        stale_days = None
        if last:
            try:
                moment = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                stale_days = (datetime.now(timezone.utc) - moment).days
            except ValueError:
                stale_days = None
        if last is None or (stale_days is not None and stale_days >= 3):
            items.append(
                AttentionItem(
                    "check",
                    "Bank data hasn't been synced recently"
                    + (f" ({stale_days} days)" if stale_days else "")
                    + ". Sync to pick up new payments.",
                    "Sync now",
                    "sync",
                )
            )

    # Bills still to pay this month, per pay period.
    database.materialize_active_bills(today.year, today.month)
    rows = database.list_month_instances(today.year, today.month, active_only=True)
    for cycle in ("1st", "15th"):
        to_pay = [
            row for row in rows
            if str(row["cycle"]) == cycle and bill_state_text(row, today=today) == "To pay"
        ]
        if not to_pay:
            continue
        total = sum(int(row["due_cents"] or 0) for row in to_pay)
        names = ", ".join(str(row["bill_name_snapshot"]) for row in to_pay[:3])
        if len(to_pay) > 3:
            names += f" and {len(to_pay) - 3} more"
        overdue = cycle == "1st" and today.day >= 15
        items.append(
            AttentionItem(
                "action",
                f"{len(to_pay)} bill(s) still to pay in the {cycle} period "
                f"({money_text(total)}): {names}."
                + (" The 1st period has passed." if overdue else ""),
                "Pay bills",
                f"pay_periods:{cycle}",
            )
        )

    # Money for this period's automatic payments.
    from .funding import build_funding_plan
    from .funding_transfer import period_funding_status

    plan = build_funding_plan(database, today.year, today.month, today=today)
    if plan.environment is not None:
        current = "1st" if today.day < 15 else "15th"
        needed = plan.first_transfer_cents if current == "1st" else plan.fifteenth_transfer_cents
        status = period_funding_status(
            database, str(plan.environment), today.year, today.month, current
        )
        if status.unassigned and not status.assigned:
            day, amount = status.unassigned[-1]
            when = day.strftime("%b %d").replace(" 0", " ") if day else "recently"
            items.append(
                AttentionItem(
                    "check",
                    f"A {money_text(amount)} transfer into Bills Checking on {when} "
                    "isn't assigned to its bills yet.",
                    "Assign it",
                    "transactions:funding",
                )
            )
        elif not status.assigned and needed > 0:
            items.append(
                AttentionItem(
                    "action",
                    f"Move {money_text(needed)} into Bills Checking for the "
                    f"{current} period's automatic payments.",
                    "See details",
                    f"pay_periods:{current}",
                )
            )

    # Last month: marked paid, but the bank never showed the payment.
    prev_year, prev_month = _previous_month(today.year, today.month)
    missing = [
        row
        for row in database.list_month_instances(prev_year, prev_month, active_only=True)
        if bill_state_text(row, today=today) == "Paid · not found at bank"
    ]
    if missing:
        names = ", ".join(str(row["bill_name_snapshot"]) for row in missing[:3])
        if len(missing) > 3:
            names += f" and {len(missing) - 3} more"
        items.append(
            AttentionItem(
                "check",
                f"{len(missing)} bill(s) from {_MONTHS[prev_month - 1]} are marked paid "
                f"but no matching bank payment was found: {names}.",
                "Re-check matches",
                "recheck",
            )
        )

    # Setup gaps that quietly make totals wrong.
    unset = [
        str(bill["name"])
        for bill in database.list_bills(active_only=True)
        if not bill["payment_account_id"]
    ]
    if unset:
        items.append(
            AttentionItem(
                "check",
                f"{len(unset)} bill(s) have no Payment Account, so they're left out of "
                f"the Bills transfer total: {', '.join(unset[:3])}"
                + ("…" if len(unset) > 3 else "."),
                "Fix bills",
                "bills",
            )
        )
    return items
