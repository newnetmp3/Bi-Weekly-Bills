from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from biweekly_bills.database import Database
from biweekly_bills.merchant_profiles import (
    counterparty_display,
    counterparty_kind,
    meaningful_merchant_name,
    merchant_key,
    plaid_transaction_metadata,
    safe_remote_logo_url,
)


class MerchantProfileTests(unittest.TestCase):
    def _db(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Database(Path(temp.name) / "bills.sqlite3")
        db.initialize()
        return db

    def test_transaction_builds_persistent_merchant_profile_from_plaid_metadata(self):
        db = self._db()
        raw = json.dumps(
            {
                "merchant_entity_id": "entity-target",
                "logo_url": "https://cdn.example.com/target.png",
                "website": "https://www.target.com/",
            }
        )
        db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id="target-1",
            plaid_account_id="checking",
            posted_date="2026-09-16",
            authorized_date=None,
            merchant_name="Target",
            name="TARGET 0001",
            amount_cents=4237,
            pending=False,
            raw_json=raw,
            last_seen_at="2026-09-16T12:00:00+00:00",
        )

        profiles = db.list_merchant_profiles("production")
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile["merchant_key"], "target")
        self.assertEqual(profile["display_name"], "Target")
        self.assertEqual(profile["merchant_entity_id"], "entity-target")
        self.assertEqual(
            profile["logo_url"],
            "https://cdn.example.com/target.png",
        )
        self.assertEqual(
            profile["website"],
            "https://www.target.com/",
        )
        self.assertEqual(profile["first_seen_at"], "2026-09-16")
        self.assertEqual(profile["last_seen_at"], "2026-09-16")

    def test_generic_account_movements_do_not_become_merchants(self):
        db = self._db()
        for tx_id, merchant in (
            ("nfo", "NFO PAYMENT RECEIVED"),
            ("ach", "ACH TRANSFER"),
            ("online", "ONLINE PAYMENT"),
        ):
            db.upsert_bank_transaction(
                environment="production",
                plaid_transaction_id=tx_id,
                plaid_account_id="card",
                posted_date="2026-09-16",
                authorized_date=None,
                merchant_name=merchant,
                name=merchant,
                amount_cents=-5000,
                pending=False,
                raw_json="{}",
                last_seen_at="2026-09-16T12:00:00+00:00",
            )

        self.assertEqual(db.list_merchant_profiles("production"), [])

    def test_logo_bytes_are_cached_and_replaced_when_logo_url_changes(self):
        db = self._db()
        raw = json.dumps(
            {"logo_url": "https://cdn.example.com/merchant-v1.png"}
        )
        db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id="merchant-1",
            plaid_account_id="checking",
            posted_date="2026-09-10",
            authorized_date=None,
            merchant_name="Example Merchant",
            name="EXAMPLE MERCHANT",
            amount_cents=1000,
            pending=False,
            raw_json=raw,
            last_seen_at="2026-09-16T12:00:00+00:00",
        )
        key = merchant_key("Example Merchant")
        db.set_merchant_logo(
            "production",
            key,
            b"cached-image",
            mime_type="image/png",
        )
        profile = db.get_merchant_profile("production", key)
        assert profile is not None
        self.assertEqual(bytes(profile["logo_data"]), b"cached-image")

        db.upsert_bank_transaction(
            environment="production",
            plaid_transaction_id="merchant-1",
            plaid_account_id="checking",
            posted_date="2026-09-10",
            authorized_date=None,
            merchant_name="Example Merchant",
            name="EXAMPLE MERCHANT",
            amount_cents=1000,
            pending=False,
            raw_json=json.dumps(
                {"logo_url": "https://cdn.example.com/merchant-v2.png"}
            ),
            last_seen_at="2026-09-16T12:00:00+00:00",
        )
        profile = db.get_merchant_profile("production", key)
        assert profile is not None
        self.assertEqual(
            profile["logo_url"],
            "https://cdn.example.com/merchant-v2.png",
        )
        self.assertIsNone(profile["logo_data"])

    def test_counterparty_distinguishes_merchant_from_connected_account(self):
        merchant = {
            "merchant_name": "Target",
            "name": "TARGET 0001",
            "internal_transfer_role": None,
            "internal_transfer_bill_name": None,
            "account_type": "depository",
            "account_name": "Bills Checking",
            "amount_cents": 4237,
        }
        account = {
            "merchant_name": "NFO PAYMENT RECEIVED",
            "name": "TRANSFER CREDIT",
            "internal_transfer_role": "destination",
            "internal_transfer_bill_name": "Green Card",
            "account_type": "credit",
            "account_name": "Green Card",
            "amount_cents": -23741,
        }

        self.assertEqual(meaningful_merchant_name(merchant), "Target")
        self.assertEqual(counterparty_kind(merchant), "merchant")
        self.assertEqual(counterparty_display(merchant), "Target")

        self.assertIsNone(meaningful_merchant_name(account))
        self.assertEqual(
            counterparty_kind(account),
            "connected-account",
        )
        self.assertEqual(
            counterparty_display(account),
            "Connected account · Green Card",
        )

    def test_plaid_transaction_metadata_flattens_enrichment(self):
        metadata = plaid_transaction_metadata(
            json.dumps(
                {
                    "merchant_entity_id": "entity-bonfire",
                    "website": "https://www.bonfire.com/",
                    "payment_channel": "online",
                    "personal_finance_category": {
                        "primary": "GENERAL_MERCHANDISE",
                        "detailed": "GENERAL_MERCHANDISE_OTHER",
                        "confidence_level": "VERY_HIGH",
                    },
                    "counterparties": [
                        {
                            "name": "Bonfire",
                            "type": "merchant",
                            "confidence_level": "VERY_HIGH",
                        }
                    ],
                    "location": {
                        "address": "123 Main St",
                        "city": "Richmond",
                        "region": "VA",
                        "postal_code": "23220",
                        "country": "US",
                    },
                }
            )
        )

        self.assertEqual(
            metadata["website"],
            "https://www.bonfire.com/",
        )
        self.assertEqual(metadata["entity_id"], "entity-bonfire")
        self.assertEqual(metadata["payment_channel"], "Online")
        self.assertIn("General Merchandise", metadata["category"])
        self.assertIn("Very High confidence", metadata["category"])
        self.assertEqual(
            metadata["counterparty"],
            "Bonfire (Merchant, Very High confidence)",
        )
        self.assertIn("Richmond, VA 23220", metadata["location"])

    def test_only_https_logo_urls_are_accepted(self):
        self.assertEqual(
            safe_remote_logo_url("https://cdn.example.com/logo.png"),
            "https://cdn.example.com/logo.png",
        )
        self.assertIsNone(
            safe_remote_logo_url("http://cdn.example.com/logo.png")
        )
        self.assertIsNone(safe_remote_logo_url("file:///tmp/logo.png"))


if __name__ == "__main__":
    unittest.main()
