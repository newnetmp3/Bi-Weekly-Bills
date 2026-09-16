from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from biweekly_bills import production_readiness
from biweekly_bills.production_guard import ProductionLockInfo


def fake_settings(environment: str):
    return SimpleNamespace(
        environment=environment,
        client_id="client",
        secret="secret",
        redirect_uri=None,
        host="127.0.0.1",
        port=8000,
    )


class ProductionReadinessTests(unittest.TestCase):
    def _patch_common(
        self,
        root: Path,
        *,
        environment: str = "sandbox",
        sandbox_item: str | None = "sandbox-item",
        production: dict | None = None,
        pending: dict | None = None,
        lock: ProductionLockInfo | None = None,
        session_reserved: bool = False,
        validation_passed: bool = True,
    ):
        production = production or {}
        pending = pending or {}
        sandbox = (
            {
                "access_token": "sandbox-token",
                "item_id": sandbox_item,
                "environment": "sandbox",
            }
            if sandbox_item
            else {}
        )

        def creds(env: str):
            return sandbox if env == "sandbox" else production

        return (
            patch(
                "biweekly_bills.production_readiness.load_settings",
                return_value=fake_settings(environment),
            ),
            patch(
                "biweekly_bills.production_readiness.load_sandbox_validation_marker",
                return_value={"passed": True, "finished_at": "2026-09-16T00:00:00+00:00"}
                if validation_passed
                else {},
            ),
            patch(
                "biweekly_bills.production_readiness.load_credentials",
                side_effect=creds,
            ),
            patch(
                "biweekly_bills.production_readiness.load_pending_production_exchange",
                return_value=pending,
            ),
            patch(
                "biweekly_bills.production_readiness.load_production_lock",
                return_value=lock,
            ),
            patch(
                "biweekly_bills.production_readiness.initial_link_session_reserved",
                return_value=session_reserved,
            ),
            patch(
                "biweekly_bills.production_readiness.credentials_path",
                return_value=root / "plaid_item_production.json",
            ),
            patch.object(
                production_readiness,
                "PENDING_PRODUCTION_PATH",
                root / "plaid_item_production.pending.json",
            ),
        )

    def test_clean_sandbox_state_is_ready_to_arm(self):
        with TemporaryDirectory() as temp:
            patches = self._patch_common(Path(temp), environment="sandbox")
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                report = production_readiness.inspect_production_readiness()

        self.assertEqual(report.action, "READY_TO_ARM")
        self.assertFalse(report.can_initial_link)

    def test_clean_production_state_allows_one_first_link(self):
        with TemporaryDirectory() as temp:
            patches = self._patch_common(Path(temp), environment="production")
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                report = production_readiness.inspect_production_readiness()

        self.assertEqual(report.action, "READY_FOR_FIRST_LINK")
        self.assertTrue(report.can_initial_link)

    def test_existing_production_item_routes_to_update_mode_only(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = root / "PRODUCTION_ITEM_CREATED.lock"
            lock = ProductionLockInfo(
                path=lock_path,
                item_id="prod-item",
                raw_text="Item ID: prod-item",
                valid=True,
            )
            production = {
                "access_token": "prod-token",
                "item_id": "prod-item",
                "environment": "production",
            }
            patches = self._patch_common(
                root,
                environment="production",
                production=production,
                lock=lock,
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                report = production_readiness.inspect_production_readiness()

        self.assertEqual(report.action, "UPDATE_MODE_ONLY")
        self.assertTrue(report.can_update_mode)
        self.assertFalse(report.can_initial_link)

    def test_pending_exchange_forces_recovery(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            pending = {
                "access_token": "pending-token",
                "item_id": "prod-item",
                "environment": "production",
            }
            lock = ProductionLockInfo(
                path=root / "PRODUCTION_ITEM_CREATED.lock",
                item_id="prod-item",
                raw_text="Item ID: prod-item",
                valid=True,
            )
            patches = self._patch_common(
                root,
                environment="sandbox",
                pending=pending,
                lock=lock,
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                report = production_readiness.inspect_production_readiness()

        self.assertEqual(report.action, "RECOVER")
        self.assertTrue(report.can_recover)
        self.assertFalse(report.can_initial_link)

    def test_mismatched_production_item_ids_are_blocked(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            production = {
                "access_token": "prod-token",
                "item_id": "prod-item-a",
                "environment": "production",
            }
            lock = ProductionLockInfo(
                path=root / "PRODUCTION_ITEM_CREATED.lock",
                item_id="prod-item-b",
                raw_text="Item ID: prod-item-b",
                valid=True,
            )
            patches = self._patch_common(
                root,
                environment="production",
                production=production,
                lock=lock,
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                report = production_readiness.inspect_production_readiness()

        self.assertEqual(report.action, "BLOCKED")
        mismatch = next(
            check
            for check in report.checks
            if check.name == "Production Item identity consistency"
        )
        self.assertEqual(mismatch.status, "FAIL")

    def test_existing_lock_without_credentials_blocks_relink(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            lock = ProductionLockInfo(
                path=root / "PRODUCTION_ITEM_CREATED.lock",
                item_id="prod-item",
                raw_text="Item ID: prod-item",
                valid=True,
            )
            patches = self._patch_common(
                root,
                environment="production",
                lock=lock,
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                report = production_readiness.inspect_production_readiness()

        self.assertEqual(report.action, "BLOCKED")
        self.assertIn("credential is missing", report.headline)

    def test_reserved_session_allows_guarded_server_only_when_other_checks_clean(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            patches = self._patch_common(
                root,
                environment="production",
                session_reserved=True,
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                report = production_readiness.assert_initial_production_link_allowed(
                    allow_reserved_session=True
                )

        self.assertEqual(report.action, "BLOCKED")
        self.assertIn("already reserved", report.headline)

    def test_missing_sandbox_validation_is_advisory_for_first_link(self):
        with TemporaryDirectory() as temp:
            patches = self._patch_common(
                Path(temp),
                environment="production",
                validation_passed=False,
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                report = production_readiness.inspect_production_readiness()

        self.assertEqual(report.action, "READY_FOR_FIRST_LINK")
        self.assertTrue(report.can_initial_link)
        sandbox_check = next(
            check
            for check in report.checks
            if check.name == "Sandbox validation"
        )
        self.assertEqual(sandbox_check.status, "WARN")

    def test_reserved_first_link_does_not_require_sandbox_validation_marker(self):
        with TemporaryDirectory() as temp:
            patches = self._patch_common(
                Path(temp),
                environment="production",
                session_reserved=True,
                validation_passed=False,
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                report = production_readiness.assert_initial_production_link_allowed(
                    allow_reserved_session=True
                )

        self.assertEqual(report.action, "BLOCKED")
        self.assertIn("already reserved", report.headline)


if __name__ == "__main__":
    unittest.main()
