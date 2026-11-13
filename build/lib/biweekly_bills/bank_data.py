from __future__ import annotations

from datetime import date
import re
from typing import Any

from .bill_rules import BILL_RULES, BillRule


def _find_list(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        for child in payload.values():
            found = _find_list(child, key)
            if found:
                return found
    elif isinstance(payload, list):
        for child in payload:
            found = _find_list(child, key)
            if found:
                return found
    return []


def extract_transactions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list) and all(isinstance(x, dict) for x in payload):
        if any("transaction_id" in x for x in payload):
            return payload
    return _find_list(payload, "transactions")


def extract_accounts(payload: Any) -> list[dict[str, Any]]:
    return _find_list(payload, "accounts")


def normalize(text: str) -> str:
    text = text.casefold().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def transaction_text(tx: dict[str, Any]) -> str:
    parts = [
        tx.get("merchant_name"),
        tx.get("name"),
        tx.get("original_description"),
        tx.get("payment_channel"),
    ]
    return normalize(" ".join(str(x) for x in parts if x))


def transaction_date(tx: dict[str, Any]) -> date | None:
    raw = tx.get("date") or tx.get("authorized_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def cycle_for_day(day: int) -> str:
    return "1st" if day < 15 else "15th"


def match_rule(tx: dict[str, Any], rule: BillRule) -> bool:
    haystack = transaction_text(tx)
    return any(normalize(alias) in haystack for alias in rule.aliases)


def find_bill_matches(
    transactions: list[dict[str, Any]],
    *,
    year: int,
    month: int,
    cycle: str,
    account_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for tx in transactions:
        tx_date = transaction_date(tx)
        if not tx_date or tx_date.year != year or tx_date.month != month:
            continue
        if cycle_for_day(tx_date.day) != cycle:
            continue
        if tx.get("pending") is True:
            continue
        if account_id and str(tx.get("account_id") or "") != account_id:
            continue
        try:
            amount = float(tx.get("amount"))
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        candidates.append(tx)

    matched: dict[str, dict[str, Any]] = {}
    for rule in BILL_RULES:
        if rule.cycle != cycle:
            continue
        hits = [tx for tx in candidates if match_rule(tx, rule)]
        if not hits:
            continue
        hits.sort(key=lambda tx: (transaction_date(tx) or date.min, str(tx.get("transaction_id") or "")), reverse=True)
        matched[rule.name] = hits[0]
    return matched
