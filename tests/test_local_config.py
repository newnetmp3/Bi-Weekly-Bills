import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from biweekly_bills import local_config


class LocalConfigTests(unittest.TestCase):
    def test_environment_state_is_separate_and_legacy_migrates_to_sandbox(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "local_config.json"
            legacy.write_text('{"bills_account_id": "sandbox-old", "transactions_cursor": "cursor-old"}\n', encoding="utf-8")

            with patch.multiple(
                local_config,
                DATA_DIR=root,
                LEGACY_CONFIG_PATH=legacy,
            ):
                sandbox = local_config.load_local_config("sandbox")
                self.assertEqual(sandbox["bills_account_id"], "sandbox-old")

                local_config.update_local_config("production", bills_account_id="prod-account")
                production = local_config.load_local_config("production")
                self.assertEqual(production["bills_account_id"], "prod-account")

                sandbox_again = local_config.load_local_config("sandbox")
                self.assertEqual(sandbox_again["bills_account_id"], "sandbox-old")
                self.assertNotEqual(
                    local_config.config_path("sandbox"),
                    local_config.config_path("production"),
                )


if __name__ == "__main__":
    unittest.main()
