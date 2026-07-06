"""フローティングパレット統合コントローラ。"""

from __future__ import annotations

from typing import Any, Protocol

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QScrollArea, QWidget

from ui_qt.floating_palette.palette_prefs import (
    TOOL_ERASER,
    TOOL_NONE,
    TOOL_PEN,
    TOOL_TEXT,
    load_palette_prefs,
    load_text_palette_colors,
    save_palette_prefs,
)
from ui_qt.floating_palette.tool_palette_window import MODE_DRAW, MODE_TEXT, ToolPaletteWindow
from ui_qt.speech import SpeechEngine
from ui_qt.speech.speech_confirm_dialog import SpeechConfirmDialog, SpeechConfirmResult
from ui_qt.stylus_overlay import CropInkImageStack
from ui_qt.stylus_prefs import load_stylus_prefs


class AnnotationPage(Protocol):
    def viewer_scroll(self) -> QScrollArea: ...
    def palette_ink_stacks(self) -> list[CropInkImageStack]: ...
    def palette_save_annotations(self, result_id: int, field_id: str, items: list) -> None: ...
    def palette_field_id(self) -> str: ...


class PaletteFabButton(QPushButton):
    """最小化時の復元 FAB。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("描画ツール", parent)
        self.setObjectName("PaletteFabButton")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint,
        )
        self.setFixedSize(96, 36)


class PaletteController:
    """描画・テキストパレットのライフサイクルと状態同期。"""

    ACTIVE_STEPS = frozenset({4, 11})

    def __init__(self, main_window: QWidget) -> None:
        self._main = main_window
        self._page: AnnotationPage | None = None
        self._step_id: int | None = None
        self._tool = TOOL_NONE
        self._show_ink = True
        self._show_text = True
        stylus = load_stylus_prefs()
        self._palm_rejection = stylus["palm_rejection"]
        self._eraser_mode = stylus["eraser_mode"]
        prefs = load_palette_prefs()

        self.tool_window = ToolPaletteWindow(None)
        self.fab = PaletteFabButton(None)
        self.tool_window.set_eraser_mode(self._eraser_mode)
        self.tool_window.set_palm_rejection(self._palm_rejection)

        fp = self.tool_window.format_panel
        self.tool_window.tool_changed.connect(self._on_tool_changed)
        self.tool_window.brush_changed.connect(self._on_brush_changed)
        self.tool_window.eraser_mode_changed.connect(self._on_eraser_mode_changed)
        self.tool_window.show_ink_changed.connect(self._on_show_ink_changed)
        self.tool_window.show_text_changed.connect(self._on_show_text_changed)
        self.tool_window.minimize_requested.connect(self._minimize)
        fp.style_changed.connect(self._on_format_style)
        fp.edit_done_requested.connect(self._on_format_edit_done)
        fp.edit_requested.connect(self._on_format_edit)
        fp.delete_requested.connect(self._on_format_delete)
        fp.speech_toggled.connect(self._on_format_speech_toggled)
        self.fab.clicked.connect(self._restore_from_fab)

        self._speech = SpeechEngine(main_window)
        self._speech.transcript_received.connect(self._on_speech_transcript)
        self._speech.error.connect(self._on_speech_error)
        self._speech.listening_changed.connect(self._on_speech_listening_changed)
        self._speech_confirm_open = False
        self._active_stack: CropInkImageStack | None = None
        fp.set_speech_available(SpeechEngine.is_available())

        self.tool_window.clear_ink_requested.connect(self._on_clear_active_ink)
        self.tool_window.clear_text_boxes_requested.connect(self._on_clear_active_text_boxes)

        self.tool_window.set_view_mode(str(prefs.get("view_mode") or "simple"))
        self.tool_window.set_brush(
            str(prefs.get("last_color") or "#111827"),
            float(prefs.get("last_width") or 2.5),
            float(prefs.get("last_alpha") or 1.0),
        )
        saved_mode = str(prefs.get("last_input_mode") or MODE_DRAW)
        saved_tool = self._normalize_saved_tool(str(prefs.get("last_tool") or TOOL_NONE))
        if saved_mode == MODE_TEXT or saved_tool == TOOL_TEXT:
            self.tool_window.set_input_mode(MODE_TEXT)
        else:
            self.tool_window.set_input_mode(MODE_DRAW)
            self.tool_window.set_draw_tool(saved_tool)
        self._tool = self.tool_window.current_tool()
        self.tool_window.set_text_palette_colors(load_text_palette_colors())
        if prefs.get("minimized"):
            self._minimize()
        else:
            x, y = int(prefs.get("x") or 100), int(prefs.get("y") or 100)
            self.tool_window.move(x, y)

    def _normalize_saved_tool(self, tool: str) -> str:
        if self._palm_rejection and tool == TOOL_PEN:
            return TOOL_NONE
        if tool not in (TOOL_PEN, TOOL_ERASER, TOOL_TEXT, TOOL_NONE):
            return TOOL_NONE
        return tool

    def ensure_palette_visible(self) -> None:
        """描画ツールまたは FAB を前面表示（グリッド描画後などに呼ぶ）。"""
        if self._step_id not in self.ACTIVE_STEPS:
            return
        prefs = load_palette_prefs()
        if prefs.get("minimized"):
            self.tool_window.hide()
            self._position_fab()
            self.fab.show()
            self.fab.raise_()
        else:
            self.fab.hide()
            self.tool_window.show()
            self.tool_window.raise_()
            self.tool_window.activateWindow()

    def attach_page(self, page: AnnotationPage | None, step_id: int) -> None:
        self._page = page
        self._step_id = step_id
        if self.tool_window.current_input_mode() == MODE_TEXT:
            self.tool_window.show_draw_mode()
        self._connect_stacks()
        self._apply_to_stacks()

    def detach(self) -> None:
        self._stop_speech()
        self._page = None
        self._step_id = None
        self._active_stack = None
        self.tool_window.hide()
        self.fab.hide()

    def show_for_step(self, step_id: int) -> None:
        if step_id not in self.ACTIVE_STEPS:
            self.detach()
            return
        self.ensure_palette_visible()

    def persist(self) -> None:
        pos = self.tool_window.pos()
        fab_pos = self.fab.pos()
        color, width, alpha = self.tool_window.current_brush()
        save_palette_prefs(
            {
                "x": pos.x(),
                "y": pos.y(),
                "minimized": not self.tool_window.isVisible() and self.fab.isVisible(),
                "fab_x": fab_pos.x(),
                "fab_y": fab_pos.y(),
                "last_color": color,
                "last_width": width,
                "last_alpha": alpha,
                "last_tool": self._tool,
                "last_input_mode": self.tool_window.current_input_mode(),
                "view_mode": self.tool_window._view_mode,
            }
        )

    def apply_text_palette_colors(self) -> None:
        self.tool_window.set_text_palette_colors(load_text_palette_colors())

    def apply_config(self) -> None:
        stylus = load_stylus_prefs()
        self._palm_rejection = stylus["palm_rejection"]
        self._eraser_mode = stylus["eraser_mode"]
        self.tool_window.set_palm_rejection(self._palm_rejection)
        self.tool_window.set_eraser_mode(self._eraser_mode)
        self.apply_text_palette_colors()
        if self._palm_rejection and self._tool == TOOL_PEN:
            self.tool_window.set_draw_tool(TOOL_NONE)
            self._tool = TOOL_NONE
        self._apply_to_stacks()

    def _position_fab(self) -> None:
        prefs = load_palette_prefs()
        fx, fy = prefs.get("fab_x"), prefs.get("fab_y")
        if fx is not None and fy is not None:
            self.fab.move(int(fx), int(fy))
            return
        vr = self._viewer_global_rect()
        if vr is None:
            self.fab.move(100, 100)
            return
        self.fab.move(vr.right() - self.fab.width() - 16, vr.bottom() - self.fab.height() - 16)

    def _viewer_global_rect(self) -> QRect | None:
        if not self._page:
            return None
        vp = self._page.viewer_scroll().viewport()
        tl = vp.mapToGlobal(QPoint(0, 0))
        return QRect(tl, vp.size())

    def _minimize(self) -> None:
        self.tool_window.hide()
        self._position_fab()
        self.fab.show()

    def _restore_from_fab(self) -> None:
        self.fab.hide()
        self.tool_window.show()
        self.tool_window.raise_()
        self.tool_window.activateWindow()

    def _connect_stacks(self) -> None:
        if not self._page:
            return
        for stack in self._page.palette_ink_stacks():
            try:
                stack.text_layer.selection_changed.disconnect(self._on_text_selection)
            except (RuntimeError, TypeError):
                pass
            try:
                stack.image_clicked.disconnect(self._on_stack_image_clicked)
            except (RuntimeError, TypeError):
                pass
            stack.text_layer.selection_changed.connect(self._on_text_selection)
            stack.image_clicked.connect(self._on_stack_image_clicked)

    def _stacks(self) -> list[CropInkImageStack]:
        if not self._page:
            return []
        return self._page.palette_ink_stacks()

    def _apply_to_stacks(self) -> None:
        color, width, alpha = self.tool_window.current_brush()
        for stack in self._stacks():
            stack.set_palm_rejection(self._palm_rejection)
            stack.set_show_ink(self._show_ink)
            stack.set_show_text(self._show_text)
            stack.set_eraser_mode(self._eraser_mode)
            stack.set_tool_mode(self._tool)
            stack.set_brush(color, width, alpha)

    def _on_tool_changed(self, tool: str) -> None:
        prev = self._tool
        self._tool = tool
        if prev == TOOL_TEXT and tool != TOOL_TEXT:
            self.finish_all_text_editing()
        if tool != TOOL_TEXT:
            for stack in self._stacks():
                stack.text_layer.clear_selection()
        self._apply_to_stacks()

    def _on_text_selection(self, box: dict[str, Any] | None) -> None:
        if not box:
            self._stop_speech()
            return
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                self._active_stack = stack
                break
        self.tool_window.show_text_mode()
        self.tool_window.format_panel.load_style(box.get("style") or {})

    def _on_stack_image_clicked(self) -> None:
        sender = self.sender()
        if isinstance(sender, CropInkImageStack):
            self._active_stack = sender
        if self._tool != TOOL_TEXT:
            return
        for stack in self._stacks():
            stack.text_layer.finish_all_editing()
            stack.text_layer.clear_selection()

    def _resolve_active_stack(self) -> CropInkImageStack | None:
        if self._active_stack is not None and self._active_stack in self._stacks():
            return self._active_stack
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                self._active_stack = stack
                return stack
        return None

    def _on_clear_active_ink(self) -> None:
        stack = self._resolve_active_stack()
        if stack is None:
            from ui_qt import helpers as h

            h.warn(self._main, "消去", "対象の画像をクリックして選択してください")
            return
        if not stack.ink_overlay.strokes():
            from ui_qt import helpers as h

            h.warn(self._main, "消去", "選択中の画像にペン描写がありません")
            return
        ans = QMessageBox.question(
            self._main,
            "確認",
            "選択中の画像のペン描写をすべて消去しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        stack.clear_ink()

    def _on_clear_active_text_boxes(self) -> None:
        self._stop_speech()
        stack = self._resolve_active_stack()
        if stack is None:
            from ui_qt import helpers as h

            h.warn(self._main, "消去", "対象の画像をクリックして選択してください")
            return
        if not stack.text_layer.annotations():
            from ui_qt import helpers as h

            h.warn(self._main, "消去", "選択中の画像にテキストボックスがありません")
            return
        ans = QMessageBox.question(
            self._main,
            "確認",
            "選択中の画像のテキストボックスをすべて消去しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        stack.clear_all_text_boxes()

    def _on_brush_changed(self, color: str, width: float, alpha: float) -> None:
        for stack in self._stacks():
            stack.set_brush(color, width, alpha)

    def _on_eraser_mode_changed(self, mode: str) -> None:
        self._eraser_mode = str(mode)
        from ui_qt.stylus_prefs import save_stylus_eraser_mode

        save_stylus_eraser_mode(self._eraser_mode)
        for stack in self._stacks():
            stack.set_eraser_mode(mode)

    def _on_show_ink_changed(self, visible: bool) -> None:
        self._show_ink = bool(visible)
        for stack in self._stacks():
            stack.set_show_ink(visible)

    def _on_show_text_changed(self, visible: bool) -> None:
        self._show_text = bool(visible)
        for stack in self._stacks():
            stack.set_show_text(visible)

    def _on_format_style(self, style: dict[str, Any]) -> None:
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.update_selected_style(style)

    def _on_format_edit_done(self) -> None:
        self._stop_speech()
        self.finish_all_text_editing()
        fw = QApplication.focusWidget()
        if fw is not None:
            fw.clearFocus()
        for stack in self._stacks():
            stack.text_layer.clear_selection()

    def _on_format_edit(self) -> None:
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.edit_selected()
                return

    def _on_format_delete(self) -> None:
        self._stop_speech()
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.delete_selected()
                return

    def finish_all_text_editing(self) -> None:
        if self._speech.is_listening():
            self._stop_speech()
        for stack in self._stacks():
            stack.text_layer.finish_all_editing()

    def _stop_speech(self) -> None:
        self._speech.stop()
        self.tool_window.format_panel.set_speech_active(False)

    def _on_format_speech_toggled(self, on: bool) -> None:
        if not on:
            self._stop_speech()
            return
        if not any(stack.text_layer.selected_box() for stack in self._stacks()):
            self.tool_window.format_panel.set_speech_active(False)
            from ui_qt import helpers as h

            h.warn(self._main, "音声入力", "テキストボックスを選択してください")
            return
        if not self._ensure_speech_target_editing():
            self.tool_window.format_panel.set_speech_active(False)
            return
        self._speech.start()

    def _ensure_speech_target_editing(self) -> bool:
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.edit_selected()
                return True
        return False

    def _on_speech_transcript(self, text: str) -> None:
        chunk = str(text or "").strip()
        if not chunk or self._speech_confirm_open:
            return
        self._speech.pause()
        self._speech_confirm_open = True
        try:
            dlg = SpeechConfirmDialog(self._main, chunk)
            result = dlg.exec()
            if result == SpeechConfirmResult.ACCEPT:
                for stack in self._stacks():
                    if stack.text_layer.append_transcript_to_selected(chunk):
                        break
                self._speech.resume()
            elif result == SpeechConfirmResult.RETRY:
                self._speech.resume()
            else:
                self._stop_speech()
        finally:
            self._speech_confirm_open = False

    def _on_speech_error(self, message: str) -> None:
        from ui_qt import helpers as h

        self._stop_speech()
        h.warn(self._main, "音声入力", message)

    def _on_speech_listening_changed(self, on: bool) -> None:
        self.tool_window.format_panel.set_speech_active(on)

    def register_stack(self, stack: CropInkImageStack) -> None:
        """新規タイル生成後に呼ぶ。"""
        stack.set_before_ink_draw(self.finish_all_text_editing)
        stack.text_layer.selection_changed.connect(self._on_text_selection)
        stack.image_clicked.connect(self._on_stack_image_clicked)
        color, width, alpha = self.tool_window.current_brush()
        stack.set_palm_rejection(self._palm_rejection)
        stack.set_show_ink(self._show_ink)
        stack.set_show_text(self._show_text)
        stack.set_eraser_mode(self._eraser_mode)
        stack.set_tool_mode(self._tool)
        stack.set_brush(color, width, alpha)
