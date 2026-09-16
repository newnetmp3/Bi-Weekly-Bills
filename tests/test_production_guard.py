from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from biweekly_bills import production_guard


class ProductionGuardTests(unittest.TestCase):
    def test_persistent_item_lock_refuses_different_item_id(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            lock = root / "PRODUCTION_ITEM_CREATED.lock"
            session = root / "PRODUCTION_LINK_IN_PROGRESS.lock"
            with patch.multiple(
                production_guard,
                PRODUCTION_LOCK=lock,
                PRODUCTION_LINK_SESSION_LOCK=session,
            ):
                production_guard.mark_production_item_created("prod-item-a")
                production_guard.mark_production_item_created("prod-item-a")
                with self.assertRaisesRegex(RuntimeError, "different Item ID"):
                    production_guard.mark_production_item_created("prod-item-b")

                info = production_guard.load_production_lock()
                self.assertIsNotNone(info)
                assert info is not None
                self.assertEqual(info.item_id, "prod-item-a")

    def test_initial_link_reservation_is_exclusive(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            lock = root / "PRODUCTION_ITEM_CREATED.lock"
            session = root / "PRODUCTION_LINK_IN_PROGRESS.lock"
            with patch.multiple(
                production_guard,
                PRODUCTION_LOCK=lock,
                PRODUCTION_LINK_SESSION_LOCK=session,
            ):
                production_guard.acquire_initial_link_session()
                self.assertTrue(production_guard.initial_link_session_reserved())
                with self.assertRaisesRegex(RuntimeError, "already reserved"):
                    production_guard.acquire_initial_link_session()
                production_guard.release_initial_link_session()
                self.assertFalse(production_guard.initial_link_session_reserved())

    def test_stale_reservation_cannot_be_cleared_when_item_evidence_exists(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            lock = root / "PRODUCTION_ITEM_CREATED.lock"
            session = root / "PRODUCTION_LINK_IN_PROGRESS.lock"
            with patch.multiple(
                production_guard,
                PRODUCTION_LOCK=lock,
                PRODUCTION_LINK_SESSION_LOCK=session,
            ), patch(
                "biweekly_bills.secure_store.load_credentials",
                return_value={
                    "access_token": "prod-token",
                    "item_id": "prod-item",
                    "environment": "production",
                },
            ), patch(
                "biweekly_bills.secure_store.load_pending_production_exchange",
                return_value={},
            ):
                production_guard.acquire_initial_link_session()
                with self.assertRaisesRegex(RuntimeError, "credential exists"):
                    production_guard.clear_initial_link_session_after_confirmation()
                self.assertTrue(session.exists())


if __name__ == "__main__":
    unittest.main()
