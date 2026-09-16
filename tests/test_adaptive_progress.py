import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from biweekly_bills.ui.adaptive_progress import AdaptiveTextProgressBar


class AdaptiveProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fill_rect_tracks_progress_from_left(self):
        bar = AdaptiveTextProgressBar()
        bar.resize(200, 20)
        bar.setRange(0, 10)
        bar.setValue(5)

        filled = bar.fill_rect()
        self.assertTrue(filled.isValid())
        self.assertGreaterEqual(filled.width(), 98)
        self.assertLessEqual(filled.width(), 100)
        self.assertLess(filled.left(), filled.right())

    def test_fill_rect_tracks_progress_from_right(self):
        bar = AdaptiveTextProgressBar()
        bar.resize(200, 20)
        bar.setRange(0, 10)
        bar.setValue(5)
        bar.setInvertedAppearance(True)

        filled = bar.fill_rect()
        self.assertTrue(filled.isValid())
        self.assertGreater(filled.left(), 95)

    def test_zero_progress_has_no_filled_text_region(self):
        bar = AdaptiveTextProgressBar()
        bar.resize(200, 20)
        bar.setRange(0, 10)
        bar.setValue(0)

        self.assertFalse(bar.fill_rect().isValid())


if __name__ == "__main__":
    unittest.main()
