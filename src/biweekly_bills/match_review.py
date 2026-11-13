"""Find and fix bank matches that landed on the wrong month.

Older versions only compared a payment with bills in its own calendar
month, so a "1st" bill paid on the last day of the previous month was
often matched one month off, and the shift rippled through later months.

``propose_match_corrections`` re-runs today's matching on a private copy of
the database with every existing match cleared, then compares. It only
suggests moving a payment between months of the *same* bill when the new
month is nearer the payment date, or filling a bill that has no match. It
never changes anything; ``apply_match_correction`` applies one suggestion
using the normal reconcile/undo paths, so each change can be undone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from .auto_reconcile import (
    auto_reconcile_transactions,
    proximity_penalty,
    reconcile_history,
)
from .database import Database
from .reconciliation import transaction_date

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


@dataclass(frozen=True)
class MatchCorrection:
    plaid_transaction_id: str
    transaction_date: str
    description: str
    amount_cents: int
    bill_name: str
    from_instance_id: int | None
    from_label: str | None
    to_instance_id: int
    to_label: str
    evidence_transaction_id: str | None = None

    @property
    def summary(self) -> str:
        if self.from_label:
            return f"Move: {self.from_label} → {self.to_label}"
        return f"New match → {self.to_label}"


def _instance_rows(conn: sqlite3.Connection) -> dict[int, Any]:
    conn.row_factory = sqlite3.Row
    return {
        int(row["id"]): row
        for row in conn.execute(
            """
            SELECT bi.id, bi.bill_id, bi.bill_name_snapshot,
                   pp.year, pp.month, pp.cycle
            FROM bill_instances bi
            JOIN pay_periods pp ON pp.id = bi.pay_period_id
            """
        )
    }


def _matches(conn: sqlite3.Connection, environment: str) -> dict[str, int]:
    return {
        str(row[0]): int(row[1])
        for row in conn.execute(
            """
            SELECT plaid_transaction_id, bill_instance_id
            FROM transaction_reconciliations
            WHERE environment=? AND disposition='matched'
              AND bill_instance_id IS NOT NULL
            """,
            (environment,),
        )
    }


def _evidence(conn: sqlite3.Connection, environment: str) -> dict[str, str]:
    return {
        str(row[0]): str(row[1])
        for row in conn.execute(
            """
            SELECT source_plaid_transaction_id, destination_plaid_transaction_id
            FROM internal_transfer_matches WHERE environment=?
            """,
            (environment,),
        )
    }


def _label(instance: Any) -> str:
    month = _MONTHS[int(instance["month"]) - 1]
    return f"{month} {int(instance['year'])}, {instance['cycle']}"


def propose_match_corrections(
    database: Database,
    environment: str = "production",
    *,
    today: date | None = None,
) -> list[MatchCorrection]:
    with tempfile.TemporaryDirectory(prefix="bwb-recheck-") as temp:
        scratch_path = Path(temp) / "recheck.sqlite3"
        source = sqlite3.connect(database.path)
        target = sqlite3.connect(scratch_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        scratch = Database(scratch_path)
        with scratch.connection() as conn:
            original = _matches(conn, environment)
        for tx_id in original:
            try:
                scratch.undo_reconciliation(environment, tx_id)
            except ValueError:
                pass
        reconcile_history(scratch, environment, today=today)
        auto_reconcile_transactions(scratch, environment)

        with scratch.connection() as conn:
            proposed = _matches(conn, environment)
            evidence = _evidence(conn, environment)
            instances = _instance_rows(conn)

    transactions = {
        str(row["plaid_transaction_id"]): row
        for row in database.list_bank_transactions(environment, limit=100000)
    }

    # A bill that loses its payment to a correction is fine only if it is
    # itself refilled by another correction or was wrong to begin with;
    # the user approves each one, so we just describe them clearly.
    corrections: list[MatchCorrection] = []
    for tx_id, new_instance_id in proposed.items():
        old_instance_id = original.get(tx_id)
        if old_instance_id == new_instance_id:
            continue
        row = transactions.get(tx_id)
        new_instance = instances.get(new_instance_id)
        if row is None or new_instance is None:
            continue
        tx_day = transaction_date(row)
        old_instance = (
            instances.get(old_instance_id) if old_instance_id is not None else None
        )

        if old_instance is not None:
            # Only ever shift a payment between months of the same bill, and
            # only toward the month it is closer to.
            if (
                old_instance["bill_id"] is None
                or old_instance["bill_id"] != new_instance["bill_id"]
            ):
                continue
            if proximity_penalty(tx_day, new_instance) >= proximity_penalty(
                tx_day, old_instance
            ):
                continue
        else:
            # Filling an empty slot: only when the bill currently has no
            # payment, or its payment is itself being moved elsewhere.
            holder = next(
                (t for t, i in original.items() if i == new_instance_id),
                None,
            )
            if holder is not None and proposed.get(holder) in (
                None,
                new_instance_id,
            ):
                continue

        corrections.append(
            MatchCorrection(
                plaid_transaction_id=tx_id,
                transaction_date=str(tx_day or row["posted_date"] or ""),
                description=str(row["merchant_name"] or row["name"] or ""),
                amount_cents=int(row["amount_cents"]),
                bill_name=str(new_instance["bill_name_snapshot"]),
                from_instance_id=old_instance_id,
                from_label=_label(old_instance) if old_instance is not None else None,
                to_instance_id=new_instance_id,
                to_label=_label(new_instance),
                evidence_transaction_id=evidence.get(tx_id),
            )
        )

    # Keep only corrections whose chain is self-consistent: a move whose
    # destination is currently held by a payment that is not itself moving
    # would strip a correct match, so drop it.
    moving = {c.plaid_transaction_id for c in corrections}
    filtered: list[MatchCorrection] = []
    for correction in corrections:
        holder = next(
            (
                t
                for t, i in original.items()
                if i == correction.to_instance_id
                and t != correction.plaid_transaction_id
            ),
            None,
        )
        if holder is not None and holder not in moving:
            continue
        filtered.append(correction)

    filtered.sort(key=lambda c: (c.bill_name, c.transaction_date))
    return filtered


def apply_match_correction(
    database: Database,
    correction: MatchCorrection,
    environment: str = "production",
) -> None:
    """Apply one suggestion through the normal undo/reconcile paths."""
    tx_id = correction.plaid_transaction_id
    if database.get_reconciliation(environment, tx_id) is not None:
        database.undo_reconciliation(environment, tx_id)

    with database.connection() as conn:
        holder = conn.execute(
            """
            SELECT plaid_transaction_id FROM transaction_reconciliations
            WHERE environment=? AND disposition='matched' AND bill_instance_id=?
            """,
            (environment, correction.to_instance_id),
        ).fetchone()
    if holder is not None:
        database.undo_reconciliation(environment, str(holder[0]))

    database.reconcile_transaction(
        environment=environment,
        plaid_transaction_id=tx_id,
        bill_instance_id=correction.to_instance_id,
    )
    if correction.evidence_transaction_id:
        # The card-side receipt is attached after the payment is linked.
        database.record_internal_transfer_match(
            environment=environment,
            source_plaid_transaction_id=tx_id,
            destination_plaid_transaction_id=correction.evidence_transaction_id,
            bill_instance_id=correction.to_instance_id,
            amount_cents=correction.amount_cents,
        )
