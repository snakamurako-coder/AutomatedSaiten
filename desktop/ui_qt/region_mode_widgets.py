"""記述欄・本人欄の矩形指定モード（自動認識 / 手動設定）トグル。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QWidget

from ui_qt.style import set_variant


def _refresh_nav_button(btn: QPushButton) -> None:
    set_variant(btn, "nav")
    style = btn.style()
    style.unpolish(btn)
    style.polish(btn)
    btn.update()


class RegionDetectModeToggle(QWidget):
    """自動認識 / 手動設定の排他トグル。"""

    def __init__(
        self,
        *,
        auto_detect: bool = True,
        on_change: Callable[[bool], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_change = on_change
        self._updating = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btn_auto = QPushButton("自動認識")
        self._btn_manual = QPushButton("手動設定")
        for btn in (self._btn_auto, self._btn_manual):
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setFixedHeight(26)
            btn.setFixedWidth(72)
            _refresh_nav_button(btn)
            self._group.addButton(btn)
            lay.addWidget(btn)

        self._btn_auto.clicked.connect(lambda: self._emit_if(True))
        self._btn_manual.clicked.connect(lambda: self._emit_if(False))
        self.set_auto_detect(auto_detect)

    def set_auto_detect(self, enabled: bool) -> None:
        self._updating = True
        self._group.blockSignals(True)
        self._btn_auto.setChecked(bool(enabled))
        self._btn_manual.setChecked(not enabled)
        self._group.blockSignals(False)
        self._updating = False
        _refresh_nav_button(self._btn_auto)
        _refresh_nav_button(self._btn_manual)

    def is_auto_detect(self) -> bool:
        return self._btn_auto.isChecked()

    def _emit_if(self, auto_detect: bool) -> None:
        if self._updating:
            return
        _refresh_nav_button(self._btn_auto)
        _refresh_nav_button(self._btn_manual)
        if self._on_change is not None:
            self._on_change(auto_detect)
