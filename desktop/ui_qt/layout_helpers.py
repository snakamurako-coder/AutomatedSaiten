"""GAS Web 版に寄せたレイアウトヘルパー。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QScrollArea,
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


def configure_crop_image_scroll(scroll: QScrollArea) -> None:
    """回答画像一覧: 横スクロールなし・縦スクロール可。"""
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)


class FlowLayout(QLayout):
    """左→右に並べ、幅不足時は改行するレイアウト。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        h_spacing: int = 6,
        v_spacing: int = 6,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout_items(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout_items(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _layout_items(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            w = hint.width()
            h = hint.height()
            if (
                x + w > effective.right() + 1
                and line_height > 0
                and x > effective.x()
            ):
                x = effective.x()
                y += line_height + self._v_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += w + self._h_spacing
            line_height = max(line_height, h)
        return y + line_height - rect.y() + margins.bottom()


class CropTileColumnPanel(QWidget):
    """クロップタイルを折り返し配置（横スクロールなし・縦スクロール可）。"""

    def __init__(
        self,
        *,
        margins: tuple[int, int, int, int] = (6, 6, 6, 6),
        spacing: int = 6,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._flow = FlowLayout(self, h_spacing=spacing, v_spacing=spacing)
        self._flow.setContentsMargins(*margins)

    def heightForWidth(self, width: int) -> int:
        return self._flow.heightForWidth(width)

    def hasHeightForWidth(self) -> bool:
        return True

    def clear_tiles(self) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.updateGeometry()

    def add_tile(self, widget: QWidget, index: int) -> None:
        del index
        widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        self._flow.addWidget(widget)
        self.updateGeometry()

    def set_message(self, text: str) -> None:
        self.clear_tiles()
        lbl = h.muted_label(text)
        lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        lbl.setWordWrap(True)
        self._flow.addWidget(lbl)
        self.updateGeometry()
