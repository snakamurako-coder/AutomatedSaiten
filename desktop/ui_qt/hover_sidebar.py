"""GAS 版と同様のホバー展開サイドバー（通常は細いグラバーのみ表示）。"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ui_qt.style import COLORS

_COLLAPSED_W = 28
_EXPANDED_W = 232
_COLLAPSE_MS = 280


class HoverSidebar(QWidget):
    """左端に細い「メニュー」グラバーを常時表示。マウスオーバーでナビを展開。"""

    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HoverSidebar")
        self._content = content
        self._expanded = False
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse)

        self._grabber = QLabel("メ\nニ\nュー")
        self._grabber.setObjectName("NavGrabber")
        self._grabber.setAlignment(Qt.AlignCenter)
        self._grabber.setToolTip("マウスを乗せるとメニューを表示")
        self._grabber.setFixedWidth(_COLLAPSED_W)
        self._grabber.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._content_host = QFrame()
        self._content_host.setObjectName("Sidebar")
        host_lay = QVBoxLayout(self._content_host)
        host_lay.setContentsMargins(14, 16, 6, 12)
        host_lay.setSpacing(3)
        host_lay.addWidget(content)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._content_host)
        lay.addWidget(self._grabber)

        self._content_host.setMaximumWidth(0)
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

    def _expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        inner = _EXPANDED_W - _COLLAPSED_W
        self._content_host.setMaximumWidth(inner)
        self.setFixedWidth(_EXPANDED_W)
        self.setStyleSheet(
            f"HoverSidebar {{ background: {COLORS['sidebar']};"
            f" border-right: 1px solid {COLORS['border']}; }}"
        )

    def _collapse(self) -> None:
        if not self._expanded:
            return
        # 子ウィジェット上にマウスがある間は閉じない
        if self.underMouse() or self._content_host.underMouse() or self._grabber.underMouse():
            return
        self._expanded = False
        self._content_host.setMaximumWidth(0)
        self.setFixedWidth(_COLLAPSED_W)
        self.setStyleSheet("HoverSidebar { background: transparent; border: none; }")
