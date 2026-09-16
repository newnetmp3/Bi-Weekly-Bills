import unittest

from biweekly_bills.bank_data import extract_accounts, extract_transactions, find_bill_matches


class BankDataTests(unittest.TestCase):
    def test_extract_payloads(self):
        self.assertEqual(extract_accounts({"accounts": [{"account_id": "a"}]}), [{"account_id": "a"}])
        self.assertEqual(extract_transactions({"transactions": [{"transaction_id": "t"}]}), [{"transaction_id": "t"}])

    def test_match_requires_correct_account_and_posted_transaction(self):
        txs = [
            {
                "transaction_id": "1",
                "account_id": "bills",
                "date": "2026-09-20",
                "merchant_name": "VERIZON WIRELESS",
                "amount": 213.07,
                "pending": False,
            },
            {
                "transaction_id": "2",
                "account_id": "other",
                "date": "2026-09-20",
                "merchant_name": "COX COMMUNICATIONS",
                "amount": 120.60,
                "pending": False,
            },
            {
                "transaction_id": "3",
                "account_id": "bills",
                "date": "2026-09-20",
                "merchant_name": "ACELLUS",
                "amount": 158.00,
                "pending": True,
            },
        ]
        matches = find_bill_matches(txs, year=2026, month=9, cycle="15th", account_id="bills")
        self.assertIn("Verizon", matches)
        self.assertNotIn("Cox", matches)
        self.assertNotIn("Acellus Academy", matches)


if __name__ == "__main__":
    unittest.main()
