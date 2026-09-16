import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from biweekly_bills import settings


class SettingsTests(unittest.TestCase):
    def test_user_bank_settings_override_project_file_and_are_private(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_env = root / "project.env"
            user_dir = root / "config"
            user_env = user_dir / "plaid.env"
            project_env.write_text(
                "PLAID_CLIENT_ID=project-client\n"
                "PLAID_SECRET=project-secret\n"
                "PLAID_ENV=sandbox\n",
                encoding="utf-8",
            )

            with patch.object(settings, "PROJECT_ENV_PATH", project_env), patch.object(
                settings, "USER_CONFIG_DIR", user_dir
            ), patch.object(settings, "USER_ENV_PATH", user_env), patch.dict(
                os.environ, {}, clear=True
            ):
                before = settings.load_settings(require_keys=False)
                self.assertEqual(before.client_id, "project-client")
                self.assertEqual(before.environment, "sandbox")

                path = settings.save_user_bank_settings(
                    client_id="user-client",
                    secret="user-secret",
                    redirect_uri="https://example.test/oauth",
                    environment="production",
                )
                self.assertEqual(path, user_env)

                after = settings.load_settings()
                self.assertEqual(after.client_id, "user-client")
                self.assertEqual(after.secret, "user-secret")
                self.assertEqual(after.environment, "production")
                self.assertEqual(
                    after.redirect_uri,
                    "https://example.test/oauth",
                )
                self.assertEqual(user_dir.stat().st_mode & 0o777, 0o700)
                self.assertEqual(user_env.stat().st_mode & 0o777, 0o600)

                with self.assertRaisesRegex(
                    ValueError,
                    "Client ID and Secret",
                ):
                    settings.save_user_bank_settings(
                        client_id=None,
                        secret=None,
                        redirect_uri=None,
                        environment="sandbox",
                    )

                settings.save_user_bank_settings(
                    client_id=None,
                    secret=None,
                    redirect_uri=None,
                    environment="production",
                )
                preserved = settings.load_settings()
                self.assertEqual(preserved.client_id, "user-client")
                self.assertEqual(preserved.secret, "user-secret")
                self.assertEqual(
                    preserved.redirect_uri,
                    "https://example.test/oauth",
                )

    def test_ui_preferences_round_trip_is_atomic_and_private(self):
        with TemporaryDirectory() as temp_dir:
            user_dir = Path(temp_dir) / "config"
            with patch.object(settings, "USER_CONFIG_DIR", user_dir):
                path = settings.save_user_ui_preferences(
                    sidebar_order=[
                        "pay_periods",
                        "overview",
                        "bills",
                    ],
                    sidebar_labels={
                        "pay_periods": "Pay Bills Now",
                    },
                )

                self.assertEqual(path, user_dir / "ui.json")
                loaded = settings.load_user_ui_preferences()
                self.assertEqual(
                    loaded["sidebar_order"],
                    ["pay_periods", "overview", "bills"],
                )
                self.assertEqual(
                    loaded["sidebar_labels"],
                    {"pay_periods": "Pay Bills Now"},
                )
                self.assertEqual(
                    user_dir.stat().st_mode & 0o777,
                    0o700,
                )
                self.assertEqual(
                    path.stat().st_mode & 0o777,
                    0o600,
                )


if __name__ == "__main__":
    unittest.main()
