from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

from .database import Database
from .funding import scheduled_transfer_requirement
from .reconciliation import transaction_date


@dataclass(frozen=True)
class FundingTransferPreview:
    environment: str
    plaid_transaction_id: str
    year: int
    month: int
    scope: str
    expected_cents: int
    actual_cents: int
    difference_cents: int
    existing_validation: bool


def _validate_candidate(row: Any) -> tuple[int, int]:
    if int(row["pending"]):
        raise ValueError("Pending transfers cannot be validated.")
    if int(row["amount_cents"]) >= 0:
        raise ValueError("Select an incoming transfer to Bills Checking.")
    if not int(row["is_bills_checking"] or 0):
        raise ValueError(
            "Select an incoming transaction deposited into the designated Bills Checking account."
        )
    tx_date = transaction_date(row)
    if tx_date is None:
        raise ValueError("Transaction does not have a usable date.")
    return tx_date.year, tx_date.month


_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
)
# Transfers made in the last week of a month often fund next month's 1st.
_BOUNDARY_DAYS = 7
# Outgoing transfer and Bills deposit must post within this many days.
_PAIR_WINDOW_DAYS = 3


@dataclass(frozen=True)
class FundingTarget:
    year: int
    month: int
    scope: str
    expected_cents: int
    bills: tuple[tuple[str, int], ...]

    @property
    def label(self) -> str:
        period = "Whole month" if self.scope == "month" else f"{self.scope} period bills"
        return f"{period} · {_MONTH_NAMES[self.month - 1]} {self.year}"


def funding_bills(
    database: Database,
    year: int,
    month: int,
    scope: str,
) -> list[tuple[str, int]]:
    """Bills paid from Bills Checking that a transfer for this scope covers."""
    bills: list[tuple[str, int]] = []
    for row in database.list_month_instances(year, month, active_only=True):
        if row["transfer_required"] is None or not int(row["transfer_required"]):
            continue
        if scope != "month" and str(row["cycle"]) != scope:
            continue
        due = int(row["due_cents"] or 0)
        if due > 0:
            bills.append((str(row["bill_name_snapshot"]), due))
    return bills


def funding_targets(database: Database, row: Any) -> list[FundingTarget]:
    """Groups of bills an incoming Bills Checking transfer could cover.

    The deposit's own month (1st period, 15th period, whole month), plus the
    next month's 1st period when the deposit lands in the last week of a
    month. Best fit by amount comes first; empty groups are left out.
    """
    year, month = _validate_candidate(row)
    tx_date = transaction_date(row)
    assert tx_date is not None
    periods: list[tuple[int, int, str]] = [
        (year, month, "1st"),
        (year, month, "15th"),
        (year, month, "month"),
    ]
    last_day = calendar.monthrange(year, month)[1]
    if tx_date.day > last_day - _BOUNDARY_DAYS:
        following = date(year, month, last_day) + timedelta(days=1)
        periods.append((following.year, following.month, "1st"))

    actual = abs(int(row["amount_cents"]))
    targets: list[FundingTarget] = []
    for target_year, target_month, scope in periods:
        bills = funding_bills(database, target_year, target_month, scope)
        expected = sum(due for _name, due in bills)
        if expected <= 0:
            continue
        targets.append(
            FundingTarget(target_year, target_month, scope, expected, tuple(bills))
        )
    def days_away(target: FundingTarget) -> int:
        anchor = date(target.year, target.month, 15 if target.scope == "15th" else 1)
        return abs((tx_date - anchor).days)

    # Closest amount first; on a tie, the pay period nearest the deposit.
    targets.sort(key=lambda t: (abs(actual - t.expected_cents), days_away(t)))
    return targets


def _looks_like_transfer(row: Any) -> bool:
    text = f"{row['merchant_name'] or ''} {row['name'] or ''}".casefold()
    return "transfer" in text or "trf" in text or "tfr" in text


