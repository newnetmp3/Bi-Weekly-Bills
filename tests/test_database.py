from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from biweekly_bills.database import Database


class DatabaseTests(unittest.TestCase):
    def test_database_initializes_and_summarizes(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            verizon_id = db.upsert_bill(
                name="Verizon",
                cycle="1st",
                default_method="Autopay",
                payment_account="Bills Checking",
                funding_account="Bills Checking",
                transfer_required=True,
            )
            db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="1st",
                bill_name="Verizon",
                bill_id=verizon_id,
                due_cents=21307,
                paid_cents=21307,
                method="Autopay",
                status="Paid",
            )

            cox_id = db.upsert_bill(
                name="Cox",
                cycle="15th",
                default_method="Autopay",
                payment_account="Bills Checking",
                funding_account="Bills Checking",
                transfer_required=True,
            )
            db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="15th",
                bill_name="Cox",
                bill_id=cox_id,
                due_cents=12060,
                paid_cents=None,
                method="Autopay",
                status="Due",
            )

            summary = db.month_summary(2026, 9)
            self.assertEqual(summary.bill_count, 2)
            self.assertEqual(summary.paid_count, 1)
            self.assertEqual(summary.due_cents, 33367)
            self.assertEqual(summary.paid_cents, 21307)
            self.assertEqual(summary.remaining_cents, 12060)

            first = db.cycle_summary(2026, 9, "1st")
            self.assertEqual(first.bill_count, 1)
            self.assertEqual(first.remaining_cents, 0)

            rows = db.list_month_instances(2026, 9)
            self.assertEqual(
                [row["bill_name_snapshot"] for row in rows],
                ["Verizon", "Cox"],
            )

    def test_pay_period_instance_can_be_edited_and_cleared(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            bill_id = db.upsert_bill(
                name="USAA",
                cycle="15th",
                payment_account="Bills Checking",
            )
            instance_id = db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="15th",
                bill_name="USAA",
                bill_id=bill_id,
                when_label="15th",
                due_cents=25000,
                paid_cents=25000,
                method="Autopay",
                status="Paid",
                extra_short="0",
            )

            db.update_bill_instance(
                instance_id,
                when_label="16th",
                due_cents=26000,
                paid_cents=None,
                method="Manual",
                status="Due",
                extra_short=None,
                payment_account_snapshot="Bills Checking",
            )

            row = db.get_bill_instance(instance_id)
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["when_label"], "16th")
            self.assertEqual(row["due_cents"], 26000)
            self.assertIsNone(row["paid_cents"])
            self.assertEqual(row["method"], "Manual")
            self.assertEqual(row["status"], "Due")
            self.assertIsNone(row["extra_short"])
            self.assertEqual(row["payment_account_snapshot"], "Bills Checking")
            self.assertEqual(row["source"], "app")

            rows = db.list_cycle_instances(2026, 9, "15th")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["bill_name_snapshot"], "USAA")

    def test_stable_bill_account_assignments_can_be_set_and_cleared(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            for account_id, name, is_bills in (
                ("bills", "Bills", True),
                ("everyday", "Primary Checking", False),
            ):
                db.upsert_bank_account(
                    environment="production",
                    plaid_account_id=account_id,
                    name=name,
                    mask="1111" if is_bills else "2222",
                    account_type="depository",
                    account_subtype="checking",
                    current_balance_cents=10000,
                    available_balance_cents=10000,
                    last_synced_at="2026-09-16T00:00:00+00:00",
                )
            db.set_bills_checking("production", "bills")

            bill_id = db.upsert_bill(
                name="Autopay Bill",
                cycle="1st",
            )
            db.set_bill_account_assignments(
                bill_id,
                payment_account_id="bills",
                transfer_source_account_id="everyday",
                transfer_required=True,
            )

            bill = db.list_bills(active_only=False)[0]
            self.assertEqual(bill["payment_account_id"], "bills")
            self.assertEqual(
                bill["transfer_source_account_id"],
                "everyday",
            )
            self.assertEqual(bill["transfer_required"], 1)

            db.set_bill_account_assignments(
                bill_id,
                payment_account_id=None,
                transfer_source_account_id=None,
                transfer_required=False,
            )
            bill = db.list_bills(active_only=False)[0]
            self.assertIsNone(bill["payment_account_id"])
            self.assertIsNone(bill["transfer_source_account_id"])
            self.assertEqual(bill["transfer_required"], 0)

    def test_payment_account_derives_bills_transfer_rule_automatically(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            for account_id, name, mask, is_bills in (
                ("bills", "Bills", "1111", True),
                ("everyday", "Primary Checking", "2222", False),
                ("mom", "Mom", "2213", False),
            ):
                db.upsert_bank_account(
                    environment="production",
                    plaid_account_id=account_id,
                    name=name,
                    mask=mask,
                    account_type="depository",
                    account_subtype="checking",
                    current_balance_cents=10000,
                    available_balance_cents=10000,
                    last_synced_at="2026-09-16T00:00:00+00:00",
                )
            db.set_bills_checking("production", "bills")
            db.set_default_transfer_source_account(
                "production",
                "everyday",
            )

            bill_id = db.upsert_bill(
                name="Autopay Bill",
                cycle="1st",
            )

            db.set_bill_payment_account(bill_id, "bills")
            bill = db.list_bills(active_only=False)[0]
            self.assertEqual(bill["payment_account_id"], "bills")
            self.assertEqual(
                bill["transfer_source_account_id"],
                "everyday",
            )
            self.assertEqual(bill["transfer_required"], 1)

            db.set_bill_payment_account(bill_id, "mom")
            bill = db.list_bills(active_only=False)[0]
            self.assertEqual(bill["payment_account_id"], "mom")
            self.assertIsNone(bill["transfer_source_account_id"])
            self.assertEqual(bill["transfer_required"], 0)

            db.set_bill_payment_account(bill_id, None)
            bill = db.list_bills(active_only=False)[0]
            self.assertIsNone(bill["payment_account_id"])
            self.assertIsNone(bill["transfer_source_account_id"])
            self.assertEqual(bill["transfer_required"], 0)

    def test_bills_payment_requires_default_transfer_source(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            db.upsert_bank_account(
                environment="production",
                plaid_account_id="bills",
                name="Bills",
                mask="1111",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=10000,
                available_balance_cents=10000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            db.set_bills_checking("production", "bills")
            bill_id = db.upsert_bill(name="Autopay Bill", cycle="1st")

            with self.assertRaisesRegex(
                ValueError,
                "Default Transfer Source",
            ):
                db.set_bill_payment_account(bill_id, "bills")

    def test_transfer_source_cannot_be_bills_destination_or_credit_account(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            db.upsert_bank_account(
                environment="production",
                plaid_account_id="bills",
                name="Bills",
                mask="1111",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=10000,
                available_balance_cents=10000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            db.set_bills_checking("production", "bills")
            db.upsert_bank_account(
                environment="production",
                plaid_account_id="card",
                name="Blue Card",
                mask=None,
                account_type="credit",
                account_subtype="credit card",
                current_balance_cents=10000,
                available_balance_cents=10000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            bill_id = db.upsert_bill(name="Bill", cycle="1st")

            with self.assertRaisesRegex(ValueError, "cannot be its own transfer source"):
                db.set_bill_account_assignments(
                    bill_id,
                    payment_account_id="bills",
                    transfer_source_account_id="bills",
                    transfer_required=True,
                )

            with self.assertRaisesRegex(ValueError, "must be depository"):
                db.set_bill_account_assignments(
                    bill_id,
                    payment_account_id="card",
                    transfer_source_account_id=None,
                    transfer_required=False,
                )

    def test_clear_sandbox_transactions_unwinds_matches_and_leaves_production_untouched(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            bill_id = db.upsert_bill(name="Sandbox Test Bill", cycle="1st")
            instance_id = db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="1st",
                bill_name="Sandbox Test Bill",
                bill_id=bill_id,
                due_cents=5000,
                paid_cents=1000,
                status="Partial",
                source="app",
            )

            for environment, account_id in (
                ("sandbox", "sandbox-checking"),
                ("production", "prod-checking"),
            ):
                db.upsert_bank_account(
                    environment=environment,
                    plaid_account_id=account_id,
                    name=f"{environment} Checking",
                    mask="1111" if environment == "sandbox" else "2222",
                    account_type="depository",
                    account_subtype="checking",
                    current_balance_cents=10000,
                    available_balance_cents=9000,
                    last_synced_at="2026-09-16T00:00:00+00:00",
                )

            db.upsert_bank_transaction(
                environment="sandbox",
                plaid_transaction_id="sandbox-tx",
                plaid_account_id="sandbox-checking",
                posted_date="2026-09-05",
                authorized_date="2026-09-05",
                merchant_name="Sandbox Merchant",
                name="Sandbox Merchant",
                amount_cents=5000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )
            db.reconcile_transaction(
                environment="sandbox",
                plaid_transaction_id="sandbox-tx",
                bill_instance_id=instance_id,
            )
            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id="prod-tx",
                plaid_account_id="prod-checking",
                posted_date="2026-09-06",
                authorized_date="2026-09-06",
                merchant_name="Real Merchant",
                name="Real Merchant",
                amount_cents=2500,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )
            db.update_sync_state(
                environment="sandbox",
                transaction_cursor="sandbox-cursor",
                transactions_update_status="HISTORICAL_UPDATE_COMPLETE",
                last_sync_at="2026-09-16T00:00:00+00:00",
            )

            matched = db.get_bill_instance(instance_id)
            self.assertEqual(matched["paid_cents"], 5000)
            self.assertEqual(matched["source"], "bank-reconciled")

            removed = db.clear_bank_transactions("sandbox")

            self.assertEqual(removed, 1)
            self.assertEqual(db.list_bank_transactions("sandbox"), [])
            self.assertIsNone(db.get_reconciliation("sandbox", "sandbox-tx"))
            self.assertIsNone(db.get_sync_state("sandbox"))
            self.assertEqual(len(db.list_bank_transactions("production")), 1)

            restored = db.get_bill_instance(instance_id)
            self.assertEqual(restored["paid_cents"], 1000)
            self.assertEqual(restored["status"], "Partial")
            self.assertEqual(restored["source"], "app")

    def test_bill_deactivation_preserves_history(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            bill_id = db.upsert_bill(name="Star", cycle="1st", active=True)
            db.upsert_bill_instance(
                year=2026,
                month=8,
                cycle="1st",
                bill_name="Star",
                bill_id=bill_id,
                due_cents=10000,
                paid_cents=10000,
            )

            db.set_bill_active(bill_id, False)

            bills = db.list_bills(active_only=False)
            self.assertEqual(bills[0]["active"], 0)
            rows = db.list_month_instances(2026, 8)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["paid_cents"], 10000)

            active_month_rows = db.list_month_instances(
                2026, 8, active_only=True
            )
            active_cycle_rows = db.list_cycle_instances(
                2026, 8, "1st", active_only=True
            )
            self.assertEqual(active_month_rows, [])
            self.assertEqual(active_cycle_rows, [])

            historical_progress = db.workflow_progress(2026, 8, "1st")
            active_progress = db.workflow_progress(
                2026, 8, "1st", active_only=True
            )
            self.assertEqual(historical_progress.bill_count, 1)
            self.assertEqual(active_progress.bill_count, 0)
            self.assertEqual(active_progress.handled_count, 0)


    def test_manual_paid_checkpoint_does_not_change_financial_state(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            bill_id = db.upsert_bill(name="Manual Bill", cycle="15th")
            instance_id = db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="15th",
                bill_name="Manual Bill",
                bill_id=bill_id,
                due_cents=12345,
                paid_cents=5000,
                method="Website",
                status="Partial",
                source="ods-import",
            )

            db.set_bill_manually_paid(instance_id, True)

            row = db.get_bill_instance(instance_id)
            assert row is not None
            self.assertEqual(row["manually_paid"], 1)
            self.assertIsNotNone(row["manually_paid_at"])
            self.assertEqual(row["paid_cents"], 5000)
            self.assertEqual(row["status"], "Partial")
            self.assertEqual(row["source"], "ods-import")
            self.assertEqual(row["bank_verified"], 0)

            progress = db.workflow_progress(2026, 9, "15th")
            self.assertEqual(progress.bill_count, 1)
            self.assertEqual(progress.handled_count, 1)
            self.assertEqual(progress.manually_paid_count, 1)
            self.assertEqual(progress.bank_verified_count, 0)

            db.set_bill_manually_paid(instance_id, False)
            row = db.get_bill_instance(instance_id)
            assert row is not None
            self.assertEqual(row["manually_paid"], 0)
            self.assertIsNone(row["manually_paid_at"])
            self.assertEqual(row["paid_cents"], 5000)
            self.assertEqual(row["status"], "Partial")

    def test_bank_transaction_period_index_and_scoped_listing(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            for tx_id, posted_date in (
                ("jan", "2026-01-10"),
                ("feb", "2026-02-10"),
                ("feb-two", "2026-02-20"),
            ):
                db.upsert_bank_transaction(
                    environment="production",
                    plaid_transaction_id=tx_id,
                    plaid_account_id=None,
                    posted_date=posted_date,
                    authorized_date=None,
                    merchant_name="Test",
                    name="TEST",
                    amount_cents=1000,
                    pending=False,
                    raw_json="{}",
                    last_seen_at="2026-09-16T00:00:00+00:00",
                )

            self.assertEqual(
                db.bank_transaction_periods("production"),
                [(2026, 2), (2026, 1)],
            )
            feb = db.list_bank_transactions(
                "production",
                limit=100,
                year=2026,
                month=2,
            )
            self.assertEqual(
                {row["plaid_transaction_id"] for row in feb},
                {"feb", "feb-two"},
            )
            with self.assertRaisesRegex(
                ValueError,
                "year and month",
            ):
                db.list_bank_transactions(
                    "production",
                    year=2026,
                )

    def test_bank_reconciliation_counts_as_handled_without_manual_checkbox(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            bill_id = db.upsert_bill(name="Verified Bill", cycle="1st")
            instance_id = db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="1st",
                bill_name="Verified Bill",
                bill_id=bill_id,
                due_cents=8000,
                paid_cents=None,
                status="Due",
            )

            db.upsert_bank_account(
                environment="production",
                plaid_account_id="checking",
                name="Bills",
                mask="1111",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=100000,
                available_balance_cents=100000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id="verified-tx",
                plaid_account_id="checking",
                posted_date="2026-09-03",
                authorized_date=None,
                merchant_name="Verified Bill",
                name="VERIFIED BILL",
                amount_cents=8000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )
            db.reconcile_transaction(
                environment="production",
                plaid_transaction_id="verified-tx",
                bill_instance_id=instance_id,
            )

            row = db.get_bill_instance(instance_id)
            assert row is not None
            self.assertEqual(row["manually_paid"], 0)
            self.assertEqual(row["bank_verified"], 1)

            progress = db.workflow_progress(2026, 9, "1st")
            self.assertEqual(progress.bill_count, 1)
            self.assertEqual(progress.handled_count, 1)
            self.assertEqual(progress.manually_paid_count, 0)
            self.assertEqual(progress.bank_verified_count, 1)

    def test_manual_and_bank_verified_bill_is_only_counted_once_as_handled(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            bill_id = db.upsert_bill(name="Both States", cycle="1st")
            instance_id = db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="1st",
                bill_name="Both States",
                bill_id=bill_id,
                due_cents=4200,
                status="Due",
            )
            db.set_bill_manually_paid(instance_id, True)

            db.upsert_bank_account(
                environment="production",
                plaid_account_id="checking",
                name="Bills",
                mask="1111",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=100000,
                available_balance_cents=100000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id="both-tx",
                plaid_account_id="checking",
                posted_date="2026-09-04",
                authorized_date=None,
                merchant_name="Both States",
                name="BOTH STATES",
                amount_cents=4200,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )
            db.reconcile_transaction(
                environment="production",
                plaid_transaction_id="both-tx",
                bill_instance_id=instance_id,
            )

            progress = db.workflow_progress(2026, 9, "1st")
            self.assertEqual(progress.handled_count, 1)
            self.assertEqual(progress.manually_paid_count, 1)
            self.assertEqual(progress.bank_verified_count, 1)

    def test_bill_aliases_closeout_and_verification_provenance(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()

            bill_id = db.upsert_bill(
                name="Alias Bill",
                cycle="1st",
            )
            db.set_bill_aliases(
                bill_id,
                ["ALIAS MERCHANT", "Alias Merchant", "  SECOND NAME  "],
            )
            self.assertEqual(
                db.list_bill_aliases(bill_id),
                ["ALIAS MERCHANT", "SECOND NAME"],
            )

            instance_id = db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="1st",
                bill_name="Alias Bill",
                bill_id=bill_id,
                due_cents=5000,
                paid_cents=5000,
                status="Paid",
            )
            db.set_bill_manually_paid(instance_id, True)

            closeout = db.month_closeout(2026, 9)
            self.assertEqual(closeout.bill_count, 1)
            self.assertEqual(closeout.handled_count, 1)
            self.assertEqual(closeout.verified_count, 0)
            self.assertEqual(closeout.paid_unverified_count, 1)

            db.upsert_bank_account(
                environment="production",
                plaid_account_id="checking",
                name="Bills",
                mask="1111",
                account_type="depository",
                account_subtype="checking",
                current_balance_cents=50000,
                available_balance_cents=50000,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id="alias-tx",
                plaid_account_id="checking",
                posted_date="2026-09-03",
                authorized_date=None,
                merchant_name="ALIAS MERCHANT",
                name="ALIAS MERCHANT",
                amount_cents=5000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T00:00:00+00:00",
            )
            db.reconcile_transaction(
                environment="production",
                plaid_transaction_id="alias-tx",
                bill_instance_id=instance_id,
            )

            row = db.get_bill_instance(instance_id)
            assert row is not None
            self.assertEqual(row["bank_verified"], 1)
            self.assertEqual(row["bank_transaction_id"], "alias-tx")
            self.assertEqual(row["bank_verified_date"], "2026-09-03")
            self.assertEqual(row["bank_verified_description"], "ALIAS MERCHANT")
            self.assertEqual(row["bank_verified_account_name"], "Bills")
            self.assertEqual(row["bank_verified_account_mask"], "1111")

            closeout = db.month_closeout(2026, 9)
            self.assertEqual(closeout.verified_count, 1)
            self.assertEqual(closeout.paid_unverified_count, 0)


    def test_active_bills_materialize_only_current_and_future_periods(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            bill_id = db.upsert_bill(
                name="New Recurring Bill",
                cycle="Both",
                latest_due="$123.45",
                default_method="Autopay",
            )

            historical = db.materialize_active_bills(
                2026,
                8,
                today=date(2026, 9, 16),
            )
            current = db.materialize_active_bills(
                2026,
                9,
                today=date(2026, 9, 16),
            )
            repeated = db.materialize_active_bills(
                2026,
                9,
                today=date(2026, 9, 16),
            )

            self.assertEqual(historical, 0)
            self.assertEqual(current, 2)
            self.assertEqual(repeated, 0)
            rows = db.list_month_instances(2026, 9)
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["cycle"] for row in rows}, {"1st", "15th"})
            self.assertEqual({row["due_cents"] for row in rows}, {12345})
            self.assertTrue(all(int(row["bill_id"]) == bill_id for row in rows))

    def test_default_transfer_source_is_generic_and_drives_bill_routing(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            for account_id, name, mask in (
                ("bills", "Household Bills", "1111"),
                ("source", "Everyday Checking", "2222"),
            ):
                db.upsert_bank_account(
                    environment="production",
                    plaid_account_id=account_id,
                    name=name,
                    mask=mask,
                    account_type="depository",
                    account_subtype="checking",
                    current_balance_cents=100000,
                    available_balance_cents=100000,
                    last_synced_at="2026-09-16T00:00:00+00:00",
                )

            db.set_bills_checking("production", "bills")
            db.set_default_transfer_source_account(
                "production",
                "source",
            )
            source = db.default_transfer_source_account("production")
            assert source is not None
            self.assertEqual(source["plaid_account_id"], "source")

            bill_id = db.upsert_bill(
                name="Generic Routed Bill",
                cycle="1st",
            )
            db.set_bill_payment_account(bill_id, "bills")
            bill = next(
                row
                for row in db.list_bills()
                if int(row["id"]) == bill_id
            )
            self.assertEqual(bill["payment_account_id"], "bills")
            self.assertEqual(
                bill["transfer_source_account_id"],
                "source",
            )
            self.assertEqual(int(bill["transfer_required"]), 1)

    def test_account_role_changes_update_active_bill_funding_only(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            for account_id, name, mask in (
                ("bills-a", "Bills A", "1001"),
                ("bills-b", "Bills B", "1002"),
                ("source-a", "Source A", "2001"),
                ("source-b", "Source B", "2002"),
            ):
                db.upsert_bank_account(
                    environment="production",
                    plaid_account_id=account_id,
                    name=name,
                    mask=mask,
                    account_type="depository",
                    account_subtype="checking",
                    current_balance_cents=100000,
                    available_balance_cents=100000,
                    last_synced_at="2026-09-16T00:00:00+00:00",
                )

            db.set_bills_checking("production", "bills-a")
            db.set_default_transfer_source_account(
                "production",
                "source-a",
            )
            bill_id = db.upsert_bill(
                name="Routed Bill",
                cycle="1st",
            )
            db.set_bill_payment_account(bill_id, "bills-a")
            instance_id = db.upsert_bill_instance(
                year=2026,
                month=8,
                cycle="1st",
                bill_name="Routed Bill",
                bill_id=bill_id,
                due_cents=5000,
                status="Due",
                source="app",
            )

            db.set_default_transfer_source_account(
                "production",
                "source-b",
            )
            bill = next(
                row
                for row in db.list_bills()
                if int(row["id"]) == bill_id
            )
            self.assertEqual(
                bill["transfer_source_account_id"],
                "source-b",
            )
            self.assertEqual(int(bill["transfer_required"]), 1)

            historical = db.get_bill_instance(instance_id)
            assert historical is not None
            self.assertEqual(historical["paid_cents"], None)
            self.assertEqual(historical["status"], "Due")

            db.set_bills_checking("production", "bills-b")
            bill = next(
                row
                for row in db.list_bills()
                if int(row["id"]) == bill_id
            )
            self.assertEqual(int(bill["transfer_required"]), 0)
            self.assertIsNone(bill["transfer_source_account_id"])

    def test_bills_checking_rejects_nonchecking_account(self):
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "bills.sqlite3")
            db.initialize()
            db.upsert_bank_account(
                environment="production",
                plaid_account_id="card",
                name="Card",
                mask=None,
                account_type="credit",
                account_subtype="credit card",
                current_balance_cents=10000,
                available_balance_cents=None,
                last_synced_at="2026-09-16T00:00:00+00:00",
            )
            with self.assertRaisesRegex(ValueError, "checking account"):
                db.set_bills_checking("production", "card")



if __name__ == "__main__":
    unittest.main()
