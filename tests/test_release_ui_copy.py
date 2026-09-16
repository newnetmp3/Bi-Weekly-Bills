import ast
import inspect
import unittest

from biweekly_bills import bank_connection, reports
from biweekly_bills.ui import (
    bank_pages,
    main_window,
    pay_periods,
    reconciliation_page,
    reports_page,
    settings_page,
    transactions_page,
)


class ReleaseUiCopyTests(unittest.TestCase):
    def test_release_facing_copy_has_no_environment_names(self):
        modules = (
            bank_connection,
            main_window,
            bank_pages,
            transactions_page,
            reports_page,
            settings_page,
            pay_periods,
            reconciliation_page,
            reports,
        )

        violations = []
        for module in modules:
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if "Production" in node.value or "Sandbox" in node.value:
                    violations.append(
                        f"{module.__name__}:{getattr(node, 'lineno', '?')}: {node.value!r}"
                    )

        self.assertEqual(
            violations,
            [],
            "Release-facing copy must not expose development environment names.",
        )


if __name__ == "__main__":
    unittest.main()
