from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .bank_data import cycle_for_day
from .bank_sync import suggested_bill_for_transaction
from .database import Database


@dataclass(frozen=True)
class ReconciliationPreview:
    plaid_transaction_id: str
    bill_instance_id: int
    bill_name: str
    year: int
    month: int
    cycle: str
    bank_amount_cents: int
    due_cents: int | None
    existing_paid_cents: int | None
    difference_cents: int | None
    already_agrees: bool


def transaction_date(row: Any) -> date | None:
    raw = row["posted_date"] or row["authorized_date"]
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def candidate_bill_instances(database: Database, row: Any) -> list[Any]:
    tx_date = transaction_date(row)
    if tx_date is None:
        return []
    cycle = cycle_for_day(tx_date.day)
    return database.list_cycle_instances(tx_date.year, tx_date.month, cycle)


def suggested_bill_instance(database: Database, row: Any) -> Any | None:
    suggestion = suggested_bill_for_transaction(row)
    if not suggestion:
        return None
    for instance in candidate_bill_instances(database, row):
        if str(instance["bill_name_snapshot"]).casefold() == suggestion.casefold():
            return instance
    return None


def build_preview(
    database: Database,
    row: Any,
    bill_instance_id: int,
) -> ReconciliationPreview:
    target = database.get_bill_instance(bill_instance_id)
    if target is None:
        raise ValueError("Target bill instance was not found.")

    tx_date = transaction_date(row)
    if tx_date is None:
        raise ValueError("Transaction does not have a usable posted/authorized date.")

    expected_cycle = cycle_for_day(tx_date.day)
    if (
        int(target["year"]) != tx_date.year
        or int(target["month"]) != tx_date.month
        or str(target["cycle"]) != expected_cycle
    ):
        raise ValueError(
            "Target bill must be in the same month and 1st/15th pay period as the transaction."
        )

    bank_amount = int(row["amount_cents"])
    due = target["due_cents"]
    paid = target["paid_cents"]
    difference = None if due is None else bank_amount - int(due)
    already_agrees = paid is not None and int(paid) == bank_amount

    return ReconciliationPreview(
        plaid_transaction_id=str(row["plaid_transaction_id"]),
        bill_instance_id=bill_instance_id,
        bill_name=str(target["bill_name_snapshot"]),
        year=int(target["year"]),
        month=int(target["month"]),
        cycle=str(target["cycle"]),
        bank_amount_cents=bank_amount,
        due_cents=None if due is None else int(due),
        existing_paid_cents=None if paid is None else int(paid),
        difference_cents=difference,
        already_agrees=already_agrees,
    )


def accept_match(
    database: Database,
    row: Any,
    bill_instance_id: int,
) -> ReconciliationPreview:
    preview = build_preview(database, row, bill_instance_id)
    database.reconcile_transaction(
        environment=str(row["environment"]),
        plaid_transaction_id=str(row["plaid_transaction_id"]),
        bill_instance_id=bill_instance_id,
    )
    return preview


def ignore_transaction(database: Database, row: Any) -> None:
    database.ignore_transaction(
        str(row["environment"]),
        str(row["plaid_transaction_id"]),
    )


def undo_reconciliation(database: Database, row: Any) -> None:
    database.undo_reconciliation(
        str(row["environment"]),
        str(row["plaid_transaction_id"]),
    )
