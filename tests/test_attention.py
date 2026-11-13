"""Overview's 'Needs attention' list."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from biweekly_bills.attention import attention_items
from biweekly_bills.database import Database


class AttentionTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Database(Path(temp.name) / "bills.sqlite3")
        self.db.initialize()
        self.today = date(2026, 11, 13)

    def _bill(self, name, cycle="1st", month=11, **instance):
        bill_id = self.db.upsert_bill(name=name, cycle=cycle)
        return self.db.upsert_bill_instance(
            year=2026, month=month, cycle=cycle, bill_name=name,
            bill_id=bill_id, **instance,
        )

    def test_bills_to_pay_and_missing_bank_payment(self):
        self._bill("Power", due_cents=12000, status="Due")
        last_month = self._bill("Water", month=10, due_cents=4000, status="Due")
        self.db.mark_bill_paid(last_month, True)

        items = attention_items(self.db, today=self.today, connection_action="sync")
        texts = " | ".join(item.text for item in items)
        self.assertIn("1 bill(s) still to pay in the 1st period ($120.00): Power.", texts)
        self.assertIn("from October are marked paid but no matching bank payment", texts)
        targets = {item.target for item in items}
        self.assertIn("pay_periods:1st", targets)
        self.assertIn("recheck", targets)

    def test_bank_connection_problem_comes_first(self):
        items = attention_items(self.db, today=self.today, connection_action="configure")
        self.assertEqual(items[0].target, "settings")

    def test_bill_without_payment_account_is_flagged(self):
        self._bill("Gym", due_cents=3000, status="Autopay")
        items = attention_items(self.db, today=self.today, connection_action="sync")
        self.assertTrue(any(item.target == "bills" and "Gym" in item.text for item in items))

    def test_nothing_to_do(self):
        paid = self._bill("Power", due_cents=12000, status="Due")
        self.db.mark_bill_paid(paid, True)
        self.db.upsert_bank_account(
            environment="production", plaid_account_id="a", name="A", mask="1",
            account_type="depository", account_subtype="checking",
            current_balance_cents=0, available_balance_cents=0,
            last_synced_at="2026-11-13T00:00:00+00:00",
        )
        self.db.upsert_bill(name="Power", cycle="1st", payment_account_id="a")
        items = [
            item for item in attention_items(self.db, today=self.today, connection_action="sync")
            if item.target != "sync"
        ]
        self.assertEqual(items, [], [item.text for item in items])


class PeriodFundingStatusTests(unittest.TestCase):
    def test_assigned_and_unassigned_transfers(self):
        from biweekly_bills.funding_transfer import period_funding_status

        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "bills.sqlite3")
            db.initialize()
            db.upsert_bank_account(
                environment="production", plaid_account_id="bills", name="Bills", mask="7198",
                account_type="depository", account_subtype="checking",
                current_balance_cents=0, available_balance_cents=0,
                last_synced_at="2026-10-16T00:00:00+00:00",
            )
            db.set_bills_checking("production", "bills")
            for tx_id, day, amount in (("sep", "2026-09-15", -89015), ("oct", "2026-10-15", -89034)):
                db.upsert_bank_transaction(
                    environment="production", plaid_transaction_id=tx_id,
                    plaid_account_id="bills", posted_date=day, authorized_date=None,
                    merchant_name=None, name="Transfer from Checking", amount_cents=amount,
                    pending=False, raw_json="{}", last_seen_at=f"{day}T00:00:00+00:00",
                )
            db.validate_funding_transfer(
                environment="production", plaid_transaction_id="sep", year=2026, month=9,
                scope="15th", expected_cents=89015, actual_cents=89015,
            )
            september = period_funding_status(db, "production", 2026, 9, "15th")
            self.assertEqual(september.assigned_total, 89015)
            self.assertEqual(september.unassigned, ())
            october = period_funding_status(db, "production", 2026, 10, "15th")
            self.assertEqual(october.assigned, ())
            self.assertEqual(october.unassigned[0][1], 89034)


if __name__ == "__main__":
    unittest.main()
