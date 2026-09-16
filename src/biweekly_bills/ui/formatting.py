from __future__ import annotations


def money(cents: int | None) -> str:
    if cents is None:
        return "—"
    return "$" + f"{cents / 100:,.2f}"


def schedule_balance(due_cents: int, paid_cents: int) -> tuple[str, int]:
    """Return a human label and absolute difference for scheduled vs paid."""
    difference = int(due_cents) - int(paid_cents)
    if difference > 0:
        return "Remaining", difference
    if difference < 0:
        return "Over scheduled", abs(difference)
    return "Settled", 0


def schedule_balance_phrase(due_cents: int, paid_cents: int) -> str:
    label, amount = schedule_balance(due_cents, paid_cents)
    if label == "Remaining":
        return f"{money(amount)} remaining"
    if label == "Over scheduled":
        return f"{money(amount)} over scheduled"
    return "Settled · $0.00"
