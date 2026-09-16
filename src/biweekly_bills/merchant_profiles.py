from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse


_SPACE_RE = re.compile(r"\s+")
_GENERIC_ACCOUNT_DESCRIPTIONS = {
    "ach payment",
    "ach transfer",
    "automatic payment",
    "electronic payment",
    "internal transfer",
    "nfo payment received",
    "online payment",
    "online transfer",
    "payment",
    "payment received",
    "transfer",
    "transfer credit",
    "transfer debit",
}


def _field(row: Any, name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        if isinstance(row, dict):
            return row.get(name, default)
        return default


def merchant_key(name: str | None) -> str:
    if not name:
        return ""
    return _SPACE_RE.sub(" ", str(name).strip()).casefold()


def _clean_metadata_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _humanize_plaid_code(value: Any) -> str:
    text = _clean_metadata_text(value)
    if not text:
        return ""
    return text.replace("_", " ").replace("-", " ").title()


def plaid_transaction_metadata(
    raw_json: str | None,
) -> dict[str, str]:
    """Flatten useful Plaid transaction enrichment into display-ready fields."""
    if not raw_json:
        return {}
    try:
        payload = json.loads(raw_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}

    result: dict[str, str] = {}

    for source_key, target_key in (
        ("logo_url", "logo_url"),
        ("website", "website"),
        ("merchant_entity_id", "entity_id"),
    ):
        value = _clean_metadata_text(payload.get(source_key))
        if value:
            result[target_key] = value

    channel = _humanize_plaid_code(payload.get("payment_channel"))
    if channel:
        result["payment_channel"] = channel

    category = payload.get("personal_finance_category")
    if isinstance(category, dict):
        primary = _humanize_plaid_code(category.get("primary"))
        detailed = _humanize_plaid_code(category.get("detailed"))
        confidence = _humanize_plaid_code(
            category.get("confidence_level")
        )
        category_parts = []
        if primary:
            category_parts.append(primary)
        if detailed and detailed.casefold() != primary.casefold():
            category_parts.append(detailed)
        if category_parts:
            category_text = " / ".join(category_parts)
            if confidence:
                category_text += f" · {confidence} confidence"
            result["category"] = category_text

    counterparties = payload.get("counterparties")
    counterparty_texts: list[str] = []
    if isinstance(counterparties, list):
        for counterparty in counterparties[:3]:
            if not isinstance(counterparty, dict):
                continue
            name = _clean_metadata_text(counterparty.get("name"))
            kind = _humanize_plaid_code(counterparty.get("type"))
            confidence = _humanize_plaid_code(
                counterparty.get("confidence_level")
            )
            if name:
                detail = name
                qualifiers = [
                    value
                    for value in (kind, confidence and f"{confidence} confidence")
                    if value
                ]
                if qualifiers:
                    detail += f" ({', '.join(qualifiers)})"
                counterparty_texts.append(detail)

            if "website" not in result:
                website = _clean_metadata_text(
                    counterparty.get("website")
                )
                if website:
                    result["website"] = website
            if "entity_id" not in result:
                entity_id = _clean_metadata_text(
                    counterparty.get("entity_id")
                )
                if entity_id:
                    result["entity_id"] = entity_id
            if "logo_url" not in result:
                logo_url = _clean_metadata_text(
                    counterparty.get("logo_url")
                )
                if logo_url:
                    result["logo_url"] = logo_url

    if counterparty_texts:
        result["counterparty"] = " · ".join(counterparty_texts)

    location = payload.get("location")
    if isinstance(location, dict):
        address = _clean_metadata_text(location.get("address"))
        city = _clean_metadata_text(location.get("city"))
        region = _clean_metadata_text(location.get("region"))
        postal = _clean_metadata_text(location.get("postal_code"))
        country = _clean_metadata_text(location.get("country"))
        store_number = _clean_metadata_text(
            location.get("store_number")
        )

        city_line = ", ".join(
            value for value in (city, region) if value
        )
        if postal:
            city_line = (
                f"{city_line} {postal}".strip()
                if city_line
                else postal
            )
        location_parts = [
            value
            for value in (address, city_line, country)
            if value
        ]
        if store_number:
            location_parts.append(f"Store {store_number}")
        if location_parts:
            result["location"] = " · ".join(location_parts)

    return result


def merchant_metadata_from_raw_json(
    raw_json: str | None,
) -> tuple[str | None, str | None, str | None]:
    metadata = plaid_transaction_metadata(raw_json)
    return (
        metadata.get("logo_url"),
        metadata.get("website"),
        metadata.get("entity_id"),
    )


def safe_remote_logo_url(value: str | None) -> str | None:
    """Allow only normal HTTPS logo URLs learned from transaction metadata."""
    if not value:
        return None
    try:
        parsed = urlparse(str(value).strip())
    except ValueError:
        return None
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        return None
    return parsed.geturl()


def meaningful_merchant_text(value: str | None) -> str | None:
    merchant = str(value or "").strip()
    if not merchant:
        return None
    key = merchant_key(merchant)
    if key in _GENERIC_ACCOUNT_DESCRIPTIONS:
        return None
    if key.startswith("nfo payment received"):
        return None
    return merchant


def meaningful_merchant_name(row: Any) -> str | None:
    """Return a merchant only when Plaid supplied a real merchant identity.

    Generic payment/transfer descriptions are account movement, not merchants.
    Keeping those separate is important for credit/loan reconciliation: an
    amount match from a checking account must not make an unrelated merchant
    charge look like a payment to a connected credit account.
    """
    return meaningful_merchant_text(
        str(_field(row, "merchant_name") or "")
    )


def counterparty_kind(row: Any) -> str:
    internal_role = str(
        _field(row, "internal_transfer_role") or ""
    ).strip()
    if internal_role:
        return "connected-account"

    account_type = merchant_key(
        str(_field(row, "account_type") or "")
    )
    amount = int(_field(row, "amount_cents", 0) or 0)
    description = merchant_key(
        str(_field(row, "name") or "")
    )
    merchant = meaningful_merchant_name(row)

    if merchant:
        return "merchant"

    if (
        account_type in {"credit", "loan"}
        and amount < 0
    ):
        return "connected-account"

    if (
        description in _GENERIC_ACCOUNT_DESCRIPTIONS
        or description.startswith("nfo payment received")
    ):
        return "account-transfer"

    return "description"


def counterparty_display(row: Any) -> str:
    kind = counterparty_kind(row)
    if kind == "merchant":
        return str(meaningful_merchant_name(row))

    if kind == "connected-account":
        target = (
            _field(row, "internal_transfer_bill_name")
            or _field(row, "account_name")
            or "connected account"
        )
        return f"Connected account · {target}"

    if kind == "account-transfer":
        target = _field(row, "internal_transfer_bill_name")
        if target:
            return f"Account transfer · {target}"

    return str(
        _field(row, "merchant_name")
        or _field(row, "name")
        or "(unnamed transaction)"
    )
