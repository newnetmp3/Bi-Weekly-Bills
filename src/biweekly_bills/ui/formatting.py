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


def relative_time(value: str | None, *, now=None) -> str:
    """Turn an ISO timestamp into plain words such as '3 hours ago'."""
    from datetime import datetime, timezone

    if not value:
        return "never"
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    seconds = int((current - moment).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = max(1, (current.astimezone().date() - moment.astimezone().date()).days)
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    return moment.astimezone().strftime("%b %d, %Y").replace(" 0", " ")