def bills_funding_pairs(rows: Iterable[Any]) -> dict[str, Any]:
    """Map outgoing transfers to the Bills Checking deposit they became.

    An outgoing transfer from another account pairs with a deposit into Bills
    Checking for the same amount posted within a few days. Each deposit is
    used once; the closest date wins.
    """
    rows = list(rows)
    deposits: dict[int, list[Any]] = defaultdict(list)
    for row in rows:
        if (
            int(row["is_bills_checking"] or 0)
            and int(row["amount_cents"]) < 0
            and not int(row["pending"])
        ):
            deposits[-int(row["amount_cents"])].append(row)

    pairs: dict[str, Any] = {}
    used: set[str] = set()
    outgoing = [
        row
        for row in rows
        if int(row["amount_cents"]) > 0
        and not int(row["pending"])
        and not int(row["is_bills_checking"] or 0)
        and str(row["account_type"] or "").casefold() == "depository"
        and _looks_like_transfer(row)
    ]
    outgoing.sort(key=lambda r: str(r["posted_date"] or r["authorized_date"] or ""))
    for source in outgoing:
        source_date = transaction_date(source)
        if source_date is None:
            continue
        best = None
        best_gap = None
        for deposit in deposits.get(int(source["amount_cents"]), []):
            deposit_id = str(deposit["plaid_transaction_id"])
            if deposit_id in used:
                continue
            deposit_date = transaction_date(deposit)
            if deposit_date is None:
                continue
            gap = abs((deposit_date - source_date).days)
            if gap <= _PAIR_WINDOW_DAYS and (best_gap is None or gap < best_gap):
                best, best_gap = deposit, gap
        if best is not None:
            used.add(str(best["plaid_transaction_id"]))
            pairs[str(source["plaid_transaction_id"])] = best
    return pairs


def bills_deposit_for_transfer(database: Database, row: Any) -> Any | None:
    """The Bills Checking deposit an outgoing transfer turned into, if any."""
    if int(row["amount_cents"]) <= 0 or int(row["is_bills_checking"] or 0):
        return None
    tx_date = transaction_date(row)
    if tx_date is None:
        return None
    nearby: list[Any] = []
    for offset in (-1, 0, 1):
        anchor = date(tx_date.year, tx_date.month, 15) + timedelta(days=31 * offset)
        nearby.extend(
            database.list_bank_transactions(
                str(row["environment"]),
                limit=5000,
                year=anchor.year,
                month=anchor.month,
            )
        )
    unique = {str(r["plaid_transaction_id"]): r for r in nearby}
    return bills_funding_pairs(unique.values()).get(str(row["plaid_transaction_id"]))


def build_funding_transfer_preview(
    database: Database,
    row: Any,
    scope: str,
    *,
    year: int | None = None,
    month: int | None = None,
) -> FundingTransferPreview:
    if scope not in {"1st", "15th", "month"}:
        raise ValueError("scope must be 1st, 15th, or month")
    tx_year, tx_month = _validate_candidate(row)
    year = tx_year if year is None else int(year)
    month = tx_month if month is None else int(month)
    expected = scheduled_transfer_requirement(
        database,
        year,
        month,
        scope,
    )
    actual = abs(int(row["amount_cents"]))
    existing = row["funding_validation_scope"] is not None
    return FundingTransferPreview(
        environment=str(row["environment"]),
        plaid_transaction_id=str(row["plaid_transaction_id"]),
        year=year,
        month=month,
        scope=scope,
        expected_cents=expected,
        actual_cents=actual,
        difference_cents=actual - expected,
        existing_validation=existing,
    )


def suggested_funding_scope(database: Database, row: Any) -> str | None:
    try:
        year, month = _validate_candidate(row)
    except ValueError:
        return None

    actual = abs(int(row["amount_cents"]))
    candidates = []
    for scope in ("1st", "15th", "month"):
        expected = scheduled_transfer_requirement(
            database,
            year,
            month,
            scope,
        )
        if expected <= 0:
            continue
        candidates.append((abs(actual - expected), scope))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], {"1st": 0, "15th": 1, "month": 2}[item[1]]))
    return candidates[0][1]


