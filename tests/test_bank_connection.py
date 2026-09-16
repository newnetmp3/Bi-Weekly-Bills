from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from biweekly_bills.bank_connection import connection_state
from biweekly_bills.database import Database


def fake_settings(*, keys=True, environment="production"):
    return SimpleNamespace(
        client_id="client" if keys else "",
        secret="secret" if keys else "",
        environment=environment,
        redirect_uri=None,
        host="127.0.0.1",
        port=8000,
    )


class BankConnectionStateTests(unittest.TestCase):
    def _db(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db = Database(Path(temp.name) / "bills.sqlite3")
        db.initialize()
        return db

    def test_new_user_without_keys_is_sent_to_configuration(self):
        db = self._db()
        with patch(
            "biweekly_bills.bank_connection.load_settings",
            return_value=fake_settings(keys=False),
        ), patch(
            "biweekly_bills.bank_connection.load_credentials",
            return_value={},
        ), patch(
            "biweekly_bills.bank_connection.load_pending_production_exchange",
            return_value={},
        ):
            state = connection_state(db)

        self.assertEqual(state.action, "configure")
        self.assertIn("API credentials", state.headline)

    def test_pending_connection_with_missing_keys_configures_before_recovery(self):
        db = self._db()
        with patch(
            "biweekly_bills.bank_connection.load_settings",
            return_value=fake_settings(keys=False),
        ), patch(
            "biweekly_bills.bank_connection.load_credentials",
            return_value={},
        ), patch(
            "biweekly_bills.bank_connection.load_pending_production_exchange",
            return_value={"access_token": "pending", "item_id": "item"},
        ):
            state = connection_state(db)

        self.assertEqual(state.action, "configure")
        self.assertIn("recovery", state.headline.casefold())

    def test_clean_first_link_state_returns_connect(self):
        db = self._db()
        readiness = SimpleNamespace(
            can_initial_link=True,
            action="READY_FOR_FIRST_LINK",
            headline="ready",
        )
        with patch(
            "biweekly_bills.bank_connection.load_settings",
            return_value=fake_settings(),
        ), patch(
            "biweekly_bills.bank_connection.load_credentials",
            return_value={},
        ), patch(
            "biweekly_bills.bank_connection.load_pending_production_exchange",
            return_value={},
        ), patch(
            "biweekly_bills.bank_connection.inspect_production_readiness",
            return_value=readiness,
        ):
            state = connection_state(db)

        self.assertEqual(state.action, "connect")
        self.assertEqual(state.primary_label, "Connect bank")

    def test_existing_healthy_connection_returns_sync_and_repair(self):
        db = self._db()
        readiness = SimpleNamespace(
            action="UPDATE_MODE_ONLY",
            headline="healthy",
        )
        with patch(
            "biweekly_bills.bank_connection.load_settings",
            return_value=fake_settings(),
        ), patch(
            "biweekly_bills.bank_connection.load_credentials",
            return_value={"access_token": "token", "item_id": "item"},
        ), patch(
            "biweekly_bills.bank_connection.load_pending_production_exchange",
            return_value={},
        ), patch(
            "biweekly_bills.bank_connection.inspect_production_readiness",
            return_value=readiness,
        ):
            state = connection_state(db)

        self.assertEqual(state.action, "sync")
        self.assertTrue(state.can_repair)

    def test_existing_inconsistent_connection_is_not_reported_healthy(self):
        db = self._db()
        readiness = SimpleNamespace(
            action="BLOCKED",
            headline="identity mismatch",
        )
        with patch(
            "biweekly_bills.bank_connection.load_settings",
            return_value=fake_settings(),
        ), patch(
            "biweekly_bills.bank_connection.load_credentials",
            return_value={"access_token": "token", "item_id": "item"},
        ), patch(
            "biweekly_bills.bank_connection.load_pending_production_exchange",
            return_value={},
        ), patch(
            "biweekly_bills.bank_connection.inspect_production_readiness",
            return_value=readiness,
        ):
            state = connection_state(db)

        self.assertEqual(state.action, "blocked")
        self.assertIn("identity mismatch", state.detail)


if __name__ == "__main__":
    unittest.main()
