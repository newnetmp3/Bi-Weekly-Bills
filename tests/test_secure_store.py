from contextlib import contextmanager
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from biweekly_bills import production_guard, secure_store


class SecureStoreTests(unittest.TestCase):
    @contextmanager
    def _patch_paths(self, root: Path):
        with patch.multiple(
            secure_store,
            APP_DIR=root,
            LEGACY_CREDENTIALS_PATH=root / "plaid_item.json",
            PENDING_PRODUCTION_PATH=root / "plaid_item_production.pending.json",
        ), patch.multiple(
            production_guard,
            PRODUCTION_LOCK=root / "PRODUCTION_ITEM_CREATED.lock",
            PRODUCTION_LINK_SESSION_LOCK=root / "PRODUCTION_LINK_IN_PROGRESS.lock",
        ):
            yield

    def test_sandbox_and_production_credentials_are_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self._patch_paths(root):
                secure_store.save_credentials(access_token="sandbox-token", item_id="sandbox-item", environment="sandbox")
                secure_store.save_credentials(access_token="prod-token", item_id="prod-item", environment="production")

                self.assertEqual(secure_store.require_access_token("sandbox"), "sandbox-token")
                self.assertEqual(secure_store.require_access_token("production"), "prod-token")
                self.assertNotEqual(
                    secure_store.credentials_path("sandbox"),
                    secure_store.credentials_path("production"),
                )

    def test_legacy_credential_migrates_only_to_matching_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root.mkdir(parents=True, exist_ok=True)
            with self._patch_paths(root):
                secure_store._atomic_write_json(
                    secure_store.LEGACY_CREDENTIALS_PATH,
                    {"access_token": "old", "item_id": "item", "environment": "sandbox"},
                )
                self.assertEqual(secure_store.load_credentials("production"), {})
                self.assertTrue(secure_store.LEGACY_CREDENTIALS_PATH.exists())

                sandbox = secure_store.load_credentials("sandbox")
                self.assertEqual(sandbox["access_token"], "old")
                self.assertFalse(secure_store.LEGACY_CREDENTIALS_PATH.exists())
                self.assertTrue(secure_store.credentials_path("sandbox").exists())

    def test_production_credential_cannot_be_replaced_with_different_item(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self._patch_paths(root):
                secure_store.save_credentials(
                    access_token="prod-token-a",
                    item_id="prod-item-a",
                    environment="production",
                )
                with self.assertRaisesRegex(RuntimeError, "different Item ID"):
                    secure_store.save_credentials(
                        access_token="prod-token-b",
                        item_id="prod-item-b",
                        environment="production",
                    )

                stored = secure_store.load_credentials("production")
                self.assertEqual(stored["item_id"], "prod-item-a")
                self.assertEqual(stored["access_token"], "prod-token-a")

    def test_pending_production_exchange_cannot_conflict_with_existing_item(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self._patch_paths(root):
                secure_store.save_credentials(
                    access_token="prod-token-a",
                    item_id="prod-item-a",
                    environment="production",
                )
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    secure_store.save_pending_production_exchange(
                        access_token="prod-token-b",
                        item_id="prod-item-b",
                    )

    def test_production_secure_store_refuses_item_that_conflicts_with_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self._patch_paths(root), patch(
                "biweekly_bills.production_guard.load_production_lock"
            ) as load_lock:
                load_lock.return_value = type(
                    "Lock",
                    (),
                    {"valid": True, "item_id": "prod-item-a"},
                )()
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    secure_store.save_credentials(
                        access_token="prod-token-b",
                        item_id="prod-item-b",
                        environment="production",
                    )

    def test_pending_production_exchange_can_be_recovered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self._patch_paths(root):
                secure_store.save_pending_production_exchange(
                    access_token="prod-token",
                    item_id="prod-item",
                )
                recovered = secure_store.recover_pending_production_credentials()

                self.assertEqual(recovered["item_id"], "prod-item")
                self.assertEqual(secure_store.require_access_token("production"), "prod-token")
                self.assertFalse(secure_store.PENDING_PRODUCTION_PATH.exists())


if __name__ == "__main__":
    unittest.main()
