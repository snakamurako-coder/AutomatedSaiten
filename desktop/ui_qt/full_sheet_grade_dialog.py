"""一枚全容採点ダイアログ — 補正全画像上で全記述欄を採点・シート注釈編集。"""

from __future__ import annotations

import copy
from typing import Any

from PIL import Image
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from models.domain_repo import calculate_domain_scores
from models.grading_status import PENDING_JUDGMENT, normalize_judgment
from models.ink_repo import (
    SHEET_FIELD_ID,
    field_local_ink_to_warped,
    get_ink_strokes,
    save_ink_strokes,
)
from models.output_repo import get_feedback_style
from models.text_annotation_repo import (
    field_local_text_to_warped,
    get_text_annotations,
    save_text_annotations,
)
from models.test_repo import update_results_field_grades
from services.compositor import (
    REGION_FILL_ALPHA,
    REGION_FILL_ALPHA_SELECTED,
    REGION_STROKE_NORMAL,
    REGION_STROKE_SELECTED,
    bgr_to_rgba_image,
    render_ink_layer,
    render_text_annotation_layer,
)
from services.feedback_exporter import gather_row_render_data
from services.feedback_renderer import render_feedback_overlay_layer
from services.image_loader import imread_bgr
from ui_qt.crop_widgets import ZoomControls
from ui_qt.style import COLORS
from ui_qt.stylus_overlay import (
    TOOL_NONE,
    CropInkImageStack,
)

MODE_GRADE = "grade"
MODE_DRAW = "draw"

CLEAR_SHEET_ONLY = "sheet"
CLEAR_INCLUDING_FIELDS = "fields"


def _mix_hex_with_white(hex_color: str, white_ratio: float = 0.82) -> str:
    raw = str(hex_color or "").lstrip("#")
    if len(raw) != 6:
        return COLORS["surface"]
    r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    w = max(0.0, min(1.0, white_ratio))
    r = int(r + (255 - r) * w)
    g = int(g + (255 - g) * w)
    b = int(b + (255 - b) * w)
    return f"#{r:02x}{g:02x}{b:02x}"


def _fill_color(stroke: str, alpha: float) -> QColor:
    c = QColor(stroke)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


class _FieldHitOverlay(QWidget):
    """採点モード用の透明ヒット＋欄枠。"""

    fieldClicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fields: list[dict[str, Any]] = []
        self._selected_id = ""
        self._show_outlines = True
        self._zoom = 1.0
        self._hit_enabled = True
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_hit_enabled(self, enabled: bool) -> None:
        self._hit_enabled = bool(enabled)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not self._hit_enabled)
        self.setCursor(
            Qt.PointingHandCursor if self._hit_enabled else Qt.ArrowCursor
        )

    def set_content(
        self,
        fields: list[dict[str, Any]],
        *,
        show_outlines: bool,
        selected_id: str = "",
        zoom: float = 1.0,
        size: tuple[int, int] | None = None,
    ) -> None:
        self._fields = list(fields)
        self._show_outlines = bool(show_outlines)
        self._selected_id = str(selected_id or "")
        self._zoom = max(0.1, float(zoom))
        if size:
            self.setFixedSize(max(1, size[0]), max(1, size[1]))
        self.update()

    def set_selected_id(self, field_id: str) -> None:
        self._selected_id = str(field_id or "")
        self.update()

    def _field_rect_disp(self, field: dict[str, Any]) -> QRect:
        x = int(float(field.get("x") or 0) * self._zoom)
        y = int(float(field.get("y") or 0) * self._zoom)
        w = max(1, int(float(field.get("width") or 0) * self._zoom))
        h = max(1, int(float(field.get("height") or 0) * self._zoom))
        return QRect(x, y, w, h)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._show_outlines and not self._selected_id:
            return
        painter = QPainter(self)
        for f in self._fields:
            fid = str(f.get("id") or "")
            rect = self._field_rect_disp(f)
            selected = fid == self._selected_id
            if not self._show_outlines and not selected:
                continue
            stroke = REGION_STROKE_SELECTED if selected else REGION_STROKE_NORMAL
            alpha = (
                REGION_FILL_ALPHA_SELECTED if selected else REGION_FILL_ALPHA
            )
            if not self._show_outlines and selected:
                alpha = 0.08
            painter.fillRect(rect, _fill_color(stroke, alpha))
            pen = QPen(QColor(stroke))
            pen.setWidth(3 if selected else 2)
            painter.setPen(pen)
            painter.drawRect(rect)
            if self._show_outlines or selected:
                label = str(f.get("displayName") or fid)
                j = str(f.get("_judgment") or "").strip()
                if j and self._show_outlines:
                    label = f"{label} [{j}]"
                painter.setPen(QColor(stroke))
                font = painter.font()
                font.setBold(True)
                font.setPointSize(9)
                painter.setFont(font)
                painter.drawText(
                    rect.adjusted(4, 2, -2, -2),
                    Qt.AlignTop | Qt.AlignLeft,
                    label,
                )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._hit_enabled or event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        hit = ""
        for f in self._fields:
            if self._field_rect_disp(f).contains(pos):
                hit = str(f.get("id") or "")
        if hit:
            self.fieldClicked.emit(hit)


