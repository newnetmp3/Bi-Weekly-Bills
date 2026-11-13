"""An app update must never require signing in to the bank again."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from biweekly_bills import local_config, paths, production_guard, settings
from biweekly_bills.ui.formatting import relative_time


class LegacyStateMigrationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.home = self.root / "home"
        self.old_install = self.root / "venv" / "lib" / "python3.13"
        (self.old_install / "data").mkdir(parents=True)
        (self.old_install / "PRODUCTION_ITEM_CREATED.lock").write_text(
            "Item ID: item-123\n", encoding="utf-8"
        )
        (self.old_install / "data" / "local_config_production.json").write_text(
            json.dumps({"bills_account_id": "acct-1", "transactions_cursor": "c1"}),
            encoding="utf-8",
        )
        self.state = self.home / ".config" / "bi-weekly-bills" / "state"
        self.user_env = self.home / ".config" / "bi-weekly-bills" / "plaid.env"
        self.patches = [
            patch.object(paths, "user_state_dir", lambda: self.state),
            patch.object(paths, "PROJECT_ROOT", self.old_install),
            patch.object(settings, "USER_ENV_PATH", self.user_env),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self._temp.cleanup()

    def test_copies_old_install_state_without_deleting_it(self):
        report = paths.migrate_legacy_state()
        self.assertIn("PRODUCTION_ITEM_CREATED.lock", report.copied)
        self.assertIn("data/local_config_production.json", report.copied)
        self.assertTrue((self.state / "PRODUCTION_ITEM_CREATED.lock").is_file())
        self.assertEqual(
            os.stat(self.state / "PRODUCTION_ITEM_CREATED.lock").st_mode & 0o777,
            0o600,
        )
        # The old copy is left alone.
        self.assertTrue((self.old_install / "PRODUCTION_ITEM_CREATED.lock").is_file())

        with patch.object(local_config, "DATA_DIR", self.state), patch.object(
            local_config, "LEGACY_CONFIG_PATH", self.state / "local_config.json"
        ):
            self.assertEqual(
                local_config.load_local_config("production")["bills_account_id"],
                "acct-1",
            )
        with patch.object(
            production_guard,
            "PRODUCTION_LOCK",
            self.state / "PRODUCTION_ITEM_CREATED.lock",
        ):
            self.assertEqual(production_guard.production_lock_item_id(), "item-123")

    def test_never_overwrites_newer_state_and_runs_once(self):
        self.state.mkdir(parents=True)
        (self.state / "PRODUCTION_ITEM_CREATED.lock").write_text(
            "Item ID: item-123\nnewer\n", encoding="utf-8"
        )
        report = paths.migrate_legacy_state()
        self.assertIn("PRODUCTION_ITEM_CREATED.lock", report.skipped_existing)
        self.assertIn(
            "newer",
            (self.state / "PRODUCTION_ITEM_CREATED.lock").read_text(encoding="utf-8"),
        )
        # Automatic startup migration happens once.
        self.assertFalse(paths.migrate_legacy_state().copied)

    def test_explicit_source_imports_checkout_env_keys_only_when_missing(self):
        checkout = self.root / "checkout"
        checkout.mkdir()
        (checkout / ".env").write_text(
            "PLAID_CLIENT_ID=cid\nPLAID_SECRET=sec\nPLAID_ENV=production\n",
            encoding="utf-8",
        )
        report = paths.migrate_legacy_state(checkout)
        self.assertTrue(report.imported_api_keys)
        self.assertIn("PLAID_CLIENT_ID=cid", self.user_env.read_text(encoding="utf-8"))

        (checkout / ".env").write_text(
            "PLAID_CLIENT_ID=other\nPLAID_SECRET=other\n", encoding="utf-8"
        )
        again = paths.migrate_legacy_state(checkout)
        self.assertFalse(again.imported_api_keys)
        self.assertIn("PLAID_CLIENT_ID=cid", self.user_env.read_text(encoding="utf-8"))


class StableLocationTests(unittest.TestCase):
    def test_connection_state_is_not_inside_the_install_folder(self):
        install = paths.PROJECT_ROOT
        for path in (
            production_guard.PRODUCTION_LOCK,
            production_guard.PRODUCTION_LINK_SESSION_LOCK,
            local_config.DATA_DIR,
        ):
            self.assertFalse(
                Path(path).is_relative_to(install) and install != Path.home(),
                f"{path} is inside the install folder {install}",
            )

    def test_bank_token_location_is_unchanged(self):
        from biweekly_bills import secure_store

        self.assertEqual(
            secure_store.APP_DIR, Path.home() / ".config" / "bi-weekly-bills"
        )


class RelativeTimeTests(unittest.TestCase):
    def test_plain_words(self):
        now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(relative_time(None, now=now), "never")
        self.assertEqual(relative_time(now.isoformat(), now=now), "just now")
        self.assertEqual(
            relative_time((now - timedelta(hours=3)).isoformat(), now=now),
            "3 hours ago",
        )
        self.assertEqual(
            relative_time((now - timedelta(days=1, hours=2)).isoformat(), now=now),
            "yesterday",
        )
        self.assertEqual(
            relative_time("2026-11-11T12:00:00+00:00", now=now), "2 days ago"
        )


if __name__ == "__main__":
    unittest.main()