def validate_funding_transfer(
    database: Database,
    row: Any,
    scope: str,
    *,
    year: int | None = None,
    month: int | None = None,
) -> FundingTransferPreview:
    preview = build_funding_transfer_preview(
        database, row, scope, year=year, month=month
    )
    database.validate_funding_transfer(
        environment=preview.environment,
        plaid_transaction_id=preview.plaid_transaction_id,
        year=preview.year,
        month=preview.month,
        scope=preview.scope,
        expected_cents=preview.expected_cents,
        actual_cents=preview.actual_cents,
    )
    return preview


def undo_funding_transfer_validation(
    database: Database,
    row: Any,
) -> None:
    database.undo_funding_transfer_validation(
        str(row["environment"]),
        str(row["plaid_transaction_id"]),
    )


@dataclass(frozen=True)
class PeriodFundingStatus:
    """What has been moved into Bills Checking for one pay period."""

    assigned: tuple[tuple[date | None, int, int, str], ...]  # date, actual, expected, scope
    unassigned: tuple[tuple[date | None, int], ...]  # date, amount

    @property
    def assigned_total(self) -> int:
        return sum(actual for _d, actual, _e, _s in self.assigned)


def period_funding_status(
    database: Database,
    environment: str,
    year: int,
    month: int,
    scope: str,
) -> PeriodFundingStatus:
    """Transfers assigned to a 1st/15th/month period, plus loose deposits.

    A whole-month assignment counts for both periods. Unassigned deposits
    are those into Bills Checking in the month (and, for the 1st period, the
    last week of the previous month) that nobody has assigned yet.
    """
    first = date(year, month, 1)
    previous = first - timedelta(days=1)
    rows: list[Any] = list(
        database.list_bank_transactions(environment, limit=5000, year=year, month=month)
    )
    if scope in {"1st", "month"}:
        rows.extend(
            database.list_bank_transactions(
                environment, limit=5000, year=previous.year, month=previous.month
            )
        )

    assigned: list[tuple[date | None, int, int, str]] = []
    unassigned: list[tuple[date | None, int]] = []
    seen: set[str] = set()
    for row in rows:
        tx_id = str(row["plaid_transaction_id"])
        if tx_id in seen:
            continue
        seen.add(tx_id)
        if not (
            int(row["is_bills_checking"] or 0)
            and int(row["amount_cents"]) < 0
            and not int(row["pending"])
        ):
            continue
        tx_day = transaction_date(row)
        saved = row["funding_validation_scope"]
        if saved is not None:
            if (
                int(row["funding_validation_year"]) == year
                and int(row["funding_validation_month"]) == month
                and (saved == scope or saved == "month" or scope == "month")
            ):
                assigned.append(
                    (
                        tx_day,
                        int(row["funding_actual_cents"]),
                        int(row["funding_expected_cents"]),
                        str(saved),
                    )
                )
            continue
        if tx_day is None:
            continue
        in_month = (tx_day.year, tx_day.month) == (year, month)
        late_previous = (
            (tx_day.year, tx_day.month) == (previous.year, previous.month)
            and tx_day.day > previous.day - _BOUNDARY_DAYS
        )
        if scope == "1st" and (late_previous or (in_month and tx_day.day < 15)):
            unassigned.append((tx_day, abs(int(row["amount_cents"]))))
        elif scope == "15th" and in_month and tx_day.day >= 8:
            unassigned.append((tx_day, abs(int(row["amount_cents"]))))
        elif scope == "month" and (in_month or late_previous):
            unassigned.append((tx_day, abs(int(row["amount_cents"]))))
    return PeriodFundingStatus(tuple(assigned), tuple(unassigned))
