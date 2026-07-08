"""定型文編集用のテキストボックス・プレビュー。"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ui_qt.floating_palette.phrase_template_prefs import (
    phrase_template_to_box,
    phrase_updates_from_box,
)
from ui_qt.floating_palette.text_box_widget import TextBoxWidget


class PhraseEditPreviewPanel(QWidget):
    """配置されるテキストボックスと同じ見た目のライブプレビュー。"""

    content_changed = Signal()
    char_format_state_changed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._phrase_id: str | None = None
        self._syncing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        hint = QLabel("プレビュー（配置時と同じテキストボックス）")
        hint.setObjectName("PaletteHintLabel")
        root.addWidget(hint)

        self._canvas = QFrame()
        self._canvas.setObjectName("PhrasePreviewCanvas")
        self._canvas.setMinimumHeight(120)
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self._canvas, 1)

        self._text_box: TextBoxWidget | None = None

    def load_template(self, tpl: dict[str, Any]) -> None:
        self._phrase_id = str(tpl.get("id") or "") or None
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

    def apply_style_char_changes(self, changes: dict[str, Any], style: dict[str, Any]) -> None:
        if self._text_box is None:
            return
        from models.text_annotation_repo import resolve_text_style

        merged = resolve_text_style({**dict(style or {}), **self._phrase_style_delta(changes, style)})
        self._text_box.apply_style_dict(merged)

    def _phrase_style_delta(
        self, changes: dict[str, Any], style: dict[str, Any]
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        if "color" in changes:
            merged["textColor"] = str(changes["color"])
        if "fontSize" in changes:
            merged["fontSize"] = float(changes["fontSize"])
        if "lineSpacing" in changes:
            merged["lineSpacing"] = float(changes["lineSpacing"])
        if "bold" in changes:
            merged["fontWeight"] = "bold" if changes["bold"] else "normal"
        if "italic" in changes:
            merged["fontStyle"] = "italic" if changes["italic"] else "normal"
        if changes.get("toggleBold"):
            merged["fontWeight"] = (
                "normal" if str(style.get("fontWeight")) == "bold" else "bold"
            )
        if changes.get("toggleItalic"):
            merged["fontStyle"] = (
                "normal" if str(style.get("fontStyle")) == "italic" else "italic"
            )
        return merged

    def is_text_editing(self) -> bool:
        return bool(self._text_box and self._text_box.is_editing())

    def start_text_editing(self) -> None:
        if self._text_box is None:
            return
        self._text_box.set_selected(True)
        self._text_box.start_editing()

    def finish_text_editing(self) -> None:
        if self._text_box is None:
            return
        if self._text_box.is_editing():
            self._text_box.finish_editing()

    def _mount_box(self, box: dict[str, Any]) -> None:
        if self._text_box is not None:
            self._text_box.blockSignals(True)
            self._text_box.setParent(None)
            self._text_box.deleteLater()
            self._text_box = None
        item = copy.deepcopy(box)
        self._text_box = TextBoxWidget(item, display_scale=1.0, parent=self._canvas)
        self._text_box.set_preview_mode(True)
        self._text_box.set_text_tool_mode(True)
        self._text_box.set_selected(True)
        self._text_box.changed.connect(self._on_box_changed)
        self._text_box.char_format_state_changed.connect(
            self._on_char_format_state_changed
        )
        self._text_box.show()
        self._layout_box()

    def _reload_box(self, box: dict[str, Any]) -> None:
        self._syncing = True
        try:
            self._mount_box(box)
        finally:
            self._syncing = False

    def _layout_box(self) -> None:
        if self._text_box is None:
            return
        self._text_box.move(12, 12)
        self._text_box.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_box()

    def _on_box_changed(self) -> None:
        if self._syncing:
            return
        self.content_changed.emit()

    def _on_char_format_state_changed(self, state: dict[str, Any]) -> None:
        if self._syncing or not self.is_text_editing():
            return
        self.char_format_state_changed.emit(state)
