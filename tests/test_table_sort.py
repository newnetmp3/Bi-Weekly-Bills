import unittest

from biweekly_bills.ui.table_sort import (
    QT_INT64_MAX,
    QT_INT64_MIN,
    SortableTableWidgetItem,
    qt_safe_sort_value,
)


class SortableTableTests(unittest.TestCase):
    def test_huge_integer_sort_keys_are_clamped_to_qt_int64(self):
        self.assertEqual(qt_safe_sort_value(-(10**30)), QT_INT64_MIN)
        self.assertEqual(qt_safe_sort_value(10**30), QT_INT64_MAX)

        # Regression test for the PySide/Shiboken startup crash: constructing
        # the item itself must not raise OverflowError.
        low = SortableTableWidgetItem("—", sort_value=-(10**30))
        high = SortableTableWidgetItem("—", sort_value=10**30)

        self.assertIsNotNone(low)
        self.assertIsNotNone(high)


if __name__ == "__main__":
    unittest.main()
