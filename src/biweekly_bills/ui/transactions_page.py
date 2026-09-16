from __future__ import annotations

from datetime import date
from typing import Any, Callable

from PySide6.QtCore import QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..auto_reconcile import (
    best_review_candidate,
    best_review_candidates,
    expected_payment_cents,
)
from ..backups import BackupManager
from ..bank_sync import suggested_bill_for_transaction
from ..database import Database
from ..merchant_profiles import (
    counterparty_display,
    counterparty_kind,
    meaningful_merchant_name,
    merchant_key,
    plaid_transaction_metadata,
    safe_remote_logo_url,
)
from ..funding_transfer import (
    build_funding_transfer_preview,
    suggested_funding_scope,
    undo_funding_transfer_validation,
    validate_funding_transfer,
)
from ..reconciliation import (
    accept_match,
    build_preview,
    candidate_bill_instances,
    ignore_transaction,
    suggested_bill_instance,
    transaction_date,
    undo_reconciliation,
)
from ..settings import load_settings
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


MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

_MERCHANT_KEY_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_MAX_LOGO_BYTES = 1024 * 1024
_MAX_LOGO_DOWNLOADS = 6


def money(cents: int | None) -> str:
    if cents is None:
        return "—"
    value = int(cents)
    if value < 0:
        return "-$" + f"{abs(value) / 100:,.2f}"
    return "$" + f"{value / 100:,.2f}"


def plaid_amount(cents: int) -> str:
    if cents < 0:
        return "+$" + f"{abs(cents) / 100:,.2f}"
    return money(cents)


