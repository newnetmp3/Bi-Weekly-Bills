from __future__ import annotations

from typing import Any

def apply_sync(
    existing: list[dict[str, Any]],
    *,
    added: list[dict[str, Any]],
    modified: list[dict[str, Any]],
    removed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge a Plaid /transactions/sync response into a local transaction cache."""
    by_id = {tx["transaction_id"]: tx for tx in existing if tx.get("transaction_id")}

    for tx in added:
        transaction_id = tx.get("transaction_id")
        if transaction_id:
            by_id[transaction_id] = tx

    for tx in modified:
        transaction_id = tx.get("transaction_id")
        if transaction_id:
            by_id[transaction_id] = tx

    for tombstone in removed:
        transaction_id = tombstone.get("transaction_id")
        if transaction_id:
            by_id.pop(transaction_id, None)

    return sorted(
        by_id.values(),
        key=lambda tx: (
            tx.get("date") or "",
            tx.get("authorized_date") or "",
            tx.get("transaction_id") or "",
        ),
        reverse=True,
    )

def transaction_label(tx: dict[str, Any]) -> str:
    return str(tx.get("merchant_name") or tx.get("name") or "(unnamed transaction)")
