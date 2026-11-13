from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


def build_funding_transfer_preview(
    database: Database,
    row: Any,
    scope: str,
) -> FundingTransferPreview:
    if scope not in {"1st", "15th", "month"}:
        raise ValueError("scope must be 1st, 15th, or month")
    year, month = _validate_candidate(row)
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
) -> FundingTransferPreview:
    preview = build_funding_transfer_preview(database, row, scope)
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
