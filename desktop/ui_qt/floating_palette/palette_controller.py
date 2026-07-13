"""フローティングパレット統合コントローラ。"""

from __future__ import annotations

import copy
from typing import Any, Protocol

from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from models.text_annotation_repo import (
    DEFAULT_TEXT_COLOR,
    DEFAULT_TEXT_STYLE,
    get_text_annotations,
    resolve_text_style,
)

from ui_qt.floating_palette.annotation_undo import AnnotationUndoStack
from ui_qt.floating_palette.palette_prefs import (
    TOOL_ERASER,
    TOOL_NONE,
    TOOL_PEN,
    TOOL_PHRASE,
    TOOL_TEXT,
    VIEW_DETAILED,
    load_palette_prefs,
    load_text_box_default_style,
    load_text_palette_colors,
    save_palette_prefs,
)
from ui_qt.floating_palette.phrase_batch_update_dialog import (
    PhraseBatchUpdateDialog,
)
from ui_qt.floating_palette.phrase_template_prefs import (
    add_phrase_template,
    delete_phrase_template,
    load_phrase_templates,
    phrase_from_text_box,
    phrase_template_by_group_id,
    touch_recent_phrase,
    update_phrase_template,
)
from ui_qt.floating_palette.tool_palette_window import (
    MODE_DRAW,
    MODE_PHRASE,
    MODE_TEXT,
    ToolPaletteWindow,
)
from ui_qt.speech import SpeechEngine
from ui_qt.speech.speech_confirm_dialog import SpeechConfirmDialog, SpeechConfirmResult
from ui_qt.speech.speech_prefs import (
    SPEECH_MODE_WINDOWS,
    is_speech_input_available,
    load_speech_input_mode,
)
from ui_qt.speech.windows_voice_typing import toggle_windows_voice_typing
from ui_qt.stylus_overlay import CropInkImageStack
from ui_qt.stylus_prefs import load_stylus_prefs


