"""④ 採点基準テーブル用ウィジェット。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QWidget,
)

JUDGMENT_OPTIONS = ("○", "△", "×")


def make_judgment_combo(
    judgment: str,
    on_change: Callable[[str], None],
) -> QComboBox:
    combo = QComboBox()
    combo.addItems(list(JUDGMENT_OPTIONS))
    combo.setToolTip("判定を選択")
    combo.setFixedHeight(28)
    combo.setMinimumWidth(52)
    combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    current = judgment if judgment in JUDGMENT_OPTIONS else "×"
    combo.blockSignals(True)
    combo.setCurrentText(current)
    combo.blockSignals(False)
    combo.currentTextChanged.connect(on_change)
    return combo


class ScoreStepWidget(QWidget):
    """得点: － 直接入力 ＋（±1）。"""

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
        self._value = 0
        self.setFixedHeight(28)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        btn_font = self.font()
        btn_font.setPointSize(13)
        btn_font.setBold(True)

        self._down = QPushButton("－")
        self._down.setFixedSize(26, 26)
        self._down.setToolTip("1点減点")
        self._down.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._down.setFont(btn_font)

        self._edit = QLineEdit()
        self._edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._edit.setFixedWidth(40)
        self._edit.setFixedHeight(24)
        self._edit.setToolTip("得点（クリックで直接入力）")
        self._edit.setValidator(QIntValidator(0, self._max_score, self))

        self._up = QPushButton("＋")
        self._up.setFixedSize(26, 26)
        self._up.setToolTip("1点加点")
        self._up.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._up.setFont(btn_font)

        lay.addStretch()
        lay.addWidget(self._down)
        lay.addWidget(self._edit)
        lay.addWidget(self._up)
        lay.addStretch()

        self._down.clicked.connect(self._decrement)
        self._up.clicked.connect(self._increment)
        self._edit.editingFinished.connect(self._commit_edit)
        self._edit.returnPressed.connect(self._commit_edit)
        self._edit.installEventFilter(self)
        self.set_value(value)

    def focus_editor(self) -> None:
        self._edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._edit.selectAll()

    def set_value(self, value: int) -> None:
        self._value = max(0, min(self._max_score, int(value)))
        self._edit.blockSignals(True)
        self._edit.setText(str(self._value))
        self._edit.blockSignals(False)

    def value(self) -> int:
        return self._value

    def _increment(self) -> None:
        self._apply_value(self._value + 1)

    def _decrement(self) -> None:
        self._apply_value(self._value - 1)

    def _commit_edit(self) -> None:
        raw = self._edit.text().strip()
        if not raw:
            parsed = 0
        else:
            try:
                parsed = int(raw)
            except ValueError:
                parsed = self._value
        self._apply_value(parsed)

    def _apply_value(self, value: int) -> None:
        clamped = max(0, min(self._max_score, int(value)))
        if clamped == self._value and self._edit.text() == str(clamped):
            return
        self._value = clamped
        self._edit.blockSignals(True)
        self._edit.setText(str(self._value))
        self._edit.blockSignals(False)
        self._on_change(self._value)

    def eventFilter(self, watched, event: QEvent) -> bool:  # noqa: ANN001, N802
        if watched is self._edit and event.type() == QEvent.Type.FocusIn:
            self._edit.selectAll()
        return super().eventFilter(watched, event)


def wrap_table_cell(widget: QWidget) -> QWidget:
    """テーブルセル中央寄せ用ラッパー。"""
    wrap = QWidget()
    wrap.setMinimumHeight(32)
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(2, 2, 2, 2)
    lay.setSpacing(0)
    lay.addStretch()
    lay.addWidget(widget)
    lay.addStretch()
    return wrap


def find_judgment_combo(table: QTableWidget, row: int, col: int = 4) -> QComboBox | None:
    wrap = table.cellWidget(row, col)
    if wrap is None:
        return None
    return wrap.findChild(QComboBox)


def find_score_widget(table: QTableWidget, row: int, col: int = 5) -> ScoreStepWidget | None:
    wrap = table.cellWidget(row, col)
    if wrap is None:
        return None
    return wrap.findChild(ScoreStepWidget)


def open_judgment_combo(table: QTableWidget, row: int, col: int = 4) -> None:
    combo = find_judgment_combo(table, row, col)
    if combo is not None:
        combo.setFocus(Qt.FocusReason.OtherFocusReason)
        combo.showPopup()


def focus_score_widget(table: QTableWidget, row: int, col: int = 5) -> None:
    widget = find_score_widget(table, row, col)
    if widget is not None:
        widget.focus_editor()
