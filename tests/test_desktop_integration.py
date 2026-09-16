import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET

from biweekly_bills.desktop_integration import (
    APP_ID,
    ICON_RESOURCE,
    LOGO_RESOURCE,
    install_desktop_integration,
)


class DesktopIntegrationTests(unittest.TestCase):
    def test_sidebar_logo_renders_to_requested_safe_width(self):
        from PySide6.QtWidgets import QApplication
        from biweekly_bills.branding import brand_logo_pixmap

        app = QApplication.instance() or QApplication([])
        pixmap = brand_logo_pixmap(184)
        self.assertFalse(pixmap.isNull())
        self.assertEqual(pixmap.width(), 184)
        self.assertLessEqual(pixmap.height(), 70)
        self.assertGreaterEqual(pixmap.height(), 60)

    def test_wordmark_stacks_bills_below_bi_weekly(self):
        payload = files("biweekly_bills").joinpath(LOGO_RESOURCE).read_bytes()
        root = ET.fromstring(payload)
        texts = {
            "".join(node.itertext()).strip(): float(node.attrib.get("y", "0"))
            for node in root.iter()
            if node.tag.endswith("text")
        }
        self.assertIn("Bi-Weekly", texts)
        self.assertIn("Bills", texts)
        self.assertGreater(texts["Bills"], texts["Bi-Weekly"] + 80)

    def test_packaged_svg_assets_are_valid(self):
        for resource in (ICON_RESOURCE, LOGO_RESOURCE):
            payload = files("biweekly_bills").joinpath(resource).read_bytes()
            self.assertGreater(len(payload), 500)
            root = ET.fromstring(payload)
            self.assertTrue(root.tag.endswith("svg"))

    def test_install_writes_wayland_matching_desktop_id_and_icon(self):
        with TemporaryDirectory() as temp:
            data_home = Path(temp) / "share"
            executable = Path(temp) / "venv with space" / "bin" / "biweekly-bills-app"

            first = install_desktop_integration(
                data_home=data_home,
                executable=str(executable),
                refresh_cache=False,
            )

            self.assertTrue(first.changed)
            self.assertEqual(
                first.desktop_file,
                data_home / "applications" / f"{APP_ID}.desktop",
            )
            self.assertEqual(
                first.icon_file,
                data_home
                / "icons"
                / "hicolor"
                / "scalable"
                / "apps"
                / f"{APP_ID}.svg",
            )
            self.assertTrue(first.desktop_file.exists())
            self.assertTrue(first.icon_file.exists())
            self.assertTrue(first.logo_file.exists())

            desktop = first.desktop_file.read_text(encoding="utf-8")
            self.assertIn("Name=Bi-Weekly Bills", desktop)
            self.assertIn("Icon=biweekly-bills", desktop)
            self.assertIn("StartupWMClass=biweekly-bills", desktop)
            self.assertIn('Exec="', desktop)
            self.assertIn("biweekly-bills-app", desktop)

            second = install_desktop_integration(
                data_home=data_home,
                executable=str(executable),
                refresh_cache=False,
            )
            self.assertFalse(second.changed)

    def test_installed_icon_matches_packaged_icon(self):
        with TemporaryDirectory() as temp:
            data_home = Path(temp) / "share"
            result = install_desktop_integration(
                data_home=data_home,
                executable="/usr/bin/biweekly-bills-app",
                refresh_cache=False,
            )
            expected = files("biweekly_bills").joinpath(ICON_RESOURCE).read_bytes()
            self.assertEqual(result.icon_file.read_bytes(), expected)


if __name__ == "__main__":
    unittest.main()
