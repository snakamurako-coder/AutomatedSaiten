"""スタイラス手書きの共通コントロール。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QWidget

from ui_qt import helpers as h


class StylusControls(QWidget):
    """パームリジェクション・手書きレイヤー表示の切替。"""

    settings_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.palm_rejection_check = QCheckBox("パームリジェクション")
        self.palm_rejection_check.setChecked(True)
        self.palm_rejection_check.setToolTip(
            "ON: スタイラスペンのみ手書き（指・マウスは選択操作）\n"
            "OFF: 指・タッチペン・マウスでも手書き可能"
        )
        self.palm_rejection_check.toggled.connect(lambda _v: self.settings_changed.emit())
        lay.addWidget(self.palm_rejection_check)

        self.show_ink_check = QCheckBox("手書きレイヤー表示")
        self.show_ink_check.setChecked(True)
        self.show_ink_check.setToolTip("手書き内容の表示／非表示（データは保持）")
        self.show_ink_check.toggled.connect(lambda _v: self.settings_changed.emit())
        lay.addWidget(self.show_ink_check)

        lay.addWidget(h.caption_label("スタイラス＝最前面レイヤー"))
        lay.addStretch()

    def palm_rejection(self) -> bool:
        return self.palm_rejection_check.isChecked()

    def show_ink_layer(self) -> bool:
        return self.show_ink_check.isChecked()

    def set_palm_rejection(self, enabled: bool) -> None:
        self.palm_rejection_check.setChecked(bool(enabled))

    def set_show_ink_layer(self, visible: bool) -> None:
        self.show_ink_check.setChecked(bool(visible))
