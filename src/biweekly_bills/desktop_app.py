from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .backups import BackupManager
from .database import Database, default_database_path
from .desktop_integration import APP_ID, APP_NAME, ensure_desktop_integration
from .ods_importer import import_ods_history
from .production_cutover import purge_sandbox_transactions_for_production


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="biweekly-bills-app",
        description="Bi-Weekly Bills PySide6 desktop application.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=default_database_path(),
        help="SQLite database path (defaults to the XDG user data directory).",
    )
    parser.add_argument(
        "--import-ods",
        type=Path,
        help="Read-only import of an existing ODS workbook before opening the app.",
    )
    parser.add_argument(
        "--legacy-year",
        type=int,
        default=2026,
        help="Year assigned to legacy month tabs without a year suffix.",
    )
    parser.add_argument(
        "--import-only",
        action="store_true",
        help="Import the workbook and exit without starting the GUI.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    database = Database(args.db)
    backup_manager = BackupManager(database)
    backup_manager.backup_before_schema_migration()
    database.initialize()

    cleanup = purge_sandbox_transactions_for_production(
        database,
        backup_manager,
    )
    if cleanup.transaction_count:
        print(
            "Production cutover cleanup: removed "
            f"{cleanup.transaction_count} Sandbox transaction(s) and "
            "their reconciliation/sync state after creating a safety backup."
        )

    if args.import_ods:
        backup_manager.create_backup("pre-ods-import")
        report = import_ods_history(
            args.import_ods,
            database,
            legacy_year=args.legacy_year,
        )
        print(
            "ODS import complete: "
            f"{report.setup_bills} Setup bills, "
            f"{report.bill_instances} monthly bill rows, "
            f"{report.months_seen} month sheets. "
            "Source workbook verified byte-for-byte unchanged."
        )

    if args.import_only:
        return 0

    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from .branding import application_icon
    from .ui.main_window import MainWindow
    from .ui.theme import APP_STYLESHEET

    try:
        ensure_desktop_integration()
    except OSError as exc:
        print(
            f"warning: desktop integration could not be refreshed: {exc}",
            file=sys.stderr,
        )

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_NAME)

    # Wayland shells match the running surface to biweekly-bills.desktop
    # through this desktop file / app-id value.
    QGuiApplication.setDesktopFileName(APP_ID)

    icon = application_icon()
    app.setWindowIcon(icon)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    window = MainWindow(database, backup_manager)
    window.setWindowIcon(icon)
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
