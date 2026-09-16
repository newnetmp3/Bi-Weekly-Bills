from __future__ import annotations

import json
from typing import Any

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.custom_sandbox_transaction import CustomSandboxTransaction
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.sandbox_transactions_create_request import SandboxTransactionsCreateRequest
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.sandbox_public_token_create_request_options import SandboxPublicTokenCreateRequestOptions
from plaid.model.sandbox_item_reset_login_request import SandboxItemResetLoginRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from .settings import Settings


def build_client(settings: Settings) -> plaid_api.PlaidApi:
    host = plaid.Environment.Sandbox if settings.environment == "sandbox" else plaid.Environment.Production
    configuration = plaid.Configuration(
        host=host,
        api_key={
            "clientId": settings.client_id,
            "secret": settings.secret,
            "plaidVersion": "2020-09-14",
        },
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def create_link_token(client: plaid_api.PlaidApi, settings: Settings) -> dict[str, Any]:
    request = LinkTokenCreateRequest(
        products=[Products("transactions")],
        client_name="Bi-Weekly Bills",
        country_codes=[CountryCode("US")],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id="bi-weekly-bills-local"),
    )
    if settings.redirect_uri:
        request["redirect_uri"] = settings.redirect_uri
    return client.link_token_create(request).to_dict()


def create_update_link_token(
    client: plaid_api.PlaidApi,
    settings: Settings,
    access_token: str,
) -> dict[str, Any]:
    request = LinkTokenCreateRequest(
        access_token=access_token,
        client_name="Bi-Weekly Bills",
        country_codes=[CountryCode("US")],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id="bi-weekly-bills-local"),
    )
    if settings.redirect_uri:
        request["redirect_uri"] = settings.redirect_uri
    return client.link_token_create(request).to_dict()


def exchange_public_token(client: plaid_api.PlaidApi, public_token: str) -> dict[str, Any]:
    request = ItemPublicTokenExchangeRequest(public_token=public_token)
    return client.item_public_token_exchange(request).to_dict()


def get_item(client: plaid_api.PlaidApi, access_token: str) -> dict[str, Any]:
    return client.item_get(ItemGetRequest(access_token=access_token)).to_dict()


def get_accounts(client: plaid_api.PlaidApi, access_token: str) -> dict[str, Any]:
    return client.accounts_get(AccountsGetRequest(access_token=access_token)).to_dict()


def get_balance(client: plaid_api.PlaidApi, access_token: str) -> dict[str, Any]:
    return client.accounts_balance_get(AccountsBalanceGetRequest(access_token=access_token)).to_dict()


def sync_transactions(
    client: plaid_api.PlaidApi,
    access_token: str,
    cursor: str | None = None,
) -> dict[str, Any]:
    starting_cursor = cursor or ""
    restart_count = 0
    while True:
        current_cursor = starting_cursor
        added: list[dict[str, Any]] = []
        modified: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        accounts: list[dict[str, Any]] = []
        update_status = "TRANSACTIONS_UPDATE_STATUS_UNKNOWN"
        try:
            for _ in range(100):
                request = TransactionsSyncRequest(
                    access_token=access_token,
                    cursor=current_cursor,
                    count=500,
                )
                response = client.transactions_sync(request).to_dict()
                added.extend(response.get("added", []))
                modified.extend(response.get("modified", []))
                removed.extend(response.get("removed", []))
                accounts = response.get("accounts", accounts)
                update_status = str(response.get("transactions_update_status") or update_status)
                current_cursor = str(response.get("next_cursor") or "")
                if not response.get("has_more", False):
                    return {
                        "added": added,
                        "modified": modified,
                        "removed": removed,
                        "accounts": accounts,
                        "next_cursor": current_cursor,
                        "transactions_update_status": update_status,
                    }
            raise RuntimeError("Plaid transaction sync exceeded 100 pages.")
        except plaid.ApiException as exc:
            error_code = ""
            try:
                body = json.loads(exc.body or "{}")
                error_code = str(body.get("error_code") or "")
            except Exception:
                pass
            if error_code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION" and restart_count < 2:
                restart_count += 1
                continue
            raise

def create_sandbox_transactions(
    client: plaid_api.PlaidApi,
    access_token: str,
    transactions: list[dict[str, Any]],
) -> dict[str, Any]:
    custom = [
        CustomSandboxTransaction(
            date_transacted=tx["date_transacted"],
            date_posted=tx["date_posted"],
            amount=float(tx["amount"]),
            description=str(tx["description"]),
            iso_currency_code=str(tx.get("iso_currency_code") or "USD"),
        )
        for tx in transactions
    ]
    request = SandboxTransactionsCreateRequest(
        access_token=access_token,
        transactions=custom,
    )
    return client.sandbox_transactions_create(request).to_dict()

def create_dynamic_transactions_sandbox_item(
    client: plaid_api.PlaidApi,
) -> dict[str, Any]:
    options = SandboxPublicTokenCreateRequestOptions(
        override_username="user_transactions_dynamic",
        override_password="pass_dynamic",
    )
    request = SandboxPublicTokenCreateRequest(
        institution_id="ins_109508",
        initial_products=[Products("transactions")],
        options=options,
    )
    public = client.sandbox_public_token_create(request).to_dict()
    public_token = str(public.get("public_token") or "")
    if not public_token:
        raise RuntimeError("Plaid did not return a Sandbox public token.")
    return exchange_public_token(client, public_token)


def sandbox_reset_login(
    client: plaid_api.PlaidApi,
    access_token: str,
) -> dict[str, Any]:
    request = SandboxItemResetLoginRequest(access_token=access_token)
    return client.sandbox_item_reset_login(request).to_dict()
