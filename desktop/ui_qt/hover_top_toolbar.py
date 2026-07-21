"""手動採点など：上部グラバー常時表示・ホバーで操作パネルをオーバーレイ展開。"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_COLLAPSED_H = 22
_COLLAPSE_MS = 280


class HoverTopToolbar(QWidget):
    """上部グラバー常時表示。展開時は画像領域の上にパネルを重ねる（レイアウトは押し出さない）。"""

    GRABBER_RESERVE = _COLLAPSED_H

    def __init__(
        self,
        content: QWidget,
        parent: QWidget | None = None,
        on_layout_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HoverTopToolbar")
        self._on_layout_changed = on_layout_changed
        self._expanded = False
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse)

        self._grabber = QLabel("操作パネル")
        self._grabber.setObjectName("ManualTopGrabber")
        self._grabber.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._grabber.setToolTip("マウスを乗せると操作パネルを表示")
        self._grabber.setFixedHeight(_COLLAPSED_H)
        self._grabber.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._content_host = QFrame()
        self._content_host.setObjectName("ManualTopToolbarPanel")
        self._content_host.hide()
        host_lay = QVBoxLayout(self._content_host)
        host_lay.setContentsMargins(0, 0, 0, 6)
        host_lay.setSpacing(0)
        host_lay.addWidget(content)

        shadow = QGraphicsDropShadowEffect(self._content_host)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 70))
        self._content_host.setGraphicsEffect(shadow)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._grabber)
        lay.addWidget(self._content_host)

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
        self.adjustSize()
        self.updateGeometry()
        if self._on_layout_changed:
            self._on_layout_changed()

    def _expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self._content_host.show()
        self._notify_layout()

    def _collapse(self) -> None:
        if not self._expanded:
            return
        if self.underMouse() or self._content_host.underMouse() or self._grabber.underMouse():
            return
        self._expanded = False
        self._content_host.hide()
        self._notify_layout()

    def sizeHint(self):  # noqa: ANN001
        from PySide6.QtCore import QSize

        if not self._expanded:
            return QSize(super().sizeHint().width(), _COLLAPSED_H)
        self._content_host.adjustSize()
        content_h = self._content_host.sizeHint().height()
        return QSize(super().sizeHint().width(), _COLLAPSED_H + content_h)


class ManualGradingWorkOverlay(QWidget):
    """画像領域を全画面表示し、上部ツールバーをオーバーレイするコンテナ。"""

    GRABBER_RESERVE = HoverTopToolbar.GRABBER_RESERVE

    def __init__(
        self,
        image_area: QWidget,
        toolbar: HoverTopToolbar,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._image_area = image_area
        self._toolbar = toolbar
        image_area.setParent(self)
        toolbar.setParent(self)
        toolbar._on_layout_changed = self._relayout  # noqa: SLF001
        self._relayout()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        reserve = HoverTopToolbar.GRABBER_RESERVE
        self._toolbar.adjustSize()
        th = max(reserve, self._toolbar.sizeHint().height())
        self._toolbar.setGeometry(0, 0, w, th)
        self._image_area.setGeometry(0, reserve, w, max(0, h - reserve))
        self._toolbar.raise_()
