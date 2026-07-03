"""④ 採点基準テーブル用ウィジェット。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

JUDGMENT_OPTIONS = ("○", "△", "×")


def make_judgment_combo(
    judgment: str,
    on_change: Callable[[str], None],
) -> QComboBox:
    combo = QComboBox()
    combo.addItems(list(JUDGMENT_OPTIONS))
    current = judgment if judgment in JUDGMENT_OPTIONS else "×"
    combo.setCurrentText(current)
    combo.setToolTip("判定を選択")
    combo.currentTextChanged.connect(on_change)
    return combo


class ScoreStepWidget(QWidget):
    """得点: ▼ 直接入力 ▲（±1）。"""

    def __init__(
        self,
        value: int,
        max_score: int,
        on_change: Callable[[int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._max_score = max(0, int(max_score))
        self._on_change = on_change
        self._block = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(1, 0, 1, 0)
        lay.setSpacing(1)

        self._down = QPushButton("▼")
        self._down.setFixedSize(24, 24)
        self._down.setToolTip("1点減点")
        self._edit = QLineEdit()
        self._edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit.setFixedWidth(42)
        self._edit.setToolTip("得点（直接入力可）")
        self._up = QPushButton("▲")
        self._up.setFixedSize(24, 24)
        self._up.setToolTip("1点加点")

        lay.addStretch()
        lay.addWidget(self._down)
        lay.addWidget(self._edit)
        lay.addWidget(self._up)
        lay.addStretch()

        self._down.clicked.connect(lambda: self._bump(-1))
        self._up.clicked.connect(lambda: self._bump(1))
        self._edit.editingFinished.connect(self._commit_edit)
        self.set_value(value)

    def set_value(self, value: int) -> None:
        self._block = True
        v = max(0, min(self._max_score, int(value)))
        self._edit.setText(str(v))
        self._block = False

    def value(self) -> int:
        raw = self._edit.text().strip()
        if not raw:
            return 0
        try:
            return max(0, min(self._max_score, int(raw)))
        except ValueError:
            return 0

    def _bump(self, delta: int) -> None:
        self.set_value(self.value() + delta)
        self._emit()

    def _commit_edit(self) -> None:
        self.set_value(self.value())
        self._emit()

    def _emit(self) -> None:
        if not self._block:
            self._on_change(self.value())


def wrap_table_cell(widget: QWidget) -> QWidget:
    """テーブルセル中央寄せ用ラッパー。"""
    wrap = QWidget()
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addStretch()
    lay.addWidget(widget)
    lay.addStretch()
    return wrap
