"""一枚全容採点ダイアログ — 補正全画像上で全記述欄を採点。"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
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
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
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

        self._chk_outlines = QCheckBox("欄枠を表示")
        self._chk_outlines.setChecked(True)
        self._chk_outlines.toggled.connect(self._on_show_outlines_toggled)
        header.addWidget(self._chk_outlines)

        self._chk_marks = QCheckBox("判定・得点を表示")
        self._chk_marks.setChecked(True)
        self._chk_marks.toggled.connect(self._on_show_marks_toggled)
        header.addWidget(self._chk_marks)

        self._btn_fullscreen = QPushButton("フルウィンドウ")
        self._btn_fullscreen.setToolTip("ウィンドウを最大化／元のサイズに戻す")
        self._btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        header.addWidget(self._btn_fullscreen)

        close_x = QPushButton("×")
        close_x.setFixedWidth(36)
        close_x.setToolTip("閉じる")
        close_x.clicked.connect(self.accept)
        header.addWidget(close_x)
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
            "回答欄を選ぶ → 配点に応じた判定リストから ○／△(点)／×／? を選ぶと、その場で保存されます。"
        )
        hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        root.addWidget(hint)

        self._palette_row = QHBoxLayout()
        self._palette_btns: dict[str, QPushButton] = {}
        root.addLayout(self._palette_row)

        btns = QHBoxLayout()
        btns.addStretch()
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        root.addLayout(btns)

        self._rebuild_palette_buttons()
        self._highlight_current_grade()
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
            btn.setEnabled(bool(self._selected_field_id))
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
        if not self._selected_field_id:
            self._ocr_label.setText("先に回答欄を選択してください。")
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
        self._refresh_canvas()

    def _on_show_marks_toggled(self, checked: bool) -> None:
        self._show_marks = bool(checked)
        if self._show_marks:
            self._ensure_marked_pixmap()
        self._refresh_canvas()

    def _toggle_fullscreen(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self._btn_fullscreen.setText("フルウィンドウ")
        else:
            self.showMaximized()
            self._btn_fullscreen.setText("元のサイズ")

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and hasattr(
            self, "_btn_fullscreen"
        ):
            if self.isMaximized():
                self._btn_fullscreen.setText("元のサイズ")
            else:
                self._btn_fullscreen.setText("フルウィンドウ")

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
            self._marked_pixmap = self._base_pixmap
            self._ocr_label.setText(f"個票合成に失敗: {e}")

    def _refresh_canvas(self) -> None:
        zoom = self._zoom.zoom_value() / 100.0
        if self._show_marks:
            if self._marked_pixmap is None:
                self._ensure_marked_pixmap()
            pm = self._marked_pixmap or self._base_pixmap
        else:
            pm = self._base_pixmap
        if pm is None:
            return
        self._canvas.set_content(
            pm,
            self._fields_for_canvas(),
            show_outlines=self._show_outlines,
            selected_id=self._selected_field_id,
            zoom=zoom,
        )

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
        # 欄選択のみ。判定はパレット操作で反映する。
        self._selected_field_id = field_id
        self._rebuild_palette_buttons()
        self._highlight_current_grade()
        self._refresh_info()
        self._canvas.set_selected_id(field_id)

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
