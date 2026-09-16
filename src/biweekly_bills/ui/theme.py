from __future__ import annotations


APP_STYLESHEET = """
QWidget {
    background: #0b1020;
    color: #edf1f7;
    font-family: Inter, "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 13px;
}
QMainWindow, #AppRoot {
    background: #0b1020;
}
#Sidebar {
    background: #0e1425;
    border-right: 1px solid #232b3e;
}
#BrandTitle {
    font-size: 18px;
    font-weight: 700;
    color: #ffffff;
}
#BrandSub {
    font-size: 11px;
    color: #7d879b;
}
QPushButton#NavButton {
    background: transparent;
    color: #9aa4b8;
    border: 0;
    border-radius: 12px;
    padding: 11px 14px;
    text-align: left;
    font-weight: 600;
}
QPushButton#NavButton:hover {
    background: #171e31;
    color: #ffffff;
}
QPushButton#NavButton:checked {
    background: #c8ff3d;
    color: #0b1020;
}
QListWidget#NavList {
    background: transparent;
    border: 0;
    outline: 0;
    padding: 0;
}
QListWidget#NavList::item {
    background: transparent;
    color: #9aa4b8;
    border: 0;
    border-radius: 12px;
    padding: 11px 14px;
    margin: 1px 0;
    font-weight: 600;
}
QListWidget#NavList::item:hover {
    background: #171e31;
    color: #ffffff;
}
QListWidget#NavList::item:selected {
    background: #c8ff3d;
    color: #0b1020;
}
QListWidget#NavList::item:selected:!active {
    background: #c8ff3d;
    color: #0b1020;
}
QPushButton#NavEditButton {
    background: transparent;
    color: #7d879b;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 0;
    font-size: 16px;
}
QPushButton#NavEditButton:hover {
    background: #171e31;
    color: #ffffff;
    border-color: #29324a;
}
QPushButton#NavEditButton:checked {
    background: #171e31;
    color: #c8ff3d;
    border-color: #3a465f;
}
#TopBar {
    background: #0b1020;
    border-bottom: 1px solid #1c2436;
}
#PageTitle {
    font-size: 26px;
    font-weight: 700;
    color: #ffffff;
}
#PageSubtitle {
    color: #8792a8;
    font-size: 12px;
}
#Pill {
    background: #171e31;
    border: 1px solid #29324a;
    border-radius: 12px;
    padding: 5px 10px;
    color: #b9c2d3;
    font-weight: 600;
}
#SafetyPill {
    background: #16211c;
    color: #c8ff3d;
    border: 1px solid #31452f;
    border-radius: 12px;
    padding: 5px 10px;
    font-weight: 700;
}
#HeroCard {
    background: #12192a;
    border: 1px solid #273149;
    border-radius: 24px;
}
#Card {
    background: #12192a;
    border: 1px solid #242e44;
    border-radius: 18px;
}
#BillDetailCard {
    background: #12192a;
    border: 1px solid #34405c;
    border-radius: 18px;
}
#CardAlt {
    background: #f3f4ee;
    color: #121521;
    border: 0;
    border-radius: 18px;
}
#CardAlt QLabel {
    background: transparent;
    color: #121521;
}
#MetricLabel {
    color: #7f8aa0;
    font-size: 11px;
    font-weight: 600;
}
#MetricValue {
    color: #ffffff;
    font-size: 28px;
    font-weight: 700;
}
#MetricValueDark {
    color: #121521;
    font-size: 23px;
    font-weight: 700;
}
#AccentValue {
    color: #c8ff3d;
    font-size: 34px;
    font-weight: 700;
}
#SectionTitle {
    font-size: 15px;
    font-weight: 700;
    color: #ffffff;
}
#Muted {
    color: #7f8aa0;
}
#AccentBar {
    background: #c8ff3d;
    border-radius: 4px;
}
#WarningBar {
    background: #ffd95a;
    border-radius: 4px;
}
QTableWidget {
    background: #111827;
    alternate-background-color: #141c2d;
    border: 1px solid #242e44;
    border-radius: 14px;
    gridline-color: #202a3d;
    selection-background-color: #232d43;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #151d2f;
    color: #8995aa;
    border: 0;
    border-bottom: 1px solid #273149;
    padding: 9px 8px;
    font-size: 11px;
    font-weight: 700;
}
QTableWidget::item {
    padding: 7px;
    border-bottom: 1px solid #1e283b;
}
QMenu {
    background: #12192a;
    color: #edf1f7;
    border: 1px solid #2a3550;
    padding: 6px;
}
QMenu::item {
    padding: 7px 24px 7px 10px;
    border-radius: 7px;
}
QMenu::item:selected {
    background: #232d43;
    color: #ffffff;
}
QMenu::item:disabled {
    color: #59657a;
}
QMenu::separator {
    height: 1px;
    background: #273149;
    margin: 5px 8px;
}
QLineEdit#InlineCellEditor {
    min-height: 30px;
    padding: 4px 7px;
    background: #0e1628;
    color: #edf1f7;
    border: 1px solid #3a4866;
    border-radius: 5px;
    selection-background-color: #30405f;
}
QLineEdit#InlineCellEditor:focus {
    border: 1px solid #c8ff3d;
    background: #111c30;
}
QLineEdit#SidebarNavEditor {
    background: #0e1628;
    color: #edf1f7;
    border: 1px solid #c8ff3d;
    border-radius: 6px;
    padding: 3px 7px;
    selection-background-color: #30405f;
}
QLineEdit#SidebarNavEditor:focus {
    background: #111c30;
    border-color: #c8ff3d;
}
QLineEdit, QComboBox, QTextEdit, QSpinBox {
    background: #0f1626;
    color: #edf1f7;
    border: 1px solid #2a3550;
    border-radius: 10px;
    padding: 8px 10px;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QSpinBox:focus {
    border: 1px solid #c8ff3d;
}
QComboBox QAbstractItemView {
    background: #111827;
    color: #edf1f7;
    selection-background-color: #25314a;
}
QPushButton#PrimaryButton {
    background: #c8ff3d;
    color: #0b1020;
    border: 0;
    border-radius: 11px;
    padding: 9px 15px;
    font-weight: 700;
}
QPushButton#PrimaryButton:hover {
    background: #d6ff72;
}
QPushButton#SecondaryButton {
    background: #171f32;
    color: #dbe1ec;
    border: 1px solid #2c3750;
    border-radius: 11px;
    padding: 9px 15px;
    font-weight: 650;
}
QPushButton#SecondaryButton:hover {
    background: #202a40;
}
QPushButton#SegmentButton {
    background: #111827;
    color: #9aa4b8;
    border: 1px solid #2a3550;
    border-radius: 10px;
    padding: 8px 14px;
    font-weight: 650;
}
QPushButton#SegmentButton:hover {
    background: #182136;
    color: #ffffff;
}
QPushButton#SegmentButton:checked {
    background: #c8ff3d;
    color: #0b1020;
    border-color: #c8ff3d;
}
QCheckBox {
    color: #dbe1ec;
    spacing: 7px;
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border: 1px solid #44506b;
    border-radius: 5px;
    background: #0f1626;
}
QCheckBox::indicator:hover {
    border-color: #c8ff3d;
}
QCheckBox::indicator:checked {
    background: #c8ff3d;
    border-color: #c8ff3d;
}
QProgressBar#WorkflowProgress {
    background: #0f1626;
    color: #edf1f7;
    border: 1px solid #2a3550;
    border-radius: 9px;
    min-height: 18px;
    text-align: center;
    font-size: 11px;
    font-weight: 700;
}
QProgressBar#WorkflowProgress::chunk {
    background: #c8ff3d;
    border-radius: 7px;
}
QPushButton#DangerButton {
    background: #291922;
    color: #ff98ae;
    border: 1px solid #4b2634;
    border-radius: 11px;
    padding: 9px 15px;
    font-weight: 700;
}
QScrollArea {
    border: 0;
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
}
QScrollBar::handle:vertical {
    background: #2a3550;
    min-height: 24px;
    border-radius: 5px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""