class FullSheetGradeDialog(QDialog):
    """選択答案の補正全画像で全記述欄を採点し、シート注釈を編集する。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        test_id: str,
        result_row: dict[str, Any],
        warped_path: str,
        fields: list[dict[str, Any]],
        points: dict[str, int],
        initial_field_id: str = "",
        palette_controller: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("一枚全容採点")
        self.resize(1100, 760)
        # WindowModal: メインはブロックしつつ、子にしたフローティングパレットは操作可能
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )

        self._test_id = test_id
        self._row = copy.deepcopy(result_row)
        self._warped_path = warped_path
        self._fields = list(fields)
        self._points = {str(k): int(v) for k, v in (points or {}).items()}
        self._judgments = dict(self._row.get("judgments") or {})
        self._scores = dict(self._row.get("scores") or {})
        self._show_outlines = True
        self._show_marks = True
        self._tool_mode = MODE_GRADE
        self._palette_controller = palette_controller
        self._palette_key: str | None = None
        self._selected_field_id = str(initial_field_id or "").strip()
        if self._selected_field_id and not any(
            str(f.get("id")) == self._selected_field_id for f in self._fields
        ):
            self._selected_field_id = ""
        if not self._selected_field_id and self._fields:
            self._selected_field_id = str(self._fields[0].get("id") or "")

        self._feedback_style = get_feedback_style()
        self._dirty_grades = False
        self._result_id = int(self._row.get("id") or 0)
        self._stack: CropInkImageStack | None = None
        self._workspace: QWidget | None = None
        self._hit: _FieldHitOverlay | None = None
        self._base_bgr = imread_bgr(self._warped_path)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        header = QHBoxLayout()
        name = str(self._row.get("fileName") or "")
        sid = str(self._row.get("studentId") or "") or "—"
        self._title = QLabel(f"{name}  （生徒ID: {sid}）")
        self._title.setStyleSheet(f"font-weight: 600; color: {COLORS['text']};")
        header.addWidget(self._title, 1)

        self._chk_outlines = QCheckBox("欄枠を表示")
        self._chk_outlines.setChecked(True)
        self._chk_outlines.toggled.connect(self._on_show_outlines_toggled)
        header.addWidget(self._chk_outlines)

        self._chk_marks = QCheckBox("判定・得点を表示")
        self._chk_marks.setChecked(True)
        self._chk_marks.toggled.connect(self._on_show_marks_toggled)
        header.addWidget(self._chk_marks)
        root.addLayout(header)

        tools = QHBoxLayout()
        tools.addWidget(QLabel("ツール:"))
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._btn_grade = QPushButton("採点（判定・配点）")
        self._btn_grade.setCheckable(True)
        self._btn_grade.setChecked(True)
        self._btn_grade.clicked.connect(lambda: self._set_tool_mode(MODE_GRADE))
        self._mode_group.addButton(self._btn_grade)
        tools.addWidget(self._btn_grade)
        self._btn_draw = QPushButton("描画ツール")
        self._btn_draw.setCheckable(True)
        self._btn_draw.setToolTip(
            "おなじみのフローティング描画パレットを表示し、全画像上で手書き・TBを編集します"
        )
        self._btn_draw.clicked.connect(lambda: self._set_tool_mode(MODE_DRAW))
        self._mode_group.addButton(self._btn_draw)
        tools.addWidget(self._btn_draw)
        tools.addStretch()
        root.addLayout(tools)

        self._zoom = ZoomControls(min_pct=10, max_pct=200, value=40)
        self._zoom.connect_zoom_changed(self._on_zoom_changed)
        root.addWidget(self._zoom)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignCenter)
        root.addWidget(self._scroll, 1)

        info = QHBoxLayout()
        self._field_label = QLabel("")
        self._field_label.setStyleSheet(f"font-weight: 600; color: {COLORS['text']};")
        info.addWidget(self._field_label)
        self._ocr_label = QLabel("")
        self._ocr_label.setWordWrap(True)
        self._ocr_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        info.addWidget(self._ocr_label, 1)
        root.addLayout(info)

        self._hint = QLabel(
            "採点: 回答欄を選んで判定を押す　／　描画ツール: フローティングパレットで答案全体レイヤーを編集（選択TBは Del で削除）"
        )
        self._hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        root.addWidget(self._hint)

        self._palette_frame = QFrame()
        self._palette_row = QHBoxLayout(self._palette_frame)
        self._palette_row.setContentsMargins(0, 0, 0, 0)
        self._palette_btns: dict[str, QPushButton] = {}
        root.addWidget(self._palette_frame)

        btns = QHBoxLayout()
        btns.addStretch()
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        root.addLayout(btns)

        for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(self._on_delete_selected_text_hotkey)

        self._rebuild_workspace()
        self._rebuild_palette_buttons()
        self._highlight_current_grade()
        self._refresh_info()
        self._apply_tool_mode()
        if self._palette_controller is not None:
            self._palette_controller.bind_full_sheet_dialog(self)

    def _load_sheet_strokes(self) -> list[dict[str, Any]]:
        return get_ink_strokes(self._test_id, self._result_id, SHEET_FIELD_ID)

    def _load_sheet_annotations(self) -> list[dict[str, Any]]:
        boxes = get_text_annotations(self._test_id, self._result_id, SHEET_FIELD_ID)
        out = []
        for b in boxes:
            item = dict(b)
            item.pop("source", None)
            out.append(item)
        return out

    def _field_underlay_ink(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in self._fields:
            fid = str(f.get("id") or "")
            if not fid:
                continue
            local = get_ink_strokes(self._test_id, self._result_id, fid)
            out.extend(field_local_ink_to_warped(local, f))
        return out

    def _field_underlay_text(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in self._fields:
            fid = str(f.get("id") or "")
            if not fid:
                continue
            local = get_text_annotations(self._test_id, self._result_id, fid)
            out.extend(field_local_text_to_warped(local, f))
        return out

    def _compose_display_pil(self) -> Image.Image:
        if self._base_bgr is None:
            return Image.new("RGB", (400, 300), (240, 240, 240))
        rgba = bgr_to_rgba_image(self._base_bgr)
        if self._show_marks:
            try:
                self._row["judgments"] = dict(self._judgments)
                self._row["scores"] = dict(self._scores)
                data = gather_row_render_data(self._test_id, self._row)
                payload = data["payload"]
                mark_layer = render_feedback_overlay_layer(
                    rgba.size,
                    payload["fields"],
                    payload["outputSlots"],
                    payload["fieldMarks"],
                    payload["totals"],
                    data["style"],
                )
                rgba = Image.alpha_composite(rgba, mark_layer)
            except Exception:
                pass
        field_ink = self._field_underlay_ink()
        if field_ink:
            rgba = Image.alpha_composite(
                rgba, render_ink_layer(rgba.size, field_ink, scale=1.0)
            )
        field_tb = self._field_underlay_text()
        if field_tb:
            rgba = Image.alpha_composite(
                rgba, render_text_annotation_layer(rgba.size, field_tb, scale=1.0)
            )
        return rgba.convert("RGB")

    def _rebuild_workspace(self) -> None:
        zoom = self._zoom.zoom_value() / 100.0
        pil = self._compose_display_pil()
        sheet_strokes = self._load_sheet_strokes()
        sheet_ann = self._load_sheet_annotations()

        self._stack = CropInkImageStack(
            pil_image=pil,
            field_id=SHEET_FIELD_ID,
            result_id=self._result_id,
            strokes=sheet_strokes,
            annotations=sheet_ann,
            zoom=zoom,
            on_strokes_changed=self._on_sheet_strokes_changed,
            on_annotations_changed=self._on_sheet_annotations_changed,
        )

        self._workspace = QWidget()
        self._workspace.setFixedSize(self._stack.size())
        self._stack.setParent(self._workspace)
        self._stack.move(0, 0)

        self._hit = _FieldHitOverlay(self._workspace)
        self._hit.setGeometry(0, 0, self._workspace.width(), self._workspace.height())
        self._hit.fieldClicked.connect(self._on_field_clicked)
        self._hit.raise_()
        self._refresh_hit_overlay()

        self._scroll.setWidget(self._workspace)
        if self._palette_controller is not None and self._tool_mode == MODE_DRAW:
            self._palette_controller.set_full_sheet_stack(self._stack)

    def ink_stack(self) -> CropInkImageStack | None:
        return self._stack

    def is_draw_mode(self) -> bool:
        return self._tool_mode == MODE_DRAW

    def _refresh_display_image_only(self) -> None:
        """判定変更時など、下敷き画像だけ差し替え（シート編集状態は維持）。"""
        if self._stack is None:
            self._rebuild_workspace()
            return
        # スタック再生成（シートストロークは現在のオーバーレイから）
        sheet_strokes = self._stack.ink_overlay.strokes()
        sheet_ann = self._stack.text_layer.annotations()
        save_ink_strokes(
            self._test_id, self._result_id, SHEET_FIELD_ID, sheet_strokes
        )
        save_text_annotations(
            self._test_id, self._result_id, SHEET_FIELD_ID, sheet_ann
        )
        old_mode = self._tool_mode
        if self._palette_controller is not None:
            self._palette_controller.set_full_sheet_stack(None)
        self._rebuild_workspace()
        self._tool_mode = old_mode
        self._apply_tool_mode()

    def _refresh_hit_overlay(self) -> None:
        if self._hit is None or self._workspace is None:
            return
        zoom = self._zoom.zoom_value() / 100.0
        self._hit.set_content(
            self._fields_for_hit(),
            show_outlines=self._show_outlines,
            selected_id=self._selected_field_id,
            zoom=zoom,
            size=(self._workspace.width(), self._workspace.height()),
        )
        self._hit.setGeometry(0, 0, self._workspace.width(), self._workspace.height())
        self._hit.raise_()

    def _fields_for_hit(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in self._fields:
            item = dict(f)
            fid = str(f.get("id") or "")
            item["_judgment"] = normalize_judgment(self._judgments.get(fid)) or ""
            out.append(item)
        return out

    def _on_sheet_strokes_changed(self, strokes: list[dict[str, Any]]) -> None:
        try:
            save_ink_strokes(
                self._test_id, self._result_id, SHEET_FIELD_ID, strokes
            )
        except Exception as e:
            self._ocr_label.setText(f"手書き保存失敗: {e}")

    def _on_sheet_annotations_changed(self, items: list[dict[str, Any]]) -> None:
        try:
            save_text_annotations(
                self._test_id, self._result_id, SHEET_FIELD_ID, items
            )
        except Exception as e:
            self._ocr_label.setText(f"TB保存失敗: {e}")

    def _set_tool_mode(self, mode: str) -> None:
        self._tool_mode = mode if mode in (MODE_GRADE, MODE_DRAW) else MODE_GRADE
        self._btn_grade.blockSignals(True)
        self._btn_draw.blockSignals(True)
        self._btn_grade.setChecked(self._tool_mode == MODE_GRADE)
        self._btn_draw.setChecked(self._tool_mode == MODE_DRAW)
        self._btn_grade.blockSignals(False)
        self._btn_draw.blockSignals(False)
        self._apply_tool_mode()

    def _apply_tool_mode(self) -> None:
        if self._stack is None or self._hit is None:
            return
        grade = self._tool_mode == MODE_GRADE
        self._palette_frame.setVisible(grade)
        self._hit.set_hit_enabled(grade)
        self._hit.raise_()
        ctrl = self._palette_controller
        if grade:
            self._stack.set_tool_mode(TOOL_NONE)
            self._stack.ink_overlay.set_drawing_enabled(False)
            self._stack.text_layer.set_placement_mode(False)
            self._stack.text_layer.set_text_tool_mode(False)
            if ctrl is not None:
                ctrl.hide_palette_for_full_sheet()
                ctrl.set_full_sheet_stack(None)
        else:
            self._stack.ink_overlay.set_drawing_enabled(True)
            if ctrl is not None:
                ctrl.set_full_sheet_stack(self._stack)
                ctrl.show_palette_for_full_sheet()
            else:
                # コントローラ無し時のフォールバック（描画不可）
                self._stack.set_tool_mode(TOOL_NONE)
                self._stack.ink_overlay.set_drawing_enabled(False)
        for btn in self._palette_btns.values():
            btn.setEnabled(grade and bool(self._selected_field_id))

    def _on_delete_selected_text_hotkey(self) -> None:
        """選択中のテキストボックスを Del/Backspace で削除（編集中はエディタ側）。"""
        if self._tool_mode != MODE_DRAW or self._stack is None:
            return
        layer = self._stack.text_layer
        if layer.has_editing_focus():
            return
        if not layer.selected_box():
            return
        layer.delete_selected()

    def request_clear_ink(self) -> None:
        scope = self._confirm_clear_scope("ink")
        if scope is None:
            return
        self._clear_ink(scope)

    def request_clear_text_boxes(self) -> None:
        scope = self._confirm_clear_scope("text")
        if scope is None:
            return
        self._clear_text_boxes(scope)

    def _confirm_clear_scope(self, kind: str) -> str | None:
        """シートのみ / 記述欄も含む / キャンセル。戻り値 CLEAR_* or None。"""
        label = "ペン描写" if kind == "ink" else "テキストボックス"
        box = QMessageBox(self)
        box.setWindowTitle(f"{label}の全消去")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"どの範囲の{label}を消去しますか？")
        box.setInformativeText(
            "一枚全容採点で書き込んだ内容は答案全体レイヤーです。\n"
            "各記述欄に個別で書いた内容は、記述欄レイヤーです。"
        )
        sheet_btn = box.addButton(
            "一枚全容レイヤーのみ", QMessageBox.ButtonRole.AcceptRole
        )
        fields_btn = box.addButton(
            "記述欄レイヤーも含む", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_btn = box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is sheet_btn:
            return CLEAR_SHEET_ONLY
        if clicked is fields_btn:
            return CLEAR_INCLUDING_FIELDS
        return None

    def _clear_ink(self, scope: str) -> None:
        if self._stack is None:
            return
        try:
            save_ink_strokes(self._test_id, self._result_id, SHEET_FIELD_ID, [])
            self._stack.ink_overlay.clear_strokes()
            if scope == CLEAR_INCLUDING_FIELDS:
                for f in self._fields:
                    fid = str(f.get("id") or "")
                    if fid:
                        save_ink_strokes(self._test_id, self._result_id, fid, [])
                self._refresh_display_image_only()
        except Exception as e:
            self._ocr_label.setText(f"ペン全消去失敗: {e}")

    def _clear_text_boxes(self, scope: str) -> None:
        if self._stack is None:
            return
        try:
            save_text_annotations(self._test_id, self._result_id, SHEET_FIELD_ID, [])
            self._stack.text_layer.set_annotations([])
            if scope == CLEAR_INCLUDING_FIELDS:
                for f in self._fields:
                    fid = str(f.get("id") or "")
                    if fid:
                        save_text_annotations(self._test_id, self._result_id, fid, [])
                self._refresh_display_image_only()
        except Exception as e:
            self._ocr_label.setText(f"TB全消去失敗: {e}")

    def _field_max_score(self, field_id: str | None = None) -> int:
        fid = field_id or self._selected_field_id
        try:
            return max(0, int(self._points.get(str(fid), 0)))
        except (TypeError, ValueError):
            return 0

    def _palette_specs(self) -> list[tuple[str, str, str, int]]:
        max_score = self._field_max_score()
        specs: list[tuple[str, str, str, int]] = [("○", "○", "○", max_score)]
        if max_score > 1:
            for s in range(max_score - 1, 0, -1):
                specs.append((f"△:{s}", f"△({s})", "△", s))
        specs.append(("×", "×", "×", 0))
        specs.append(("?", "?", PENDING_JUDGMENT, 0))
        return specs

    def _key_for_grade(self, field_id: str) -> str | None:
        j = normalize_judgment(self._judgments.get(field_id))
        if not j:
            return None
        if j == "○":
            return "○"
        if j == "×":
            return "×"
        if j == PENDING_JUDGMENT:
            return "?"
        if j == "△":
            try:
                sc = int(self._scores.get(field_id) or 0)
            except (TypeError, ValueError):
                sc = 0
            key = f"△:{sc}"
            if key in {s[0] for s in self._palette_specs()}:
                return key
        return None

    def _palette_stroke(self, key: str) -> str:
        mark = (self._feedback_style or {}).get("mark") or {}
        if key == "○":
            return str((mark.get("maru") or {}).get("strokeColor") or "#dc2626")
        if key.startswith("△:"):
            return str((mark.get("sankaku") or {}).get("strokeColor") or "#ea580c")
        if key == "×":
            return str((mark.get("batsu") or {}).get("strokeColor") or "#2563eb")
        return "#a16207"

    def _btn_style(self, key: str, *, active: bool) -> str:
        stroke = self._palette_stroke(key)
        if active:
            soft = _mix_hex_with_white(stroke, 0.82)
            return (
                f"QPushButton {{ background: {soft}; color: {stroke}; font-weight: 800;"
                f" font-size: 15px; border: 2px solid {stroke}; border-radius: 6px; }}"
                f"QPushButton:hover {{ background: {_mix_hex_with_white(stroke, 0.7)}; }}"
            )
        return (
            f"QPushButton {{ background: {COLORS['surface']}; color: {COLORS['text_muted']};"
            f" font-weight: 700; font-size: 14px; border: 2px solid {COLORS['border']};"
            f" border-radius: 6px; }}"
            f"QPushButton:hover {{ border-color: {stroke}; color: {stroke}; }}"
        )

    def _rebuild_palette_buttons(self) -> None:
        while self._palette_row.count():
            item = self._palette_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._palette_btns.clear()
        for key, label, _j, _s in self._palette_specs():
            btn = QPushButton(label)
            btn.setFixedHeight(36)
            btn.setMinimumWidth(44)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setEnabled(
                self._tool_mode == MODE_GRADE and bool(self._selected_field_id)
            )
            btn.setStyleSheet(self._btn_style(key, active=False))
            btn.clicked.connect(lambda _c=False, k=key: self._on_palette_clicked(k))
            self._palette_btns[key] = btn
            self._palette_row.addWidget(btn)
        self._palette_row.addStretch()

    def _highlight_current_grade(self) -> None:
        self._palette_key = (
            self._key_for_grade(self._selected_field_id)
            if self._selected_field_id
            else None
        )
        for k, btn in self._palette_btns.items():
            btn.setStyleSheet(self._btn_style(k, active=k == self._palette_key))

    def _on_palette_clicked(self, key: str) -> None:
        if self._tool_mode != MODE_GRADE or not self._selected_field_id:
            return
        spec = next((s for s in self._palette_specs() if s[0] == key), None)
        if spec is None:
            return
        self._apply_judgment_to_field(
            self._selected_field_id, spec[2], spec[3]
        )
        self._palette_key = key
        for k, btn in self._palette_btns.items():
            btn.setStyleSheet(self._btn_style(k, active=k == key))

    def _on_show_outlines_toggled(self, checked: bool) -> None:
        self._show_outlines = bool(checked)
        self._refresh_hit_overlay()

    def _on_show_marks_toggled(self, checked: bool) -> None:
        self._show_marks = bool(checked)
        self._refresh_display_image_only()

    def _on_zoom_changed(self) -> None:
        self._refresh_display_image_only()

    def _refresh_info(self) -> None:
        fid = self._selected_field_id
        field = next((f for f in self._fields if str(f.get("id")) == fid), None)
        name = str((field or {}).get("displayName") or fid or "—")
        max_sc = self._field_max_score(fid)
        j = normalize_judgment(self._judgments.get(fid)) or "未採点"
        sc = self._scores.get(fid)
        sc_txt = f"{sc}" if sc is not None and sc != "" else "—"
        self._field_label.setText(
            f"選択: {name}  （配点 {max_sc}）  判定 {j} / {sc_txt}点"
        )
        texts = self._row.get("textMapping") or {}
        ocr = str(texts.get(fid) or "").strip() or "（OCRテキストなし）"
        if len(ocr) > 120:
            ocr = ocr[:119] + "…"
        self._ocr_label.setText(ocr)

    def _on_field_clicked(self, field_id: str) -> None:
        self._selected_field_id = field_id
        self._rebuild_palette_buttons()
        self._highlight_current_grade()
        self._refresh_info()
        if self._hit is not None:
            self._hit.set_selected_id(field_id)

    def _apply_judgment_to_field(
        self, field_id: str, judgment: str, score: int
    ) -> None:
        max_score = self._field_max_score(field_id)
        nj = normalize_judgment(judgment) or judgment
        if nj == "○":
            score = max_score
        elif nj == "×" or nj == PENDING_JUDGMENT:
            score = 0
        elif nj == "△":
            if max_score <= 1:
                return
            score = max(1, min(int(score), max_score - 1))
        try:
            update_results_field_grades(
                self._test_id,
                field_id,
                [int(self._row["id"])],
                nj,
                int(score),
            )
        except Exception as e:
            self._ocr_label.setText(f"保存失敗: {e}")
            return
        self._judgments[field_id] = nj
        self._scores[field_id] = int(score)
        self._row["judgments"] = dict(self._judgments)
        self._row["scores"] = dict(self._scores)
        self._dirty_grades = True
        self._refresh_info()
        if self._show_marks:
            self._refresh_display_image_only()
        else:
            self._refresh_hit_overlay()

    def accept(self) -> None:
        if self._palette_controller is not None:
            self._palette_controller.unbind_full_sheet_dialog(self)
        if self._stack is not None:
            try:
                save_ink_strokes(
                    self._test_id,
                    self._result_id,
                    SHEET_FIELD_ID,
                    self._stack.ink_overlay.strokes(),
                )
                save_text_annotations(
                    self._test_id,
                    self._result_id,
                    SHEET_FIELD_ID,
                    self._stack.text_layer.annotations(),
                )
            except Exception:
                pass
        if self._dirty_grades:
            try:
                calculate_domain_scores(self._test_id)
            except Exception:
                pass
        super().accept()

    def reject(self) -> None:
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._palette_controller is not None:
            self._palette_controller.unbind_full_sheet_dialog(self)
        super().closeEvent(event)
