"""GAS 版と同様のホバー展開サイドバー（作業画面の上にオーバーレイ表示）。"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui_qt.style import COLORS

_COLLAPSED_W = 28
_EXPANDED_W = 232
_PANEL_W = _EXPANDED_W - _COLLAPSED_W
_COLLAPSE_MS = 280


class HoverSidebar(QWidget):
    """左端グラバー常時表示。展開時は作業画面の上にパネルを重ねる（レイアウトは押し出さない）。"""

    def __init__(
        self,
        content: QWidget,
        parent: QWidget | None = None,
        on_layout_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HoverSidebar")
        self._on_layout_changed = on_layout_changed
        self._expanded = False
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse)

        # 1文字ずつ改行して縦書き表示（付箋グラバー）
        self._grabber = QLabel("\n".join("メニュー"))
        self._grabber.setObjectName("NavGrabber")
        self._grabber.setAlignment(Qt.AlignCenter)
        self._grabber.setToolTip("マウスを乗せるとメニューを表示")
        self._grabber.setFixedWidth(_COLLAPSED_W)
        self._grabber.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._grabber.setWordWrap(False)

        self._content_host = QFrame()
        self._content_host.setObjectName("Sidebar")
        self._content_host.hide()
        host_lay = QVBoxLayout(self._content_host)
        host_lay.setContentsMargins(14, 16, 8, 12)
        host_lay.setSpacing(3)
        host_lay.addWidget(content)

        shadow = QGraphicsDropShadowEffect(self._content_host)
        shadow.setBlurRadius(24)
        shadow.setOffset(4, 0)
        shadow.setColor(QColor(0, 0, 0, 70))
        self._content_host.setGraphicsEffect(shadow)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._content_host)
        lay.addWidget(self._grabber)

        self._content_host.setFixedWidth(_PANEL_W)
        self.setFixedWidth(_COLLAPSED_W)
        self.setMouseTracking(True)
        self._content_host.setMouseTracking(True)
        self._grabber.setMouseTracking(True)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._collapse_timer.stop()
        self._expand()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._collapse_timer.start(_COLLAPSE_MS)
        super().leaveEvent(event)

    def _notify_layout(self) -> None:
        self.raise_()
        if self._on_layout_changed:
            self._on_layout_changed()

    def _expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self._content_host.show()
        self.setFixedWidth(_EXPANDED_W)
        self._notify_layout()

    def _collapse(self) -> None:
        if not self._expanded:
            return
        if self.underMouse() or self._content_host.underMouse() or self._grabber.underMouse():
            return
        self._expanded = False
        self._content_host.hide()
        self.setFixedWidth(_COLLAPSED_W)
        self._notify_layout()


class OverlayCentral(QWidget):
    """作業画面を全幅表示し、サイドバーを左上にオーバーレイするコンテナ。"""

    GRABBER_RESERVE = _COLLAPSED_W

    def __init__(self, content: QWidget, sidebar: HoverSidebar, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._content = content
        self._sidebar = sidebar
        content.setParent(self)
        sidebar.setParent(self)
        sidebar._on_layout_changed = self._relayout  # noqa: SLF001
        self._relayout()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        self._content.setGeometry(0, 0, w, h)
        sw = self._sidebar.width()
        self._sidebar.setGeometry(0, 0, sw, h)
        self._sidebar.raise_()
