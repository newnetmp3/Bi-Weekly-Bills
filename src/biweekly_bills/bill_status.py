"""One plain-language status per bill, shared by every screen and report."""

from __future__ import annotations

from datetime import date


def bill_state_text(row, *, today: date | None = None) -> str:
    """One plain-language status combining the Paid tick, Status and bank."""
    if int(row["bank_verified"] or 0):
        return "✓ Paid · confirmed by bank"
    status = str(row["status"] or "").strip().casefold()
    due = int(row["due_cents"] or 0)
    manually_paid = bool(int(row["manually_paid"] or 0))
    current = today or date.today()
    past = (int(row["year"]), int(row["month"])) < (current.year, current.month)
    if manually_paid or status == "paid":
        return "Paid · not found at bank" if past else "Paid · waiting for bank"
    if status in {"no payment due", "skip", "skipped"} or (
        due <= 0 and not int(row["paid_cents"] or 0)
    ):
        return "No payment due"
    if status == "autopay" or str(row["method"] or "").strip().casefold() == "autopay":
        return "Autopay · not seen yet" if past else "Autopay · waiting for bank"
    return "Not paid yet" if past else "To pay"


def needs_action(row) -> bool:
    return bill_state_text(row) in {"To pay", "Not paid yet"}
