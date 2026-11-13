from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..backups import BackupManager
from ..database import Database
from ..match_review import MatchCorrection, apply_match_correction
from .formatting import money


class MatchReviewDialog(QDialog):
    """Lets the user approve suggested bank-match corrections one by one."""

    def __init__(
        self,
        database: Database,
        backup_manager: BackupManager,
        corrections: list[MatchCorrection],
        parent=None,
    ):
        super().__init__(parent)
        self.database = database
        self.backup_manager = backup_manager
        self.corrections = corrections
        self.applied = 0
        self.setWindowTitle("Re-check bank matches")
        self.resize(980, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QLabel("Re-check bank matches")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        if corrections:
            intro = (
                "These payments look like they belong to a different month, "
                "or can fill a bill that has no bank match yet. Tick the ones "
                "you agree with and choose Apply. A backup is made first, and "
                "every change can be undone from Transactions."
            )
        else:
            intro = "Everything already lines up. There is nothing to change."
        explanation = QLabel(intro)
        explanation.setObjectName("Muted")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.table = QTableWidget(len(corrections), 6)
        self.table.setHorizontalHeaderLabels(
            ["Apply", "Bill", "Payment date", "Amount", "Bank description", "Change"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        for row, correction in enumerate(corrections):
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            check.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, check)
            values = (
                correction.bill_name,
                correction.transaction_date,
                money(correction.amount_cents),
                correction.description,
                correction.summary,
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)
        self.table.setVisible(bool(corrections))
        layout.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        if corrections:
            select_all = QPushButton("Tick all")
            select_all.setObjectName("SecondaryButton")
            select_all.clicked.connect(lambda: self._set_all(True))
            clear_all = QPushButton("Untick all")
            clear_all.setObjectName("SecondaryButton")
            clear_all.clicked.connect(lambda: self._set_all(False))
            buttons.addWidget(select_all)
            buttons.addWidget(clear_all)
        buttons.addStretch(1)
        close = QPushButton("Close" if not corrections else "Cancel")
        close.setObjectName("SecondaryButton")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        if corrections:
            self.apply_button = QPushButton("Apply ticked changes")
            self.apply_button.setObjectName("PrimaryButton")
            self.apply_button.clicked.connect(self._apply)
            buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def selected_corrections(self) -> list[MatchCorrection]:
        return [
            correction
            for row, correction in enumerate(self.corrections)
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def _apply(self) -> None:
        chosen = self.selected_corrections()
        if not chosen:
            self.status.setText("Nothing is ticked.")
            return
        try:
            self.backup_manager.create_backup("pre-match-recheck")
        except Exception as exc:
            self.status.setText(f"Backup failed, nothing was changed: {exc}")
            return
        failures: list[str] = []
        for correction in chosen:
            try:
                apply_match_correction(self.database, correction)
                self.applied += 1
            except Exception as exc:
                failures.append(f"{correction.bill_name} {correction.transaction_date}: {exc}")
        if failures:
            self.status.setText(
                f"Applied {self.applied}; {len(failures)} could not be applied: "
                + "; ".join(failures[:3])
            )
            self.apply_button.setEnabled(False)
            return
        self.accept()
