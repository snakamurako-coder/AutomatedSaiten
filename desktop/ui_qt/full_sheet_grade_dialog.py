"""一枚全容採点ダイアログ — 補正全画像上で全記述欄を採点。"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from models.domain_repo import calculate_domain_scores
from models.grading_status import PENDING_JUDGMENT, normalize_judgment
from models.output_repo import get_feedback_style
from models.test_repo import update_results_field_grades
from services.compositor import (
    REGION_FILL_ALPHA,
    REGION_FILL_ALPHA_SELECTED,
    REGION_STROKE_NORMAL,
    REGION_STROKE_SELECTED,
)
from services.feedback_renderer import render_feedback_for_row
from services.image_loader import imread_bgr
from ui_qt.crop_widgets import ZoomControls
from ui_qt.helpers import bgr_to_qpixmap, pil_to_qpixmap
from ui_qt.style import COLORS

VIEW_OUTLINE = "outline"
VIEW_MARKED = "marked"


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


class _SheetCanvas(QWidget):
    """補正画像（または個票合成）＋記述欄ヒット。"""

    fieldClicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._fields: list[dict[str, Any]] = []
        self._selected_id = ""
        self._show_outlines = True
        self._zoom = 1.0
        self._native_w = 1
        self._native_h = 1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_content(
        self,
        pixmap: QPixmap,
        fields: list[dict[str, Any]],
        *,
        show_outlines: bool,
        selected_id: str = "",
        zoom: float = 1.0,
    ) -> None:
        self._pixmap = pixmap
        self._fields = list(fields)
        self._show_outlines = bool(show_outlines)
        self._selected_id = str(selected_id or "")
        self._zoom = max(0.1, float(zoom))
        self._native_w = max(1, pixmap.width())
        self._native_h = max(1, pixmap.height())
        self._recompute_size()
        self.update()

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.1, float(zoom))
        self._recompute_size()
        self.update()

    def set_selected_id(self, field_id: str) -> None:
        self._selected_id = str(field_id or "")
        self.update()

    def _recompute_size(self) -> None:
        w = max(1, int(self._native_w * self._zoom))
        h = max(1, int(self._native_h * self._zoom))
        self.setFixedSize(w, h)

    def _field_rect_disp(self, field: dict[str, Any]) -> QRect:
        x = int(float(field.get("x") or 0) * self._zoom)
        y = int(float(field.get("y") or 0) * self._zoom)
        w = max(1, int(float(field.get("width") or 0) * self._zoom))
        h = max(1, int(float(field.get("height") or 0) * self._zoom))
        return QRect(x, y, w, h)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS["sidebar"]))
        if self._pixmap is None:
            painter.setPen(QColor(COLORS["text_secondary"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "画像なし")
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._pixmap)

        if not self._show_outlines and not self._selected_id:
            # 判定付きでも選択中だけ薄い枠
            pass

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
            label = str(f.get("displayName") or fid)
            j = str(f.get("_judgment") or "").strip()
            if j:
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
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        # 後勝ち（重なり時は上の欄）
        hit = ""
        for f in self._fields:
            if self._field_rect_disp(f).contains(pos):
                hit = str(f.get("id") or "")
        if hit:
            self.fieldClicked.emit(hit)


class FullSheetGradeDialog(QDialog):
    """選択答案の補正全画像で全記述欄を採点する。"""

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
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("一枚全容採点")
        self.resize(1100, 760)
        self.setModal(True)

        self._test_id = test_id
        self._row = copy.deepcopy(result_row)
        self._warped_path = warped_path
        self._fields = list(fields)
        self._points = {str(k): int(v) for k, v in (points or {}).items()}
        self._judgments = dict(self._row.get("judgments") or {})
        self._scores = dict(self._row.get("scores") or {})
        self._view_mode = VIEW_OUTLINE
        self._palette_key: str | None = None
        self._selected_field_id = str(initial_field_id or "").strip()
        if self._selected_field_id and not any(
            str(f.get("id")) == self._selected_field_id for f in self._fields
        ):
            self._selected_field_id = ""
        if not self._selected_field_id and self._fields:
            self._selected_field_id = str(self._fields[0].get("id") or "")

        self._feedback_style = get_feedback_style()
        self._base_pixmap: QPixmap | None = None
        self._marked_pixmap: QPixmap | None = None
        self._dirty_grades = False

        self._load_base_image()

        root = QVBoxLayout(self)
        root.setSpacing(8)

        header = QHBoxLayout()
        name = str(self._row.get("fileName") or "")
        sid = str(self._row.get("studentId") or "") or "—"
        self._title = QLabel(f"{name}  （生徒ID: {sid}）")
        self._title.setStyleSheet(f"font-weight: 600; color: {COLORS['text']};")
        header.addWidget(self._title, 1)
        self._btn_outline = QPushButton("欄枠のみ")
        self._btn_outline.setCheckable(True)
        self._btn_outline.setChecked(True)
        self._btn_outline.clicked.connect(lambda: self._set_view_mode(VIEW_OUTLINE))
        header.addWidget(self._btn_outline)
        self._btn_marked = QPushButton("判定付き（個票風）")
        self._btn_marked.setCheckable(True)
        self._btn_marked.clicked.connect(lambda: self._set_view_mode(VIEW_MARKED))
        header.addWidget(self._btn_marked)
        root.addLayout(header)

        self._zoom = ZoomControls(min_pct=10, max_pct=200, value=40)
        self._zoom.connect_zoom_changed(self._on_zoom_changed)
        root.addWidget(self._zoom)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignCenter)
        self._canvas = _SheetCanvas()
        self._canvas.fieldClicked.connect(self._on_field_clicked)
        scroll.setWidget(self._canvas)
        root.addWidget(scroll, 1)

        info = QHBoxLayout()
        self._field_label = QLabel("")
        self._field_label.setStyleSheet(f"font-weight: 600; color: {COLORS['text']};")
        info.addWidget(self._field_label)
        self._ocr_label = QLabel("")
        self._ocr_label.setWordWrap(True)
        self._ocr_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        info.addWidget(self._ocr_label, 1)
        root.addLayout(info)

        hint = QLabel(
            "判定パレットを選んでから記述欄をタップすると、その判定が即座に保存されます。"
        )
        hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        root.addWidget(hint)

        self._palette_row = QHBoxLayout()
        self._palette_group = QButtonGroup(self)
        self._palette_group.setExclusive(True)
        self._palette_btns: dict[str, QPushButton] = {}
        root.addLayout(self._palette_row)

        btns = QHBoxLayout()
        btns.addStretch()
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        root.addLayout(btns)

        self._rebuild_palette_buttons()
        self._refresh_info()
        self._refresh_canvas()

    def _load_base_image(self) -> None:
        bgr = imread_bgr(self._warped_path)
        if bgr is None:
            self._base_pixmap = QPixmap(400, 300)
            self._base_pixmap.fill(QColor(COLORS["surface"]))
            return
        self._base_pixmap = bgr_to_qpixmap(bgr)

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
        )

    def _rebuild_palette_buttons(self) -> None:
        while self._palette_row.count():
            item = self._palette_row.takeAt(0)
            w = item.widget()
            if w is not None:
                self._palette_group.removeButton(w)
                w.deleteLater()
        self._palette_btns.clear()
        valid = {s[0] for s in self._palette_specs()}
        if self._palette_key not in valid:
            self._palette_key = None
        for key, label, _j, _s in self._palette_specs():
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == self._palette_key)
            btn.setFixedHeight(36)
            btn.setMinimumWidth(44)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._btn_style(key, active=btn.isChecked()))
            btn.toggled.connect(lambda checked, k=key: self._on_palette_toggled(k, checked))
            self._palette_group.addButton(btn)
            self._palette_btns[key] = btn
            self._palette_row.addWidget(btn)
        self._palette_row.addStretch()

    def _on_palette_toggled(self, key: str, checked: bool) -> None:
        if checked:
            self._palette_key = key
        elif self._palette_key == key:
            self._palette_key = None
        for k, btn in self._palette_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == self._palette_key)
            btn.setStyleSheet(self._btn_style(k, active=k == self._palette_key))
            btn.blockSignals(False)

    def _set_view_mode(self, mode: str) -> None:
        self._view_mode = mode
        self._btn_outline.setChecked(mode == VIEW_OUTLINE)
        self._btn_marked.setChecked(mode == VIEW_MARKED)
        if mode == VIEW_MARKED:
            self._ensure_marked_pixmap()
        self._refresh_canvas()

    def _on_zoom_changed(self) -> None:
        self._canvas.set_zoom(self._zoom.zoom_value() / 100.0)

    def _fields_for_canvas(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in self._fields:
            item = dict(f)
            fid = str(f.get("id") or "")
            item["_judgment"] = normalize_judgment(self._judgments.get(fid)) or ""
            out.append(item)
        return out

    def _ensure_marked_pixmap(self) -> None:
        self._row["judgments"] = dict(self._judgments)
        self._row["scores"] = dict(self._scores)
        try:
            img = render_feedback_for_row(self._test_id, self._row)
            self._marked_pixmap = pil_to_qpixmap(img)
        except Exception as e:
            # 合成失敗時は補正画像にフォールバック
            self._marked_pixmap = self._base_pixmap
            self._ocr_label.setText(f"個票合成に失敗: {e}")

    def _refresh_canvas(self) -> None:
        zoom = self._zoom.zoom_value() / 100.0
        if self._view_mode == VIEW_MARKED:
            if self._marked_pixmap is None:
                self._ensure_marked_pixmap()
            pm = self._marked_pixmap or self._base_pixmap
            show_outlines = False
        else:
            pm = self._base_pixmap
            show_outlines = True
        if pm is None:
            return
        self._canvas.set_content(
            pm,
            self._fields_for_canvas(),
            show_outlines=show_outlines,
            selected_id=self._selected_field_id,
            zoom=zoom,
        )

    def _refresh_info(self) -> None:
        fid = self._selected_field_id
        field = next((f for f in self._fields if str(f.get("id")) == fid), None)
        name = str((field or {}).get("displayName") or fid or "—")
        j = normalize_judgment(self._judgments.get(fid)) or "未採点"
        sc = self._scores.get(fid)
        sc_txt = f"{sc}" if sc is not None and sc != "" else "—"
        self._field_label.setText(f"選択: {name}  判定 {j} / {sc_txt}点")
        texts = self._row.get("textMapping") or {}
        ocr = str(texts.get(fid) or "").strip() or "（OCRテキストなし）"
        if len(ocr) > 120:
            ocr = ocr[:119] + "…"
        self._ocr_label.setText(ocr)

    def _sync_palette_checked(self) -> None:
        for k, btn in self._palette_btns.items():
            btn.blockSignals(True)
            btn.setChecked(k == self._palette_key)
            btn.setStyleSheet(self._btn_style(k, active=k == self._palette_key))
            btn.blockSignals(False)

    def _on_field_clicked(self, field_id: str) -> None:
        pending_key = self._palette_key
        pending_spec = None
        if pending_key:
            pending_spec = next(
                (s for s in self._palette_specs() if s[0] == pending_key),
                None,
            )
        self._selected_field_id = field_id
        self._rebuild_palette_buttons()
        if pending_key and pending_key in self._palette_btns:
            self._palette_key = pending_key
            self._sync_palette_checked()
        elif pending_spec is not None and pending_spec[2] != "△":
            for key, _label, judgment, _score in self._palette_specs():
                if judgment == pending_spec[2]:
                    self._palette_key = key
                    self._sync_palette_checked()
                    break
        self._refresh_info()
        self._canvas.set_selected_id(field_id)
        if pending_spec is not None:
            self._apply_judgment_to_field(
                field_id, pending_spec[2], pending_spec[3]
            )

    def _apply_palette_to_field(self, field_id: str) -> None:
        key = self._palette_key
        if not key:
            return
        spec = next((s for s in self._palette_specs() if s[0] == key), None)
        if spec is None:
            return
        self._apply_judgment_to_field(field_id, spec[2], spec[3])

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
        if self._view_mode == VIEW_MARKED:
            self._ensure_marked_pixmap()
        self._refresh_canvas()

    def accept(self) -> None:
        if self._dirty_grades:
            try:
                calculate_domain_scores(self._test_id)
            except Exception:
                pass
        super().accept()

    def reject(self) -> None:
        if self._dirty_grades:
            try:
                calculate_domain_scores(self._test_id)
            except Exception:
                pass
        super().reject()
