"""定型文編集用のテキストボックス・プレビュー。"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ui_qt.floating_palette.phrase_template_prefs import (
    phrase_template_to_box,
    phrase_updates_from_box,
)
from ui_qt.floating_palette.text_box_widget import TextBoxWidget

_PREVIEW_MIN_H = 120
_PREVIEW_EDIT_MIN_H = 220
_PREVIEW_CANVAS_MAX_H = 260


class _PreviewCanvas(QFrame):
    """プレビュー用キャンバス（リサイズ時に子を再配置）。"""

    resized = Signal()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.resized.emit()


class PhraseEditPreviewPanel(QWidget):
    """配置されるテキストボックスと同じ見た目のライブプレビュー。"""

    content_changed = Signal()
    char_format_state_changed = Signal(dict)
    layout_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._phrase_id: str | None = None
        self._syncing = False
        self._text_editing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._hint = QLabel(self._hint_text())
        self._hint.setObjectName("PaletteHintLabel")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

        self._canvas = _PreviewCanvas()
        self._canvas.setObjectName("PhrasePreviewCanvas")
        self._canvas.setMinimumHeight(_PREVIEW_MIN_H)
        self._canvas.setMaximumHeight(_PREVIEW_CANVAS_MAX_H)
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._canvas.resized.connect(self._layout_box)
        root.addWidget(self._canvas, 1)

        self._text_box: TextBoxWidget | None = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(220, _PREVIEW_MIN_H + 36)

    def sizeHint(self) -> QSize:  # noqa: N802
        h = _PREVIEW_EDIT_MIN_H if self._text_editing else _PREVIEW_MIN_H
        return QSize(260, h + 36)

    def _hint_text(self) -> str:
        if self._text_editing:
            return (
                "文字を編集中。範囲を選んで書式パネルから色・サイズ・装飾を変更できます"
            )
        return (
            "プレビュー（配置時と同じテキストボックス）\n"
            "ダブルクリックで文言編集・四隅ドラッグでサイズ調整"
        )

    def load_template(self, tpl: dict[str, Any]) -> None:
        self._phrase_id = str(tpl.get("id") or "") or None
        self._text_editing = False
        self._mount_box(phrase_template_to_box(tpl))

    def export_updates(self) -> dict[str, Any]:
        if not self._phrase_id or self._text_box is None:
            return {}
        return phrase_updates_from_box(self._phrase_id, self._text_box.box_data())

    def apply_style_dict(self, style: dict[str, Any]) -> None:
        if self._text_box is None:
            return
        self._text_box.apply_style_dict(style)

    def apply_char_format(self, changes: dict[str, Any]) -> None:
        if self._text_box is None:
            return
        self._text_box.apply_char_format(changes)

    def set_focus_guard_widgets(self, widgets: list[QWidget] | tuple[QWidget, ...]) -> None:
        if self._text_box is None:
            return
        self._text_box.set_focus_guard_widgets(widgets)

    def is_text_editing(self) -> bool:
        return bool(self._text_box and self._text_box.is_editing())

    def current_char_format_state(self) -> dict[str, Any]:
        if self._text_box is None:
            return {}
        return self._text_box.current_char_format_state()

    def start_text_editing(self) -> None:
        if self._text_box is None:
            return
        self._text_box.set_selected(True)
        self._text_box.start_editing()
        self._set_text_editing_focus(True)

    def finish_text_editing(self) -> None:
        if self._text_box is None:
            return
        if self._text_box.is_editing():
            self._text_box.finish_editing()
        self._set_text_editing_focus(False)

    def _mount_box(self, box: dict[str, Any]) -> None:
        if self._text_box is not None:
            self._text_box.blockSignals(True)
            self._text_box.setParent(None)
            self._text_box.deleteLater()
            self._text_box = None
        item = copy.deepcopy(box)
        item["x"] = 0.0
        item["y"] = 0.0
        self._text_box = TextBoxWidget(item, display_scale=1.0, parent=self._canvas)
        self._text_box.set_preview_mode(True)
        self._text_box.set_preview_resize_enabled(True)
        self._text_box.set_text_tool_mode(True)
        self._text_box.set_selected(True)
        self._text_box.changed.connect(self._on_box_changed)
        self._text_box.char_format_state_changed.connect(
            self._on_char_format_state_changed
        )
        self._text_box.editing_started.connect(self._on_text_editing_started)
        self._text_box.editing_committed.connect(self._on_text_editing_finished)
        self._text_box.interactive_change_finished.connect(self._on_box_resized)
        self._text_box.show()
        self._update_canvas_height()
        self._layout_box()

    def _update_canvas_height(self) -> None:
        target = _PREVIEW_EDIT_MIN_H if self._text_editing else _PREVIEW_MIN_H
        if self._canvas.minimumHeight() != target:
            self._canvas.setMinimumHeight(target)
            self.layout_changed.emit()

    def _layout_box(self) -> None:
        if self._text_box is None:
            return
        cw = max(1, self._canvas.width())
        ch = max(1, self._canvas.height())
        tw = max(1, self._text_box.width())
        th = max(1, self._text_box.height())
        x = max(0, (cw - tw) // 2)
        y = max(0, (ch - th) // 2)
        self._text_box.move(x, y)
        self._text_box.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_box()

    def _on_box_changed(self) -> None:
        if self._syncing:
            return
        self._layout_box()
        self.content_changed.emit()

    def _on_box_resized(self) -> None:
        self._layout_box()
        self.content_changed.emit()

    def _on_char_format_state_changed(self, state: dict[str, Any]) -> None:
        if self._syncing or not self.is_text_editing():
            return
        self.char_format_state_changed.emit(state)

    def _on_text_editing_started(self) -> None:
        self._set_text_editing_focus(True)

    def _on_text_editing_finished(self) -> None:
        self._set_text_editing_focus(False)

    def _set_text_editing_focus(self, on: bool) -> None:
        self._text_editing = bool(on)
        self._hint.setText(self._hint_text())
        self._update_canvas_height()
        self._layout_box()
