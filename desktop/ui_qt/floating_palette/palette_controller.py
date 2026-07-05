"""フローティングパレット統合コントローラ。"""

from __future__ import annotations

from typing import Any, Protocol

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QPushButton, QScrollArea, QWidget

from ui_qt.floating_palette.format_palette_placer import clamp_window_to_viewer
from ui_qt.floating_palette.format_palette_window import FormatPaletteWindow
from ui_qt.floating_palette.palette_prefs import (
    TOOL_ERASER,
    TOOL_PEN,
    TOOL_TEXT,
    load_palette_prefs,
    save_palette_prefs,
)
from ui_qt.floating_palette.tool_palette_window import ToolPaletteWindow
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
        self._tool = TOOL_PEN
        self._show_ink = True
        self._eraser_mode = load_stylus_prefs()["eraser_mode"]
        prefs = load_palette_prefs()

        self.tool_window = ToolPaletteWindow(main_window)
        self.format_window = FormatPaletteWindow(main_window)
        self.fab = PaletteFabButton(main_window)
        self.tool_window.set_eraser_mode(self._eraser_mode)

        self.tool_window.tool_changed.connect(self._on_tool_changed)
        self.tool_window.brush_changed.connect(self._on_brush_changed)
        self.tool_window.eraser_mode_changed.connect(self._on_eraser_mode_changed)
        self.tool_window.show_ink_changed.connect(self._on_show_ink_changed)
        self.tool_window.minimize_requested.connect(self._minimize)
        self.format_window.style_changed.connect(self._on_format_style)
        self.format_window.edit_requested.connect(self._on_format_edit)
        self.format_window.delete_requested.connect(self._on_format_delete)
        self.fab.clicked.connect(self._restore_from_fab)

        self.tool_window.set_view_mode(str(prefs.get("view_mode") or "simple"))
        self.tool_window.set_brush(
            str(prefs.get("last_color") or "#111827"),
            float(prefs.get("last_width") or 2.5),
            float(prefs.get("last_alpha") or 1.0),
        )
        self.tool_window.set_tool(str(prefs.get("last_tool") or TOOL_PEN))
        if prefs.get("minimized"):
            self._minimize()
        else:
            x, y = int(prefs.get("x") or 100), int(prefs.get("y") or 100)
            self.tool_window.move(x, y)

    def attach_page(self, page: AnnotationPage | None, step_id: int) -> None:
        self._page = page
        self._step_id = step_id
        self._connect_stacks()
        self._apply_to_stacks()
        if prefs := load_palette_prefs():
            if not prefs.get("minimized"):
                self._clamp_tool_window()

    def detach(self) -> None:
        self.format_window.hide_palette()
        self._page = None
        self._step_id = None
        self.tool_window.hide()
        self.fab.hide()

    def show_for_step(self, step_id: int) -> None:
        if step_id not in self.ACTIVE_STEPS:
            self.detach()
            return
        prefs = load_palette_prefs()
        if prefs.get("minimized"):
            self.tool_window.hide()
            self._position_fab()
            self.fab.show()
        else:
            self.fab.hide()
            self.tool_window.show()
            self.tool_window.raise_()
            self._clamp_tool_window()

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
                "view_mode": self.tool_window._view_mode,
            }
        )

    def apply_config(self) -> None:
        self._apply_to_stacks()

    def _viewer_global_rect(self) -> QRect | None:
        if not self._page:
            return None
        vp = self._page.viewer_scroll().viewport()
        tl = vp.mapToGlobal(QPoint(0, 0))
        return QRect(tl, vp.size())

    def _clamp_tool_window(self) -> None:
        vr = self._viewer_global_rect()
        if vr is None:
            return
        geo = self.tool_window.frameGeometry()
        pos = clamp_window_to_viewer(geo, vr)
        self.tool_window.move(pos)

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

    def _minimize(self) -> None:
        self.tool_window.hide()
        self._position_fab()
        self.fab.show()

    def _restore_from_fab(self) -> None:
        self.fab.hide()
        self.tool_window.show()
        self.tool_window.raise_()
        self._clamp_tool_window()

    def _connect_stacks(self) -> None:
        if not self._page:
            return
        for stack in self._page.palette_ink_stacks():
            try:
                stack.text_layer.selection_changed.disconnect(self._on_text_selection)
            except (RuntimeError, TypeError):
                pass
            stack.text_layer.selection_changed.connect(self._on_text_selection)

    def _stacks(self) -> list[CropInkImageStack]:
        if not self._page:
            return []
        return self._page.palette_ink_stacks()

    def _apply_to_stacks(self) -> None:
        stylus = load_stylus_prefs()
        color, width, alpha = self.tool_window.current_brush()
        for stack in self._stacks():
            stack.set_palm_rejection(stylus["palm_rejection"])
            stack.set_show_ink(self._show_ink)
            stack.set_eraser_mode(self._eraser_mode)
            stack.set_tool_mode(self._tool)
            stack.set_brush(color, width, alpha)
            stack.text_layer.set_placement_mode(self._tool == TOOL_TEXT)

    def _on_tool_changed(self, tool: str) -> None:
        self._tool = tool
        self._apply_to_stacks()
        if tool != TOOL_TEXT:
            self.format_window.hide_palette()

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

    def _on_text_selection(self, box: dict[str, Any] | None) -> None:
        if not box:
            self.format_window.hide_palette()
            return
        st = box.get("style") or {}
        self.format_window.load_style(st)
        self.format_window.show()
        self.format_window.raise_()
        for stack in self._stacks():
            sel = stack.text_layer.selected_box()
            if sel and str(sel.get("id")) == str(box.get("id")):
                w = stack.text_layer
                for child in w.children():
                    if hasattr(child, "box_id") and child.box_id == str(box.get("id")):
                        tl = child.mapToGlobal(QPoint(0, 0))
                        rect = QRect(tl, child.size())
                        self.format_window.reposition_near(
                            rect,
                            viewer_global=self._viewer_global_rect(),
                        )
                        break
                break

    def _on_format_style(self, style: dict[str, Any]) -> None:
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.update_selected_style(style)

    def _on_format_edit(self) -> None:
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.edit_selected()
                return

    def _on_format_delete(self) -> None:
        for stack in self._stacks():
            if stack.text_layer.selected_box():
                stack.text_layer.delete_selected()
                self.format_window.hide_palette()
                return

    def register_stack(self, stack: CropInkImageStack) -> None:
        """新規タイル生成後に呼ぶ。"""
        stack.text_layer.selection_changed.connect(self._on_text_selection)
        stylus = load_stylus_prefs()
        color, width, alpha = self.tool_window.current_brush()
        stack.set_palm_rejection(stylus["palm_rejection"])
        stack.set_show_ink(self._show_ink)
        stack.set_eraser_mode(self._eraser_mode)
        stack.set_tool_mode(self._tool)
        stack.set_brush(color, width, alpha)
        stack.text_layer.set_placement_mode(self._tool == TOOL_TEXT)
