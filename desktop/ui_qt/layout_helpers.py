"""GAS Web 版に寄せたレイアウトヘルパー。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui_qt import helpers as h
from ui_qt.style import COLORS


def make_expanding(widget: QWidget, *, vertical: bool = True, horizontal: bool = True) -> QWidget:
    hp = QSizePolicy.Expanding if horizontal else QSizePolicy.Preferred
    vp = QSizePolicy.Expanding if vertical else QSizePolicy.Preferred
    widget.setSizePolicy(hp, vp)
    return widget


class CollapsibleSection(QFrame):
    """GAS の折りたたみパネル（ー / ＋）。"""

    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        collapsed: bool = True,
        tint: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CollapsibleSection")
        bg = tint or COLORS["surface"]
        self.setStyleSheet(
            f"#CollapsibleSection {{ background: {bg}; border: 1px solid {COLORS['border']};"
            f" border-radius: 8px; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        head = QHBoxLayout()
        self._title = QLabel(title)
        self._title.setStyleSheet("font-weight: 700; font-size: 12px; color: #374151;")
        head.addWidget(self._title)
        head.addStretch()
        self._toggle = h.button("＋" if collapsed else "ー", self._on_toggle)
        self._toggle.setFixedWidth(32)
        self._toggle.setToolTip("展開" if collapsed else "折りたたむ")
        head.addWidget(self._toggle)
        outer.addLayout(head)

        self._content = content
        self._content.setVisible(not collapsed)
        outer.addWidget(content)

    def _on_toggle(self) -> None:
        show = not self._content.isVisible()
        self._content.setVisible(show)
        self._toggle.setText("ー" if show else "＋")
        self._toggle.setToolTip("折りたたむ" if show else "展開")

    def set_expanded(self, expanded: bool) -> None:
        self._content.setVisible(expanded)
        self._toggle.setText("ー" if expanded else "＋")


def viewport_work_height(
    reserved: int = 160,
    *,
    min_height: int = 512,
    max_ratio: float = 0.85,
    widget: QWidget | None = None,
) -> int:
    """GAS の calc(100vh - Nrem) / clamp 相当の作業領域高さ。"""
    from PySide6.QtWidgets import QApplication

    avail = 800
    if widget is not None:
        win = widget.window()
        if win is not None and win.height() > 400:
            avail = win.height()
    if avail <= 400:
        app = QApplication.instance()
        if app and app.primaryScreen():
            avail = app.primaryScreen().availableGeometry().height()
    target = avail - reserved
    cap = int(avail * max_ratio)
    return max(min_height, min(cap, target))


def main_table_frame(title: str, table: QWidget) -> QFrame:
    """メイン一覧テーブル用フレーム（残り高さを占有）。"""
    frame = QFrame()
    frame.setObjectName("MainTableFrame")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    if title:
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight: 700; font-size: 12px; color: #374151;")
        lay.addWidget(lbl)
    make_expanding(table)
    lay.addWidget(table, 1)
    make_expanding(frame)
    return frame


class CropTileColumnPanel(QWidget):
    """クロップタイルを列ごとに縦積み（行同期グリッドの高さ余白を作らない）。"""

    def __init__(
        self,
        *,
        columns: int = 4,
        margins: tuple[int, int, int, int] = (6, 6, 6, 6),
        spacing: int = 6,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._columns_count = max(1, int(columns))
        self.setStyleSheet("background: transparent;")
        root = QHBoxLayout(self)
        root.setContentsMargins(*margins)
        root.setSpacing(spacing)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._column_layouts: list[QVBoxLayout] = []
        for _ in range(self._columns_count):
            host = QWidget()
            host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            col = QVBoxLayout(host)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(spacing)
            col.setAlignment(Qt.AlignmentFlag.AlignTop)
            self._column_layouts.append(col)
            root.addWidget(host, 1)

    def clear_tiles(self) -> None:
        for col in self._column_layouts:
            while col.count():
                item = col.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

    def add_tile(self, widget: QWidget, index: int) -> None:
        widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        col_idx = int(index) % self._columns_count
        self._column_layouts[col_idx].addWidget(
            widget, 0, Qt.AlignmentFlag.AlignTop
        )

    def set_message(self, text: str) -> None:
        self.clear_tiles()
        lbl = h.muted_label(text)
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._column_layouts[0].addWidget(lbl, 0, Qt.AlignmentFlag.AlignTop)
