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
