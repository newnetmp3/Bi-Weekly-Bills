from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from biweekly_bills.bank_sync import (
    set_sandbox_bills_account,
    suggested_bill_for_transaction,
    sync_production_to_sqlite,
    sync_sandbox_to_sqlite,
)
from biweekly_bills.database import Database


class BankSyncSQLiteTests(unittest.TestCase):
    def test_nfo_payment_received_marks_only_the_owning_account_verified(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            for account_id, name in (
                ("acct-line", "Dad's Overdraft"),
                ("acct-card", "Family Visa"),
                ("acct-pending", "Pending Credit"),
            ):
                db.upsert_bank_account(
                    environment="production",
                    plaid_account_id=account_id,
                    name=name,
                    mask="0001",
                    account_type="credit",
                    account_subtype="line of credit",
                    current_balance_cents=0,
                    available_balance_cents=0,
                    last_synced_at="2026-09-16T00:00:00+00:00",
                )

            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id="tx-nfo",
                plaid_account_id="acct-line",
                posted_date="2026-09-15",
                authorized_date=None,
                merchant_name=None,
                name="  nfo payment received  ",
                amount_cents=-25000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )
            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id="tx-near-miss",
                plaid_account_id="acct-card",
                posted_date="2026-09-15",
                authorized_date=None,
                merchant_name=None,
                name="NFO PAYMENT RECEIVED ONLINE",
                amount_cents=-18000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )
            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id="tx-pending",
                plaid_account_id="acct-pending",
                posted_date="2026-09-15",
                authorized_date=None,
                merchant_name=None,
                name="NFO PAYMENT RECEIVED",
                amount_cents=-9000,
                pending=True,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )

            accounts = {
                row["plaid_account_id"]: row
                for row in db.list_bank_accounts("production")
            }
            self.assertEqual(accounts["acct-line"]["verified"], 1)
            self.assertEqual(accounts["acct-card"]["verified"], 0)
            self.assertEqual(accounts["acct-pending"]["verified"], 0)

            account = db.get_bank_account("production", "acct-line")
            assert account is not None
            self.assertEqual(account["verified"], 1)

            db.upsert_bank_account(
                environment="production",
                plaid_account_id="acct-green-card",
                name="Green Card",
                mask=None,
                account_type="credit",
                account_subtype="credit card",
                current_balance_cents=120000,
                available_balance_cents=None,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id="tx-green-card-nfo",
                plaid_account_id="acct-green-card",
                posted_date="2026-09-15",
                authorized_date=None,
                merchant_name="NFO PAYMENT RECEIVED",
                name="TRANSFER CREDIT",
                amount_cents=-32000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )

            green_card = db.get_bank_account(
                "production",
                "acct-green-card",
            )
            assert green_card is not None
            self.assertIsNone(green_card["mask"])
            self.assertEqual(green_card["verified"], 1)

            db.upsert_bank_account(
                environment="production",
                plaid_account_id="acct-checking-nfo",
                name="Checking With Same Text",
                mask="1234",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=100000,
                available_balance_cents=100000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id="tx-checking-nfo",
                plaid_account_id="acct-checking-nfo",
                posted_date="2026-09-15",
                authorized_date=None,
                merchant_name="NFO PAYMENT RECEIVED",
                name="NFO PAYMENT RECEIVED",
                amount_cents=-5000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )

            checking = db.get_bank_account(
                "production",
                "acct-checking-nfo",
            )
            assert checking is not None
            self.assertEqual(checking["verified"], 0)

    def test_sync_sandbox_persists_accounts_transactions_and_state(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            fake_settings = SimpleNamespace(
                environment="sandbox",
                client_id="client",
                secret="secret",
                redirect_uri=None,
                host="127.0.0.1",
                port=8000,
            )
            accounts_payload = {
                "accounts": [
                    {
                        "account_id": "acct-checking",
                        "name": "Bills Checking",
                        "mask": "2113",
                        "type": "depository",
                        "subtype": "checking",
                        "balances": {"current": 1500.25, "available": 1400.25},
                    },
                    {
                        "account_id": "acct-savings",
                        "name": "Savings",
                        "mask": "9988",
                        "type": "depository",
                        "subtype": "savings",
                        "balances": {"current": 4000.0, "available": 4000.0},
                    },
                ]
            }
            tx_result = {
                "added": [
                    {
                        "transaction_id": "tx-verizon",
                        "account_id": "acct-checking",
                        "date": "2026-09-20",
                        "authorized_date": "2026-09-19",
                        "merchant_name": "Verizon Wireless",
                        "name": "VERIZON WIRELESS",
                        "amount": 213.07,
                        "pending": False,
                    }
                ],
                "modified": [],
                "removed": [],
                "accounts": accounts_payload["accounts"],
                "next_cursor": "cursor-2",
                "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
            }

            with (
                patch("biweekly_bills.bank_sync.load_settings", return_value=fake_settings),
                patch("biweekly_bills.bank_sync.require_access_token", return_value="sandbox-token"),
                patch("biweekly_bills.bank_sync.build_client", return_value=object()),
                patch(
                    "biweekly_bills.bank_sync.get_item",
                    return_value={"item": {"item_id": "item-sandbox", "error": None}},
                ),
                patch(
                    "biweekly_bills.bank_sync.get_balance",
                    return_value=accounts_payload,
                ),
                patch(
                    "biweekly_bills.bank_sync.sync_transactions",
                    return_value=tx_result,
                ) as sync_transactions_mock,
                patch(
                    "biweekly_bills.bank_sync.load_local_config",
                    return_value={
                        "bills_account_id": "acct-checking",
                        "transactions_cursor": "cursor-1",
                    },
                ),
                patch("biweekly_bills.bank_sync.update_local_config") as update_config,
            ):
                report = sync_sandbox_to_sqlite(db)

            self.assertEqual(sync_transactions_mock.call_count, 1)
            sync_args, sync_kwargs = sync_transactions_mock.call_args
            self.assertEqual(sync_args[1], "sandbox-token")
            self.assertIsNone(sync_kwargs["cursor"])

            self.assertEqual(report.account_count, 2)
            self.assertEqual(report.added_count, 1)
            self.assertEqual(report.transaction_count, 1)
            self.assertEqual(report.item_id, "item-sandbox")

            accounts = db.list_bank_accounts("sandbox")
            checking = next(row for row in accounts if row["plaid_account_id"] == "acct-checking")
            self.assertEqual(checking["current_balance_cents"], 150025)
            self.assertEqual(checking["available_balance_cents"], 140025)
            self.assertEqual(checking["is_bills_checking"], 1)

            transactions = db.list_bank_transactions("sandbox")
            self.assertEqual(len(transactions), 1)
            self.assertEqual(transactions[0]["merchant_name"], "Verizon Wireless")
            self.assertEqual(transactions[0]["amount_cents"], 21307)

            state = db.get_sync_state("sandbox")
            self.assertIsNotNone(state)
            assert state is not None
            self.assertEqual(state["transaction_cursor"], "cursor-2")
            self.assertEqual(
                state["transactions_update_status"],
                "HISTORICAL_UPDATE_COMPLETE",
            )
            update_config.assert_called_with(
                "sandbox",
                transactions_cursor="cursor-2",
            )

    def test_isolated_sync_can_skip_legacy_cursor_persistence(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            fake_settings = SimpleNamespace(
                environment="sandbox",
                client_id="client",
                secret="secret",
                redirect_uri=None,
                host="127.0.0.1",
                port=8000,
            )
            with (
                patch("biweekly_bills.bank_sync.load_settings", return_value=fake_settings),
                patch("biweekly_bills.bank_sync.require_access_token", return_value="sandbox-token"),
                patch("biweekly_bills.bank_sync.build_client", return_value=object()),
                patch(
                    "biweekly_bills.bank_sync.get_item",
                    return_value={"item": {"item_id": "item-sandbox", "error": None}},
                ),
                patch(
                    "biweekly_bills.bank_sync.get_balance",
                    return_value={
                        "accounts": [
                            {
                                "account_id": "acct-checking",
                                "name": "Checking",
                                "mask": "2113",
                                "type": "depository",
                                "subtype": "checking",
                                "balances": {"current": 100.0, "available": 90.0},
                            }
                        ]
                    },
                ),
                patch(
                    "biweekly_bills.bank_sync.sync_transactions",
                    return_value={
                        "added": [],
                        "modified": [],
                        "removed": [],
                        "next_cursor": "validation-cursor",
                        "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
                    },
                ),
                patch(
                    "biweekly_bills.bank_sync.load_local_config",
                    return_value={},
                ),
                patch("biweekly_bills.bank_sync.update_local_config") as update_config,
            ):
                report = sync_sandbox_to_sqlite(
                    db,
                    persist_local_cursor=False,
                )

            self.assertEqual(report.account_count, 1)
            self.assertEqual(
                db.get_sync_state("sandbox")["transaction_cursor"],
                "validation-cursor",
            )
            update_config.assert_not_called()

    def test_production_sync_uses_existing_locked_item_and_persists_real_cache(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            bill_id = db.upsert_bill(
                name="Verizon",
                cycle="15th",
                active=True,
            )
            instance_id = db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="15th",
                bill_name="Verizon",
                bill_id=bill_id,
                due_cents=21307,
                paid_cents=None,
                status="Due",
            )

            fake_settings = SimpleNamespace(
                environment="production",
                client_id="client",
                secret="secret",
                redirect_uri=None,
                host="127.0.0.1",
                port=8000,
            )
            readiness = SimpleNamespace(production_item_id="prod-item")
            accounts_payload = {
                "accounts": [
                    {
                        "account_id": "prod-checking",
                        "name": "NFCU Checking",
                        "mask": "1234",
                        "type": "depository",
                        "subtype": "checking",
                        "balances": {"current": 1200.50, "available": 1100.25},
                    },
                    {
                        "account_id": "prod-savings",
                        "name": "NFCU Savings",
                        "mask": "5678",
                        "type": "depository",
                        "subtype": "savings",
                        "balances": {"current": 5000.0, "available": 5000.0},
                    },
                ]
            }
            tx_result = {
                "added": [
                    {
                        "transaction_id": "prod-tx-1",
                        "account_id": "prod-checking",
                        "date": "2026-09-15",
                        "authorized_date": "2026-09-14",
                        "merchant_name": "Verizon",
                        "name": "VERIZON",
                        "amount": 213.07,
                        "pending": False,
                    }
                ],
                "modified": [],
                "removed": [],
                "next_cursor": "prod-cursor-1",
                "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
            }

            with (
                patch("biweekly_bills.bank_sync.load_settings", return_value=fake_settings),
                patch(
                    "biweekly_bills.production_readiness.assert_production_sync_allowed",
                    return_value=readiness,
                ) as guard,
                patch(
                    "biweekly_bills.bank_sync.require_access_token",
                    return_value="prod-token",
                ),
                patch("biweekly_bills.bank_sync.build_client", return_value=object()),
                patch(
                    "biweekly_bills.bank_sync.get_item",
                    return_value={"item": {"item_id": "prod-item", "error": None}},
                ),
                patch(
                    "biweekly_bills.bank_sync.get_balance",
                    return_value=accounts_payload,
                ),
                patch(
                    "biweekly_bills.bank_sync.sync_transactions",
                    return_value=tx_result,
                ),
                patch(
                    "biweekly_bills.bank_sync.load_local_config",
                    return_value={},
                ),
                patch("biweekly_bills.bank_sync.update_local_config") as update_config,
            ):
                report = sync_production_to_sqlite(db)

            guard.assert_called_once_with(db)
            self.assertEqual(report.environment, "production")
            self.assertEqual(report.item_id, "prod-item")
            self.assertEqual(report.account_count, 2)
            self.assertEqual(report.transaction_count, 1)
            self.assertEqual(report.auto_matched_count, 1)
            self.assertEqual(report.auto_review_count, 0)

            matched_bill = db.get_bill_instance(instance_id)
            assert matched_bill is not None
            self.assertEqual(matched_bill["paid_cents"], 21307)
            self.assertEqual(matched_bill["status"], "Paid")
            self.assertEqual(matched_bill["source"], "bank-reconciled")

            accounts = db.list_bank_accounts("production")
            self.assertEqual(len(accounts), 2)
            self.assertEqual(accounts[0]["environment"], "production")

            transactions = db.list_bank_transactions("production")
            self.assertEqual(len(transactions), 1)
            self.assertEqual(transactions[0]["plaid_transaction_id"], "prod-tx-1")
            self.assertEqual(
                transactions[0]["reconciliation_disposition"],
                "matched",
            )
            self.assertEqual(
                transactions[0]["reconciled_bill_name"],
                "Verizon",
            )

            update_config.assert_called_with(
                "production",
                transactions_cursor="prod-cursor-1",
            )

    def test_production_sync_refuses_item_identity_mismatch_before_sqlite_writes(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            fake_settings = SimpleNamespace(
                environment="production",
                client_id="client",
                secret="secret",
                redirect_uri=None,
                host="127.0.0.1",
                port=8000,
            )
            readiness = SimpleNamespace(production_item_id="locked-prod-item")

            with (
                patch("biweekly_bills.bank_sync.load_settings", return_value=fake_settings),
                patch(
                    "biweekly_bills.production_readiness.assert_production_sync_allowed",
                    return_value=readiness,
                ),
                patch(
                    "biweekly_bills.bank_sync.require_access_token",
                    return_value="prod-token",
                ),
                patch("biweekly_bills.bank_sync.build_client", return_value=object()),
                patch(
                    "biweekly_bills.bank_sync.get_item",
                    return_value={
                        "item": {
                            "item_id": "different-prod-item",
                            "error": None,
                        }
                    },
                ),
                patch("biweekly_bills.bank_sync.get_balance") as balance,
                patch("biweekly_bills.bank_sync.sync_transactions") as transactions,
            ):
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    sync_production_to_sqlite(db)

            balance.assert_not_called()
            transactions.assert_not_called()
            self.assertEqual(db.list_bank_accounts("production"), [])
            self.assertEqual(db.list_bank_transactions("production"), [])

    def test_production_environment_is_refused_before_network_access(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            fake_settings = SimpleNamespace(
                environment="production",
                client_id="client",
                secret="secret",
            )
            with (
                patch("biweekly_bills.bank_sync.load_settings", return_value=fake_settings),
                patch("biweekly_bills.bank_sync.require_access_token") as token,
            ):
                with self.assertRaisesRegex(RuntimeError, "environment mismatch"):
                    sync_sandbox_to_sqlite(db)
                token.assert_not_called()

    def test_bill_suggestion_can_come_from_non_bills_checking(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            db.upsert_bank_account(
                environment="sandbox",
                plaid_account_id="acct-checking",
                name="Checking",
                mask="2113",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=100000,
                available_balance_cents=100000,
                last_synced_at="2026-09-15T12:00:00+00:00",
            )
            db.upsert_bank_transaction(
                environment="sandbox",
                plaid_transaction_id="tx-verizon",
                plaid_account_id="acct-checking",
                posted_date="2026-09-20",
                authorized_date=None,
                merchant_name="Verizon Wireless",
                name="VERIZON",
                amount_cents=21307,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-15T12:00:00+00:00",
            )

            row = db.list_bank_transactions("sandbox")[0]
            self.assertEqual(suggested_bill_for_transaction(row), "Verizon")

            with patch("biweekly_bills.bank_sync.update_local_config"):
                set_sandbox_bills_account(db, "acct-checking")

            row = db.list_bank_transactions("sandbox")[0]
            self.assertEqual(suggested_bill_for_transaction(row), "Verizon")


if __name__ == "__main__":
    unittest.main()
