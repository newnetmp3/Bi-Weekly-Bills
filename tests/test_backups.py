from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from biweekly_bills.backups import BackupManager, database_schema_version, validate_sqlite
from biweekly_bills.database import Database, SCHEMA_VERSION


class BackupManagerTests(unittest.TestCase):
    def _fixture(self, *, retention: int = 30):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        db = Database(root / "bills.sqlite3")
        db.initialize()
        manager = BackupManager(
            db,
            backup_dir=root / "backups",
            retention=retention,
        )
        return root, db, manager

    def test_create_backup_is_verified_and_listed(self):
        _, db, manager = self._fixture()
        db.upsert_bill(name="Verizon", cycle="1st")

        info = manager.create_backup("pre-bill-save")

        self.assertTrue(info.path.exists())
        self.assertTrue(info.integrity_ok)
        self.assertTrue(validate_sqlite(info.path))
        backups = manager.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].path, info.path)
        self.assertEqual(backups[0].reason, "pre bill save")

    def test_metadata_only_listing_does_not_integrity_scan_every_backup(self):
        root, db, manager = self._fixture()
        db.upsert_bill(name="Verizon", cycle="1st")
        info = manager.create_backup("verified")

        fresh_manager = BackupManager(
            db,
            backup_dir=root / "backups",
        )
        with patch(
            "biweekly_bills.backups.validate_sqlite"
        ) as validate:
            backups = fresh_manager.list_backups(verify=False)

        validate.assert_not_called()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].path, info.path)
        self.assertIsNone(backups[0].integrity_ok)

    def test_restore_rolls_database_back_and_creates_safety_backup(self):
        _, db, manager = self._fixture()

        db.upsert_bill(name="Before", cycle="1st")
        snapshot = manager.create_backup("known-good")

        db.upsert_bill(name="After", cycle="15th")
        self.assertEqual(
            [row["name"] for row in db.list_bills(active_only=False)],
            ["After", "Before"],
        )

        restored, safety = manager.restore(snapshot.path)

        self.assertEqual(restored.path, snapshot.path)
        self.assertTrue(safety.path.exists())
        self.assertTrue(validate_sqlite(safety.path))
        self.assertEqual(
            [row["name"] for row in db.list_bills(active_only=False)],
            ["Before"],
        )

    def test_retention_keeps_only_newest_backups(self):
        _, _, manager = self._fixture(retention=2)

        manager.create_backup("one")
        manager.create_backup("two")
        manager.create_backup("three")

        backups = manager.list_backups()
        self.assertEqual(len(backups), 2)
        self.assertEqual(
            {info.reason for info in backups},
            {"two", "three"},
        )

    def test_schema_backup_only_runs_when_existing_version_is_old(self):
        _, db, manager = self._fixture()

        self.assertEqual(database_schema_version(db.path), SCHEMA_VERSION)
        self.assertIsNone(manager.backup_before_schema_migration())

        conn = sqlite3.connect(db.path)
        try:
            conn.execute(
                "UPDATE metadata SET value=? WHERE key='schema_version'",
                (str(SCHEMA_VERSION - 1),),
            )
            conn.commit()
        finally:
            conn.close()

        info = manager.backup_before_schema_migration()
        self.assertIsNotNone(info)
        assert info is not None
        self.assertTrue(info.integrity_ok)
        self.assertEqual(info.reason, "pre schema migration")

    def test_restore_rejects_files_outside_managed_backup_directory(self):
        root, _, manager = self._fixture()
        outside = root / "outside.sqlite3"
        outside.write_bytes(b"not a database")

        with self.assertRaisesRegex(ValueError, "outside the managed backup directory"):
            manager.restore(outside)


if __name__ == "__main__":
    unittest.main()
