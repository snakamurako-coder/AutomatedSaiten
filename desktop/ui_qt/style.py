"""Qt 版テーマ（GAS Web 版のデザイン言語を QSS で再現）。"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

COLORS = {
    "bg": "#f3f4f6",
    "surface": "#ffffff",
    "sidebar": "#f9fafb",
    "border": "#e5e7eb",
    "border_strong": "#cbd5e1",
    "text": "#1f2937",
    "text_secondary": "#6b7280",
    "text_muted": "#9ca3af",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_soft": "#eff6ff",
    "success": "#16a34a",
    "success_hover": "#15803d",
    "success_soft": "#dcfce7",
    "danger": "#dc2626",
    "danger_soft": "#fef2f2",
    "select_bg": "#dbeafe",
}

_QSS = f"""
QWidget {{
    color: {COLORS["text"]};
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {COLORS["bg"]};
}}

/* --- サイドバー --- */
#Sidebar {{
    background: {COLORS["sidebar"]};
    border-right: 1px solid {COLORS["border"]};
}}
#SidebarTitle {{
    font-size: 17px;
    font-weight: 700;
    color: #111827;
}}
#SidebarCaption {{
    font-size: 11px;
    color: {COLORS["text_secondary"]};
}}
QPushButton[variant="nav"] {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 8px 12px;
    text-align: left;
    font-size: 12px;
}}
QPushButton[variant="nav"]:hover {{
    background: #eef2f7;
}}
QPushButton[variant="nav"]:checked {{
    background: {COLORS["accent"]};
    color: white;
    font-weight: 700;
}}
QPushButton[variant="nav"]:disabled {{
    color: {COLORS["text_muted"]};
}}

/* --- コンテンツ --- */
#ContentArea {{
    background: {COLORS["surface"]};
}}
QLabel[role="title"] {{
    font-size: 18px;
    font-weight: 700;
    color: #111827;
}}
QLabel[role="muted"] {{
    color: {COLORS["text_secondary"]};
    font-size: 12px;
}}
QLabel[role="caption"] {{
    color: {COLORS["text_muted"]};
    font-size: 11px;
}}

/* --- ボタン --- */
QPushButton {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border_strong"]};
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 600;
    font-size: 12px;
}}
QPushButton:hover {{
    background: #f1f5f9;
}}
QPushButton:pressed {{
    background: #e2e8f0;
}}
QPushButton:disabled {{
    color: {COLORS["text_muted"]};
    background: #f9fafb;
    border-color: {COLORS["border"]};
}}
QPushButton[variant="primary"] {{
    background: {COLORS["accent"]};
    border: 1px solid {COLORS["accent_hover"]};
    color: white;
}}
QPushButton[variant="primary"]:hover {{
    background: {COLORS["accent_hover"]};
}}
QPushButton[variant="primary"]:disabled {{
    background: #93c5fd;
    color: #eff6ff;
}}
QPushButton[variant="success"] {{
    background: {COLORS["success"]};
    border: 1px solid {COLORS["success_hover"]};
    color: white;
}}
QPushButton[variant="success"]:hover {{
    background: {COLORS["success_hover"]};
}}
QPushButton[variant="danger-soft"] {{
    background: #fee2e2;
    border: 1px solid #fca5a5;
    color: #b91c1c;
}}
QPushButton[variant="danger-soft"]:hover {{
    background: #fecaca;
}}

/* --- 入力 --- */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border_strong"]};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {COLORS["select_bg"]};
    selection-color: {COLORS["text"]};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {COLORS["accent"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border"]};
    selection-background-color: {COLORS["select_bg"]};
    selection-color: {COLORS["text"]};
}}

/* --- グループボックス（カード） --- */
QGroupBox {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    margin-top: 10px;
    padding: 10px 10px 8px 10px;
    font-weight: 700;
    font-size: 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #374151;
}}

/* --- テーブル --- */
QTableWidget, QTableView, QListWidget {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    gridline-color: {COLORS["border"]};
    alternate-background-color: #fafafa;
    font-size: 12px;
}}
QHeaderView::section {{
    background: {COLORS["sidebar"]};
    border: none;
    border-bottom: 1px solid {COLORS["border"]};
    border-right: 1px solid {COLORS["border"]};
    padding: 6px 8px;
    font-weight: 700;
    font-size: 11px;
    color: #374151;
}}
QTableWidget::item, QListWidget::item {{
    padding: 3px;
}}
QTableWidget::item:selected, QListWidget::item:selected {{
    background: {COLORS["select_bg"]};
    color: {COLORS["text"]};
}}

/* --- プログレスバー --- */
QProgressBar {{
    background: {COLORS["border"]};
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    font-size: 10px;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {COLORS["accent"]};
    border-radius: 5px;
}}

/* --- スクロールバー --- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #d1d5db;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #9ca3af;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #d1d5db;
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #9ca3af;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* --- その他 --- */
QScrollArea {{
    border: none;
    background: transparent;
}}
/* ビューポートと中身をパレット色で塗らせない（黒背景対策） */
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QCheckBox, QRadioButton {{
    font-size: 12px;
    spacing: 6px;
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: {COLORS["border"]};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
    background: {COLORS["accent"]};
}}
QSplitter::handle {{
    background: {COLORS["border"]};
}}
QStatusBar {{
    background: {COLORS["sidebar"]};
    border-top: 1px solid {COLORS["border"]};
    font-size: 11px;
    color: {COLORS["text_secondary"]};
}}
"""


def _build_light_palette() -> QPalette:
    """OS がダークモードでも常にライト配色で表示するためのパレット。

    QSS が背景を指定していないウィジェット（QScrollArea のビューポート等）は
    パレット色で塗られるため、ここを固定しないとダークモード環境で
    「黒背景 + 黒文字」になる。
    """
    p = QPalette()
    text = QColor(COLORS["text"])
    p.setColor(QPalette.Window, QColor(COLORS["bg"]))
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, QColor(COLORS["surface"]))
    p.setColor(QPalette.AlternateBase, QColor("#fafafa"))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor(COLORS["surface"]))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.ToolTipBase, QColor(COLORS["surface"]))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, QColor(COLORS["text_muted"]))
    p.setColor(QPalette.Highlight, QColor(COLORS["accent"]))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Link, QColor(COLORS["accent"]))
    p.setColor(QPalette.Light, QColor("#ffffff"))
    p.setColor(QPalette.Midlight, QColor(COLORS["border"]))
    p.setColor(QPalette.Mid, QColor(COLORS["border_strong"]))
    p.setColor(QPalette.Dark, QColor("#9ca3af"))
    p.setColor(QPalette.Shadow, QColor("#6b7280"))
    disabled = QColor(COLORS["text_muted"])
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    p.setColor(QPalette.Disabled, QPalette.Base, QColor("#f9fafb"))
    return p


def apply_style(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setPalette(_build_light_palette())
    families = set(QFontDatabase.families())
    for name in ("Yu Gothic UI", "Meiryo UI", "Segoe UI"):
        if name in families:
            app.setFont(QFont(name, 10))
            break
    app.setStyleSheet(_QSS)


def set_variant(widget, variant: str) -> None:
    """QSS の variant プロパティを設定する。"""
    widget.setProperty("variant", variant)


def set_role(widget, role: str) -> None:
    widget.setProperty("role", role)