class TransactionsPage(QWidget):
    def __init__(
        self,
        database: Database,
        on_data_changed: Callable[[], None],
        backup_manager: BackupManager,
        *,
        auto_refresh: bool = True,
    ):
        super().__init__()
        self.database = database
        self.on_data_changed = on_data_changed
        self.backup_manager = backup_manager
        self.selected_transaction_id: str | None = None
        self._filters_initialized = False
        self._rows: list[Any] = []
        self._review_cache: dict[str, tuple[Any | None, bool]] = {}
        self._merchant_profiles: dict[str, Any] = {}
        self._merchant_icons: dict[str, QIcon] = {}
        self._logo_queue: list[tuple[str, str]] = []
        self._logo_requested: set[str] = set()
        self._logo_pending: dict[QNetworkReply, str] = {}
        self._network = QNetworkAccessManager(self)
        self._account_icon = QIcon.fromTheme("bank")
        if self._account_icon.isNull():
            self._account_icon = QIcon.fromTheme("wallet-open")
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(160)
        self._filter_timer.timeout.connect(self._apply_filters)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(14)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search merchant or description…")
        self.search.textChanged.connect(self._schedule_filter)
        toolbar.addWidget(self.search, 2)

        self.account_filter = QComboBox()
        self.account_filter.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self.account_filter, 1)

        self.period_filter = QComboBox()
        self.period_filter.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.period_filter, 1)

        self.state_filter = QComboBox()
        self.state_filter.addItem("Posted only", "posted")
        self.state_filter.addItem("All states", "all")
        self.state_filter.addItem("Pending only", "pending")
        self.state_filter.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self.state_filter, 1)

        self.match_filter = QComboBox()
        self.match_filter.addItem("All reconciliation states", "all")
        self.match_filter.addItem("Suggested", "suggested")
        self.match_filter.addItem("Needs review", "review")
        self.match_filter.addItem("Reconciled", "matched")
        self.match_filter.addItem("Ignored", "ignored")
        self.match_filter.addItem("Funding transfers", "funding")
        self.match_filter.addItem("Internal transfers", "internal")
        self.match_filter.addItem("Unresolved", "unresolved")
        self.match_filter.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self.match_filter, 1)

        refresh = QPushButton("Refresh local")
        refresh.setObjectName("SecondaryButton")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)

        root.addLayout(toolbar)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        self.title_label = QLabel("Bank transactions")
        self.title_label.setObjectName("SectionTitle")
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.summary)
        card_layout.addLayout(header)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Date",
                "Merchant / Description",
                "Account",
                "Amount",
                "State",
                "Suggested bill",
                "Reconciliation",
            ]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setIconSize(QSize(24, 24))
        self.table.verticalHeader().setVisible(False)
        configure_resizable_columns(
            self.table,
            (120, 320, 230, 125, 105, 230, 240),
        )
        set_sortable(self.table)
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.table.customContextMenuRequested.connect(
            self._show_table_context_menu
        )
        self.table.itemSelectionChanged.connect(self._load_selected)
        card_layout.addWidget(self.table)
        root.addWidget(card, 1)

        self.reconcile_card = QFrame()
        self.reconcile_card.setObjectName("Card")
        reconcile_layout = QVBoxLayout(self.reconcile_card)
        reconcile_layout.setContentsMargins(18, 16, 18, 18)
        reconcile_layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("Reconcile selected transaction")
        title.setObjectName("SectionTitle")
        self.selected_label = QLabel("Select a posted payment transaction above.")
        self.selected_label.setObjectName("Muted")
        top.addWidget(title)
        top.addSpacing(10)
        top.addWidget(self.selected_label)
        top.addStretch(1)
        reconcile_layout.addLayout(top)

        target_row = QHBoxLayout()
        target_label = QLabel("Target bill")
        target_label.setObjectName("MetricLabel")
        target_row.addWidget(target_label)
        self.target_bill = QComboBox()
        self.target_bill.currentIndexChanged.connect(self._update_preview)
        target_row.addWidget(self.target_bill, 2)
        self.period_label = QLabel("")
        self.period_label.setObjectName("Pill")
        target_row.addWidget(self.period_label)
        target_row.addStretch(1)
        reconcile_layout.addLayout(target_row)

        metrics = QHBoxLayout()
        self.expected_value = self._metric(metrics, "Expected")
        self.bank_value = self._metric(metrics, "Bank amount")
        self.existing_paid_value = self._metric(metrics, "Existing Paid")
        self.difference_value = self._metric(metrics, "Bank − Expected")
        reconcile_layout.addLayout(metrics)

        self.warning = QLabel("")
        self.warning.setObjectName("Muted")
        self.warning.setWordWrap(True)
        reconcile_layout.addWidget(self.warning)

        actions = QHBoxLayout()
        self.accept_button = QPushButton("Accept Match")
        self.accept_button.setObjectName("PrimaryButton")
        self.accept_button.clicked.connect(self._accept)
        actions.addWidget(self.accept_button)

        self.ignore_button = QPushButton("Ignore")
        self.ignore_button.setObjectName("SecondaryButton")
        self.ignore_button.clicked.connect(self._ignore)
        actions.addWidget(self.ignore_button)

        self.undo_button = QPushButton("Undo Match / Ignore")
        self.undo_button.setObjectName("SecondaryButton")
        self.undo_button.clicked.connect(self._undo)
        actions.addWidget(self.undo_button)

        actions.addStretch(1)
        self.action_status = QLabel("")
        self.action_status.setObjectName("Muted")
        actions.addWidget(self.action_status)
        reconcile_layout.addLayout(actions)

        root.addWidget(self.reconcile_card)

        self.funding_card = QFrame()
        self.funding_card.setObjectName("Card")
        funding_layout = QVBoxLayout(self.funding_card)
        funding_layout.setContentsMargins(18, 16, 18, 18)
        funding_layout.setSpacing(10)

        funding_top = QHBoxLayout()
        funding_title = QLabel("Validate Bills funding transfer")
        funding_title.setObjectName("SectionTitle")
        self.funding_selected_label = QLabel(
            "Select an incoming transfer deposited into Bills Checking."
        )
        self.funding_selected_label.setObjectName("Muted")
        funding_top.addWidget(funding_title)
        funding_top.addSpacing(10)
        funding_top.addWidget(self.funding_selected_label)
        funding_top.addStretch(1)
        funding_layout.addLayout(funding_top)

        funding_scope_row = QHBoxLayout()
        funding_scope_label = QLabel("Funding scope")
        funding_scope_label.setObjectName("MetricLabel")
        funding_scope_row.addWidget(funding_scope_label)
        self.funding_scope = QComboBox()
        self.funding_scope.addItem("1st pay period", "1st")
        self.funding_scope.addItem("15th pay period", "15th")
        self.funding_scope.addItem("Whole month", "month")
        self.funding_scope.currentIndexChanged.connect(
            self._update_funding_transfer_preview
        )
        funding_scope_row.addWidget(self.funding_scope)
        self.funding_period_label = QLabel("")
        self.funding_period_label.setObjectName("Pill")
        funding_scope_row.addWidget(self.funding_period_label)
        funding_scope_row.addStretch(1)
        funding_layout.addLayout(funding_scope_row)

        funding_metrics = QHBoxLayout()
        self.funding_expected_value = self._metric(
            funding_metrics, "Scheduled transfer target"
        )
        self.funding_actual_value = self._metric(
            funding_metrics, "Bank transfer"
        )
        self.funding_difference_value = self._metric(
            funding_metrics, "Transfer − Scheduled"
        )
        funding_layout.addLayout(funding_metrics)

        self.funding_warning = QLabel("")
        self.funding_warning.setObjectName("Muted")
        self.funding_warning.setWordWrap(True)
        funding_layout.addWidget(self.funding_warning)

        funding_actions = QHBoxLayout()
        self.validate_funding_button = QPushButton("Validate Funding Transfer")
        self.validate_funding_button.setObjectName("PrimaryButton")
        self.validate_funding_button.clicked.connect(
            self._validate_funding_transfer
        )
        funding_actions.addWidget(self.validate_funding_button)

        self.undo_funding_button = QPushButton("Undo Funding Validation")
        self.undo_funding_button.setObjectName("SecondaryButton")
        self.undo_funding_button.clicked.connect(
            self._undo_funding_validation
        )
        funding_actions.addWidget(self.undo_funding_button)
        funding_actions.addStretch(1)

        self.funding_action_status = QLabel("")
        self.funding_action_status.setObjectName("Muted")
        funding_actions.addWidget(self.funding_action_status)
        funding_layout.addLayout(funding_actions)

        root.addWidget(self.funding_card)
        self.funding_card.setVisible(False)

        self.transaction_detail_card = QFrame()
        self.transaction_detail_card.setObjectName("Card")
        detail_layout = QVBoxLayout(self.transaction_detail_card)
        detail_layout.setContentsMargins(18, 14, 18, 16)
        detail_layout.setSpacing(8)

        detail_title_row = QHBoxLayout()
        detail_title = QLabel("Transaction Detail")
        detail_title.setObjectName("SectionTitle")
        self.transaction_detail_note = QLabel(
            "Select a transaction to see Plaid merchant metadata."
        )
        self.transaction_detail_note.setObjectName("Muted")
        detail_title_row.addWidget(detail_title)
        detail_title_row.addSpacing(10)
        detail_title_row.addWidget(self.transaction_detail_note)
        detail_title_row.addStretch(1)
        detail_layout.addLayout(detail_title_row)

        detail_grid = QGridLayout()
        detail_grid.setHorizontalSpacing(12)
        detail_grid.setVerticalSpacing(5)
        detail_grid.setColumnStretch(1, 1)
        detail_grid.setColumnStretch(3, 1)
        self._transaction_detail_fields: dict[
            str, tuple[QLabel, QLabel]
        ] = {}

        detail_definitions = (
            ("merchant", "Merchant", 0, 0),
            ("description", "Bank description", 0, 2),
            ("website", "Website", 1, 0),
            ("entity_id", "Plaid entity", 1, 2),
            ("category", "Plaid category", 2, 0),
            ("payment_channel", "Payment channel", 2, 2),
            ("counterparty", "Counterparty", 3, 0),
            ("location", "Location", 3, 2),
        )
        for key, label_text, row_index, column in detail_definitions:
            label = QLabel(label_text)
            label.setObjectName("MetricLabel")
            value = QLabel("")
            value.setObjectName("Muted")
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            detail_grid.addWidget(label, row_index, column)
            detail_grid.addWidget(value, row_index, column + 1)
            self._transaction_detail_fields[key] = (label, value)
            setattr(self, f"detail_{key}_value", value)

        detail_layout.addLayout(detail_grid)
        root.addWidget(self.transaction_detail_card)

        self._clear_reconciliation_panel()
        self._clear_funding_transfer_panel()
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
        menu, row_index, _column = context

        id_item = self.table.item(row_index, 0)
        tx_id = (
            ""
            if id_item is None
            else str(
                id_item.data(Qt.ItemDataRole.UserRole) or ""
            )
        )
        row = (
            None
            if not tx_id
            else self.database.get_bank_transaction(
                self._environment(),
                tx_id,
            )
        )

        if tx_id:
            copy_id = menu.addAction("Copy transaction ID")
            copy_id.triggered.connect(
                lambda checked=False, value=tx_id:
                    copy_text(value)
            )

        if row is not None and row["plaid_account_id"]:
            account_action = menu.addAction(
                "Filter to this account"
            )
            account_action.triggered.connect(
                lambda checked=False,
                account_id=str(row["plaid_account_id"]):
                    self._filter_to_account(account_id)
            )

        menu.addSeparator()

        if self.accept_button.isEnabled():
            accept = menu.addAction("Accept current match")
            accept.triggered.connect(self._accept)

        if self.ignore_button.isEnabled():
            ignore = menu.addAction("Ignore transaction")
            ignore.triggered.connect(self._ignore)

        if self.undo_button.isEnabled():
            undo = menu.addAction("Undo match / ignore")
            undo.triggered.connect(self._undo)

        if self.validate_funding_button.isEnabled():
            validate = menu.addAction(
                "Validate funding transfer"
            )
            validate.triggered.connect(
                self._validate_funding_transfer
            )

        if self.undo_funding_button.isEnabled():
            undo_funding = menu.addAction(
                "Undo funding validation"
            )
            undo_funding.triggered.connect(
                self._undo_funding_validation
            )

        review_index = self.match_filter.findData("review")
        if review_index >= 0:
            menu.addSeparator()
            show_review = menu.addAction(
                "Show Needs review transactions"
            )
            show_review.triggered.connect(
                lambda checked=False, index=review_index:
                    self.match_filter.setCurrentIndex(index)
            )

        menu.addSeparator()
        refresh = menu.addAction("Refresh Transactions")
        refresh.triggered.connect(self.refresh)
        show_table_context_menu(self.table, menu, position)

    def _filter_to_account(self, account_id: str) -> None:
        index = self.account_filter.findData(account_id)
        if index >= 0:
            self.account_filter.setCurrentIndex(index)

    def _environment(self) -> str:
        return load_settings(require_keys=False).environment

    def _metric(self, layout: QHBoxLayout, label_text: str) -> QLabel:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 8, 12, 8)
        label = QLabel(label_text)
        label.setObjectName("MetricLabel")
        value = QLabel("—")
        value.setObjectName("SectionTitle")
        box.addWidget(label)
        box.addWidget(value)
        layout.addWidget(card, 1)
        return value

    def _rebuild_account_filter(self) -> None:
        selected = self.account_filter.currentData()
        environment = self._environment()
        accounts = self.database.list_bank_accounts(environment)

        self.account_filter.blockSignals(True)
        self.account_filter.clear()
        self.account_filter.addItem("All checking accounts", "__checking__")
        self.account_filter.addItem("All accounts", None)
        for account in accounts:
            label = str(account["name"] or "(unnamed)")
            if account["mask"]:
                label += f" ••••{account['mask']}"
            self.account_filter.addItem(label, account["plaid_account_id"])

        if selected is not None:
            index = self.account_filter.findData(selected)
            if index >= 0:
                self.account_filter.setCurrentIndex(index)
        elif not self._filters_initialized:
            self.account_filter.setCurrentIndex(0)
        self.account_filter.blockSignals(False)

    @staticmethod
    def _period_key(year: int, month: int) -> str:
        return f"{year:04d}-{month:02d}"

    @staticmethod
    def _period_from_key(value: Any) -> tuple[int, int] | None:
        if value in (None, ""):
            return None
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return int(value[0]), int(value[1])
        year_text, month_text = str(value).split("-", 1)
        return int(year_text), int(month_text)

    def _rebuild_period_filter(
        self,
        periods: list[tuple[int, int]],
    ) -> None:
        selected = self.period_filter.currentData()
        selected_all_dates = (
            self._filters_initialized and selected is None
        )
        if selected is not None:
            try:
                selected_period = self._period_from_key(selected)
                selected = (
                    self._period_key(*selected_period)
                    if selected_period is not None
                    else None
                )
            except (TypeError, ValueError):
                selected = None

        today = date.today()
        current_period = (today.year, today.month)
        current_period_key = self._period_key(*current_period)

        self.period_filter.blockSignals(True)
        self.period_filter.clear()
        self.period_filter.addItem(
            f"{MONTH_NAMES[today.month - 1]} {today.year} (current)",
            current_period_key,
        )
        self.period_filter.addItem("All dates", None)
        for year, month in periods:
            if (year, month) == current_period:
                continue
            self.period_filter.addItem(
                f"{MONTH_NAMES[month - 1]} {year}",
                self._period_key(year, month),
            )

        if selected is not None:
            index = self.period_filter.findData(selected)
            if index >= 0:
                self.period_filter.setCurrentIndex(index)
        elif selected_all_dates:
            all_dates_index = self.period_filter.findData(None)
            if all_dates_index >= 0:
                self.period_filter.setCurrentIndex(all_dates_index)
        elif not self._filters_initialized:
            self.period_filter.setCurrentIndex(0)
        self.period_filter.blockSignals(False)


    def _schedule_filter(self, *_args) -> None:
        self._filter_timer.start()

    def refresh(self) -> None:
        """Reload local bank data; filters/search then operate in memory."""
        environment = self._environment()
        self.title_label.setText("Bank transactions")

        periods = self.database.bank_transaction_periods(environment)
        self._rebuild_account_filter()
        self._rebuild_period_filter(periods)

        period = self._period_from_key(self.period_filter.currentData())
        if period is None:
            self._rows = self.database.list_bank_transactions(
                environment,
                limit=5000,
            )
        else:
            self._rows = self.database.list_bank_transactions(
                environment,
                limit=5000,
                year=period[0],
                month=period[1],
            )

        self._merchant_profiles = {
            str(profile["merchant_key"]): profile
            for profile in self.database.list_merchant_profiles(
                environment
            )
        }
        self._merchant_icons.clear()
        self._review_cache.clear()
        self._filters_initialized = True
        self._apply_filters()
        self._queue_missing_logos()

    def _merchant_icon(self, key: str) -> QIcon:
        if not key:
            return QIcon()
        cached = self._merchant_icons.get(key)
        if cached is not None:
            return cached

        profile = self._merchant_profiles.get(key)
        if profile is None or profile["logo_data"] is None:
            return QIcon()

        pixmap = QPixmap()
        if not pixmap.loadFromData(bytes(profile["logo_data"])):
            return QIcon()

        icon = QIcon(pixmap)
        self._merchant_icons[key] = icon
        return icon

    def _queue_missing_logos(self) -> None:
        present_keys = {
            merchant_key(meaningful_merchant_name(row))
            for row in self._rows
            if meaningful_merchant_name(row)
        }
        for key in sorted(present_keys):
            if not key or key in self._logo_requested:
                continue
            profile = self._merchant_profiles.get(key)
            if profile is None or profile["logo_data"] is not None:
                continue
            logo_url = safe_remote_logo_url(profile["logo_url"])
            if not logo_url:
                continue
            self._logo_requested.add(key)
            self._logo_queue.append((key, logo_url))
        self._pump_logo_queue()

    def _pump_logo_queue(self) -> None:
        while (
            self._logo_queue
            and len(self._logo_pending) < _MAX_LOGO_DOWNLOADS
        ):
            key, logo_url = self._logo_queue.pop(0)
            request = QNetworkRequest(QUrl(logo_url))
            request.setTransferTimeout(6000)
            request.setRawHeader(
                b"User-Agent",
                b"Bi-Weekly-Bills/merchant-logo-cache",
            )
            reply = self._network.get(request)
            self._logo_pending[reply] = key
            reply.finished.connect(
                lambda current=reply: self._logo_finished(current)
            )

    def _logo_finished(self, reply: QNetworkReply) -> None:
        key = self._logo_pending.pop(reply, "")
        try:
            if (
                key
                and reply.error()
                == QNetworkReply.NetworkError.NoError
            ):
                payload = bytes(reply.readAll())
                pixmap = QPixmap()
                if (
                    payload
                    and len(payload) <= _MAX_LOGO_BYTES
                    and pixmap.loadFromData(payload)
                ):
                    mime = str(
                        reply.header(
                            QNetworkRequest.KnownHeaders.ContentTypeHeader
                        )
                        or ""
                    ) or None
                    self.database.set_merchant_logo(
                        self._environment(),
                        key,
                        payload,
                        mime_type=mime,
                    )
                    profile = self.database.get_merchant_profile(
                        self._environment(),
                        key,
                    )
                    if profile is not None:
                        self._merchant_profiles[key] = profile
                    self._merchant_icons[key] = QIcon(pixmap)
                    self._apply_logo_to_visible_rows(key)
        finally:
            reply.deleteLater()
            self._pump_logo_queue()

    def _apply_logo_to_visible_rows(self, key: str) -> None:
        icon = self._merchant_icon(key)
        if icon.isNull():
            return
        for row_index in range(self.table.rowCount()):
            item = self.table.item(row_index, 1)
            if (
                item is not None
                and str(item.data(_MERCHANT_KEY_ROLE) or "") == key
            ):
                item.setIcon(icon)

    def _ensure_review_cache(self, rows: list[Any]) -> None:
        candidates = [
            row
            for row in rows
            if (
                not int(row["pending"] or 0)
                and int(row["amount_cents"]) > 0
                and row["reconciliation_disposition"] is None
                and row["internal_transfer_role"] is None
                and str(row["plaid_transaction_id"])
                not in self._review_cache
            )
        ]
        if not candidates:
            return
        self._review_cache.update(
            best_review_candidates(
                self.database,
                candidates,
                active_only=False,
            )
        )

    def _apply_filters(self, *_args) -> None:
        previous_tx = self.selected_transaction_id
        environment = self._environment()
        search = self.search.text().strip().casefold()
        account_id = self.account_filter.currentData()
        state = self.state_filter.currentData()
        match_state = self.match_filter.currentData()

        cheap_rows: list[Any] = []
        for row in self._rows:
            if account_id == "__checking__":
                if (
                    str(row["account_type"] or "").casefold() != "depository"
                    or str(row["account_subtype"] or "").casefold() != "checking"
                ):
                    continue
            elif account_id and row["plaid_account_id"] != account_id:
                continue

            if state == "posted" and int(row["pending"]):
                continue
            if state == "pending" and not int(row["pending"]):
                continue

            merchant = str(row["merchant_name"] or row["name"] or "")
            description = str(row["name"] or "")
            account_name = str(row["account_name"] or "")
            if (
                search
                and search
                not in f"{merchant} {description} {account_name}".casefold()
            ):
                continue
            cheap_rows.append(row)

        self._ensure_review_cache(cheap_rows)

        filtered: list[tuple[Any, str | None, str]] = []
        for row in cheap_rows:
            suggestion = suggested_bill_for_transaction(row)
            review_assessment = None
            review_ambiguous = False
            if (
                not int(row["pending"] or 0)
                and int(row["amount_cents"]) > 0
                and row["reconciliation_disposition"] is None
                and row["internal_transfer_role"] is None
            ):
                review_assessment, review_ambiguous = self._review_cache.get(
                    str(row["plaid_transaction_id"]),
                    (None, False),
                )

            review_suggestion = (
                review_assessment.bill_name
                if review_assessment is not None
                else None
            )
            display_suggestion = suggestion or review_suggestion
            disposition = row["reconciliation_disposition"]
            funding_scope = row["funding_validation_scope"]
            internal_role = row["internal_transfer_role"]
            internal_bill = row["internal_transfer_bill_name"]
            needs_review = (
                disposition is None
                and internal_role is None
                and funding_scope is None
                and review_assessment is not None
            )

            if internal_role == "source":
                reconciliation = (
                    f"Internal transfer paid → {internal_bill}"
                )
            elif internal_role == "destination":
                reconciliation = (
                    f"Internal transfer received → {internal_bill}"
                )
            elif funding_scope:
                scope_label = (
                    "Whole month"
                    if funding_scope == "month"
                    else f"{funding_scope} pay period"
                )
                reconciliation = f"Funding validated → {scope_label}"
            elif disposition == "matched":
                reconciliation = f"Matched → {row['reconciled_bill_name']}"
            elif disposition == "ignored":
                reconciliation = "Ignored"
            elif suggestion:
                reconciliation = "Suggested"
            elif needs_review:
                reconciliation = (
                    "Needs review · ambiguous"
                    if review_ambiguous
                    else "Needs review"
                )
            else:
                reconciliation = "Unresolved"

            if (
                match_state == "suggested"
                and reconciliation != "Suggested"
            ):
                continue
            if match_state == "review" and not needs_review:
                continue
            if (
                match_state == "matched"
                and disposition != "matched"
                and internal_role is None
            ):
                continue
            if match_state == "ignored" and disposition != "ignored":
                continue
            if match_state == "funding" and not funding_scope:
                continue
            if match_state == "internal" and internal_role is None:
                continue
            if (
                match_state == "unresolved"
                and reconciliation != "Unresolved"
            ):
                continue

            filtered.append((row, display_suggestion, reconciliation))

        self.table.blockSignals(True)
        sorting = begin_table_refresh(self.table)
        self.table.setRowCount(len(filtered))
        for index, (row, suggestion, reconciliation) in enumerate(filtered):
            counterparty = counterparty_display(row)
            kind = counterparty_kind(row)
            merchant_name = meaningful_merchant_name(row)
            profile_key = merchant_key(merchant_name)
            account = row["account_name"] or "(unknown account)"
            if row["account_mask"]:
                account += f" ••••{row['account_mask']}"

            raw_date = row["posted_date"] or row["authorized_date"] or ""
            values = [
                (raw_date or "—", str(raw_date)),
                (counterparty, str(counterparty).casefold()),
                (account, str(account).casefold()),
                (
                    plaid_amount(int(row["amount_cents"])),
                    int(row["amount_cents"]),
                ),
                (
                    "Pending" if int(row["pending"]) else "Posted",
                    int(row["pending"]),
                ),
                (
                    suggestion or "—",
                    str(suggestion or "").casefold(),
                ),
                (
                    reconciliation,
                    str(reconciliation).casefold(),
                ),
            ]
            for col, (value, sort_value) in enumerate(values):
                item = SortableTableWidgetItem(
                    str(value),
                    sort_value=sort_value,
                    user_data=row["plaid_transaction_id"],
                )
                if col == 1:
                    raw_description = str(row["name"] or "")
                    if kind == "merchant" and profile_key:
                        item.setData(_MERCHANT_KEY_ROLE, profile_key)
                        icon = self._merchant_icon(profile_key)
                        if not icon.isNull():
                            item.setIcon(icon)
                        item.setToolTip(
                            "Merchant"
                            + (
                                f" · {raw_description}"
                                if raw_description
                                and raw_description != counterparty
                                else ""
                            )
                        )
                    elif kind in {
                        "connected-account",
                        "account-transfer",
                    }:
                        if not self._account_icon.isNull():
                            item.setIcon(self._account_icon)
                        item.setToolTip(
                            "Connected account / account transfer"
                            + (
                                f" · {raw_description}"
                                if raw_description
                                else ""
                            )
                        )
                    elif raw_description:
                        item.setToolTip(raw_description)
                if col == 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(index, col, item)

        end_table_refresh(self.table, sorting)
        self.table.blockSignals(False)

        selected_row_index = None
        if previous_tx:
            for row_index in range(self.table.rowCount()):
                item = self.table.item(row_index, 0)
                if (
                    item is not None
                    and str(
                        item.data(Qt.ItemDataRole.UserRole) or ""
                    )
                    == previous_tx
                ):
                    selected_row_index = row_index
                    break

        if selected_row_index is not None:
            self.table.selectRow(selected_row_index)
        elif previous_tx:
            self.selected_transaction_id = None
            self._clear_reconciliation_panel()

        state_row = self.database.get_sync_state(environment)
        last_sync = (
            state_row["last_sync_at"]
            if state_row is not None
            else None
        )
        scope = self.period_filter.currentText() or "All dates"
        self.summary.setText(
            f"{len(filtered)} shown · {len(self._rows)} loaded for {scope}"
            + (
                f" · last sync {last_sync}"
                if last_sync
                else " · not synced yet"
            )
        )

    def _clear_transaction_detail(self) -> None:
        self.transaction_detail_note.setText(
            "Select a transaction to see Plaid merchant metadata."
        )
        self.transaction_detail_note.setVisible(True)
        for label, value in self._transaction_detail_fields.values():
            label.setVisible(False)
            value.setVisible(False)
            value.clear()

    def _load_transaction_detail(self, row: Any) -> None:
        merchant_name = meaningful_merchant_name(row)
        profile_key = merchant_key(merchant_name)
        profile = (
            self._merchant_profiles.get(profile_key)
            if profile_key
            else None
        )
        metadata = plaid_transaction_metadata(row["raw_json"])

        merchant_display = (
            str(profile["display_name"] or "")
            if profile is not None
            else ""
        ) or str(
            merchant_name
            or row["merchant_name"]
            or row["name"]
            or "(unnamed transaction)"
        )
        website = metadata.get("website", "")
        entity_id = metadata.get("entity_id", "")
        if profile is not None:
            website = website or str(profile["website"] or "")
            entity_id = entity_id or str(
                profile["merchant_entity_id"] or ""
            )

        fields = {
            "merchant": merchant_display,
            "description": str(row["name"] or ""),
            "website": website,
            "entity_id": entity_id,
            "category": metadata.get("category", ""),
            "payment_channel": metadata.get(
                "payment_channel",
                "",
            ),
            "counterparty": metadata.get("counterparty", ""),
            "location": metadata.get("location", ""),
        }

        visible_count = 0
        enriched_count = 0
        for key, (label, value) in self._transaction_detail_fields.items():
            text = str(fields.get(key, "") or "").strip()
            visible = bool(text)
            label.setVisible(visible)
            value.setVisible(visible)
            value.setText(text)
            if visible:
                visible_count += 1
                if key not in {"merchant", "description"}:
                    enriched_count += 1

        if enriched_count:
            self.transaction_detail_note.setText(
                "Merchant metadata supplied by Plaid."
            )
        elif visible_count:
            self.transaction_detail_note.setText(
                "Plaid did not provide additional merchant metadata "
                "for this transaction."
            )
        else:
            self.transaction_detail_note.setText(
                "No transaction metadata is available."
            )
        self.transaction_detail_note.setVisible(True)

    def _selected_row(self) -> Any | None:
        if not self.selected_transaction_id:
            return None
        return self.database.get_bank_transaction(
            self._environment(),
            self.selected_transaction_id,
        )

    def _load_selected(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        tx_id = str(items[0].data(Qt.ItemDataRole.UserRole) or "")
        if not tx_id:
            return
        self.selected_transaction_id = tx_id

        row = self._selected_row()
        if row is None:
            self._clear_reconciliation_panel()
            return

        self._load_transaction_detail(row)

        if row["internal_transfer_role"] == "destination":
            self._load_internal_transfer_evidence(row)
            return

        funding_mode = (
            row["funding_validation_scope"] is not None
            or (
                not int(row["pending"])
                and int(row["amount_cents"]) < 0
                and int(row["is_bills_checking"] or 0)
            )
        )
        self.reconcile_card.setVisible(not funding_mode)
        self.funding_card.setVisible(funding_mode)

        merchant = (
            row["merchant_name"]
            or row["name"]
            or "(unnamed transaction)"
        )
        self.selected_label.setText(
            f"{merchant} · {plaid_amount(int(row['amount_cents']))} · "
            f"{row['posted_date'] or row['authorized_date'] or 'no date'}"
        )

        candidates = candidate_bill_instances(self.database, row)
        self.target_bill.blockSignals(True)
        self.target_bill.clear()
        self.target_bill.addItem("Choose a bill…", None)
        for instance in candidates:
            label = str(instance["bill_name_snapshot"])
            due = instance["due_cents"]
            if due is not None:
                label += f" · expected {money(int(due))}"
            self.target_bill.addItem(label, int(instance["id"]))

        preferred_id = row["reconciled_bill_instance_id"]
        if preferred_id is None:
            suggested = suggested_bill_instance(self.database, row)
            preferred_id = (
                int(suggested["id"])
                if suggested is not None
                else None
            )
        if preferred_id is None:
            review, ambiguous = best_review_candidate(
                self.database,
                row,
                active_only=False,
            )
            if review is not None and not ambiguous:
                preferred_id = review.bill_instance_id

        # Automatic matching can legitimately cross the 1st/15th date
        # boundary. If the matched bill is outside the manual candidate cycle,
        # still show the authoritative reconciled target in the inspector.
        if (
            preferred_id is not None
            and self.target_bill.findData(int(preferred_id)) < 0
        ):
            matched_instance = self.database.get_bill_instance(
                int(preferred_id)
            )
            if matched_instance is not None:
                label = str(matched_instance["bill_name_snapshot"])
                due = matched_instance["due_cents"]
                if due is not None:
                    label += f" · expected {money(int(due))}"
                self.target_bill.addItem(
                    label,
                    int(preferred_id),
                )

        if preferred_id is not None:
            idx = self.target_bill.findData(int(preferred_id))
            if idx >= 0:
                self.target_bill.setCurrentIndex(idx)
        self.target_bill.blockSignals(False)

        tx_date = transaction_date(row)
        if tx_date:
            cycle = "1st" if tx_date.day < 15 else "15th"
            self.period_label.setText(
                f"{MONTH_NAMES[tx_date.month - 1]} "
                f"{tx_date.year} · {cycle}"
            )
        else:
            self.period_label.setText("No usable transaction date")

        disposition = row["reconciliation_disposition"]
        self.undo_button.setEnabled(
            disposition in {"matched", "ignored"}
            or row["internal_transfer_role"] is not None
        )
        self.ignore_button.setEnabled(not int(row["pending"]))
        self.accept_button.setEnabled(False)
        self._update_preview()
        self._load_funding_transfer_panel(row)

    def _load_internal_transfer_evidence(self, row: Any) -> None:
        self.reconcile_card.setVisible(True)
        self.funding_card.setVisible(False)
        self._clear_funding_transfer_panel(keep_selection=True)

        merchant = (
            row["merchant_name"]
            or row["name"]
            or "(incoming internal transfer)"
        )
        account = row["account_name"] or "credit/loan account"
        if row["account_mask"]:
            account += f" ••••{row['account_mask']}"

        self.selected_label.setText(
            f"{merchant} · {plaid_amount(int(row['amount_cents']))} · "
            f"{row['posted_date'] or row['authorized_date'] or 'no date'}"
        )

        bill_instance_id = row["internal_transfer_bill_instance_id"]
        bill = (
            self.database.get_bill_instance(int(bill_instance_id))
            if bill_instance_id is not None
            else None
        )
        bill_name = str(
            row["internal_transfer_bill_name"] or "matched bill"
        )

        self.target_bill.blockSignals(True)
        self.target_bill.clear()
        if bill is not None:
            label = bill_name
            if bill["due_cents"] is not None:
                label += f" · expected {money(int(bill['due_cents']))}"
            self.target_bill.addItem(
                label,
                int(bill_instance_id),
            )
            self.target_bill.setCurrentIndex(0)
        else:
            self.target_bill.addItem(bill_name, None)
        self.target_bill.blockSignals(False)

        tx_date = transaction_date(row)
        if tx_date:
            self.period_label.setText(
                f"{MONTH_NAMES[tx_date.month - 1]} {tx_date.year} · "
                "internal transfer evidence"
            )
        else:
            self.period_label.setText("Internal transfer evidence")

        expected = (
            None
            if bill is None
            else expected_payment_cents(bill)
        )
        actual = abs(int(row["amount_cents"]))
        existing_paid = (
            None
            if bill is None or bill["paid_cents"] is None
            else int(bill["paid_cents"])
        )
        self.expected_value.setText(money(expected))
        self.bank_value.setText(money(actual))
        self.existing_paid_value.setText(money(existing_paid))
        if expected is None:
            self.difference_value.setText("—")
        else:
            difference = actual - expected
            if difference == 0:
                self.difference_value.setText("$0.00")
            elif difference > 0:
                self.difference_value.setText(
                    "+" + money(difference)
                )
            else:
                self.difference_value.setText(
                    "-" + money(abs(difference))
                )

        source_id = row["internal_transfer_source_transaction_id"]
        source = (
            self.database.get_bank_transaction(
                self._environment(),
                str(source_id),
            )
            if source_id
            else None
        )
        source_account = (
            source["account_name"]
            if source is not None and source["account_name"]
            else "the configured Payment Account"
        )
        if (
            source is not None
            and source["account_mask"]
        ):
            source_account += f" ••••{source['account_mask']}"

        self.warning.setText(
            f"Verified internal payment for {bill_name}: "
            f"{money(actual)} left {source_account} and the same amount "
            f"arrived in {account}. The outgoing side is the bill payment; "
            "this incoming side is retained as verification evidence. "
            "Undo removes the pair and restores the bill's prior state."
        )
        self.action_status.setText("")
        self.accept_button.setEnabled(False)
        self.ignore_button.setEnabled(False)
        self.undo_button.setEnabled(True)


    def _update_preview(self) -> None:
        row = self._selected_row()
        bill_instance_id = self.target_bill.currentData()
        if row is None or bill_instance_id is None:
            self.expected_value.setText("—")
            self.bank_value.setText("—" if row is None else plaid_amount(int(row["amount_cents"])))
            self.existing_paid_value.setText("—")
            self.difference_value.setText("—")
            self.accept_button.setEnabled(False)
            return

        try:
            preview = build_preview(self.database, row, int(bill_instance_id))
        except ValueError as exc:
            self.warning.setText(str(exc))
            self.accept_button.setEnabled(False)
            return

        self.expected_value.setText(money(preview.due_cents))
        self.bank_value.setText(money(preview.bank_amount_cents))
        self.existing_paid_value.setText(money(preview.existing_paid_cents))
        if preview.difference_cents is None:
            self.difference_value.setText("—")
        elif preview.difference_cents == 0:
            self.difference_value.setText("$0.00")
        elif preview.difference_cents > 0:
            self.difference_value.setText("+" + money(preview.difference_cents))
        else:
            self.difference_value.setText("-" + money(abs(preview.difference_cents)))

        suggestion = suggested_bill_for_transaction(row)
        duplicate_count = self._duplicate_candidate_count(row, suggestion) if suggestion else 0

        messages: list[str] = []
        if preview.already_agrees:
            messages.append("Existing Paid already agrees with the bank amount.")
        elif preview.existing_paid_cents is not None:
            messages.append(
                f"Existing Paid is {money(preview.existing_paid_cents)}; accepting will replace it "
                f"with {money(preview.bank_amount_cents)}. Undo restores the previous value."
            )
        if duplicate_count > 1:
            messages.append(
                f"Warning: {duplicate_count} posted transactions in this pay period "
                f"plausibly match {suggestion}. Review before accepting."
            )

        if row["internal_transfer_role"] == "source":
            destination_id = row[
                "internal_transfer_destination_transaction_id"
            ]
            destination = (
                self.database.get_bank_transaction(
                    self._environment(),
                    str(destination_id),
                )
                if destination_id
                else None
            )
            destination_account = (
                destination["account_name"]
                if (
                    destination is not None
                    and destination["account_name"]
                )
                else "the credit/loan account"
            )
            if (
                destination is not None
                and destination["account_mask"]
            ):
                destination_account += (
                    f" ••••{destination['account_mask']}"
                )
            messages.append(
                "Verified by paired internal transfer: the same amount "
                f"arrived in {destination_account}."
            )

        disposition = row["reconciliation_disposition"]
        if disposition == "matched":
            messages.append(f"Currently reconciled to {row['reconciled_bill_name']}.")
        elif disposition == "ignored":
            messages.append("This transaction is currently ignored.")

        if not int(row["is_bills_checking"] or 0):
            account = row["account_name"] or "this linked account"
            if row["account_mask"]:
                account += f" ••••{row['account_mask']}"
            messages.append(
                f"Payment came from {account}, not the Bills account. "
                "That is valid for reconciliation and will not count as Bills-account funding."
            )

        self.warning.setText(" ".join(messages))
        self.accept_button.setEnabled(
            not int(row["pending"]) and int(row["amount_cents"]) > 0
        )

    def _load_funding_transfer_panel(self, row: Any) -> None:
        actual = int(row["amount_cents"])
        is_candidate = (
            not int(row["pending"])
            and actual < 0
            and int(row["is_bills_checking"] or 0)
        )
        if not is_candidate:
            self._clear_funding_transfer_panel(keep_selection=True)
            if actual >= 0:
                self.funding_warning.setText(
                    "This is not an incoming transfer to Bills Checking."
                )
            elif int(row["pending"]):
                self.funding_warning.setText(
                    "Pending incoming transfers cannot be validated yet."
                )
            else:
                self.funding_warning.setText(
                    "Funding transfer validation only applies to deposits into the designated Bills Checking account."
                )
            return

        merchant = row["merchant_name"] or row["name"] or "(incoming transfer)"
        self.funding_selected_label.setText(
            f"{merchant} · {plaid_amount(actual)}"
        )

        tx_date = transaction_date(row)
        if tx_date:
            self.funding_period_label.setText(
                f"{MONTH_NAMES[tx_date.month - 1]} {tx_date.year}"
            )

        saved_scope = row["funding_validation_scope"]
        scope = str(saved_scope) if saved_scope else suggested_funding_scope(
            self.database, row
        )
        if scope:
            index = self.funding_scope.findData(scope)
            if index >= 0:
                self.funding_scope.blockSignals(True)
                self.funding_scope.setCurrentIndex(index)
                self.funding_scope.blockSignals(False)

        self._update_funding_transfer_preview()

    def _update_funding_transfer_preview(self) -> None:
        row = self._selected_row()
        if row is None:
            self._clear_funding_transfer_panel(keep_selection=True)
            return

        scope = self.funding_scope.currentData()
        if scope is None:
            self.validate_funding_button.setEnabled(False)
            return

        try:
            preview = build_funding_transfer_preview(
                self.database,
                row,
                str(scope),
            )
        except ValueError as exc:
            self.funding_expected_value.setText("—")
            self.funding_actual_value.setText(
                plaid_amount(int(row["amount_cents"]))
            )
            self.funding_difference_value.setText("—")
            self.funding_warning.setText(str(exc))
            self.validate_funding_button.setEnabled(False)
            self.undo_funding_button.setEnabled(
                row["funding_validation_scope"] is not None
            )
            return

        self.funding_expected_value.setText(
            money(preview.expected_cents)
        )
        self.funding_actual_value.setText(
            money(preview.actual_cents)
        )
        if preview.difference_cents == 0:
            self.funding_difference_value.setText("$0.00")
        elif preview.difference_cents > 0:
            self.funding_difference_value.setText(
                "+" + money(preview.difference_cents)
            )
        else:
            self.funding_difference_value.setText(
                "-" + money(abs(preview.difference_cents))
            )

        messages = [
            "Expected is the scheduled total of Transfer Required bills for this scope; "
            "it does not change when autopays later post."
        ]
        if preview.expected_cents == 0:
            messages.append(
                "No Transfer Required bills have a scheduled amount in this scope."
            )
        elif preview.difference_cents == 0:
            messages.append(
                "This bank transfer exactly covers the scheduled aggregate."
            )
        elif preview.difference_cents > 0:
            messages.append(
                f"The transfer is {money(preview.difference_cents)} over the scheduled aggregate."
            )
        else:
            messages.append(
                f"The transfer is {money(abs(preview.difference_cents))} short of the scheduled aggregate."
            )

        if row["funding_validation_scope"] is not None:
            saved_scope = str(row["funding_validation_scope"])
            saved_label = (
                "Whole month"
                if saved_scope == "month"
                else f"{saved_scope} pay period"
            )
            messages.append(
                f"Currently validated as {saved_label}: "
                f"expected {money(int(row['funding_expected_cents']))}, "
                f"actual {money(int(row['funding_actual_cents']))}, "
                f"difference {money(int(row['funding_difference_cents']))}."
            )

        self.funding_warning.setText(" ".join(messages))
        self.validate_funding_button.setEnabled(
            not int(row["pending"])
            and int(row["amount_cents"]) < 0
            and int(row["is_bills_checking"] or 0)
            and preview.expected_cents > 0
        )
        self.undo_funding_button.setEnabled(
            row["funding_validation_scope"] is not None
        )

    def _validate_funding_transfer(self) -> None:
        row = self._selected_row()
        scope = self.funding_scope.currentData()
        if row is None or scope is None:
            return
        try:
            self.backup_manager.create_backup(
                "pre-funding-transfer-validation"
            )
            preview = validate_funding_transfer(
                self.database,
                row,
                str(scope),
            )
        except Exception as exc:
            self.funding_action_status.setText(str(exc))
            return

        if preview.difference_cents == 0:
            result = "exactly matches"
        elif preview.difference_cents > 0:
            result = f"is {money(preview.difference_cents)} over"
        else:
            result = f"is {money(abs(preview.difference_cents))} short"

        message = (
            f"Validated: {money(preview.actual_cents)} {result} the "
            f"{money(preview.expected_cents)} scheduled transfer target."
        )
        self.on_data_changed()
        self.refresh()
        self.funding_action_status.setText(message)

    def _undo_funding_validation(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        try:
            self.backup_manager.create_backup(
                "pre-funding-transfer-validation-undo"
            )
            undo_funding_transfer_validation(self.database, row)
        except Exception as exc:
            self.funding_action_status.setText(str(exc))
            return
        message = (
            "Funding transfer validation removed. No bill payment values were changed."
        )
        self.on_data_changed()
        self.refresh()
        self.funding_action_status.setText(message)

    def _clear_funding_transfer_panel(
        self,
        *,
        keep_selection: bool = False,
    ) -> None:
        if not keep_selection:
            self.funding_selected_label.setText(
                "Select an incoming transfer deposited into Bills Checking."
            )
            self.funding_period_label.setText("")
        self.funding_expected_value.setText("—")
        self.funding_actual_value.setText("—")
        self.funding_difference_value.setText("—")
        self.funding_warning.setText("")
        self.funding_action_status.setText("")
        self.validate_funding_button.setEnabled(False)
        self.undo_funding_button.setEnabled(False)

    def _duplicate_candidate_count(self, selected_row: Any, suggestion: str) -> int:
        selected_date = transaction_date(selected_row)
        if selected_date is None:
            return 0
        selected_cycle = "1st" if selected_date.day < 15 else "15th"

        count = 0
        for row in self.database.list_bank_transactions(
            self._environment(),
            limit=5000,
            year=selected_date.year,
            month=selected_date.month,
        ):
            if int(row["pending"]):
                continue
            tx_date = transaction_date(row)
            if tx_date is None:
                continue
            cycle = "1st" if tx_date.day < 15 else "15th"
            if (
                tx_date.year != selected_date.year
                or tx_date.month != selected_date.month
                or cycle != selected_cycle
            ):
                continue
            if suggested_bill_for_transaction(row) == suggestion:
                count += 1
        return count

    def _accept(self) -> None:
        row = self._selected_row()
        bill_instance_id = self.target_bill.currentData()
        if row is None or bill_instance_id is None:
            self.action_status.setText("Choose a target bill first.")
            return

        try:
            self.backup_manager.create_backup("pre-reconcile-accept")
            preview = accept_match(self.database, row, int(bill_instance_id))
        except Exception as exc:
            self.action_status.setText(str(exc))
            return

        if preview.already_agrees:
            self.action_status.setText(
                f"Matched {preview.bill_name}; existing Paid already agreed with the bank."
            )
        else:
            self.action_status.setText(
                f"Matched {preview.bill_name}; Paid is now {money(preview.bank_amount_cents)}."
            )
        self.on_data_changed()
        self.refresh()

    def _ignore(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        try:
            self.backup_manager.create_backup("pre-reconcile-ignore")
            ignore_transaction(self.database, row)
        except Exception as exc:
            self.action_status.setText(str(exc))
            return
        self.action_status.setText("Transaction marked ignored. No bill is linked to it.")
        self.on_data_changed()
        self.refresh()

    def _undo(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        try:
            self.backup_manager.create_backup("pre-reconcile-undo")
            undo_reconciliation(self.database, row)
        except Exception as exc:
            self.action_status.setText(str(exc))
            return
        self.action_status.setText(
            "Reconciliation undone. Any prior Paid/Status values were restored."
        )
        self.on_data_changed()
        self.refresh()

    def _clear_reconciliation_panel(self) -> None:
        self.selected_transaction_id = None
        self.reconcile_card.setVisible(True)
        self.funding_card.setVisible(False)
        self.selected_label.setText("Select a posted payment transaction above.")
        self.target_bill.blockSignals(True)
        self.target_bill.clear()
        self.target_bill.addItem("Choose a bill…", None)
        self.target_bill.blockSignals(False)
        self.period_label.setText("")
        self.expected_value.setText("—")
        self.bank_value.setText("—")
        self.existing_paid_value.setText("—")
        self.difference_value.setText("—")
        self.warning.setText("")
        self.action_status.setText("")
        self.accept_button.setEnabled(False)
        self.ignore_button.setEnabled(False)
        self.undo_button.setEnabled(False)
        self._clear_funding_transfer_panel()
        self._clear_transaction_detail()
