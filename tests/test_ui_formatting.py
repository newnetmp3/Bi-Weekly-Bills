import unittest

from biweekly_bills.ui.formatting import schedule_balance, schedule_balance_phrase


class FormattingTests(unittest.TestCase):
    def test_remaining_balance(self):
        self.assertEqual(schedule_balance(25000, 20000), ("Remaining", 5000))
        self.assertEqual(schedule_balance_phrase(25000, 20000), "$50.00 remaining")

    def test_over_scheduled_balance(self):
        self.assertEqual(schedule_balance(232346, 247272), ("Over scheduled", 14926))
        self.assertEqual(
            schedule_balance_phrase(232346, 247272),
            "$149.26 over scheduled",
        )

    def test_settled_balance(self):
        self.assertEqual(schedule_balance(10000, 10000), ("Settled", 0))
        self.assertEqual(schedule_balance_phrase(10000, 10000), "Settled · $0.00")


if __name__ == "__main__":
    unittest.main()
