from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from biweekly_bills.database import Database
from biweekly_bills.funding import build_funding_plan, funding_lines, scheduled_transfer_requirement


class FundingPlannerTests(unittest.TestCase):
    def _database(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Database(Path(temp.name) / "bills.sqlite3")
        db.initialize()
        return db

    def _account(
        self,
        db,
        account_id,
        name,
        mask,
        subtype="checking",
        balance=0,
    ):
        db.upsert_bank_account(
            environment="production",
            plaid_account_id=account_id,
            name=name,
            mask=mask,
            account_type="depository",
            account_subtype=subtype,
            current_balance_cents=balance,
            available_balance_cents=balance,
            last_synced_at="2026-09-15T20:00:00+00:00",
        )

    def test_transfer_required_is_included_independent_of_payment_account(self):
        db = self._database()
        self._account(db, "bills", "Bills", "1111")
        self._account(db, "everyday", "Primary Checking", "2222")
        self._account(db, "savings", "Regular Savings", "4483", subtype="savings")
        db.set_bills_checking("production", "bills")

        autopay = db.upsert_bill(
            name="Autopay Utility",
            cycle="1st",
            payment_account_id="bills",
            transfer_source_account_id="everyday",
            transfer_required=True,
        )
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="1st",
            bill_name="Autopay Utility",
            bill_id=autopay,
            due_cents=10000,
            paid_cents=2500,
        )

        direct_card = db.upsert_bill(
            name="Direct Credit Card",
            cycle="1st",
            payment_account_id="everyday",
            transfer_source_account_id=None,
            transfer_required=False,
        )
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="1st",
            bill_name="Direct Credit Card",
            bill_id=direct_card,
            due_cents=30000,
            paid_cents=0,
        )

        explicit_transfer = db.upsert_bill(
            name="Everyday-paid but transfer-funded",
            cycle="15th",
            payment_account_id="everyday",
            transfer_source_account_id="savings",
            transfer_required=True,
        )
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="15th",
            bill_name="Everyday-paid but transfer-funded",
            bill_id=explicit_transfer,
            due_cents=9000,
            paid_cents=None,
        )

        lines, review = funding_lines(db, 2026, 9)

        self.assertEqual(
            [line.bill_name for line in lines],
            ["Autopay Utility", "Everyday-paid but transfer-funded"],
        )
        self.assertEqual(lines[0].remaining_cents, 7500)
        self.assertEqual(lines[1].remaining_cents, 9000)
        self.assertEqual(lines[1].payment_account_id, "everyday")
        self.assertEqual(lines[1].transfer_source_account_id, "savings")
        self.assertEqual(review, 0)

    def test_current_bills_balance_is_allocated_first_then_fifteenth(self):
        db = self._database()
        self._account(db, "bills-account", "Bills", "1111", balance=8000)
        self._account(db, "everyday", "Primary Checking", "2222")
        db.set_bills_checking("production", "bills-account")

        first_bill = db.upsert_bill(
            name="First Bill",
            cycle="1st",
            payment_account_id="bills-account",
            transfer_source_account_id="everyday",
            transfer_required=True,
        )
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="1st",
            bill_name="First Bill",
            bill_id=first_bill,
            due_cents=10000,
            paid_cents=None,
        )

        fifteenth_bill = db.upsert_bill(
            name="Fifteenth Bill",
            cycle="15th",
            payment_account_id="bills-account",
            transfer_source_account_id="everyday",
            transfer_required=True,
        )
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="15th",
            bill_name="Fifteenth Bill",
            bill_id=fifteenth_bill,
            due_cents=20000,
            paid_cents=5000,
        )

        plan = build_funding_plan(
            db,
            2026,
            9,
            today=date(2026, 9, 15),
        )

        self.assertEqual(plan.first_required_cents, 10000)
        self.assertEqual(plan.fifteenth_required_cents, 15000)
        self.assertEqual(plan.total_required_cents, 25000)
        self.assertEqual(plan.available_balance_cents, 8000)
        self.assertTrue(plan.balance_applied)
        self.assertEqual(plan.first_transfer_cents, 2000)
        self.assertEqual(plan.fifteenth_transfer_cents, 15000)
        self.assertEqual(plan.total_transfer_cents, 17000)

    def test_non_current_month_does_not_apply_todays_bank_balance(self):
        db = self._database()
        self._account(db, "bills-account", "Bills", "1111", balance=50000)
        self._account(db, "everyday", "Primary Checking", "2222")
        db.set_bills_checking("production", "bills-account")

        bill_id = db.upsert_bill(
            name="Future Autopay",
            cycle="15th",
            payment_account_id="bills-account",
            transfer_source_account_id="everyday",
            transfer_required=True,
        )
        db.upsert_bill_instance(
            year=2026,
            month=10,
            cycle="15th",
            bill_name="Future Autopay",
            bill_id=bill_id,
            due_cents=12000,
        )

        plan = build_funding_plan(
            db,
            2026,
            10,
            today=date(2026, 9, 15),
        )
        self.assertFalse(plan.balance_applied)
        self.assertEqual(plan.fifteenth_transfer_cents, 12000)
        self.assertEqual(plan.total_transfer_cents, 12000)

    def test_sandbox_accounts_do_not_activate_real_funding_planner(self):
        db = self._database()

        bill_id = db.upsert_bill(
            name="Sandbox-only Autopay",
            cycle="1st",
            payment_account_id="sandbox-bills",
            transfer_source_account_id="sandbox-everyday",
            transfer_required=True,
        )
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="1st",
            bill_name="Sandbox-only Autopay",
            bill_id=bill_id,
            due_cents=10000,
        )

        db.upsert_bank_account(
            environment="sandbox",
            plaid_account_id="sandbox-bills",
            name="Checking",
            mask="0157",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=5000,
            available_balance_cents=5000,
            last_synced_at="2026-09-15T20:00:00+00:00",
        )
        db.set_bills_checking("sandbox", "sandbox-bills")

        plan = build_funding_plan(
            db,
            2026,
            9,
            today=date(2026, 9, 15),
        )

        self.assertIsNone(plan.environment)
        self.assertIsNone(plan.available_balance_cents)
        self.assertEqual(plan.total_required_cents, 0)
        self.assertEqual(plan.total_transfer_cents, 0)
        self.assertEqual(plan.review_bill_count, 0)

    def test_source_breakdown_groups_transfer_sources(self):
        db = self._database()
        self._account(db, "prod-bills", "Bills", "1111")
        self._account(db, "everyday", "Primary Checking", "2222")
        self._account(db, "savings", "Regular Savings", "4483", subtype="savings")
        db.set_bills_checking("production", "prod-bills")

        for name, source_id, cents in (
            ("Bill A", "everyday", 10000),
            ("Bill B", "everyday", 5000),
            ("Bill C", "savings", 2500),
        ):
            bill_id = db.upsert_bill(
                name=name,
                cycle="1st",
                payment_account_id="prod-bills",
                transfer_source_account_id=source_id,
                transfer_required=True,
            )
            db.upsert_bill_instance(
                year=2026,
                month=9,
                cycle="1st",
                bill_name=name,
                bill_id=bill_id,
                due_cents=cents,
            )

        plan = build_funding_plan(db, 2026, 9, today=date(2026, 9, 15))
        self.assertEqual(
            dict(plan.source_totals),
            {
                "Regular Savings ••••4483": 2500,
                "Primary Checking ••••2222": 15000,
            },
        )

    def test_inactive_bills_are_excluded_from_live_and_scheduled_funding(self):
        db = self._database()
        self._account(db, "prod-bills", "Bills", "1111")
        self._account(db, "everyday", "Primary Checking", "2222")
        db.set_bills_checking("production", "prod-bills")

        bill_id = db.upsert_bill(
            name="Cancelled Autopay",
            cycle="1st",
            payment_account_id="prod-bills",
            transfer_source_account_id="everyday",
            transfer_required=True,
        )
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="1st",
            bill_name="Cancelled Autopay",
            bill_id=bill_id,
            due_cents=25000,
        )
        db.set_bill_active(bill_id, False)

        lines, review = funding_lines(db, 2026, 9)
        plan = build_funding_plan(
            db,
            2026,
            9,
            today=date(2026, 9, 15),
        )
        scheduled = scheduled_transfer_requirement(
            db,
            2026,
            9,
            "month",
        )

        self.assertEqual(lines, [])
        self.assertEqual(review, 0)
        self.assertEqual(plan.total_required_cents, 0)
        self.assertEqual(plan.total_transfer_cents, 0)
        self.assertEqual(scheduled, 0)

    def test_missing_transfer_source_is_included_but_flagged_for_review(self):
        db = self._database()
        self._account(db, "prod-bills", "Bills", "1111")
        db.set_bills_checking("production", "prod-bills")

        bill_id = db.upsert_bill(
            name="Needs source",
            cycle="1st",
            payment_account_id="prod-bills",
            transfer_source_account_id=None,
            transfer_required=True,
        )
        db.upsert_bill_instance(
            year=2026,
            month=9,
            cycle="1st",
            bill_name="Needs source",
            bill_id=bill_id,
            due_cents=5000,
        )

        lines, review = funding_lines(db, 2026, 9)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].remaining_cents, 5000)
        self.assertEqual(review, 1)


if __name__ == "__main__":
    unittest.main()
