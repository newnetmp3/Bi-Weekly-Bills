from datetime import date
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from biweekly_bills.backups import BackupManager
from biweekly_bills.database import Database
from biweekly_bills.merchant_profiles import merchant_key
from biweekly_bills.ui.transactions_page import TransactionsPage


def _tiny_png() -> bytes:
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.black)
    data = QByteArray()
    buffer = QBuffer(data)
    self_opened = buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not self_opened:
        raise RuntimeError("Unable to open in-memory PNG buffer.")
    try:
        if not image.save(buffer, "PNG"):
            raise RuntimeError("Unable to encode test PNG.")
    finally:
        buffer.close()
    return bytes(data)


_TINY_PNG = _tiny_png()


class TransactionMerchantUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _fixture(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        db = Database(root / "bills.sqlite3")
        db.initialize()
        backups = BackupManager(
            db,
            backup_dir=root / "backups",
        )
        return db, backups

    def test_cached_merchant_logo_is_displayed_next_to_merchant_name(self):
        db, backups = self._fixture()
        today = date.today()
        db.upsert_bank_account(
            environment="sandbox",
            plaid_account_id="checking",
            name="Checking",
            mask="1234",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=100000,
            available_balance_cents=100000,
            last_synced_at=today.isoformat(),
        )
        db.upsert_bank_transaction(
            environment="sandbox",
            plaid_transaction_id="target-tx",
            plaid_account_id="checking",
            posted_date=today.isoformat(),
            authorized_date=None,
            merchant_name="Target",
            name="TARGET 0001",
            amount_cents=4299,
            pending=False,
            raw_json=json.dumps(
                {
                    "logo_url": "https://cdn.example.com/target.png",
                    "website": "https://www.target.com/",
                }
            ),
            last_seen_at=today.isoformat(),
        )
        db.set_merchant_logo(
            "sandbox",
            merchant_key("Target"),
            _TINY_PNG,
            mime_type="image/png",
        )

        with patch.dict(os.environ, {"PLAID_ENV": "sandbox"}):
            page = TransactionsPage(db, lambda: None, backups)
            page.resize(1200, 820)
            page.show()
            self.app.processEvents()

            self.assertEqual(page.table.rowCount(), 1)
            merchant = page.table.item(0, 1)
            self.assertEqual(merchant.text(), "Target")
            self.assertFalse(merchant.icon().isNull())
            self.assertIn("Merchant", merchant.toolTip())
            page.close()

    def test_transaction_detail_shows_plaid_merchant_metadata(self):
        db, backups = self._fixture()
        today = date.today()
        db.upsert_bank_account(
            environment="sandbox",
            plaid_account_id="checking",
            name="Checking",
            mask="1234",
            account_type="depository",
            account_subtype="checking",
            current_balance_cents=100000,
            available_balance_cents=100000,
            last_synced_at=today.isoformat(),
        )
        db.upsert_bank_transaction(
            environment="sandbox",
            plaid_transaction_id="bonfire-tx",
            plaid_account_id="checking",
            posted_date=today.isoformat(),
            authorized_date=None,
            merchant_name="Bonfire",
            name="BONFIRE* SHIRT ORDER",
            amount_cents=3875,
            pending=False,
            raw_json=json.dumps(
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
                        "city": "Richmond",
                        "region": "VA",
                        "country": "US",
                    },
                }
            ),
            last_seen_at=today.isoformat(),
        )

        with patch.dict(os.environ, {"PLAID_ENV": "sandbox"}):
            page = TransactionsPage(db, lambda: None, backups)
            page.resize(1400, 1000)
            page.show()
            self.app.processEvents()

            self.assertEqual(page.table.rowCount(), 1)
            page.table.selectRow(0)
            self.app.processEvents()

            self.assertEqual(
                page.detail_merchant_value.text(),
                "Bonfire",
            )
            self.assertEqual(
                page.detail_description_value.text(),
                "BONFIRE* SHIRT ORDER",
            )
            self.assertEqual(
                page.detail_website_value.text(),
                "https://www.bonfire.com/",
            )
            self.assertEqual(
                page.detail_entity_id_value.text(),
                "entity-bonfire",
            )
            self.assertIn(
                "General Merchandise",
                page.detail_category_value.text(),
            )
            self.assertEqual(
                page.detail_payment_channel_value.text(),
                "Online",
            )
            self.assertIn(
                "Bonfire",
                page.detail_counterparty_value.text(),
            )
            self.assertIn(
                "Richmond, VA",
                page.detail_location_value.text(),
            )
            self.assertIn(
                "Merchant metadata supplied by Plaid",
                page.transaction_detail_note.text(),
            )
            page.close()

    def test_credit_account_receipt_is_labeled_as_connected_account_not_merchant(self):
        db, backups = self._fixture()
        today = date.today()
        db.upsert_bank_account(
            environment="sandbox",
            plaid_account_id="green-card",
            name="Green Card",
            mask=None,
            account_type="credit",
            account_subtype="credit card",
            current_balance_cents=23741,
            available_balance_cents=None,
            last_synced_at=today.isoformat(),
        )
        db.upsert_bank_transaction(
            environment="sandbox",
            plaid_transaction_id="green-receipt",
            plaid_account_id="green-card",
            posted_date=today.isoformat(),
            authorized_date=None,
            merchant_name="NFO PAYMENT RECEIVED",
            name="TRANSFER CREDIT",
            amount_cents=-23741,
            pending=False,
            raw_json="{}",
            last_seen_at=today.isoformat(),
        )

        with patch.dict(os.environ, {"PLAID_ENV": "sandbox"}):
            page = TransactionsPage(db, lambda: None, backups)
            all_accounts = page.account_filter.findText("All accounts")
            self.assertGreaterEqual(all_accounts, 0)
            page.account_filter.setCurrentIndex(all_accounts)
            self.app.processEvents()

            self.assertEqual(page.table.rowCount(), 1)
            counterparty = page.table.item(0, 1)
            self.assertEqual(
                counterparty.text(),
                "Connected account · Green Card",
            )
            self.assertIn(
                "Connected account",
                counterparty.toolTip(),
            )
            self.assertEqual(
                db.list_merchant_profiles("sandbox"),
                [],
            )
            page.close()


if __name__ == "__main__":
    unittest.main()
