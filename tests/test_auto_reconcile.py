from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from biweekly_bills.auto_reconcile import (
    auto_reconcile_transactions,
    best_review_candidates,
    reconcile_history,
)
from biweekly_bills.database import Database


class AutoReconcileTests(unittest.TestCase):
    def _db(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Database(Path(temp.name) / "bills.sqlite3")
        db.initialize()
        return db

    def _bill(
        self,
        db: Database,
        *,
        name: str,
        due_cents: int,
        cycle: str = "15th",
        active: bool = True,
        payment_account_id: str | None = None,
        paid_cents: int | None = None,
        status: str = "Due",
    ) -> int:
        bill_id = db.upsert_bill(
            name=name,
            cycle=cycle,
            active=active,
            payment_account_id=payment_account_id,
        )
        return db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle=cycle,
            bill_name=name,
            bill_id=bill_id,
            due_cents=due_cents,
            paid_cents=paid_cents,
            status=status,
        )

    def _tx(
        self,
        db: Database,
        *,
        tx_id: str,
        merchant: str,
        amount_cents: int,
        posted_date: str = "2026-09-14",
        pending: bool = False,
        account_id: str | None = None,
    ) -> None:
        db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id=tx_id,
            plaid_account_id=account_id,
            posted_date=posted_date,
            authorized_date=None,
            merchant_name=merchant,
            name=merchant.upper(),
            amount_cents=amount_cents,
            pending=pending,
            raw_json="{}",
            last_seen_at="2026-09-16T00:00:00+00:00",
        )

    def _account(
        self,
        db: Database,
        *,
        account_id: str,
        name: str,
        account_type: str,
        account_subtype: str,
        mask: str,
    ) -> None:
        db.upsert_bank_account(
            environment="production",
            plaid_account_id=account_id,
            name=name,
            mask=mask,
            account_type=account_type,
            account_subtype=account_subtype,
            current_balance_cents=100000,
            available_balance_cents=100000,
            last_synced_at="2026-09-16T00:00:00+00:00",
        )

    def test_bulk_review_reuses_month_instances_and_aliases(self):
        db = self._db()
        bill_id = db.upsert_bill(
            name="Power Utility",
            cycle="15th",
        )
        db.set_bill_aliases(
            bill_id,
            ["ELECTRIC PAYMENT"],
        )
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="15th",
            bill_name="Power Utility",
            bill_id=bill_id,
            due_cents=10000,
            status="Due",
        )
        for tx_id, amount in (
            ("tx-review-a", 12000),
            ("tx-review-b", 12500),
        ):
            self._tx(
                db,
                tx_id=tx_id,
                merchant="ELECTRIC PAYMENT",
                amount_cents=amount,
            )

        rows = db.list_bank_transactions(
            "production",
            limit=100,
            year=2026,
            month=9,
        )
        with patch.object(
            db,
            "list_month_instances",
            wraps=db.list_month_instances,
        ) as month_rows, patch.object(
            db,
            "list_bill_aliases",
            wraps=db.list_bill_aliases,
        ) as aliases:
            results = best_review_candidates(
                db,
                rows,
                active_only=False,
            )

        self.assertEqual(set(results), {"tx-review-a", "tx-review-b"})
        self.assertEqual(month_rows.call_count, 1)
        aliases.assert_not_called()

    def test_internal_credit_line_payment_pairs_both_bank_sides(self):
        db = self._db()
        self._account(
            db,
            account_id="acct-payment",
            name="Bills Checking",
            account_type="depository",
            account_subtype="checking",
            mask="1111",
        )
        self._account(
            db,
            account_id="acct-dads-overdraft",
            name="Dad's Overdraft",
            account_type="credit",
            account_subtype="line of credit",
            mask="4455",
        )
        instance_id = self._bill(
            db,
            name="Dad's Overdraft",
            due_cents=25000,
            payment_account_id="acct-payment",
        )
        self._tx(
            db,
            tx_id="tx-overdraft-out",
            merchant="Internal Transfer",
            amount_cents=25000,
            posted_date="2026-09-15",
            account_id="acct-payment",
        )
        self._tx(
            db,
            tx_id="tx-overdraft-in",
            merchant="Payment Received",
            amount_cents=-25000,
            posted_date="2026-09-16",
            account_id="acct-dads-overdraft",
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 1)
        self.assertEqual(report.internal_transfer_count, 1)
        self.assertEqual(report.review_count, 0)
        self.assertEqual(report.matches[0].match_kind, "internal-transfer")
        self.assertEqual(
            report.matches[0].evidence_transaction_id,
            "tx-overdraft-in",
        )

        bill = db.get_bill_instance(instance_id)
        assert bill is not None
        self.assertEqual(bill["paid_cents"], 25000)
        self.assertEqual(bill["status"], "Paid")
        self.assertEqual(bill["source"], "bank-reconciled")

        source = db.get_bank_transaction(
            "production",
            "tx-overdraft-out",
        )
        destination = db.get_bank_transaction(
            "production",
            "tx-overdraft-in",
        )
        assert source is not None
        assert destination is not None
        self.assertEqual(source["reconciliation_disposition"], "matched")
        self.assertEqual(source["internal_transfer_role"], "source")
        self.assertEqual(
            source["internal_transfer_bill_name"],
            "Dad's Overdraft",
        )
        self.assertEqual(
            destination["internal_transfer_role"],
            "destination",
        )
        self.assertEqual(
            destination["internal_transfer_bill_name"],
            "Dad's Overdraft",
        )

        # Undo is symmetric: selecting the incoming credit-line side must
        # unwind the outgoing bill match and restore both transactions.
        db.undo_reconciliation("production", "tx-overdraft-in")

        restored = db.get_bill_instance(instance_id)
        assert restored is not None
        self.assertIsNone(restored["paid_cents"])
        self.assertEqual(restored["status"], "Due")
        self.assertIsNone(
            db.get_reconciliation(
                "production",
                "tx-overdraft-out",
            )
        )
        source = db.get_bank_transaction(
            "production",
            "tx-overdraft-out",
        )
        destination = db.get_bank_transaction(
            "production",
            "tx-overdraft-in",
        )
        assert source is not None
        assert destination is not None
        self.assertIsNone(source["internal_transfer_role"])
        self.assertIsNone(destination["internal_transfer_role"])

    def test_internal_credit_payment_requires_configured_payment_account(self):
        db = self._db()
        self._account(
            db,
            account_id="acct-payment",
            name="Bills Checking",
            account_type="depository",
            account_subtype="checking",
            mask="1111",
        )
        self._account(
            db,
            account_id="acct-wrong-source",
            name="Other Checking",
            account_type="depository",
            account_subtype="checking",
            mask="2222",
        )
        self._account(
            db,
            account_id="acct-dads-overdraft",
            name="Dad's Overdraft",
            account_type="loan",
            account_subtype="line of credit",
            mask="4455",
        )
        instance_id = self._bill(
            db,
            name="Dad's Overdraft",
            due_cents=25000,
            payment_account_id="acct-payment",
        )
        self._tx(
            db,
            tx_id="tx-wrong-source",
            merchant="Internal Transfer",
            amount_cents=25000,
            account_id="acct-wrong-source",
        )
        self._tx(
            db,
            tx_id="tx-overdraft-in",
            merchant="Payment Received",
            amount_cents=-25000,
            account_id="acct-dads-overdraft",
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 0)
        self.assertEqual(report.internal_transfer_count, 0)
        bill = db.get_bill_instance(instance_id)
        assert bill is not None
        self.assertIsNone(bill["paid_cents"])

    def test_duplicate_internal_transfer_sources_are_left_for_review(self):
        db = self._db()
        self._account(
            db,
            account_id="acct-payment",
            name="Bills Checking",
            account_type="depository",
            account_subtype="checking",
            mask="1111",
        )
        self._account(
            db,
            account_id="acct-card",
            name="Family Visa",
            account_type="credit",
            account_subtype="credit card",
            mask="9911",
        )
        self._bill(
            db,
            name="Family Visa",
            due_cents=18000,
            payment_account_id="acct-payment",
        )
        for tx_id, posted_date in (
            ("tx-card-out-1", "2026-09-14"),
            ("tx-card-out-2", "2026-09-15"),
        ):
            self._tx(
                db,
                tx_id=tx_id,
                merchant="Internal Transfer",
                amount_cents=18000,
                posted_date=posted_date,
                account_id="acct-payment",
            )
        self._tx(
            db,
            tx_id="tx-card-in",
            merchant="Payment Received",
            amount_cents=-18000,
            posted_date="2026-09-15",
            account_id="acct-card",
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 0)
        self.assertEqual(report.internal_transfer_count, 0)
        self.assertGreaterEqual(report.review_count, 1)

    def test_removed_internal_transfer_evidence_unwinds_bill_match(self):
        db = self._db()
        self._account(
            db,
            account_id="acct-payment",
            name="Bills Checking",
            account_type="depository",
            account_subtype="checking",
            mask="1111",
        )
        self._account(
            db,
            account_id="acct-card",
            name="Family Visa",
            account_type="credit",
            account_subtype="credit card",
            mask="9911",
        )
        instance_id = self._bill(
            db,
            name="Family Visa",
            due_cents=18000,
            payment_account_id="acct-payment",
        )
        self._tx(
            db,
            tx_id="tx-card-out",
            merchant="Internal Transfer",
            amount_cents=18000,
            account_id="acct-payment",
        )
        self._tx(
            db,
            tx_id="tx-card-in",
            merchant="Payment Received",
            amount_cents=-18000,
            account_id="acct-card",
        )
        report = auto_reconcile_transactions(db, "production")
        self.assertEqual(report.internal_transfer_count, 1)

        db.delete_bank_transaction("production", "tx-card-in")

        bill = db.get_bill_instance(instance_id)
        assert bill is not None
        self.assertIsNone(bill["paid_cents"])
        self.assertEqual(bill["status"], "Due")
        self.assertIsNone(
            db.get_reconciliation("production", "tx-card-out")
        )
        source = db.get_bank_transaction(
            "production",
            "tx-card-out",
        )
        assert source is not None
        self.assertIsNone(source["internal_transfer_role"])

    def test_exact_name_and_amount_auto_match_even_across_pay_period_boundary(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="Verizon",
            due_cents=21307,
            cycle="15th",
        )
        self._tx(
            db,
            tx_id="tx-verizon",
            merchant="Verizon Wireless",
            amount_cents=21307,
            posted_date="2026-09-14",
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 1)
        self.assertEqual(report.review_count, 0)
        self.assertEqual(report.matches[0].bill_instance_id, instance_id)

        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertEqual(instance["paid_cents"], 21307)
        self.assertEqual(instance["status"], "Paid")
        self.assertEqual(instance["source"], "bank-reconciled")

        tx = db.get_bank_transaction("production", "tx-verizon")
        assert tx is not None
        self.assertEqual(tx["reconciliation_disposition"], "matched")
        self.assertEqual(tx["reconciled_bill_name"], "Verizon")

    def test_paid_bills_verify_from_generic_outgoing_configured_payment_account(self):
        db = self._db()
        self._account(
            db,
            account_id="acct-bills",
            name="Bills Checking",
            account_type="depository",
            account_subtype="checking",
            mask="1111",
        )
        expected = {
            "Star Card": 12750,
            "Family Card": 18321,
        }
        instance_ids = {}
        for name, amount in expected.items():
            instance_id = self._bill(
                db,
                name=name,
                due_cents=amount,
                paid_cents=amount,
                status="Paid",
                payment_account_id="acct-bills",
            )
            db.set_bill_manually_paid(instance_id, True)
            instance_ids[name] = instance_id
            self._tx(
                db,
                tx_id=f"tx-{name.lower().replace(' ', '-')}",
                merchant="ACH PAYMENT",
                amount_cents=amount,
                account_id="acct-bills",
            )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 2)
        self.assertEqual(report.review_count, 0)
        self.assertEqual(
            {match.match_kind for match in report.matches},
            {"payment-account"},
        )
        self.assertEqual(
            {match.bill_name for match in report.matches},
            set(expected),
        )
        for name, instance_id in instance_ids.items():
            bill = db.get_bill_instance(instance_id)
            assert bill is not None
            self.assertEqual(bill["bank_verified"], 1, name)
            self.assertEqual(
                bill["paid_cents"],
                expected[name],
                name,
            )

    def test_named_merchant_is_not_suggested_as_connected_credit_bill(self):
        db = self._db()
        self._account(
            db,
            account_id="acct-bills",
            name="Bills Checking",
            account_type="depository",
            account_subtype="checking",
            mask="1111",
        )
        self._account(
            db,
            account_id="acct-green-card",
            name="Green Card",
            account_type="credit",
            account_subtype="credit card",
            mask="",
        )
        instance_id = self._bill(
            db,
            name="Green Card",
            due_cents=23741,
            paid_cents=23741,
            status="Paid",
            payment_account_id="acct-bills",
        )
        db.set_bill_manually_paid(instance_id, True)
        self._tx(
            db,
            tx_id="tx-target",
            merchant="Target",
            amount_cents=23741,
            account_id="acct-bills",
        )

        row = db.get_bank_transaction("production", "tx-target")
        assert row is not None
        review, ambiguous = best_review_candidates(
            db,
            [row],
            active_only=False,
        )["tx-target"]
        self.assertIsNone(review)
        self.assertFalse(ambiguous)

        report = auto_reconcile_transactions(db, "production")
        self.assertEqual(report.matched_count, 0)
        bill = db.get_bill_instance(instance_id)
        assert bill is not None
        self.assertEqual(bill["bank_verified"], 0)

    def test_generic_ach_can_still_verify_connected_credit_bill_by_account(self):
        db = self._db()
        self._account(
            db,
            account_id="acct-bills",
            name="Bills Checking",
            account_type="depository",
            account_subtype="checking",
            mask="1111",
        )
        self._account(
            db,
            account_id="acct-green-card",
            name="Green Card",
            account_type="credit",
            account_subtype="credit card",
            mask="",
        )
        instance_id = self._bill(
            db,
            name="Green Card",
            due_cents=23741,
            paid_cents=23741,
            status="Paid",
            payment_account_id="acct-bills",
        )
        db.set_bill_manually_paid(instance_id, True)
        self._tx(
            db,
            tx_id="tx-generic-credit-payment",
            merchant="ACH PAYMENT",
            amount_cents=23741,
            account_id="acct-bills",
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 1)
        self.assertEqual(report.matches[0].bill_name, "Green Card")
        self.assertEqual(report.matches[0].match_kind, "payment-account")
        bill = db.get_bill_instance(instance_id)
        assert bill is not None
        self.assertEqual(bill["bank_verified"], 1)

    def test_wrong_account_does_not_verify_paid_bill_even_with_matching_name(self):
        db = self._db()
        self._account(
            db,
            account_id="acct-bills",
            name="Bills Checking",
            account_type="depository",
            account_subtype="checking",
            mask="1111",
        )
        self._account(
            db,
            account_id="acct-other",
            name="Other Checking",
            account_type="depository",
            account_subtype="checking",
            mask="2222",
        )
        instance_id = self._bill(
            db,
            name="Star Card",
            due_cents=12750,
            paid_cents=12750,
            status="Paid",
            payment_account_id="acct-bills",
        )
        db.set_bill_manually_paid(instance_id, True)
        self._tx(
            db,
            tx_id="tx-star-wrong-account",
            merchant="Star Card",
            amount_cents=12750,
            account_id="acct-other",
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 0)
        bill = db.get_bill_instance(instance_id)
        assert bill is not None
        self.assertEqual(bill["bank_verified"], 0)

    def test_same_account_same_amount_paid_bills_are_left_for_review(self):
        db = self._db()
        self._account(
            db,
            account_id="acct-bills",
            name="Bills Checking",
            account_type="depository",
            account_subtype="checking",
            mask="1111",
        )
        for name in ("Card One", "Card Two"):
            instance_id = self._bill(
                db,
                name=name,
                due_cents=15000,
                paid_cents=15000,
                status="Paid",
                payment_account_id="acct-bills",
            )
            db.set_bill_manually_paid(instance_id, True)
        self._tx(
            db,
            tx_id="tx-generic-payment",
            merchant="PAYMENT",
            amount_cents=15000,
            account_id="acct-bills",
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 0)
        self.assertGreaterEqual(report.review_count, 1)

    def test_historical_paid_amount_is_preferred_over_due_for_direct_match(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="Variable Card",
            due_cents=15000,
            paid_cents=23741,
            status="Paid",
        )
        db.set_bill_manually_paid(instance_id, True)
        self._tx(
            db,
            tx_id="tx-variable-card",
            merchant="Variable Card",
            amount_cents=23741,
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 1)
        self.assertEqual(report.review_count, 0)
        match = report.matches[0]
        self.assertEqual(match.expected_cents, 23741)
        self.assertEqual(match.difference_cents, 0)

        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertEqual(instance["bank_verified"], 1)
        self.assertEqual(instance["paid_cents"], 23741)
        self.assertEqual(instance["source"], "bank-reconciled")

    def test_historical_paid_amount_is_preferred_for_internal_credit_match(self):
        db = self._db()
        self._account(
            db,
            account_id="acct-payment",
            name="Bills Checking",
            account_type="depository",
            account_subtype="checking",
            mask="1111",
        )
        self._account(
            db,
            account_id="acct-green-card",
            name="Green Card",
            account_type="credit",
            account_subtype="credit card",
            mask="",
        )
        instance_id = self._bill(
            db,
            name="Green Card",
            due_cents=15000,
            paid_cents=23741,
            status="Paid",
            payment_account_id="acct-payment",
        )
        db.set_bill_manually_paid(instance_id, True)
        self._tx(
            db,
            tx_id="tx-green-out",
            merchant="Transfer",
            amount_cents=23741,
            posted_date="2026-09-15",
            account_id="acct-payment",
        )
        self._tx(
            db,
            tx_id="tx-green-in",
            merchant="NFO PAYMENT RECEIVED",
            amount_cents=-23741,
            posted_date="2026-09-16",
            account_id="acct-green-card",
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 1)
        self.assertEqual(report.internal_transfer_count, 1)
        self.assertEqual(report.matches[0].expected_cents, 23741)

        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertEqual(instance["bank_verified"], 1)
        self.assertEqual(instance["paid_cents"], 23741)

    def test_partial_paid_amount_does_not_replace_due_as_expected_amount(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="Installment",
            due_cents=10000,
            paid_cents=4000,
            status="Partial",
        )
        self._tx(
            db,
            tx_id="tx-installment",
            merchant="Installment",
            amount_cents=10000,
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 1)
        self.assertEqual(report.matches[0].expected_cents, 10000)
        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertEqual(instance["paid_cents"], 10000)

    def test_small_amount_drift_can_auto_match_with_strong_name(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="City Water",
            due_cents=10000,
        )
        self._tx(
            db,
            tx_id="tx-water",
            merchant="City Water Utility",
            amount_cents=10100,
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 1)
        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertEqual(instance["paid_cents"], 10100)

    def test_amount_outside_safe_tolerance_is_not_auto_matched(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="City Water",
            due_cents=10000,
        )
        self._tx(
            db,
            tx_id="tx-water-high",
            merchant="City Water Utility",
            amount_cents=11200,
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 0)
        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertIsNone(instance["paid_cents"])

    def test_inactive_bill_is_never_auto_matched(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="Old Service",
            due_cents=5000,
            active=False,
        )
        self._tx(
            db,
            tx_id="tx-old-service",
            merchant="Old Service",
            amount_cents=5000,
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 0)
        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertIsNone(instance["paid_cents"])

    def test_two_transactions_competing_for_same_bill_are_left_for_review(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="Internet Service",
            due_cents=7500,
        )
        self._tx(
            db,
            tx_id="tx-internet-1",
            merchant="Internet Service",
            amount_cents=7500,
        )
        self._tx(
            db,
            tx_id="tx-internet-2",
            merchant="Internet Service",
            amount_cents=7500,
            posted_date="2026-09-15",
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 0)
        self.assertEqual(report.review_count, 2)
        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertIsNone(instance["paid_cents"])

    def test_two_plausible_bills_are_left_for_review(self):
        db = self._db()
        self._bill(
            db,
            name="City Water",
            due_cents=10000,
        )
        self._bill(
            db,
            name="City Water Service",
            due_cents=10000,
            cycle="1st",
        )
        self._tx(
            db,
            tx_id="tx-city-water",
            merchant="City Water Service",
            amount_cents=10000,
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 0)
        self.assertEqual(report.review_count, 1)

    def test_abbreviation_can_match_expanded_bank_name(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="NFCU",
            due_cents=25000,
        )
        self._tx(
            db,
            tx_id="tx-nfcu",
            merchant="Navy Federal Credit Union",
            amount_cents=25000,
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 1)
        self.assertEqual(report.matches[0].bill_instance_id, instance_id)

    def test_custom_merchant_alias_participates_in_auto_matching(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="Household Internet",
            due_cents=8999,
        )
        bill = db.get_bill_instance(instance_id)
        assert bill is not None
        db.set_bill_aliases(int(bill["bill_id"]), ["ISP AUTOPAY 4821"])
        self._tx(
            db,
            tx_id="tx-isp-alias",
            merchant="ISP AUTOPAY 4821",
            amount_cents=8999,
        )

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.matched_count, 1)
        self.assertEqual(report.matches[0].bill_instance_id, instance_id)

    def test_history_rescan_can_match_inactive_historical_bill(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="Retired Utility",
            due_cents=6400,
            active=False,
        )
        self._tx(
            db,
            tx_id="tx-retired-utility",
            merchant="Retired Utility",
            amount_cents=6400,
        )

        normal = auto_reconcile_transactions(db, "production")
        self.assertEqual(normal.matched_count, 0)

        history = reconcile_history(
            db,
            "production",
            today=date(2026, 10, 1),
        )

        self.assertEqual(history.matched_count, 1)
        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertEqual(instance["bank_verified"], 1)

    def test_history_rescan_does_not_touch_current_month(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="Current Month Bill",
            due_cents=7700,
        )
        self._tx(
            db,
            tx_id="tx-current-month",
            merchant="Current Month Bill",
            amount_cents=7700,
        )

        history = reconcile_history(
            db,
            "production",
            today=date(2026, 9, 16),
        )

        self.assertEqual(history.matched_count, 0)
        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertEqual(instance["bank_verified"], 0)

    def test_existing_ignored_reconciliation_is_respected(self):
        db = self._db()
        instance_id = self._bill(
            db,
            name="Streaming Service",
            due_cents=1999,
        )
        self._tx(
            db,
            tx_id="tx-streaming",
            merchant="Streaming Service",
            amount_cents=1999,
        )
        db.ignore_transaction("production", "tx-streaming")

        report = auto_reconcile_transactions(db, "production")

        self.assertEqual(report.scanned_count, 0)
        self.assertEqual(report.matched_count, 0)
        instance = db.get_bill_instance(instance_id)
        assert instance is not None
        self.assertIsNone(instance["paid_cents"])


if __name__ == "__main__":
    unittest.main()