class AnnotationPage(Protocol):
    def viewer_scroll(self) -> QScrollArea: ...
    def palette_ink_stacks(self) -> list[CropInkImageStack]: ...
    def palette_save_annotations(self, result_id: int, field_id: str, items: list) -> None: ...
    def palette_field_id(self) -> str: ...
    def palette_test_id(self) -> str | None: ...


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
        pp = self.tool_window.phrase_panel
        self.tool_window.tool_changed.connect(self._on_tool_changed)
        self.tool_window.brush_changed.connect(self._on_brush_changed)
        self.tool_window.eraser_mode_changed.connect(self._on_eraser_mode_changed)
        self.tool_window.show_ink_changed.connect(self._on_show_ink_changed)
        self.tool_window.show_text_changed.connect(self._on_show_text_changed)
        self.tool_window.minimize_requested.connect(self._minimize)
        self.tool_window.view_mode_changed.connect(self._on_view_mode_changed)
        self.tool_window.input_mode_changed.connect(self._on_input_mode_changed)
        fp.style_changed.connect(self._on_format_style)
        fp.char_format_changed.connect(self._on_char_format_changed)
        fp.edit_done_requested.connect(self._on_format_edit_done)
        fp.edit_requested.connect(self._on_format_edit)
        fp.delete_requested.connect(self._on_format_delete)
        fp.speech_toggled.connect(self._on_format_speech_toggled)
        pp.phrase_selected.connect(self._on_phrase_selected)
        pp.phrase_edit_requested.connect(self._on_phrase_edit_requested)
        pp.phrase_deleted.connect(self._on_phrase_deleted)
        pp.phrase_batch_update_requested.connect(
            self._on_phrase_batch_update_requested
        )
        pp.copy_from_textbox_requested.connect(self._on_copy_phrase_from_textbox)
        pp.placement_cancel_requested.connect(self._cancel_phrase_placement)
        self.tool_window.full_sheet_grade_requested.connect(
            self._on_full_sheet_grade_requested
        )
        preview = self.tool_window.phrase_preview
        preview.content_changed.connect(
            lambda: self._persist_phrase_from_preview(reload_list=False)
        )
        preview.char_format_state_changed.connect(
            self._on_phrase_preview_char_format_state
        )
        self.fab.clicked.connect(self._restore_from_fab)

        self._speech = SpeechEngine(main_window)
        self._speech.transcript_received.connect(self._on_speech_transcript)
        self._speech.error.connect(self._on_speech_error)
        self._speech.listening_changed.connect(self._on_speech_listening_changed)
        self._speech.phase_changed.connect(self._on_speech_phase_changed)
        self._speech_confirm_open = False
        self._speech_manual_finalize = False
        self._pending_speech_text: str | None = None
        self._windows_speech_active = False
        self._windows_voice_prep_attempt = 0
        self._settings_overlay_active = False
        self._active_stack: CropInkImageStack | None = None
        self._active_result_id: int | None = None
        self._pending_phrase_id: str | None = None
        self._pending_phrase_template: dict[str, Any] | None = None
        self._editing_phrase_id: str | None = None
        self._full_sheet_dialog: Any | None = None
        self._full_sheet_stack: CropInkImageStack | None = None
        self._full_sheet_palette_prev_visible = False
        self._undo = AnnotationUndoStack()
        self._undo.set_on_changed(self._sync_undo_ui)
        self.refresh_speech_prefs()

        self.tool_window.clear_ink_requested.connect(self._on_clear_active_ink)
        self.tool_window.clear_text_boxes_requested.connect(self._on_clear_active_text_boxes)
        self.tool_window.undo_requested.connect(self._on_undo_requested)
        self.tool_window.redo_requested.connect(self._on_redo_requested)

        for parent in (self._main, self.tool_window):
            shortcut = QShortcut(QKeySequence.StandardKey.Delete, parent)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._on_delete_selected_text_hotkey)
            undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, parent)
            undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            undo_shortcut.activated.connect(self._on_undo_requested)
            redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, parent)
            redo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            redo_shortcut.activated.connect(self._on_redo_requested)

        self.tool_window.set_view_mode(str(prefs.get("view_mode") or "simple"))
        self._on_view_mode_changed(self.tool_window._view_mode)
        self.tool_window.set_brush(
            str(prefs.get("last_color") or "#111827"),
            float(prefs.get("last_width") or 2.5),
            float(prefs.get("last_alpha") or 1.0),
        )
        saved_mode = str(prefs.get("last_input_mode") or MODE_DRAW)
        saved_tool = self._normalize_saved_tool(str(prefs.get("last_tool") or TOOL_NONE))
        if saved_mode == MODE_TEXT or saved_tool == TOOL_TEXT:
            self.tool_window.set_input_mode(MODE_TEXT)
        elif saved_mode == MODE_PHRASE or saved_tool == TOOL_PHRASE:
            self.tool_window.set_input_mode(MODE_PHRASE)
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
        if tool not in (TOOL_PEN, TOOL_ERASER, TOOL_TEXT, TOOL_PHRASE, TOOL_NONE):
            return TOOL_NONE
        return tool

    def ensure_palette_visible(self) -> None:
        """描画ツールまたは FAB を前面表示（グリッド描画後などに呼ぶ）。"""
        if self._step_id not in self.ACTIVE_STEPS:
            return
        if self._settings_overlay_active:
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

    def set_settings_overlay_active(self, active: bool) -> None:
        """詳細設定を最前面にする間、描画ツールの常に手前を一時解除する。"""
        self._settings_overlay_active = bool(active)
        for win in (self.tool_window, self.fab):
            win.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, not self._settings_overlay_active)
            if win.isVisible():
                win.show()
        if not self._settings_overlay_active:
            self.ensure_palette_visible()

    def attach_page(self, page: AnnotationPage | None, step_id: int) -> None:
        self._page = page
        self._step_id = step_id
        if self.tool_window.current_input_mode() not in (MODE_TEXT, MODE_PHRASE):
            self.tool_window.show_draw_mode()
        self._connect_stacks()
        self._apply_to_stacks()

    def detach(self) -> None:
        self._stop_speech()
        self._undo.clear()
        self._page = None
        self._step_id = None
        self._active_stack = None
        self._active_result_id = None
        self.tool_window.hide()
        self.fab.hide()

    def set_active_result_id(self, result_id: int) -> None:
        """グリッド再描画後も有効な、選択中画像の result_id（rowIndex）。"""
        self._active_result_id = int(result_id)
        self._active_stack = None
        for stack in self._stacks():
            if stack.result_id == self._active_result_id:
                self._active_stack = stack
                return

    def show_for_step(self, step_id: int) -> None:
        if step_id not in self.ACTIVE_STEPS:
            self.detach()
            return
        prefs = load_palette_prefs()
        if not prefs.get("minimized"):
            screen = self._main.screen() or QApplication.primaryScreen()
            self.tool_window.center_on_screen(screen)
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

    def apply_text_box_default_style(self) -> None:
        style = load_text_box_default_style()
        fp = self.tool_window.format_panel
        # 未選択時の書式パネル表示を、配置既定に合わせておく。
        if not any(s.text_layer.selected_box() for s in self._stacks()):
            fp.load_style(style)
            fp.sync_char_format(
                {
                    "color": str(style.get("textColor") or DEFAULT_TEXT_COLOR),
                    "fontSize": int(style.get("fontSize") or 14),
                    "lineSpacing": int(style.get("lineSpacing") or 20),
                    "bold": False,
                    "italic": False,
                    "underline": False,
                }
            )

    def apply_config(self) -> None:
        stylus = load_stylus_prefs()
        self._palm_rejection = stylus["palm_rejection"]
        self._eraser_mode = stylus["eraser_mode"]
        self.tool_window.set_palm_rejection(self._palm_rejection)
        self.tool_window.set_eraser_mode(self._eraser_mode)
        self.apply_text_palette_colors()
        self.apply_text_box_default_style()
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
            self._bind_stack(stack)

    def _bind_stack(self, stack: CropInkImageStack) -> None:
        self._unbind_stack(stack)
        stack.set_before_ink_draw(self.finish_all_text_editing)

        def on_selection_changed(box: dict[str, Any] | None) -> None:
            self._on_text_selection(box)

        def on_image_clicked() -> None:
            self._on_stack_image_clicked(stack)

        stack._palette_on_selection_changed = on_selection_changed  # type: ignore[attr-defined]
        stack._palette_on_image_clicked = on_image_clicked  # type: ignore[attr-defined]
        stack.text_layer.selection_changed.connect(on_selection_changed)
        stack.image_clicked.connect(on_image_clicked)

        def on_ink_history(before: list, after: list) -> None:
            self._on_ink_history_commit(stack, before, after)

        stack._palette_on_ink_history = on_ink_history  # type: ignore[attr-defined]
        stack.ink_overlay.ink_history_commit.connect(on_ink_history)

        stack.text_layer.set_undo_stack(self._undo, stack)
        self._undo.register_stack(stack)

        def on_char_format_state(state: dict[str, Any]) -> None:
            if stack.text_layer.selected_box():
                self.tool_window.format_panel.sync_char_format(state)

        stack._palette_on_char_format_state = on_char_format_state  # type: ignore[attr-defined]
        stack.text_layer.char_format_state_changed.connect(on_char_format_state)
        stack.text_layer.set_focus_guard_widgets(
            self.tool_window.text_format_focus_widgets()
        )

        color, width, alpha = self.tool_window.current_brush()
        stack.set_palm_rejection(self._palm_rejection)
        stack.set_show_ink(self._show_ink)
        stack.set_show_text(self._show_text)
        stack.set_eraser_mode(self._eraser_mode)
        self._sync_stack_phrase_template(stack)
        stack.set_tool_mode(self._tool)
        stack.set_brush(color, width, alpha)

    def _sync_stack_phrase_template(self, stack: CropInkImageStack) -> None:
        if self._tool == TOOL_PHRASE and self._pending_phrase_template:
            pid = str(self._pending_phrase_id or "")

            def on_placed(phrase_id: str = pid) -> None:
                self._on_phrase_placed(phrase_id)

            stack.text_layer.set_phrase_place_template(
                copy.deepcopy(self._pending_phrase_template),
                on_placed=on_placed,
            )
        elif self._tool != TOOL_PHRASE:
            stack.text_layer.clear_phrase_place_template()

    def _ensure_phrase_tool_mode(self) -> None:
        if (
            self.tool_window.current_input_mode() != MODE_PHRASE
            or self._tool != TOOL_PHRASE
        ):
            self.tool_window.show_phrase_mode()
        self._tool = TOOL_PHRASE
        self._apply_to_stacks()

    def _unbind_stack(self, stack: CropInkImageStack) -> None:
        sel_handler = getattr(stack, "_palette_on_selection_changed", None)
        if sel_handler is not None:
            try:
                stack.text_layer.selection_changed.disconnect(sel_handler)
            except (RuntimeError, TypeError):
                pass
            delattr(stack, "_palette_on_selection_changed")
        handler = getattr(stack, "_palette_on_image_clicked", None)
        if handler is not None:
            try:
                stack.image_clicked.disconnect(handler)
            except (RuntimeError, TypeError):
                pass
            delattr(stack, "_palette_on_image_clicked")
        ink_handler = getattr(stack, "_palette_on_ink_history", None)
        if ink_handler is not None:
            try:
                stack.ink_overlay.ink_history_commit.disconnect(ink_handler)
            except (RuntimeError, TypeError):
                pass
            delattr(stack, "_palette_on_ink_history")
        char_handler = getattr(stack, "_palette_on_char_format_state", None)
        if char_handler is not None:
            try:
                stack.text_layer.char_format_state_changed.disconnect(char_handler)
            except (RuntimeError, TypeError):
                pass
            delattr(stack, "_palette_on_char_format_state")
        stack.text_layer.set_undo_stack(None, None)
        self._undo.unregister_stack(stack)

    def _on_ink_history_commit(
        self,
        stack: CropInkImageStack,
        before_strokes: list,
        after_strokes: list,
    ) -> None:
        annotations = stack.text_layer.annotations()
        before = {"strokes": before_strokes, "annotations": annotations}
        after = {"strokes": after_strokes, "annotations": annotations}
        self._undo.push(stack, before, after)

    def _sync_undo_ui(self) -> None:
        self.tool_window.set_undo_available(self._undo.can_undo())
        self.tool_window.set_redo_available(self._undo.can_redo())

    def _after_history_apply(self) -> None:
        self._sync_undo_ui()
        for stack in self._stacks():
            box = stack.text_layer.selected_box()
            if box:
                self._set_active_stack(stack)
                self.tool_window.show_text_mode()
                self.tool_window.format_panel.load_style(box.get("style") or {})
                return

    def _on_undo_requested(self) -> None:
        if self._step_id not in self.ACTIVE_STEPS:
            return
        if not self._undo.undo(self._stacks()):
            return
        self._after_history_apply()
        if hasattr(self._main, "show_app_message"):
            self._main.show_app_message("直前の操作を戻しました", level="info")

    def _on_redo_requested(self) -> None:
        if self._step_id not in self.ACTIVE_STEPS:
            return
        if not self._undo.redo(self._stacks()):
            return
        self._after_history_apply()
        if hasattr(self._main, "show_app_message"):
            self._main.show_app_message("操作をやり直しました", level="info")

    def _stacks(self) -> list[CropInkImageStack]:
        stacks: list[CropInkImageStack] = []
        if self._page:
            stacks.extend(self._page.palette_ink_stacks())
        if self._full_sheet_stack is not None:
            stacks.append(self._full_sheet_stack)
        return stacks

    def bind_full_sheet_dialog(self, dialog: Any) -> None:
        """一枚全容ダイアログ開始（初期は採点モード＝パレット格納）。"""
        self._full_sheet_dialog = dialog
        self._full_sheet_palette_prev_visible = self.tool_window.isVisible()
        self.hide_palette_for_full_sheet()

    def unbind_full_sheet_dialog(self, dialog: Any | None = None) -> None:
        if dialog is not None and self._full_sheet_dialog is not dialog:
            return
        self.set_full_sheet_stack(None)
        self._full_sheet_dialog = None
        # 閉じたあと通常のパレット表示へ戻す
        self.fab.hide()
        if self._full_sheet_palette_prev_visible and not self._settings_overlay_active:
            self.tool_window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.tool_window.show()
            self.tool_window.raise_()
        else:
            self.ensure_palette_visible()

    def set_full_sheet_stack(self, stack: CropInkImageStack | None) -> None:
        if self._full_sheet_stack is not None and self._full_sheet_stack is not stack:
            self._unbind_stack(self._full_sheet_stack)
            if self._active_stack is self._full_sheet_stack:
                self._active_stack = None
        self._full_sheet_stack = stack
        if stack is not None:
            self._bind_stack(stack)
            self._set_active_stack(stack)
            self._apply_to_stacks()

    def show_palette_for_full_sheet(self) -> None:
        """一枚全容の描画モード: フローティングパレットを手前に出す。"""
        self.fab.hide()
        # モーダルダイアログの上に重ねる
        self.tool_window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.tool_window.show()
        self.tool_window.raise_()
        self.tool_window.activateWindow()
        if self._full_sheet_stack is not None:
            self._set_active_stack(self._full_sheet_stack)
            self._apply_to_stacks()

    def hide_palette_for_full_sheet(self) -> None:
        """一枚全容の採点モード: パレットを格納。"""
        self.tool_window.hide()
        self.fab.hide()

    def _apply_to_stacks(self) -> None:
        color, width, alpha = self.tool_window.current_brush()
        for stack in self._stacks():
            stack.set_palm_rejection(self._palm_rejection)
            stack.set_show_ink(self._show_ink)
            stack.set_show_text(self._show_text)
            stack.set_eraser_mode(self._eraser_mode)
            self._sync_stack_phrase_template(stack)
            stack.set_tool_mode(self._tool)
            stack.set_brush(color, width, alpha)

    def _on_tool_changed(self, tool: str) -> None:
        prev = self._tool
        self._tool = tool
        text_like = (TOOL_TEXT, TOOL_PHRASE)
        if prev in text_like and tool not in text_like:
            self.finish_all_text_editing()
        if tool != TOOL_PHRASE:
            self._cancel_phrase_placement()
            self._end_phrase_edit()
        if tool not in text_like:
            for stack in self._stacks():
                stack.text_layer.clear_selection()
        self._apply_to_stacks()

    def _set_active_stack(self, stack: CropInkImageStack) -> None:
        self._active_stack = stack
        self._active_result_id = stack.result_id

    def _page_focus_result_id(self) -> int | None:
        if not self._page:
            return None
        fn = getattr(self._page, "palette_focus_result_id", None)
        if not callable(fn):
            return None
        rid = fn()
        return int(rid) if rid is not None else None

    def _on_text_selection(self, box: dict[str, Any] | None) -> None:
        if not box:
            if not self._pending_speech_text:
                self._stop_speech()
            return
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                self._set_active_stack(stack)
                break
        if self.tool_window.current_input_mode() != MODE_PHRASE:
            self.tool_window.show_text_mode()
            self.tool_window.format_panel.load_style(box.get("style") or {})

    def _on_stack_image_clicked(self, stack: CropInkImageStack) -> None:
        self._set_active_stack(stack)
        if self._tool not in (TOOL_TEXT, TOOL_PHRASE):
            return
        for s in self._stacks():
            s.text_layer.finish_all_editing()
            s.text_layer.clear_selection()

    def _resolve_active_stack(self) -> CropInkImageStack | None:
        if self._full_sheet_stack is not None and self._full_sheet_dialog is not None:
            if getattr(self._full_sheet_dialog, "is_draw_mode", lambda: False)():
                self._active_stack = self._full_sheet_stack
                return self._full_sheet_stack

        stacks = self._stacks()
        # 全容スタックを通常解決から除外（採点モード時）
        stacks = [s for s in stacks if s is not self._full_sheet_stack]
        if not stacks:
            return None

        rid = self._active_result_id
        if rid is None:
            rid = self._page_focus_result_id()

        if rid is not None:
            for stack in stacks:
                if stack.result_id == rid:
                    self._active_stack = stack
                    return stack

        for stack in stacks:
            if stack.text_layer.selected_box():
                self._set_active_stack(stack)
                return stack

        return None

    def _on_clear_active_ink(self) -> None:
        if self._full_sheet_dialog is not None and self._full_sheet_dialog.is_draw_mode():
            self._full_sheet_dialog.request_clear_ink()
            return
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
        if self._full_sheet_dialog is not None and self._full_sheet_dialog.is_draw_mode():
            self._full_sheet_dialog.request_clear_text_boxes()
            return
        self._stop_speech()
        stack = self._resolve_active_stack()
        if stack is None:
            from ui_qt import helpers as h

            h.warn(self._main, "消去", "対象の画像をクリックして選択してください")
            return
        if not stack.text_layer.field_local_annotations():
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

    def _on_view_mode_changed(self, mode: str) -> None:
        detailed = str(mode or "") == VIEW_DETAILED
        self.tool_window.format_panel.set_detailed_controls_visible(detailed)

    def _on_input_mode_changed(self, mode: str) -> None:
        if str(mode or "") != MODE_PHRASE:
            self._end_phrase_edit()

    def _on_char_format_changed(self, changes: dict[str, Any]) -> None:
        if self._editing_phrase_id:
            preview = self.tool_window.phrase_preview
            preview.apply_char_format(changes)
            self._persist_phrase_from_preview(reload_list=False)
            return
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.apply_char_format_to_selected(changes)
                return

    def _on_format_style(self, style: dict[str, Any]) -> None:
        if self._editing_phrase_id:
            self.tool_window.phrase_preview.apply_style_dict(style)
            self.tool_window.format_panel.load_style(style)
            self._persist_phrase_from_preview(reload_list=False)
            return
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.update_selected_style(style)

    def _on_format_edit_done(self) -> None:
        if self._editing_phrase_id:
            self._end_phrase_edit()
            return
        self._stop_speech()
        self.finish_all_text_editing()
        fw = QApplication.focusWidget()
        if fw is not None:
            fw.clearFocus()
        for stack in self._stacks():
            stack.text_layer.clear_selection()

    def _on_format_edit(self) -> None:
        if self._editing_phrase_id:
            self.tool_window.phrase_preview.start_text_editing()
            return
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.edit_selected()
                return

    def _on_format_delete(self) -> None:
        if self._editing_phrase_id:
            pid = self._editing_phrase_id
            self._end_phrase_edit()
            delete_phrase_template(pid)
            self.tool_window.phrase_panel.reload_templates()
            return
        self._stop_speech()
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.delete_selected()
                return

    def _on_delete_selected_text_hotkey(self) -> None:
        """選択中のテキストボックスを Del で削除（文字編集中はエディタに任せる）。"""
        if self._tool not in (TOOL_TEXT, TOOL_PHRASE):
            return
        if any(stack.text_layer.has_editing_focus() for stack in self._stacks()):
            return
        if not self._has_selected_text_box():
            return
        self._on_format_delete()

    def refresh_speech_prefs(self) -> None:
        mode = load_speech_input_mode()
        fp = self.tool_window.format_panel
        fp.set_speech_available(is_speech_input_available(mode))
        fp.set_speech_mode(mode)
        if self._is_speech_active():
            self._stop_speech()

    def _is_speech_active(self) -> bool:
        if self._windows_speech_active:
            return True
        return self._speech.is_listening()

    def finish_all_text_editing(self) -> None:
        if self._is_speech_active():
            self._stop_speech()
        for stack in self._stacks():
            stack.text_layer.finish_all_editing()

    def _stop_speech(self) -> None:
        if self._windows_speech_active:
            toggle_windows_voice_typing()
            self._windows_speech_active = False
        self._speech.stop()
        self._release_speech_input_guards()
        self._cancel_speech_placement()
        self.tool_window.format_panel.set_speech_active(False)
        self.tool_window.format_panel.set_speech_phase("idle")

    def _has_selected_text_box(self) -> bool:
        return any(stack.text_layer.selected_box() for stack in self._stacks())

    def _cancel_speech_placement(self) -> None:
        self._pending_speech_text = None
        for stack in self._stacks():
            stack.text_layer.clear_speech_place_text()

    def _phrase_template_by_id(self, phrase_id: str) -> dict[str, Any] | None:
        pid = str(phrase_id or "").strip()
        if not pid:
            return None
        for tpl in load_phrase_templates():
            if str(tpl.get("id")) == pid:
                return tpl
        return None

    def _cancel_phrase_placement(self) -> None:
        self._pending_phrase_id = None
        self._pending_phrase_template = None
        self.tool_window.phrase_panel.set_pending_phrase(None)
        for stack in self._stacks():
            stack.text_layer.clear_phrase_place_template()
            stack.sync_place_cursor()

    def _on_phrase_selected(self, phrase_id: str) -> None:
        template = self._phrase_template_by_id(phrase_id)
        if template is None:
            return
        if self._editing_phrase_id and self._editing_phrase_id != phrase_id:
            self._end_phrase_edit()
        self._ensure_phrase_tool_mode()
        self._stop_speech()
        self.finish_all_text_editing()
        self._pending_phrase_id = str(phrase_id)
        self._pending_phrase_template = copy.deepcopy(template)
        self.tool_window.phrase_panel.set_pending_phrase(phrase_id)
        self._apply_to_stacks()
        if hasattr(self._main, "show_app_message"):
            self._main.show_app_message(
                "定型文: ドラッグして貼り付ける位置を指定してください",
                level="info",
            )

    def _on_phrase_placed(self, phrase_id: str) -> None:
        touch_recent_phrase(phrase_id)
        self.tool_window.phrase_panel.reload_templates()
        self._apply_to_stacks()

    def _on_phrase_edit_requested(self, phrase_id: str) -> None:
        template = self._phrase_template_by_id(phrase_id)
        if template is None:
            return
        self._cancel_phrase_placement()
        self._stop_speech()
        self.finish_all_text_editing()
        self._editing_phrase_id = str(phrase_id)
        self._ensure_phrase_tool_mode()
        self.tool_window.phrase_panel.set_editing_phrase(phrase_id)
        self.tool_window.phrase_preview.load_template(template)
        self.tool_window.set_phrase_format_editor_visible(True)
        fp = self.tool_window.format_panel
        fp.set_detailed_controls_visible(True)
        self.tool_window.phrase_preview.set_focus_guard_widgets(
            self.tool_window.phrase_format_focus_widgets()
        )
        fp.load_style(template.get("style") or {})
        self._sync_phrase_format_char_state(template)

    def _on_phrase_preview_char_format_state(self, state: dict[str, Any]) -> None:
        if not self._editing_phrase_id:
            return
        self.tool_window.format_panel.sync_char_format(state)

    def _on_phrase_batch_update_requested(self, group_id: str) -> None:
        self.open_phrase_batch_update_dialog(group_id)

    def _on_full_sheet_grade_requested(self) -> None:
        self.open_full_sheet_grade_dialog()

    def open_full_sheet_grade_dialog(self) -> None:
        """選択中答案の補正全画像で全記述欄を採点する。"""
        from ui_qt import helpers as h
        from models.database import connect
        from models.test_repo import (
            get_all_results,
            get_answer_fields,
            get_points_conn,
        )
        from services.crop_preview import resolve_warped_path
        from ui_qt.full_sheet_grade_dialog import FullSheetGradeDialog

        stack = self._resolve_active_stack()
        if stack is None:
            h.warn(
                self._main,
                "一枚全容採点",
                "対象の画像をクリックして選択してください。",
            )
            return

        test_id = ""
        if self._page is not None:
            test_id_fn = getattr(self._page, "palette_test_id", None)
            test_id = str(test_id_fn() or "") if callable(test_id_fn) else ""
        if not test_id:
            app = getattr(self._main, "app", None)
            test_id = str(getattr(app, "active_test_id", None) or "")
        if not test_id:
            h.warn(self._main, "一枚全容採点", "テストが選択されていません。")
            return

        result_id = int(stack.result_id)
        row = next(
            (r for r in get_all_results(test_id) if int(r.get("id") or 0) == result_id),
            None,
        )
        if row is None:
            h.warn(self._main, "一枚全容採点", "答案データが見つかりません。")
            return

        try:
            warped = resolve_warped_path(row)
        except FileNotFoundError as e:
            h.warn(self._main, "一枚全容採点", str(e))
            return

        fields = get_answer_fields(test_id)
        if not fields:
            h.warn(self._main, "一枚全容採点", "記述欄が設定されていません。")
            return

        with connect() as conn:
            points = get_points_conn(conn, test_id)

        dlg = FullSheetGradeDialog(
            self._main,
            test_id=test_id,
            result_row=row,
            warped_path=warped,
            fields=fields,
            points=points or {},
            initial_field_id=str(getattr(stack, "field_id", "") or ""),
            palette_controller=self,
        )
        # パレットはダイアログ側で描画時のみ前面表示する（採点時は格納）
        self.set_settings_overlay_active(True)
        try:
            dlg.raise_()
            dlg.activateWindow()
            dlg.exec()
        finally:
            self.unbind_full_sheet_dialog(dlg)
            self.set_settings_overlay_active(False)
            self._refresh_crop_grid_annotations()
            if self._page is not None:
                refresh_fn = getattr(self._page, "refresh", None)
                if callable(refresh_fn):
                    refresh_fn()

    def open_phrase_batch_update_dialog(
        self,
        group_id: str,
        *,
        test_id: str | None = None,
        template: dict[str, Any] | None = None,
    ) -> None:
        """定型文詳細のグループ ID クリックと同じ一括更新ダイアログを開く。"""
        from ui_qt import helpers as h

        gid = str(group_id or "").strip()
        if not gid:
            return
        resolved_test_id = str(test_id or "").strip()
        if not resolved_test_id and self._page is not None:
            test_id_fn = getattr(self._page, "palette_test_id", None)
            resolved_test_id = str(test_id_fn() or "") if callable(test_id_fn) else ""
        if not resolved_test_id:
            app = getattr(self._main, "app", None)
            resolved_test_id = str(getattr(app, "active_test_id", None) or "")
        if not resolved_test_id:
            h.warn(self._main, "一括更新", "テストが選択されていません。")
            return
        tpl = dict(template) if isinstance(template, dict) else None
        if tpl is None:
            tpl = phrase_template_by_group_id(gid)
        if tpl is None:
            h.warn(self._main, "一括更新", f"定型文 ID {gid} が見つかりません。")
            return
        dlg = PhraseBatchUpdateDialog(
            self._main,
            test_id=resolved_test_id,
            template=tpl,
        )
        dlg.applied.connect(self._on_phrase_batch_applied)
        self.set_settings_overlay_active(True)
        try:
            dlg.raise_()
            dlg.activateWindow()
            dlg.exec()
        finally:
            self.set_settings_overlay_active(False)

    def _on_phrase_batch_applied(self, _count: int) -> None:
        self._reload_all_stack_annotations()
        self._refresh_crop_grid_annotations()

    def _reload_all_stack_annotations(self) -> None:
        if not self._page:
            return
        test_id_fn = getattr(self._page, "palette_test_id", None)
        test_id = test_id_fn() if callable(test_id_fn) else None
        if not test_id:
            return
        for stack in self._stacks():
            items = get_text_annotations(test_id, stack.result_id, stack.field_id)
            stack.text_layer.set_annotations(items)

    def _refresh_crop_grid_annotations(self) -> None:
        if not self._page:
            return
        fn = getattr(self._page, "palette_refresh_annotation_cache", None)
        if callable(fn):
            fn()

    def _on_phrase_deleted(self, phrase_id: str) -> None:
        if self._editing_phrase_id == str(phrase_id):
            self._end_phrase_edit()
        if self._pending_phrase_id == str(phrase_id):
            self._cancel_phrase_placement()

    def _end_phrase_edit(self) -> None:
        if not self._editing_phrase_id:
            return
        self.tool_window.phrase_preview.finish_text_editing()
        self.tool_window.phrase_preview.set_focus_guard_widgets(())
        self._persist_phrase_from_preview(reload_list=True)
        self._editing_phrase_id = None
        self.tool_window.set_phrase_format_editor_visible(False)
        self.tool_window.phrase_panel.set_editing_phrase(None)
        detailed = str(self.tool_window._view_mode or "") == VIEW_DETAILED
        self.tool_window.format_panel.set_detailed_controls_visible(detailed)

    def _sync_phrase_format_char_state(self, tpl: dict[str, Any]) -> None:
        st = resolve_text_style(tpl.get("style") or {})
        self.tool_window.format_panel.sync_char_format(
            {
                "color": str(st.get("textColor") or DEFAULT_TEXT_COLOR),
                "fontSize": int(st.get("fontSize") or DEFAULT_TEXT_STYLE.get("fontSize") or 14),
                "lineSpacing": int(
                    st.get("lineSpacing")
                    or DEFAULT_TEXT_STYLE.get("lineSpacing")
                    or 20
                ),
                "bold": str(st.get("fontWeight") or "") == "bold",
                "italic": str(st.get("fontStyle") or "") == "italic",
                "underline": False,
            }
        )

    def _persist_phrase_from_preview(self, *, reload_list: bool) -> None:
        pid = self._editing_phrase_id
        if not pid:
            return
        updates = self.tool_window.phrase_preview.export_updates()
        if not updates:
            return
        updated = update_phrase_template(pid, **updates)
        self._after_phrase_template_updated(updated, reload_list=reload_list)

    def _after_phrase_template_updated(
        self, updated: dict[str, Any] | None, *, reload_list: bool = True
    ) -> None:
        if updated is None:
            return
        pid = str(updated.get("id") or "")
        if self._pending_phrase_id == pid:
            self._pending_phrase_template = copy.deepcopy(updated)
            self._apply_to_stacks()
        if self._editing_phrase_id == pid:
            if reload_list:
                self.tool_window.phrase_panel.reload_templates()
                self.tool_window.phrase_panel.set_editing_phrase(pid)
            self._sync_phrase_format_char_state(updated)
        elif reload_list:
            self.tool_window.phrase_panel.reload_templates()

    def _on_copy_phrase_from_textbox(self) -> None:
        box: dict[str, Any] | None = None
        for stack in self._stacks():
            selected = stack.text_layer.selected_box()
            if selected:
                box = selected
                self._set_active_stack(stack)
                break
        if box is None:
            from ui_qt import helpers as h

            h.warn(
                self._main,
                "定型文",
                "コピー元のテキストボックスを選択してください",
            )
            return
        tpl = add_phrase_template(phrase_from_text_box(box))
        self.tool_window.phrase_panel.reload_templates()
        if hasattr(self._main, "show_app_message"):
            self._main.show_app_message(
                f"定型文「{tpl.get('label', '')}」を登録しました",
                level="info",
            )

    def _begin_speech_placement(self, text: str) -> None:
        chunk = str(text or "").strip()
        if not chunk:
            return
        self._speech.stop()
        self._release_speech_input_guards()
        self._cancel_speech_placement()
        self._pending_speech_text = chunk
        self._ensure_text_tool_for_speech_placement()
        for stack in self._stacks():
            stack.text_layer.set_speech_place_text(
                chunk,
                on_placed=self._on_speech_box_placed,
            )
        self.tool_window.format_panel.set_speech_active(False)
        self.tool_window.format_panel.set_speech_phase("placing")
        if hasattr(self._main, "show_app_message"):
            self._main.show_app_message(
                "音声入力: テキストボックスを配置する場所をクリックしてください",
                level="info",
            )

    def _on_speech_box_placed(self) -> None:
        self._cancel_speech_placement()
        self.tool_window.format_panel.set_speech_phase("idle")
        for stack in self._stacks():
            box = stack.text_layer.selected_box()
            if box:
                self._set_active_stack(stack)
                self.tool_window.show_text_mode()
                self.tool_window.format_panel.load_style(box.get("style") or {})
                break

    def _ensure_text_tool_for_speech_placement(self) -> None:
        self.tool_window.show_text_mode()
        if self._tool != TOOL_TEXT:
            self.tool_window.set_tool(TOOL_TEXT)
            self._on_tool_changed(TOOL_TEXT)

    def _on_format_speech_toggled(self, on: bool) -> None:
        if not on:
            if self._windows_speech_active:
                self._stop_speech()
            else:
                self._finalize_app_speech()
            return
        if (
            load_speech_input_mode() == SPEECH_MODE_WINDOWS
            and self._has_selected_text_box()
        ):
            self._on_windows_speech_toggled(on)
            return
        self._start_app_speech()

    def _start_app_speech(self) -> None:
        """アプリ内認識を開始（未選択時は確認後に配置場所をクリック）。"""
        self._cancel_speech_placement()
        if self._has_selected_text_box():
            if not self._ensure_speech_target_editing():
                self.tool_window.format_panel.set_speech_active(False)
                return
        else:
            self.tool_window.show_text_mode()
        self.tool_window.format_panel.set_speech_phase("preparing")
        self._speech_manual_finalize = False
        self._speech.start()

    def _finalize_app_speech(self) -> None:
        """認識中ボタン再押下: その時点までを認識して終了する。"""
        phase = self._speech.phase()
        if phase in ("preparing", "recognizing") and (
            self._speech.is_listening() or phase == "preparing"
        ):
            self._speech_manual_finalize = True
            self._speech.finalize_and_stop()
            self.tool_window.format_panel.set_speech_active(False)
            self.tool_window.format_panel.set_speech_phase("idle")
            QTimer.singleShot(400, self._end_manual_finalize_if_no_confirm)
            return
        self._speech_manual_finalize = False
        self._stop_speech()

    def _end_manual_finalize_if_no_confirm(self) -> None:
        if self._speech_manual_finalize and not self._speech_confirm_open:
            self._speech_manual_finalize = False

    def _on_windows_speech_toggled(self, on: bool) -> None:
        if not on:
            self._stop_speech()
            return
        if not self._ensure_speech_target_editing():
            self.tool_window.format_panel.set_speech_active(False)
            return
        self.tool_window.format_panel.release_speech_button_focus()
        self._windows_voice_prep_attempt = 0
        QTimer.singleShot(50, self._prepare_windows_voice_typing)

    def _prepare_windows_voice_typing(self) -> None:
        """テキスト末尾にカーソルを置いてから Windows 音声入力を起動する。"""
        fp = self.tool_window.format_panel
        if not fp.is_speech_checked():
            return

        self.tool_window.format_panel.release_speech_button_focus()
        self._main.raise_()
        self._main.activateWindow()

        ready = self._prepare_speech_target_editor()
        self._windows_voice_prep_attempt += 1
        if ready:
            QTimer.singleShot(120, self._launch_windows_voice_typing)
        elif self._windows_voice_prep_attempt < 12:
            QTimer.singleShot(50, self._prepare_windows_voice_typing)
        else:
            QTimer.singleShot(80, self._launch_windows_voice_typing)

    def _prepare_speech_target_editor(self) -> bool:
        for stack in self._stacks():
            layer = stack.text_layer
            if not layer.selected_box():
                continue
            if layer.prepare_selected_speech_input():
                return True
            if layer.is_selected_editor_focused_at_end():
                return True
        return False

    def _release_speech_input_guards(self) -> None:
        for stack in self._stacks():
            stack.text_layer.release_selected_speech_input_guard()

    def _launch_windows_voice_typing(self) -> None:
        from ui_qt import helpers as h

        fp = self.tool_window.format_panel
        if not fp.is_speech_checked():
            return

        self.tool_window.format_panel.release_speech_button_focus()
        self._prepare_speech_target_editor()
        if toggle_windows_voice_typing():
            self._windows_speech_active = True
            fp.set_speech_active(True)
            fp.set_speech_phase("windows")
            return
        fp.set_speech_active(False)
        h.warn(
            self._main,
            "音声入力",
            "Windows 音声入力（Win+H）を起動できませんでした。\n"
            "Windows の音声設定とマイク許可を確認してください。",
        )

    def _ensure_speech_target_editing(self, *, caret_at_end: bool = False) -> bool:
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.edit_selected(caret_at_end=caret_at_end)
                return True
        return False

    def _focus_speech_target_at_end(self) -> bool:
        for stack in self._stacks():
            if stack.text_layer.focus_selected_caret_at_end():
                return True
        return False

    def _on_speech_transcript(self, text: str) -> None:
        chunk = str(text or "").strip()
        if not chunk or self._speech_confirm_open:
            if self._speech_manual_finalize:
                self._speech_manual_finalize = False
                self._stop_speech()
            return
        manual_finalize = self._speech_manual_finalize
        self._speech_confirm_open = True
        if not manual_finalize:
            self._speech.pause()
        self.tool_window.format_panel.set_speech_phase("idle")
        QApplication.processEvents()
        try:
            self.tool_window.format_panel.set_speech_phase("paused")
            dlg = SpeechConfirmDialog(self._main, chunk)
            result = dlg.exec()
            if result == SpeechConfirmResult.ACCEPT:
                placed = False
                for stack in self._stacks():
                    if stack.text_layer.append_transcript_to_selected(chunk):
                        placed = True
                        break
                if placed:
                    if not manual_finalize:
                        self._resume_app_speech_after_confirm()
                else:
                    self._begin_speech_placement(chunk)
            elif result == SpeechConfirmResult.RETRY:
                if not manual_finalize:
                    self._resume_app_speech_after_confirm()
            else:
                self._stop_speech()
        finally:
            self._speech_confirm_open = False
            if manual_finalize:
                self._speech_manual_finalize = False
                if not self._pending_speech_text:
                    self._stop_speech()

    def _resume_app_speech_after_confirm(self) -> None:
        """確認ダイアログ後にアプリ内認識を再開し、ボタンを認識中表示に戻す。"""
        self._speech_confirm_open = False
        self._speech.resume()
        self.tool_window.format_panel.set_speech_active(True)
        self.tool_window.format_panel.set_speech_phase("recognizing")

    def _on_speech_error(self, message: str) -> None:
        from ui_qt import helpers as h

        msg = str(message or "").strip()
        if not msg:
            return
        if msg.startswith("音声が検出されません"):
            h.warn(self._main, "音声入力", msg)
            return
        self._stop_speech()
        h.warn(self._main, "音声入力", msg)

    def _on_speech_listening_changed(self, on: bool) -> None:
        self.tool_window.format_panel.set_speech_active(on)

    def _on_speech_phase_changed(self, phase: str) -> None:
        if self._speech_confirm_open and phase in ("preparing", "recognizing"):
            return
        if self._pending_speech_text and phase in ("preparing", "recognizing"):
            return
        self.tool_window.format_panel.set_speech_phase(phase)
        status = {
            "preparing": "音声入力: マイクを準備しています…",
            "recognizing": "音声入力: 認識中 — 話してください",
            "paused": "音声入力: 認識結果を確認中",
            "placing": "音声入力: 配置する場所をクリックしてください",
        }.get(phase)
        if status and hasattr(self._main, "show_app_message"):
            self._main.show_app_message(status, level="info")

    def register_stack(self, stack: CropInkImageStack) -> None:
        """新規タイル生成後に呼ぶ。"""
        self._bind_stack(stack)
