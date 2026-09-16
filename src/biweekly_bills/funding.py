from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from .database import Database


@dataclass(frozen=True)
class FundingLine:
    bill_instance_id: int
    bill_name: str
    cycle: str
    due_cents: int
    paid_cents: int
    remaining_cents: int
    payment_account_id: str | None
    payment_account_label: str | None
    transfer_source_account_id: str | None
    transfer_source_label: str | None


@dataclass(frozen=True)
class FundingPlan:
    year: int
    month: int
    environment: str | None
    bills_account_name: str | None
    bills_account_mask: str | None
    available_balance_cents: int | None
    first_required_cents: int
    fifteenth_required_cents: int
    total_required_cents: int
    first_transfer_cents: int
    fifteenth_transfer_cents: int
    total_transfer_cents: int
    included_bill_count: int
    review_bill_count: int
    source_totals: tuple[tuple[str, int], ...]
    balance_applied: bool


def preferred_bank_environment(database: Database) -> str | None:
    if database.list_bank_accounts("production"):
        return "production"
    return None


def designated_bills_account(database: Database, environment: str | None) -> Any | None:
    if environment is None:
        return None
    return next(
        (
            row
            for row in database.list_bank_accounts(environment)
            if int(row["is_bills_checking"] or 0)
        ),
        None,
    )


def _remaining(row: Any) -> int:
    due = int(row["due_cents"] or 0)
    paid = int(row["paid_cents"] or 0)
    return max(due - paid, 0)


def _joined_account_label(
    name: str | None,
    mask: str | None,
    account_id: str | None,
) -> str | None:
    if name:
        label = str(name)
        if mask:
            label += f" ••••{mask}"
        return label
    if account_id:
        return f"Unavailable account ({account_id})"
    return None


def funding_lines(database: Database, year: int, month: int) -> tuple[list[FundingLine], int]:
    """Return unpaid bills explicitly marked for transfer into Bills Checking.

    transfer_required is the sole inclusion switch. Payment Account is an
    independent description of how the bill is actually paid and never decides
    whether the bill belongs in Bills-account transfer planning.
    """

    included: list[FundingLine] = []
    review_count = 0
    bills_account = designated_bills_account(database, "production")
    bills_account_id = (
        str(bills_account["plaid_account_id"])
        if bills_account is not None
        else None
    )

    for row in database.list_month_instances(year, month, active_only=True):
        remaining = _remaining(row)
        transfer_required = row["transfer_required"]

        if transfer_required is None:
            if remaining > 0:
                review_count += 1
            continue

        if not int(transfer_required) or remaining <= 0:
            continue

        payment_id = (
            str(row["payment_account_id"])
            if row["payment_account_id"]
            else None
        )
        source_id = (
            str(row["transfer_source_account_id"])
            if row["transfer_source_account_id"]
            else None
        )
        payment_label = _joined_account_label(
            row["payment_account_name"],
            row["payment_account_mask"],
            payment_id,
        )
        source_label = _joined_account_label(
            row["transfer_source_account_name"],
            row["transfer_source_account_mask"],
            source_id,
        )

        needs_review = (
            not payment_id
            or not source_id
            or source_label is None
            or (bills_account_id is not None and source_id == bills_account_id)
        )
        if needs_review:
            review_count += 1

        included.append(
            FundingLine(
                bill_instance_id=int(row["id"]),
                bill_name=str(row["bill_name_snapshot"]),
                cycle=str(row["cycle"]),
                due_cents=int(row["due_cents"] or 0),
                paid_cents=int(row["paid_cents"] or 0),
                remaining_cents=remaining,
                payment_account_id=payment_id,
                payment_account_label=payment_label,
                transfer_source_account_id=source_id,
                transfer_source_label=source_label,
            )
        )

    return included, review_count


def scheduled_transfer_requirement(
    database: Database,
    year: int,
    month: int,
    scope: str,
) -> int:
    """Scheduled gross transfer target for aggregate Bills-account funding.

    Unlike the live funding planner, this intentionally ignores Paid values and
    the current Bills balance. It answers: how much was scheduled to be funded
    into Bills Checking for this 1st/15th/month scope?
    """

    if scope not in {"1st", "15th", "month"}:
        raise ValueError("scope must be 1st, 15th, or month")

    total = 0
    for row in database.list_month_instances(year, month, active_only=True):
        if row["transfer_required"] is None or not int(row["transfer_required"]):
            continue
        if scope != "month" and str(row["cycle"]) != scope:
            continue
        total += int(row["due_cents"] or 0)
    return total


def build_funding_plan(
    database: Database,
    year: int,
    month: int,
    *,
    today: date | None = None,
) -> FundingPlan:
    today = today or date.today()
    environment = preferred_bank_environment(database)

    if environment is None:
        return FundingPlan(
            year=year,
            month=month,
            environment=None,
            bills_account_name=None,
            bills_account_mask=None,
            available_balance_cents=None,
            first_required_cents=0,
            fifteenth_required_cents=0,
            total_required_cents=0,
            first_transfer_cents=0,
            fifteenth_transfer_cents=0,
            total_transfer_cents=0,
            included_bill_count=0,
            review_bill_count=0,
            source_totals=(),
            balance_applied=False,
        )

    lines, review_count = funding_lines(database, year, month)

    first_required = sum(line.remaining_cents for line in lines if line.cycle == "1st")
    fifteenth_required = sum(line.remaining_cents for line in lines if line.cycle == "15th")
    total_required = first_required + fifteenth_required

    source_totals_dict: dict[str, int] = defaultdict(int)
    for line in lines:
        source = line.transfer_source_label or "Transfer source not set"
        source_totals_dict[source] += line.remaining_cents
    source_totals = tuple(
        sorted(source_totals_dict.items(), key=lambda item: item[0].casefold())
    )

    bills_account = designated_bills_account(database, environment)

    available: int | None = None
    bills_name: str | None = None
    bills_mask: str | None = None
    if bills_account is not None:
        bills_name = str(bills_account["name"] or "Bills Checking")
        bills_mask = str(bills_account["mask"] or "") or None
        raw_available = bills_account["available_balance_cents"]
        if raw_available is None:
            raw_available = bills_account["current_balance_cents"]
        if raw_available is not None:
            available = int(raw_available)

    balance_applied = (
        available is not None
        and year == today.year
        and month == today.month
    )

    if balance_applied:
        balance = int(available)
        first_transfer = max(first_required - balance, 0)
        balance_after_first = max(balance - first_required, 0)
        fifteenth_transfer = max(fifteenth_required - balance_after_first, 0)

        if balance < 0:
            first_transfer = first_required + abs(balance)
            fifteenth_transfer = fifteenth_required
    else:
        first_transfer = first_required
        fifteenth_transfer = fifteenth_required

    total_transfer = first_transfer + fifteenth_transfer

    return FundingPlan(
        year=year,
        month=month,
        environment=environment,
        bills_account_name=bills_name,
        bills_account_mask=bills_mask,
        available_balance_cents=available,
        first_required_cents=first_required,
        fifteenth_required_cents=fifteenth_required,
        total_required_cents=total_required,
        first_transfer_cents=first_transfer,
        fifteenth_transfer_cents=fifteenth_transfer,
        total_transfer_cents=total_transfer,
        included_bill_count=len(lines),
        review_bill_count=review_count,
        source_totals=source_totals,
        balance_applied=balance_applied,
    )
