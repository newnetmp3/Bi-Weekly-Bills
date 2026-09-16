import argparse
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from biweekly_bills import cli


def production_settings():
    return SimpleNamespace(
        environment="production",
        client_id="client",
        secret="secret",
        redirect_uri=None,
        host="127.0.0.1",
        port=8000,
    )


class CliProductionSafetyTests(unittest.TestCase):
    def test_initial_production_link_uses_readiness_and_session_reservation(self):
        order = []

        with patch("biweekly_bills.cli.load_settings", return_value=production_settings()), patch(
            "biweekly_bills.cli.preflight_store"
        ), patch(
            "biweekly_bills.cli.assert_initial_production_link_allowed",
            side_effect=lambda *_args, **_kwargs: order.append("assert"),
        ), patch(
            "biweekly_bills.cli.acquire_initial_link_session",
            side_effect=lambda: order.append("reserve"),
        ), patch(
            "biweekly_bills.cli._run_link_flow",
            side_effect=lambda *_args, **_kwargs: order.append("run") or 0,
        ), patch(
            "biweekly_bills.cli.release_initial_link_session",
            side_effect=lambda: order.append("release"),
        ):
            result = cli.cmd_link(argparse.Namespace())

        self.assertEqual(result, 0)
        self.assertEqual(order, ["assert", "reserve", "run", "release"])

    def test_initial_production_link_releases_reservation_on_failure(self):
        with patch("biweekly_bills.cli.load_settings", return_value=production_settings()), patch(
            "biweekly_bills.cli.preflight_store"
        ), patch(
            "biweekly_bills.cli.assert_initial_production_link_allowed"
        ), patch(
            "biweekly_bills.cli.acquire_initial_link_session"
        ), patch(
            "biweekly_bills.cli._run_link_flow",
            side_effect=RuntimeError("boom"),
        ), patch(
            "biweekly_bills.cli.release_initial_link_session"
        ) as release:
            with self.assertRaisesRegex(RuntimeError, "boom"):
                cli.cmd_link(argparse.Namespace())

        release.assert_called_once()

    def test_production_update_mode_recreates_guard_for_same_existing_item(self):
        with patch("biweekly_bills.cli.load_settings", return_value=production_settings()), patch(
            "biweekly_bills.cli.require_access_token",
            return_value="prod-token",
        ), patch(
            "biweekly_bills.cli.assert_production_update_allowed"
        ), patch(
            "biweekly_bills.cli.load_credentials",
            return_value={
                "access_token": "prod-token",
                "item_id": "prod-item",
                "environment": "production",
            },
        ), patch(
            "biweekly_bills.cli.mark_production_item_created"
        ) as mark, patch(
            "biweekly_bills.cli._run_link_flow",
            return_value=0,
        ) as run:
            result = cli.cmd_update_link(argparse.Namespace())

        self.assertEqual(result, 0)
        mark.assert_called_once_with("prod-item")
        run.assert_called_once()

    def test_recovery_requires_readiness_recovery_state(self):
        with patch(
            "biweekly_bills.cli.assert_production_recovery_allowed",
            side_effect=RuntimeError("not recoverable"),
        ), patch(
            "biweekly_bills.cli.load_pending_production_exchange"
        ) as pending:
            with self.assertRaisesRegex(RuntimeError, "not recoverable"):
                cli.cmd_recover_production(argparse.Namespace())

        pending.assert_not_called()


if __name__ == "__main__":
    unittest.main()
