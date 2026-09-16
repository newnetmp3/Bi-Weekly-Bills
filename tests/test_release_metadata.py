from pathlib import Path
import tomllib
import unittest

import biweekly_bills


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_package_version_and_release_assets_are_consistent(self):
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]

        self.assertEqual(project["version"], biweekly_bills.__version__)
        self.assertRegex(project["version"], r"^\d+\.\d+\.\d+$")

        user_guide = ROOT / "docs" / "USER_GUIDE.md"
        release_notes = ROOT / "docs" / "RELEASE_NOTES_1.0.md"
        release_workflow = ROOT / ".github" / "workflows" / "release.yml"

        self.assertTrue(user_guide.is_file())
        self.assertTrue(release_notes.is_file())
        self.assertTrue(release_workflow.is_file())

        guide = user_guide.read_text(encoding="utf-8")
        self.assertIn("# Bi-Weekly Bills 1.0 User Guide", guide)
        self.assertIn("## 2. First run and bank connection", guide)
        self.assertIn("## 6. Transactions", guide)
        self.assertIn("## 9. Settings, backups, and restore", guide)


if __name__ == "__main__":
    unittest.main()
