from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..backups import BackupManager
from ..bank_sync import (
    BankSyncReport,
    connection_status,
    set_bills_account,
    sync_production_to_sqlite,
    sync_sandbox_to_sqlite,
)
from ..database import Database
from ..settings import load_settings
from .busy_cursor import begin_busy_cursor, end_busy_cursor
from .context_menu import (
    begin_table_context_menu,
    copy_text,
    show_table_context_menu,
)
from .table_sort import (
    SortableTableWidgetItem,
    begin_table_refresh,
    configure_resizable_columns,
    end_table_refresh,
    set_sortable,
)


def money(cents: int | None) -> str:
    if cents is None:
        return "—"
    value = int(cents)
    if value < 0:
        return f"$-{abs(value) / 100:,.2f}"
    return f"${value / 100:,.2f}"


class _SyncWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, database: Database, environment: str):
        super().__init__()
        self.database = database
        self.environment = environment

    @Slot()
    def run(self) -> None:
        try:
            if self.environment == "production":
                result = sync_production_to_sqlite(self.database)
            else:
                result = sync_sandbox_to_sqlite(self.database)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class AccountsPage(QWidget):
    def __init__(
        self,
        database: Database,
        on_data_changed: Callable[[], None],
        backup_manager: BackupManager,
        *,
        embedded: bool = False,
        auto_refresh: bool = True,
    ):
        super().__init__()
        self.database = database
        self.on_data_changed = on_data_changed
        self.backup_manager = backup_manager
        self._thread: QThread | None = None
        self._worker: _SyncWorker | None = None
        self._busy_cursor_active = False

        self.embedded = embedded
        root = QVBoxLayout(self)
        root.setContentsMargins(
            0 if embedded else 28,
            0 if embedded else 24,
            0 if embedded else 28,
            0 if embedded else 28,
        )
        root.setSpacing(12 if embedded else 16)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.connection_card, self.connection_label, self.connection_value, self.connection_detail = self._summary_card(
            "Bank connection"
        )
        self.account_card, _, self.account_value, self.account_detail = self._summary_card(
            "Accounts"
        )
        self.sync_card, _, self.sync_value, self.sync_detail = self._summary_card(
            "Last sync"
        )
        cards.addWidget(self.connection_card)
        cards.addWidget(self.account_card)
        cards.addWidget(self.sync_card)
        if not embedded:
            root.addLayout(cards)

        toolbar = QHBoxLayout()
        self.sync_button = QPushButton("Sync bank data")
        self.sync_button.setObjectName("PrimaryButton")
        self.sync_button.clicked.connect(self._start_sync)
        self.sync_button.setVisible(not embedded)
        toolbar.addWidget(self.sync_button)

        refresh = QPushButton("Refresh local")
        refresh.setObjectName("SecondaryButton")
        refresh.clicked.connect(self.refresh)
        refresh.setVisible(not embedded)
        toolbar.addWidget(refresh)

        self.use_button = QPushButton("Use selected as Bills Checking")
        self.use_button.setObjectName("SecondaryButton")
        self.use_button.clicked.connect(self._use_selected)
        toolbar.addWidget(self.use_button)

        self.source_button = QPushButton(
            "Use selected as Transfer Source"
        )
        self.source_button.setObjectName("SecondaryButton")
        self.source_button.clicked.connect(
            self._use_selected_as_transfer_source
        )
        toolbar.addWidget(self.source_button)
        self.use_button.setEnabled(False)
        self.source_button.setEnabled(False)

        toolbar.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        toolbar.addWidget(self.status)
        root.addLayout(toolbar)

        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel(
            "Connected accounts" if embedded else "Plaid accounts"
        )
        title.setObjectName("SectionTitle")
        hint = QLabel(
            "Choose which checking account receives the aggregate bill transfer."
            if embedded
            else "Select the account that represents Bills Checking."
        )
        hint.setObjectName("Muted")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(hint)
        layout.addLayout(header)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Account",
                "Mask",
                "Type",
                "Subtype",
                "Current",
                "Available",
                "Verified",
                "Bills Checking",
                "Transfer Source",
            ]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        configure_resizable_columns(
            self.table,
            (280, 90, 115, 135, 125, 125, 110, 150, 160),
        )
        set_sortable(self.table)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.table.customContextMenuRequested.connect(
            self._show_table_context_menu
        )
        self.table.itemSelectionChanged.connect(
            self._update_role_buttons
        )
        layout.addWidget(self.table)
        root.addWidget(card, 1)

        if auto_refresh:
            self.refresh()

    def _show_table_context_menu(self, position) -> None:
        context = begin_table_context_menu(
            self.table,
            position,
            parent=self,
        )
        if context is None:
            return
        menu, row, _column = context

        account_item = self.table.item(row, 0)
        raw_id = (
            None
            if account_item is None
            else account_item.data(Qt.ItemDataRole.UserRole)
        )
        account_id = "" if raw_id is None else str(raw_id)
        if account_id:
            copy_id = menu.addAction("Copy account ID")
            copy_id.triggered.connect(
                lambda checked=False, value=account_id:
                    copy_text(value)
            )

        bills_item = self.table.item(row, 7)
        is_bills = (
            bills_item is not None
            and bills_item.text().strip() == "YES"
        )
        use_bills = menu.addAction(
            "Already Bills Checking"
            if is_bills
            else "Use as Bills Checking"
        )
        use_bills.setEnabled(not is_bills)
        use_bills.triggered.connect(self._use_selected)

        source_item = self.table.item(row, 8)
        is_source = (
            source_item is not None
            and source_item.text().strip() == "YES"
        )
        use_source = menu.addAction(
            "Already Default Transfer Source"
            if is_source
            else "Use as Default Transfer Source"
        )
        use_source.setEnabled(not is_source)
        use_source.triggered.connect(
            self._use_selected_as_transfer_source
        )

        if not self.embedded:
            menu.addSeparator()
            sync = menu.addAction("Sync Accounts")
            sync.setEnabled(self.sync_button.isEnabled())
            sync.triggered.connect(self._start_sync)
            refresh = menu.addAction("Refresh local")
            refresh.triggered.connect(self.refresh)
        show_table_context_menu(self.table, menu, position)

    def _summary_card(self, label_text: str):
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        label = QLabel(label_text)
        label.setObjectName("MetricLabel")
        value = QLabel("—")
        value.setObjectName("MetricValue")
        detail = QLabel("")
        detail.setObjectName("Muted")
        layout.addWidget(label)
        layout.addWidget(value)
        layout.addWidget(detail)
        return card, label, value, detail

    def refresh(self) -> None:
        selected_account_id = self._selected_account_id()
        environment = load_settings(require_keys=False).environment
        connection = connection_status(environment)

        self.connection_label.setText("Bank connection")
        self.sync_button.setText("Sync Accounts")

        if connection["credential_present"] and connection["keys_configured"]:
            self.connection_value.setText("Connected")
            self.connection_detail.setText("Secure bank connection is active.")
        elif connection["credential_present"]:
            self.connection_value.setText("Needs attention")
            self.connection_detail.setText(
                "Bank connection exists, but API credentials are incomplete."
            )
        else:
            self.connection_value.setText("Not linked")
            self.connection_detail.setText(
                "No bank connection was found."
            )

        accounts = self.database.list_bank_accounts(environment)
        self.account_value.setText(str(len(accounts)))
        selected = next(
            (row for row in accounts if int(row["is_bills_checking"])),
            None,
        )
        transfer_source = self.database.default_transfer_source_account(
            environment
        )
        role_details: list[str] = []
        role_details.append(
            f"Bills Checking: {selected['name']}"
            if selected
            else "Bills Checking not selected"
        )
        role_details.append(
            f"Transfer Source: {transfer_source['name']}"
            if transfer_source is not None
            else "Transfer Source not selected"
        )
        self.account_detail.setText(" · ".join(role_details))

        state = self.database.get_sync_state(environment)
        if state is None or not state["last_sync_at"]:
            self.sync_value.setText("Never")
            self.sync_detail.setText(
                "Click Sync Accounts to refresh balances and transactions."
            )
        else:
            self.sync_value.setText("Synced")
            status = str(state["transactions_update_status"] or "unknown")
            self.sync_detail.setText(f"{state['last_sync_at']} · {status}")

        self.status.setText(
            "Sync refreshes balances and transactions using the existing bank connection."
        )

        sorting = begin_table_refresh(self.table)
        self.table.setRowCount(len(accounts))
        for row_index, row in enumerate(accounts):
            values = [
                (row["name"] or "(unnamed account)", str(row["name"] or "").casefold()),
                (f"••••{row['mask']}" if row["mask"] else "—", str(row["mask"] or "")),
                (row["account_type"] or "—", str(row["account_type"] or "").casefold()),
                (row["account_subtype"] or "—", str(row["account_subtype"] or "").casefold()),
                (
                    money(row["current_balance_cents"]),
                    -9_000_000_000_000_000_000 if row["current_balance_cents"] is None else int(row["current_balance_cents"]),
                ),
                (
                    money(row["available_balance_cents"]),
                    -9_000_000_000_000_000_000 if row["available_balance_cents"] is None else int(row["available_balance_cents"]),
                ),
                (
                    "✓ Verified" if int(row["verified"] or 0) else "—",
                    int(row["verified"] or 0),
                ),
                (
                    "YES" if int(row["is_bills_checking"]) else "",
                    int(row["is_bills_checking"]),
                ),
                (
                    "YES"
                    if (
                        transfer_source is not None
                        and str(transfer_source["plaid_account_id"])
                        == str(row["plaid_account_id"])
                    )
                    else "",
                    int(
                        transfer_source is not None
                        and str(transfer_source["plaid_account_id"])
                        == str(row["plaid_account_id"])
                    ),
                ),
            ]
            for col, (value, sort_value) in enumerate(values):
                item = SortableTableWidgetItem(
                    str(value),
                    sort_value=sort_value,
                    user_data=row["plaid_account_id"],
                )
                if col in {4, 5}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if col == 6 and int(row["verified"] or 0):
                    item.setForeground(QColor("#c8ff3d"))
                self.table.setItem(row_index, col, item)
        end_table_refresh(self.table, sorting)

        selection_restored = self._select_account_id(
            selected_account_id
        )
        checking_rows = [
            index
            for index, account in enumerate(accounts)
            if str(account["account_type"] or "").casefold()
                == "depository"
            and str(account["account_subtype"] or "").casefold()
                == "checking"
        ]
        if (
            not selection_restored
            and selected is None
            and len(checking_rows) == 1
        ):
            self.table.selectRow(checking_rows[0])
            if self.embedded:
                self.status.setText(
                    "One checking account was found. Review it, then "
                    "choose Use selected as Bills Checking."
                )
        elif (
            not selection_restored
            and selected is not None
            and transfer_source is None
        ):
            source_rows = [
                index
                for index in checking_rows
                if not int(accounts[index]["is_bills_checking"] or 0)
            ]
            if len(source_rows) == 1:
                self.table.selectRow(source_rows[0])
                if self.embedded:
                    self.status.setText(
                        "Bills Checking is set. Review the remaining checking "
                        "account, then choose Use selected as Transfer Source."
                    )
        self._update_role_buttons()

    def _selected_account_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") or None

    def _select_account_id(self, account_id: str | None) -> bool:
        if not account_id:
            return False
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == account_id:
                self.table.selectRow(row)
                self.table.setCurrentCell(row, 0)
                return True
        return False

    def _update_role_buttons(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.use_button.setEnabled(False)
            self.source_button.setEnabled(False)
            self.use_button.setText("Use selected as Bills Checking")
            self.source_button.setText(
                "Use selected as Transfer Source"
            )
            return

        account_type = self.table.item(row, 2)
        account_subtype = self.table.item(row, 3)
        bills_item = self.table.item(row, 7)
        source_item = self.table.item(row, 8)
        is_checking = (
            account_type is not None
            and account_subtype is not None
            and account_type.text().strip().casefold() == "depository"
            and account_subtype.text().strip().casefold() == "checking"
        )
        is_bills = (
            bills_item is not None
            and bills_item.text().strip() == "YES"
        )
        is_source = (
            source_item is not None
            and source_item.text().strip() == "YES"
        )

        self.use_button.setEnabled(is_checking and not is_bills)
        self.use_button.setText(
            "Selected is Bills Checking"
            if is_bills
            else "Use selected as Bills Checking"
        )
        self.source_button.setEnabled(
            is_checking and not is_bills and not is_source
        )
        self.source_button.setText(
            "Selected is Transfer Source"
            if is_source
            else "Use selected as Transfer Source"
        )

    @staticmethod
    def _account_label(account) -> str:
        label = str(account["name"] or "(unnamed account)")
        mask = str(account["mask"] or "")
        if mask:
            label += f" ••••{mask}"
        return label

    def _use_selected(self) -> None:
        account_id = self._selected_account_id()
        if not account_id:
            self.status.setText("Select an account first.")
            return
        try:
            self.status.setText("Saving Bills Checking selection…")
            self.backup_manager.create_backup(
                "pre-bills-account-role-change"
            )
            environment = load_settings(require_keys=False).environment
            set_bills_account(self.database, environment, account_id)
            saved = self.database.get_bank_account(
                environment,
                account_id,
            )
            if saved is None or not int(saved["is_bills_checking"] or 0):
                raise RuntimeError(
                    "Bills Checking selection could not be verified after saving."
                )
            label = self._account_label(saved)
        except Exception as exc:
            self.status.setText(str(exc))
            return

        self.refresh()
        self._select_account_id(account_id)
        self._update_role_buttons()
        self.on_data_changed()
        self.status.setText(f"Bills Checking saved: {label}.")

    def _use_selected_as_transfer_source(self) -> None:
        account_id = self._selected_account_id()
        if not account_id:
            self.status.setText("Select an account first.")
            return
        try:
            self.status.setText("Saving Transfer Source selection…")
            self.backup_manager.create_backup(
                "pre-transfer-source-role-change"
            )
            environment = load_settings(require_keys=False).environment
            self.database.set_default_transfer_source_account(
                environment,
                account_id,
            )
            saved = self.database.default_transfer_source_account(
                environment
            )
            if (
                saved is None
                or str(saved["plaid_account_id"]) != account_id
            ):
                raise RuntimeError(
                    "Transfer Source selection could not be verified after saving."
                )
            label = self._account_label(saved)
        except Exception as exc:
            self.status.setText(str(exc))
            return

        self.refresh()
        self._select_account_id(account_id)
        self._update_role_buttons()
        self.on_data_changed()
        self.status.setText(f"Transfer Source saved: {label}.")

    def _start_sync(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        environment = load_settings(require_keys=False).environment
        self.sync_button.setEnabled(False)
        self.status.setText(
            "Syncing balances and transactions…"
        )
        if not self._busy_cursor_active:
            begin_busy_cursor()
            self._busy_cursor_active = True

        thread = QThread(self)
        worker = _SyncWorker(self.database, environment)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._sync_finished)
        worker.failed.connect(self._sync_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._thread_finished)

        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _sync_finished(self, result: Any) -> None:
        report = result
        if not isinstance(report, BankSyncReport):
            self.status.setText("Bank sync returned an unexpected result.")
            return
        self.refresh()

        auto_detail = ""
        if report.auto_matched_count:
            auto_detail += f" · {report.auto_matched_count} bill(s) auto-matched"
        if report.auto_internal_transfer_count:
            auto_detail += (
                f" · {report.auto_internal_transfer_count} internal transfer pair(s) verified"
            )
        if report.auto_review_count:
            auto_detail += f" · {report.auto_review_count} ambiguous payment(s) left for review"

        self.status.setText(
            f"Sync complete: {report.account_count} accounts, "
            f"{report.transaction_count} stored transactions "
            f"(+{report.added_count} / ~{report.modified_count} / -{report.removed_count})"
            f"{auto_detail}."
        )
        self.on_data_changed()

    @Slot(str)
    def _sync_failed(self, message: str) -> None:
        self.status.setText(f"Bank sync failed: {message}")

    @Slot()
    def _thread_finished(self) -> None:
        self.sync_button.setEnabled(True)
        if self._busy_cursor_active:
            end_busy_cursor()
            self._busy_cursor_active = False
        self._thread = None
        self._worker = None
