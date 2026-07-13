"""④ 採点基準ページ（OCR置換・みなし採点・外れ値画像確認）。"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.criteria_repo import (
    get_answer_rows_for_pattern,
    get_outlier_answer_groups,
    merge_unique_with_criteria,
    save_grading_criteria,
)
from models.ink_repo import (
    SHEET_FIELD_ID,
    get_ink_strokes_batch,
    project_sheet_ink_to_field_local,
    save_ink_strokes,
)
from models.text_annotation_repo import (
    get_text_annotations,
    get_text_annotations_batch,
    project_sheet_text_to_field_local,
    save_text_annotations,
    sheet_boxes_without_ids,
)
from models.database import connect
from models.test_repo import get_answer_fields, get_points_conn
from models.text_processing import (
    apply_deemed_scoring_to_field,
    apply_text_replacements_to_field,
    get_deemed_draft,
    get_ocr_replacements,
    save_deemed_scoring_draft,
    save_ocr_replacements,
)
from services.crop_preview import load_crops_for_rows
from services.gemini_rubric import generate_rubric_with_gemini
from ui_qt import helpers as h
from ui_qt.criteria_widgets import (
    ScoreStepWidget,
    find_judgment_combo,
    find_score_widget,
    focus_score_widget,
    make_judgment_combo,
    make_phrase_group_id_cell,
    make_table_action_cell,
    open_judgment_combo,
    wrap_table_cell,
)
from ui_qt.floating_palette.phrase_template_prefs import template_dict_from_uniform_config
from ui_qt.crop_widgets import CropDisplayControls
from ui_qt.uniform_feedback_dialog import UniformFeedbackDialog
from ui_qt.stylus_overlay import CropInkImageStack
from ui_qt.layout_helpers import (
    CollapsibleSection,
    main_table_frame,
    make_expanding,
    viewport_work_height,
)
from ui_qt.style import COLORS
from ui_qt.table_cells import (
    make_editable_item,
    make_readonly_item,
    make_toggle_item,
    set_toggle_checked,
    start_cell_edit,
    wire_toggle_columns,
)


class Step4Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._fields: list[dict[str, Any]] = []
        self._criteria_rows: list[dict[str, Any]] = []
        self._ocr_replace_rows: list[dict[str, Any]] = []
        self._deemed_checked_by_field: dict[str, dict[str, bool]] = {}
        self._incorrect_checked_by_field: dict[str, dict[str, bool]] = {}
        self._outlier_groups: list[dict[str, Any]] = []
        self._outlier_flat_rows: list[dict[str, Any]] = []
        self._crop_grid_results: list[dict[str, Any]] = []
        self._ink_stacks: list[CropInkImageStack] = []

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        make_expanding(self._scroll)
        outer.addWidget(self._scroll, 1)

        body = QWidget()
        self._scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(8)

        root.addWidget(h.title_label("④ 採点基準の設定"))
        root.addWidget(
            h.muted_label("OCR置換・みなし採点で解答を整えてから、判定・得点の基準を設定します。")
        )

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("記述欄"))
        self.field_combo = QComboBox()
        self.field_combo.setMinimumWidth(240)
        self.field_combo.currentIndexChanged.connect(self._on_field_changed)
        toolbar.addWidget(self.field_combo)
        toolbar.addWidget(h.button("解答を集約", self._on_aggregate))
        toolbar.addWidget(h.button("AI原案", self._on_gemini))
        toolbar.addWidget(h.button("基準を保存", self._on_save_criteria, variant="primary"))
        toolbar.addStretch()
        root.addLayout(toolbar)

        root.addWidget(self._build_ocr_replace_section())
        root.addWidget(self._build_deemed_box())
        root.addWidget(main_table_frame("", self._build_criteria_table()))
        root.addWidget(self._build_outlier_box())
        self._apply_viewport_heights()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._apply_viewport_heights()

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self._apply_viewport_heights()

    def _apply_viewport_heights(self) -> None:
        if not hasattr(self, "crop_scroll"):
            return
        crop_h = viewport_work_height(160, min_height=512, max_ratio=0.85, widget=self)
        self.crop_scroll.setMinimumHeight(crop_h)
        self.crop_scroll.setMaximumHeight(crop_h)
        crit_h = viewport_work_height(224, min_height=240, max_ratio=0.55, widget=self)
        self.criteria_table.setMinimumHeight(min(280, crit_h))
        self.criteria_table.setMaximumHeight(crit_h)

    # ==================== UI 構築 ====================

    def _build_ocr_replace_section(self) -> CollapsibleSection:
        body = QFrame()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(
            h.caption_label(
                "「置換ルールを保存」はルールのみ。「置換を適用して再集約」で採点結果のテキスト列を書き換えます。"
            )
        )
        self.ocr_table = QTableWidget(0, 3)
        self.ocr_table.setHorizontalHeaderLabels(["検索", "置換後", "正規表現"])
        self.ocr_table.setColumnWidth(0, 240)
        self.ocr_table.setColumnWidth(1, 240)
        self.ocr_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.ocr_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.ocr_table.setMaximumHeight(160)
        lay.addWidget(self.ocr_table)

        edit_row = QHBoxLayout()
        self.ocr_search_edit = QLineEdit()
        self.ocr_search_edit.setPlaceholderText("検索")
        edit_row.addWidget(self.ocr_search_edit)
        self.ocr_replace_edit = QLineEdit()
        self.ocr_replace_edit.setPlaceholderText("置換後")
        edit_row.addWidget(self.ocr_replace_edit)
        self.ocr_regex_check = QCheckBox("正規表現")
        edit_row.addWidget(self.ocr_regex_check)
        edit_row.addWidget(h.button("行追加", self._on_ocr_row_add))
        edit_row.addWidget(h.button("行削除", self._on_ocr_row_delete))
        edit_row.addWidget(h.button("ルール保存", self._on_save_ocr_rules))
        edit_row.addWidget(h.button("置換を適用して再集約", self._on_apply_ocr, variant="success"))
        edit_row.addStretch()
        lay.addLayout(edit_row)
        return CollapsibleSection(
            "OCRテキスト置換",
            body,
            collapsed=True,
            tint="#fffbeb",
        )

    def _build_deemed_box(self) -> QGroupBox:
        box = QGroupBox("みなし採点")
        box.setStyleSheet(
            f"QGroupBox {{ background: #eef2ff; border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
        )
        lay = QVBoxLayout(box)
        lay.addWidget(
            h.caption_label(
                "正答例を指定し、表の「みなし」「不正解」列をクリックで選択 → 適用で正答例に統一します。"
            )
        )
        row = QHBoxLayout()
        row.addWidget(QLabel("正答例"))
        self.deemed_canonical_edit = QLineEdit()
        row.addWidget(self.deemed_canonical_edit, 1)
        row.addWidget(h.button("下書き保存", self._on_save_deemed_draft))
        row.addWidget(h.button("みなし採点を適用して再集約", self._on_apply_deemed, variant="success"))
        lay.addLayout(row)
        return box

    def _build_criteria_table(self) -> QTableWidget:
        self.criteria_table = QTableWidget(0, 10)
        self.criteria_table.setHorizontalHeaderLabels(
            [
                "みなし",
                "不正解",
                "解答",
                "人数",
                "判定",
                "得点",
                "一律フィードバック",
                "一律フィードバックID",
                "備考",
                "操作",
            ]
        )
        widths = [52, 52, 220, 52, 72, 118, 180, 130, 200, 60]
        for i, w in enumerate(widths):
            self.criteria_table.setColumnWidth(i, w)
        self.criteria_table.horizontalHeader().setStretchLastSection(True)
        self.criteria_table.setSelectionBehavior(QTableWidget.SelectItems)
        self.criteria_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.criteria_table.verticalHeader().setDefaultSectionSize(36)
        self.criteria_table.verticalHeader().setVisible(False)
        make_expanding(self.criteria_table)
        wire_toggle_columns(
            self.criteria_table,
            (0, 1),
            self._on_criteria_toggle,
        )
        self.criteria_table.cellClicked.connect(self._on_criteria_cell_clicked)
        self.criteria_table.itemChanged.connect(self._on_criteria_item_changed)
        return self.criteria_table

    def _on_criteria_toggle(self, row: int, col: int, checked: bool) -> None:
        if row >= len(self._criteria_rows):
            return
        fid = self._selected_field_id()
        if not fid:
            return
        ans = self._criteria_rows[row]["answer_text"]
        if col == 0:
            if self._canonical() and ans == self._canonical():
                return
            if checked:
                self._deemed_map(fid)[ans] = True
            else:
                self._deemed_map(fid).pop(ans, None)
        elif col == 1:
            if checked:
                self._incorrect_map(fid)[ans] = True
            else:
                self._incorrect_map(fid).pop(ans, None)
        self._sync_checks_to_rows()
        self._apply_criteria_table_styles()
        self.criteria_table.setCurrentCell(row, col)

    def _on_criteria_cell_clicked(self, row: int, col: int) -> None:
        if col == 4:
            open_judgment_combo(self.criteria_table, row, col)
        elif col == 5:
            focus_score_widget(self.criteria_table, row, col)
        elif col == 6:
            self._open_uniform_feedback_dialog(row)
        elif col == 7:
            self._open_uniform_feedback_batch_update(row)
        elif col == 8:
            start_cell_edit(self.criteria_table, row, col)
        elif col == 9:
            if row < len(self._criteria_rows):
                ans = self._criteria_rows[row]["answer_text"]
                if not self._should_skip_crop(ans):
                    self._show_answer_pattern_crops(ans)

    def _on_criteria_item_changed(self, item: QTableWidgetItem) -> None:
        row, col = item.row(), item.column()
        if row < 0 or row >= len(self._criteria_rows) or col != 8:
            return
        self._criteria_rows[row]["reason"] = item.text().strip()

    def _field_max_score(self) -> int:
        fid = self._selected_field_id()
        test_id = self.app.active_test_id
        if not fid or not test_id:
            return 99
        with connect() as conn:
            pts = get_points_conn(conn, test_id)
        return max(1, int(pts.get(fid, 1)))

    @staticmethod
    def _default_judgment(row: dict[str, Any]) -> str:
        j = str(row.get("judgment") or "").strip()
        return j if j in ("○", "△", "×") else "×"

    def _default_score(self, row: dict[str, Any], max_score: int | None = None) -> int:
        cap = max_score if max_score is not None else self._field_max_score()
        s = row.get("score")
        if s != "" and s is not None:
            try:
                return max(0, min(cap, int(s)))
            except (TypeError, ValueError):
                pass
        if str(row.get("answer_text") or "") == "なし":
            return 0
        return min(1, cap)

    def _set_criteria_judgment(self, row: int, judgment: str) -> None:
        if 0 <= row < len(self._criteria_rows):
            self._criteria_rows[row]["judgment"] = judgment

    def _set_criteria_score(self, row: int, score: int) -> None:
        if 0 <= row < len(self._criteria_rows):
            self._criteria_rows[row]["score"] = int(score)

    def _build_outlier_box(self) -> QGroupBox:
        box = QGroupBox("外れ値・少数派解答の確認（回答欄画像）")
        box.setStyleSheet(
            f"QGroupBox {{ background: #f8fafc; border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
        )
        lay = QVBoxLayout(box)
        lay.addWidget(
            h.caption_label(
                "「みなし」「不正解」「表示」列はクリックで切替。画像タイルクリックでもみなしを切替えられます。"
            )
        )

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("人数上限 ≤"))
        self.outlier_max_spin = QSpinBox()
        self.outlier_max_spin.setRange(1, 99)
        self.outlier_max_spin.setValue(2)
        ctrl.addWidget(self.outlier_max_spin)
        ctrl.addWidget(h.button("外れ値を検出", self._on_fetch_outliers))
        self.hide_incorrect_check = QCheckBox("不正解対象の解答の画像は表示しない")
        self.hide_incorrect_check.setChecked(True)
        self.hide_incorrect_check.toggled.connect(lambda _c: self._purge_incorrect_from_grid())
        ctrl.addWidget(self.hide_incorrect_check)
        ctrl.addWidget(h.button("なし（未回答）を確認", self._on_show_none_crops))
        ctrl.addWidget(h.button("表示を全選択", lambda: self._select_all_outlier(True)))
        ctrl.addWidget(h.button("表示を解除", lambda: self._select_all_outlier(False)))
        ctrl.addWidget(h.button("選択を画像表示", self._on_show_selected_crops, variant="primary"))
        ctrl.addStretch()
        lay.addLayout(ctrl)

        zoom_row = QHBoxLayout()
        self.crop_controls = CropDisplayControls()
        self.crop_controls.connect_zoom_changed(self._render_crop_grid)
        self.crop_controls.connect_meta_changed(self._render_crop_grid)
        zoom_row.addWidget(self.crop_controls, 1)
        lay.addLayout(zoom_row)

        self.outlier_table = QTableWidget(0, 8)
        self.outlier_table.setHorizontalHeaderLabels(
            ["みなし", "不正解", "解答", "人数", "表示", "生徒ID", "ファイル名", "操作"]
        )
        for i, w in enumerate([52, 52, 220, 48, 48, 90, 200, 60]):
            self.outlier_table.setColumnWidth(i, w)
        self.outlier_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.outlier_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.outlier_table.setMaximumHeight(144)
        wire_toggle_columns(
            self.outlier_table,
            (0, 1, 4),
            self._on_outlier_toggle,
        )
        self.outlier_table.cellClicked.connect(self._on_outlier_cell_clicked)
        lay.addWidget(self.outlier_table)

        self.crop_scroll = QScrollArea()
        self.crop_scroll.setWidgetResizable(True)
        self.crop_scroll.viewport().setAttribute(Qt.WA_TabletTracking, True)
        self.crop_scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['surface']}; }}"
        )
        self.crop_panel = QWidget()
        self.crop_panel.setStyleSheet("background: transparent;")
        self.crop_grid = QGridLayout(self.crop_panel)
        self.crop_grid.setContentsMargins(8, 8, 8, 8)
        self.crop_grid.setSpacing(8)
        self.crop_scroll.setWidget(self.crop_panel)
        lay.addWidget(self.crop_scroll)
        return box

    # ==================== 状態ヘルパー ====================

    def _deemed_map(self, field_id: str) -> dict[str, bool]:
        return self._deemed_checked_by_field.setdefault(field_id, {})

    def _incorrect_map(self, field_id: str) -> dict[str, bool]:
        return self._incorrect_checked_by_field.setdefault(field_id, {})

    def _canonical(self) -> str:
        return self.deemed_canonical_edit.text().strip()

    def _is_deemed(self, fid: str, ans: str) -> bool:
        if self._canonical() and ans == self._canonical():
            return False
        return bool(self._deemed_map(fid).get(ans))

    def _is_incorrect(self, fid: str, ans: str) -> bool:
        return bool(self._incorrect_map(fid).get(ans))

    def _selected_field_id(self) -> str | None:
        idx = self.field_combo.currentIndex()
        if idx < 0 or idx >= len(self._fields):
            return None
        return self._fields[idx]["id"]

    def _toggle_deemed(self, fid: str, ans: str) -> None:
        if self._canonical() and ans == self._canonical():
            return
        m = self._deemed_map(fid)
        if m.get(ans):
            m.pop(ans, None)
        else:
            m[ans] = True
        self._sync_checks_to_rows()
        self._refresh_check_views()

    def _on_crop_image_clicked(self, fid: str, result_id: int, ans: str) -> None:
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.set_active_result_id(result_id)
        self._toggle_deemed(fid, ans)

    def _toggle_incorrect(self, fid: str, ans: str) -> None:
        m = self._incorrect_map(fid)
        if m.get(ans):
            m.pop(ans, None)
        else:
            m[ans] = True
        self._sync_checks_to_rows()
        self._refresh_check_views()
        self._purge_incorrect_from_grid()

    def _sync_checks_to_rows(self) -> None:
        fid = self._selected_field_id()
        if not fid:
            return
        for row in self._criteria_rows:
            ans = row["answer_text"]
            row["deemed"] = self._is_deemed(fid, ans)
            row["incorrect"] = self._is_incorrect(fid, ans)

    def _refresh_check_views(self) -> None:
        self._apply_criteria_table_styles()
        self._render_outlier_table()
        self._render_crop_grid()

    def _sync_criteria_from_widgets(self) -> None:
        t = self.criteria_table
        for i in range(min(t.rowCount(), len(self._criteria_rows))):
            combo = find_judgment_combo(t, i)
            if combo is not None:
                self._criteria_rows[i]["judgment"] = combo.currentText()
            score_w = find_score_widget(t, i)
            if score_w is not None:
                self._criteria_rows[i]["score"] = score_w.value()
            reason_item = t.item(i, 8)
            if reason_item is not None:
                self._criteria_rows[i]["reason"] = reason_item.text().strip()

    def _apply_criteria_table_styles(self) -> None:
        fid = self._selected_field_id() or ""
        canonical = self._canonical()
        t = self.criteria_table
        t.blockSignals(True)
        for i, row in enumerate(self._criteria_rows):
            ans = row.get("answer_text", "")
            if canonical and ans == canonical:
                deemed_item = t.item(i, 0)
                if deemed_item is not None:
                    deemed_item.setText("—")
            else:
                deemed_item = t.item(i, 0)
                if deemed_item is not None:
                    set_toggle_checked(deemed_item, self._is_deemed(fid, ans))
            incorrect_item = t.item(i, 1)
            if incorrect_item is not None:
                set_toggle_checked(incorrect_item, self._is_incorrect(fid, ans))

            bg = None
            if row.get("deemed") or self._is_deemed(fid, ans):
                bg = COLORS["accent_soft"]
            elif row.get("incorrect") or self._is_incorrect(fid, ans):
                bg = COLORS["danger_soft"]
            color = QColor(bg) if bg else QColor()
            for c in (0, 1, 2, 3, 6, 7, 8):
                item = t.item(i, c)
                if item is None:
                    continue
                if bg:
                    item.setBackground(color)
                else:
                    item.setData(Qt.ItemDataRole.BackgroundRole, None)
        t.blockSignals(False)

    def _should_skip_crop(self, ans: str) -> bool:
        if not self.hide_incorrect_check.isChecked():
            return False
        fid = self._selected_field_id()
        return bool(fid and self._is_incorrect(fid, ans))

    # ==================== 再読込 ====================

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        self._fields = get_answer_fields(self.app.active_test_id)
        current = self.field_combo.currentIndex()
        self.field_combo.blockSignals(True)
        self.field_combo.clear()
        self.field_combo.addItems([f"{f['displayName']} ({f['id']})" for f in self._fields])
        if self._fields:
            self.field_combo.setCurrentIndex(current if 0 <= current < len(self._fields) else 0)
        self.field_combo.blockSignals(False)
        if self._fields:
            self._load_field_state()
            self._aggregate()

    def _on_field_changed(self, _index: int) -> None:
        if not self.app.active_test_id or not self._fields:
            return
        self._outlier_groups = []
        self._outlier_flat_rows = []
        self._crop_grid_results = []
        self._load_field_state()
        self._aggregate()
        self._render_outlier_table()
        self._render_crop_grid()

    def _load_field_state(self) -> None:
        fid = self._selected_field_id()
        if not fid:
            return
        self._ocr_replace_rows = [
            {"search": r["search"], "replace": r["replace"], "useRegex": r["useRegex"]}
            for r in get_ocr_replacements(self.app.active_test_id, fid)
        ]
        self._render_ocr_table()
        draft = get_deemed_draft(self.app.active_test_id, fid)
        self.deemed_canonical_edit.setText(draft.get("canonical", ""))
        self._deemed_map(fid).clear()
        for src in draft.get("sources") or []:
            self._deemed_map(fid)[src] = True

    # ==================== OCR置換 ====================

    def _render_ocr_table(self) -> None:
        self.ocr_table.setRowCount(0)
        for row in self._ocr_replace_rows:
            r = self.ocr_table.rowCount()
            self.ocr_table.insertRow(r)
            self.ocr_table.setItem(r, 0, QTableWidgetItem(row.get("search", "")))
            self.ocr_table.setItem(r, 1, QTableWidgetItem(row.get("replace", "")))
            self.ocr_table.setItem(r, 2, QTableWidgetItem("はい" if row.get("useRegex") else ""))

    def _on_ocr_row_add(self) -> None:
        search = self.ocr_search_edit.text().strip()
        if not search:
            h.warn(self, "入力不足", "検索文字列を入力してください。")
            return
        self._ocr_replace_rows.append(
            {
                "search": search,
                "replace": self.ocr_replace_edit.text(),
                "useRegex": self.ocr_regex_check.isChecked(),
            }
        )
        self._render_ocr_table()
        self.ocr_search_edit.clear()
        self.ocr_replace_edit.clear()
        self.ocr_regex_check.setChecked(False)

    def _on_ocr_row_delete(self) -> None:
        row = self.ocr_table.currentRow()
        if 0 <= row < len(self._ocr_replace_rows):
            del self._ocr_replace_rows[row]
            self._render_ocr_table()

    def _on_save_ocr_rules(self) -> None:
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        try:
            save_ocr_replacements(self.app.active_test_id, fid, self._ocr_replace_rows)
            h.info(self, "保存完了", "OCR置換ルールを保存しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_apply_ocr(self) -> None:
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        try:
            res = apply_text_replacements_to_field(
                self.app.active_test_id, fid, self._ocr_replace_rows
            )
            save_ocr_replacements(self.app.active_test_id, fid, self._ocr_replace_rows)
            self._aggregate()
            self._on_fetch_outliers(silent=True)
            h.info(self, "適用完了", f"{res.get('replacedCount', 0)} 件のテキストを置換しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))

    # ==================== みなし採点 ====================

    def _deemed_sources(self) -> list[str]:
        fid = self._selected_field_id()
        if not fid:
            return []
        canonical = self._canonical()
        return [k for k, v in self._deemed_map(fid).items() if v and k != canonical]

    def _on_save_deemed_draft(self) -> None:
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        try:
            save_deemed_scoring_draft(
                self.app.active_test_id, fid, self._canonical(), self._deemed_sources()
            )
            h.info(self, "保存完了", "みなし採点の下書きを保存しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_apply_deemed(self) -> None:
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        sources = self._deemed_sources()
        try:
            res = apply_deemed_scoring_to_field(
                self.app.active_test_id, fid, self._canonical(), sources
            )
            self.deemed_canonical_edit.setText(res.get("canonical", ""))
            self._deemed_map(fid).clear()
            for src in sources:
                self._incorrect_map(fid).pop(src, None)
            self._aggregate()
            self._purge_deemed_from_outlier(sources)
            self._on_fetch_outliers(silent=True)
            h.info(self, "適用完了", f"{res.get('updatedCount', 0)} 件を正答例に統一しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))

    # ==================== 採点基準テーブル ====================

    def _aggregate(self) -> None:
        fid = self._selected_field_id()
        if not fid:
            return
        self._criteria_rows = merge_unique_with_criteria(self.app.active_test_id, fid)
        self._sync_checks_to_rows()
        self._render_criteria_table()

    def _on_aggregate(self) -> None:
        if not self.app.require_active_test():
            return
        if not self._selected_field_id():
            h.warn(self, "記述欄未選択", "記述欄を選択してください。")
            return
        self._aggregate()

    def _open_uniform_feedback_batch_update(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self._criteria_rows):
            return
        uniform_cfg = self._criteria_rows[row_index].get("uniform_feedback") or {}
        if not isinstance(uniform_cfg, dict):
            return
        gid = str(uniform_cfg.get("phraseGroupId") or "").strip()
        if not gid:
            return
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is None or not hasattr(ctrl, "open_phrase_batch_update_dialog"):
            return
        template = None
        try:
            template = template_dict_from_uniform_config(uniform_cfg)
        except ValueError:
            template = None
        ctrl.open_phrase_batch_update_dialog(
            gid,
            test_id=str(self.app.active_test_id or ""),
            template=template,
        )

    def _open_uniform_feedback_dialog(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self._criteria_rows):
            return
        fid = self._selected_field_id()
        if not fid or not self.app.active_test_id:
            return
        field = next((f for f in self._fields if str(f.get("id")) == fid), None)
        if field is None:
            h.warn(self, "一律フィードバック", "記述欄が見つかりません。")
            return
        row = self._criteria_rows[row_index]
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None and hasattr(ctrl, "set_settings_overlay_active"):
            ctrl.set_settings_overlay_active(True)
        dlg = UniformFeedbackDialog(
            self,
            test_id=str(self.app.active_test_id),
            field=field,
            answer_text=str(row.get("answer_text") or ""),
            initial_config=row.get("uniform_feedback")
            if isinstance(row.get("uniform_feedback"), dict)
            else None,
        )
        dlg.applied.connect(lambda _c, cfg, r=row_index: self._on_uniform_feedback_applied(r, cfg))
        try:
            dlg.exec()
        finally:
            if ctrl is not None and hasattr(ctrl, "set_settings_overlay_active"):
                ctrl.set_settings_overlay_active(False)

    def _on_uniform_feedback_applied(self, row_index: int, config: dict[str, Any]) -> None:
        if 0 <= row_index < len(self._criteria_rows):
            self._criteria_rows[row_index]["uniform_feedback"] = copy.deepcopy(config or {})
        self._render_criteria_table()
        self.palette_refresh_annotation_cache()
        self._reload_visible_stack_annotations()
        if self._crop_grid_results:
            self._render_crop_grid()

    def _reload_visible_stack_annotations(self) -> None:
        test_id = self.palette_test_id()
        fid = self._selected_field_id() or ""
        if not test_id or not fid:
            return
        field = next((f for f in self._fields if str(f.get("id")) == fid), None)
        for stack in self._ink_stacks:
            rid_attr = getattr(stack, "result_id", 0)
            rid = int(rid_attr() if callable(rid_attr) else rid_attr)
            local = get_text_annotations(test_id, rid, fid)
            sheet = get_text_annotations(test_id, rid, SHEET_FIELD_ID)
            sheet_local = (
                project_sheet_text_to_field_local(sheet, field) if field else []
            )
            stack.text_layer.set_annotations(list(local) + list(sheet_local))
            sheet_ink = get_ink_strokes_batch(test_id, SHEET_FIELD_ID, [rid]).get(rid, [])
            if field:
                stack.ink_overlay.set_background_strokes(
                    project_sheet_ink_to_field_local(sheet_ink, field)
                )

    def _render_criteria_table(self) -> None:
        self._sync_criteria_from_widgets()
        fid = self._selected_field_id() or ""
        canonical = self._canonical()
        t = self.criteria_table
        t.blockSignals(True)
        t.clearContents()
        t.setRowCount(len(self._criteria_rows))
        max_score = self._field_max_score()
        for i, row in enumerate(self._criteria_rows):
            ans = row.get("answer_text", "")
            if canonical and ans == canonical:
                deemed_item = make_readonly_item("—", center=True)
            else:
                deemed_item = make_toggle_item(self._is_deemed(fid, ans))
            incorrect_item = make_toggle_item(self._is_incorrect(fid, ans))
            count_item = make_readonly_item(str(row.get("count", 0)), center=True)
            answer_item = make_readonly_item(ans)
            reason_item = make_editable_item(str(row.get("reason", "") or ""))
            uniform_cfg = row.get("uniform_feedback") or {}
            uniform_text = "（未設定）"
            uniform_gid = "—"
            if isinstance(uniform_cfg, dict) and uniform_cfg:
                preview_text = str(uniform_cfg.get("text") or uniform_cfg.get("label") or "")
                uniform_text = preview_text if preview_text else "（未設定）"
                uniform_gid = str(uniform_cfg.get("phraseGroupId") or "") or "—"
            uniform_item = make_readonly_item(uniform_text)
            uniform_gid_item = make_readonly_item(uniform_gid, center=True)
            uniform_gid_widget = None
            if uniform_gid and uniform_gid != "—":
                uniform_gid_widget = make_phrase_group_id_cell(
                    uniform_gid,
                    lambda _c=False, r=i: self._open_uniform_feedback_batch_update(r),
                )

            judgment = self._default_judgment(row)
            score_val = self._default_score(row, max_score)
            self._criteria_rows[i]["judgment"] = judgment
            self._criteria_rows[i]["score"] = score_val

            j_combo = make_judgment_combo(
                judgment,
                lambda j, r=i: self._set_criteria_judgment(r, j),
            )
            score_widget = ScoreStepWidget(
                score_val,
                max_score,
                lambda s, r=i: self._set_criteria_score(r, s),
            )

            t.setItem(i, 0, deemed_item)
            t.setItem(i, 1, incorrect_item)
            t.setItem(i, 2, answer_item)
            t.setItem(i, 3, count_item)
            t.setCellWidget(i, 4, wrap_table_cell(j_combo))
            t.setCellWidget(i, 5, wrap_table_cell(score_widget))
            t.setItem(i, 6, uniform_item)
            if uniform_gid_widget is not None:
                t.setCellWidget(i, 7, wrap_table_cell(uniform_gid_widget))
            else:
                t.setItem(i, 7, uniform_gid_item)
            t.setItem(i, 8, reason_item)

            if self._should_skip_crop(ans):
                action_widget = make_table_action_cell("除外", None)
            else:
                action_widget = make_table_action_cell(
                    "表示",
                    lambda _c=False, r=i: self._show_answer_pattern_crops(
                        str(self._criteria_rows[r].get("answer_text") or "")
                    ),
                    tooltip="クリックで回答欄画像を下に表示",
                )

            t.setCellWidget(i, 9, wrap_table_cell(action_widget))
            t.setRowHeight(i, 36)
        t.blockSignals(False)
        self._apply_criteria_table_styles()

    def _on_outlier_toggle(self, row: int, col: int, checked: bool) -> None:
        if row >= len(self._outlier_flat_rows):
            return
        flat = self._outlier_flat_rows[row]
        fid = self._selected_field_id()
        if not fid:
            return
        ans = flat["answer_text"]
        if col == 0:
            if checked:
                self._deemed_map(fid)[ans] = True
            else:
                self._deemed_map(fid).pop(ans, None)
            self._sync_checks_to_rows()
            self._apply_criteria_table_styles()
        elif col == 1:
            if checked:
                self._incorrect_map(fid)[ans] = True
            else:
                self._incorrect_map(fid).pop(ans, None)
            self._sync_checks_to_rows()
            self._apply_criteria_table_styles()
        elif col == 4:
            if flat.get("skip_img"):
                return
            flat["show"] = checked
        self._render_outlier_table()
        self.outlier_table.setCurrentCell(row, col)

    def _on_outlier_cell_clicked(self, row: int, col: int) -> None:
        if col != 7 or row >= len(self._outlier_flat_rows):
            return
        flat = self._outlier_flat_rows[row]
        if flat.get("skip_img"):
            return
        self._show_answer_pattern_crops(flat["answer_text"])

    def _show_answer_pattern_crops(self, answer_text: str) -> None:
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        if self._should_skip_crop(answer_text):
            h.warn(
                self,
                "除外",
                f"不正解対象のため「{answer_text}」の画像は表示しません。",
            )
            return
        rows = get_answer_rows_for_pattern(self.app.active_test_id, fid, answer_text)
        if not rows:
            h.info(self, "該当なし", "該当する回答がありません。")
            return
        self._load_crops_async(rows, allow_incorrect=False)

    def _on_save_criteria(self) -> None:
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        self._sync_criteria_from_widgets()
        rules = []
        for row in self._criteria_rows:
            judgment = str(row.get("judgment") or "").strip()
            if not judgment:
                continue
            try:
                score = int(row.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            rules.append(
                {
                    "answer_text": row["answer_text"],
                    "judgment": judgment,
                    "score": score,
                    "reason": row.get("reason") or "",
                    "uniform_feedback": row.get("uniform_feedback")
                    if isinstance(row.get("uniform_feedback"), dict)
                    else None,
                }
            )
        if not rules:
            h.warn(self, "保存不可", "判定が入力された行がありません。")
            return
        try:
            save_grading_criteria(self.app.active_test_id, fid, rules)
            h.info(self, "保存完了", f"採点基準を {len(rules)} 件保存しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_gemini(self) -> None:
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        if not self._criteria_rows:
            self._aggregate()
        unique = [
            {"answer_text": r["answer_text"], "count": r["count"]} for r in self._criteria_rows
        ]
        test_id = self.app.active_test_id

        def done(result, err):
            if err:
                h.error(self, "AI原案エラー", str(err))
                return
            ai_map = {
                str(item["answer_text"]): item for item in result.get("scrutinized_list", [])
            }
            for row in self._criteria_rows:
                ai = ai_map.get(row["answer_text"])
                if not ai:
                    continue
                row["judgment"] = ai.get("judgment", "")
                row["score"] = ai.get("recommended_score", "")
                row["reason"] = ai.get("reason", "")
            self._render_criteria_table()
            h.info(self, "AI原案", "Gemini の原案を表に反映しました。内容を確認して「基準を保存」してください。")

        h.run_in_thread(self, lambda: generate_rubric_with_gemini(test_id, fid, unique), done)

    # ==================== 外れ値 ====================

    def _on_fetch_outliers(self, silent: bool = False) -> None:
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        max_count = self.outlier_max_spin.value()
        self._outlier_groups = get_outlier_answer_groups(
            self.app.active_test_id, fid, max_count
        )
        self._build_outlier_flat_rows()
        self._render_outlier_table()
        if not silent:
            h.info(self, "検出完了", f"{len(self._outlier_groups)} 種類の外れ値解答（人数 ≤ {max_count}）")

    def _build_outlier_flat_rows(self) -> None:
        self._outlier_flat_rows = []
        for gi, group in enumerate(self._outlier_groups):
            for ri, row in enumerate(group.get("rows") or []):
                skip = self._should_skip_crop(group["answer_text"])
                self._outlier_flat_rows.append(
                    {
                        "key": f"{gi}:{ri}",
                        "group_index": gi,
                        "row_index": ri,
                        "answer_text": group["answer_text"],
                        "group_count": group["count"],
                        "show": not skip,
                        "skip_img": skip,
                        **row,
                    }
                )

    def _render_outlier_table(self) -> None:
        fid = self._selected_field_id() or ""
        t = self.outlier_table
        t.blockSignals(True)
        t.setRowCount(len(self._outlier_flat_rows))
        for i, row in enumerate(self._outlier_flat_rows):
            ans = row["answer_text"]
            deemed_item = make_toggle_item(self._is_deemed(fid, ans))
            incorrect_item = make_toggle_item(self._is_incorrect(fid, ans))
            if row.get("skip_img"):
                show_item = make_readonly_item("—", center=True)
            else:
                show_item = make_toggle_item(bool(row.get("show")))
            items = [
                deemed_item,
                incorrect_item,
                make_readonly_item(ans),
                make_readonly_item(str(row["group_count"]), center=True),
                show_item,
                make_readonly_item(str(row.get("studentId") or "-")),
                make_readonly_item(str(row.get("fileName") or "")),
            ]
            for c, item in enumerate(items):
                t.setItem(i, c, item)
            if row.get("skip_img"):
                action_widget = make_table_action_cell("—", None)
            else:
                action_widget = make_table_action_cell(
                    "表示",
                    lambda _c=False, a=ans: self._show_answer_pattern_crops(a),
                    tooltip="クリックで回答欄画像を下に表示",
                )
            t.setCellWidget(i, 7, wrap_table_cell(action_widget))
        t.blockSignals(False)

    def _select_all_outlier(self, checked: bool) -> None:
        for row in self._outlier_flat_rows:
            if row.get("skip_img"):
                continue
            row["show"] = checked
        self._render_outlier_table()

    def _on_show_selected_crops(self) -> None:
        rows = [
            r for r in self._outlier_flat_rows if r.get("show") and not r.get("skip_img")
        ]
        if not rows:
            h.warn(self, "未選択", "表示する回答を選択してください。")
            return
        self._load_crops_async(rows, allow_incorrect=False)

    def _on_show_none_crops(self) -> None:
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        rows = get_answer_rows_for_pattern(self.app.active_test_id, fid, "なし")
        if not rows:
            h.info(self, "なし", "「なし」の回答は見つかりませんでした。")
            return
        self._load_crops_async(rows, allow_incorrect=True)

    def _load_crops_async(self, rows: list[dict[str, Any]], allow_incorrect: bool) -> None:
        fid = self._selected_field_id()
        if not fid or not self.app.active_test_id:
            return
        field = next((f for f in self._fields if f["id"] == fid), None)
        if not field:
            h.error(self, "エラー", "記述欄が見つかりません。")
            return
        if not allow_incorrect and self.hide_incorrect_check.isChecked():
            rows = [r for r in rows if not self._should_skip_crop(r.get("answer_text", ""))]
        if not rows:
            h.info(self, "除外", "表示対象がありません（不正解対象は除外されます）。")
            return

        self._clear_crop_grid()
        self.crop_grid.addWidget(h.muted_label(f"画像を読み込み中…（{len(rows)}枚）"), 0, 0)

        def done(results, err):
            if err:
                h.error(self, "画像読込エラー", str(err))
                return
            test_id = self.app.active_test_id
            result_ids = [
                int((r.get("row") or {}).get("rowIndex") or 0)
                for r in results
            ]
            ink_map = get_ink_strokes_batch(test_id, fid, result_ids) if test_id else {}
            text_map = get_text_annotations_batch(test_id, fid, result_ids) if test_id else {}
            sheet_ink_map = (
                get_ink_strokes_batch(test_id, SHEET_FIELD_ID, result_ids) if test_id else {}
            )
            sheet_text_map = (
                get_text_annotations_batch(test_id, SHEET_FIELD_ID, result_ids)
                if test_id
                else {}
            )
            for r in results:
                row = r.get("row") or {}
                rid = int(row.get("rowIndex") or 0)
                r["ink_strokes"] = ink_map.get(rid, [])
                sheet_local = project_sheet_ink_to_field_local(
                    sheet_ink_map.get(rid, []), field
                )
                r["sheet_ink_strokes"] = sheet_local
                local_tb = text_map.get(rid, [])
                sheet_tb = project_sheet_text_to_field_local(
                    sheet_text_map.get(rid, []), field
                )
                r["text_annotations"] = list(local_tb) + list(sheet_tb)
            self._crop_grid_results = results
            self._render_crop_grid()
            self._scroll_to_crop_viewer()

        h.run_in_thread(self, lambda: load_crops_for_rows(rows, field), done)

    def _scroll_to_crop_viewer(self) -> None:
        if not hasattr(self, "_scroll") or not hasattr(self, "crop_scroll"):
            return

        def _do() -> None:
            self._scroll.ensureWidgetVisible(self.crop_scroll, 0, 32)

        QTimer.singleShot(0, _do)

    def viewer_scroll(self) -> QScrollArea:
        return self.crop_scroll

    def palette_ink_stacks(self) -> list[CropInkImageStack]:
        return self._ink_stacks

    def palette_field_id(self) -> str:
        return self._selected_field_id() or ""

    def palette_test_id(self) -> str | None:
        tid = getattr(self.app, "active_test_id", None)
        return str(tid) if tid else None

    def palette_refresh_annotation_cache(self) -> None:
        test_id = self.palette_test_id()
        fid = self._selected_field_id() or ""
        if not test_id or not fid:
            return
        field = next((f for f in self._fields if str(f.get("id")) == fid), None)
        result_ids = [
            int((item.get("row") or {}).get("rowIndex") or 0)
            for item in self._crop_grid_results
        ]
        text_map = get_text_annotations_batch(test_id, fid, result_ids)
        sheet_text_map = get_text_annotations_batch(test_id, SHEET_FIELD_ID, result_ids)
        for item in self._crop_grid_results:
            row = item.get("row") or {}
            rid = int(row.get("rowIndex") or 0)
            local_tb = text_map.get(rid, [])
            sheet_tb = (
                project_sheet_text_to_field_local(sheet_text_map.get(rid, []), field)
                if field
                else []
            )
            item["text_annotations"] = list(local_tb) + list(sheet_tb)

    def palette_save_annotations(
        self, result_id: int, field_id: str, items: list
    ) -> None:
        test_id = self.app.active_test_id
        if not test_id or not field_id:
            return
        try:
            save_text_annotations(test_id, result_id, field_id, items)
        except Exception as e:
            h.error(self, "テキスト保存エラー", str(e))
            return
        for r in self._crop_grid_results:
            row = r.get("row") or {}
            if int(row.get("rowIndex") or 0) == int(result_id):
                # キャッシュのローカル部分のみ更新（シートTBは維持）
                prev = r.get("text_annotations") or []
                sheet = [a for a in prev if str(a.get("source") or "") == "sheet"]
                r["text_annotations"] = list(items) + sheet
                break

    def _on_sheet_box_deleted(self, result_id: int, box_id: str) -> None:
        test_id = self.app.active_test_id
        bid = str(box_id or "").strip()
        if not test_id or not bid:
            return
        try:
            boxes = get_text_annotations(test_id, int(result_id), SHEET_FIELD_ID)
            save_text_annotations(
                test_id,
                int(result_id),
                SHEET_FIELD_ID,
                sheet_boxes_without_ids(boxes, {bid}),
            )
        except Exception as e:
            h.error(self, "シートTB削除エラー", str(e))
            return
        for stack in self._ink_stacks:
            if int(stack.result_id) != int(result_id):
                continue
            anns = [
                a
                for a in stack.text_layer.annotations()
                if str(a.get("id") or "") != bid
            ]
            stack.text_layer.set_annotations(anns)
        for r in self._crop_grid_results:
            row = r.get("row") or {}
            if int(row.get("rowIndex") or 0) == int(result_id):
                r["text_annotations"] = [
                    a
                    for a in (r.get("text_annotations") or [])
                    if str(a.get("id") or "") != bid
                ]
                break

    def _apply_stylus_settings(self) -> None:
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.apply_config()

    def _save_ink_strokes(self, result_id: int, strokes: list) -> None:
        test_id = self.app.active_test_id
        fid = self._selected_field_id()
        if not test_id or not fid or result_id is None:
            return
        try:
            save_ink_strokes(test_id, result_id, fid, strokes)
        except Exception as e:
            h.error(self, "手書き保存エラー", str(e))
            return
        for r in self._crop_grid_results:
            row = r.get("row") or {}
            if int(row.get("rowIndex") or 0) == int(result_id):
                r["ink_strokes"] = list(strokes)
                break

    # ==================== 画像タイル ====================

    def _clear_crop_grid(self) -> None:
        while self.crop_grid.count():
            item = self.crop_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_crop_grid(self) -> None:
        self._clear_crop_grid()
        self._ink_stacks = []
        if not self._crop_grid_results:
            self.crop_grid.addWidget(
                h.muted_label("「選択を画像表示」または外れ値一覧の「1枚」で回答欄画像を表示します"),
                0,
                0,
            )
            ctrl = getattr(self.app, "palette_controller", None)
            if ctrl is not None:
                ctrl.ensure_palette_visible()
            return

        fid = self._selected_field_id() or ""
        zoom = max(30, min(400, self.crop_controls.zoom_value())) / 100.0
        cols = 4
        for idx, item in enumerate(self._crop_grid_results):
            r, c = divmod(idx, cols)
            tile = self._make_crop_tile(item, fid, zoom)
            self.crop_grid.addWidget(tile, r, c, Qt.AlignTop | Qt.AlignLeft)
        # 余白を埋めるダミー
        self.crop_grid.setColumnStretch(cols, 1)
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.ensure_palette_visible()

    def _make_crop_tile(self, item: dict[str, Any], fid: str, zoom: float) -> QWidget:
        tile = QFrame()
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(2)

        if not item.get("ok"):
            tile.setStyleSheet(
                f"QFrame {{ background: {COLORS['danger_soft']}; border: 1px solid #fca5a5;"
                f" border-radius: 6px; }}"
            )
            err_parts = []
            if self.crop_controls.show_file_name():
                err_parts.append(str(item["row"].get("fileName") or "—"))
            if self.crop_controls.show_id():
                err_parts.append(f"ID: {item['row'].get('studentId') or '-'}")
            err_parts.append(str(item.get("error") or "読込失敗"))
            err = QLabel("\n".join(err_parts))
            err.setStyleSheet(f"color: {COLORS['danger']}; border: none; font-size: 10px;")
            err.setWordWrap(True)
            lay.addWidget(err)
            return tile

        row = item["row"]
        ans = row.get("answer_text") or ""
        deemed = self._is_deemed(fid, ans)
        border = COLORS["selection"] if deemed else COLORS["border"]
        bg = COLORS["selection_soft"] if deemed else COLORS["surface"]
        border_w = 3 if deemed else 2
        tile.setStyleSheet(
            f"QFrame {{ background: {bg}; border: {border_w}px solid {border};"
            f" border-radius: 6px; }}"
        )
        tile.setCursor(Qt.PointingHandCursor)

        pil = item["pil"]
        row_index = int(row.get("rowIndex") or 0)
        placement_meta = {
            "resultId": row_index,
            "fieldId": fid,
            "studentId": row.get("studentId"),
            "studentName": str(row.get("name") or row.get("studentName") or ""),
        }
        ink_stack = CropInkImageStack(
            pil_image=pil,
            field_id=fid,
            result_id=row_index,
            strokes=item.get("ink_strokes") or [],
            sheet_strokes=item.get("sheet_ink_strokes") or [],
            annotations=item.get("text_annotations") or [],
            zoom=zoom,
            placement_meta=placement_meta,
            on_strokes_changed=lambda s, rid=row_index: self._save_ink_strokes(rid, s),
            on_annotations_changed=lambda s, rid=row_index, f=fid: self.palette_save_annotations(
                rid, f, s
            ),
            on_sheet_box_deleted=lambda bid, rid=row_index: self._on_sheet_box_deleted(
                rid, bid
            ),
        )
        ink_stack.image_clicked.connect(
            lambda f=fid, rid=row_index, a=ans: self._on_crop_image_clicked(f, rid, a)
        )
        self._ink_stacks.append(ink_stack)
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.register_stack(ink_stack)
        lay.addWidget(ink_stack)

        if self.crop_controls.show_id():
            id_label = QLabel(f"ID: {row.get('studentId') or '-'}")
            id_label.setStyleSheet("border: none; font-size: 10px; font-weight: 700;")
            lay.addWidget(id_label)
        if self.crop_controls.show_file_name():
            file_label = QLabel(str(row.get("fileName") or ""))
            file_label.setStyleSheet(
                f"border: none; font-size: 9px; color: {COLORS['text_secondary']};"
            )
            file_label.setWordWrap(True)
            lay.addWidget(file_label)
        if self.crop_controls.show_ocr_text():
            ans_label = QLabel(ans)
            ans_label.setStyleSheet(
                f"border: none; font-size: 10px; color: {COLORS['accent']}; font-family: Consolas;"
            )
            ans_label.setWordWrap(True)
            lay.addWidget(ans_label)

        return tile

    def _purge_incorrect_from_grid(self) -> None:
        fid = self._selected_field_id()
        if not fid or not self.hide_incorrect_check.isChecked():
            return
        self._crop_grid_results = [
            r
            for r in self._crop_grid_results
            if not self._is_incorrect(fid, (r.get("row") or {}).get("answer_text", ""))
        ]
        for row in self._outlier_flat_rows:
            if self._should_skip_crop(row.get("answer_text", "")):
                row["show"] = False
                row["skip_img"] = True
        self._render_outlier_table()
        self._render_crop_grid()

    def _purge_deemed_from_outlier(self, applied_sources: list[str]) -> None:
        source_set = set(applied_sources or [])
        self._outlier_groups = [
            g for g in self._outlier_groups if g.get("answer_text") not in source_set
        ]
        self._crop_grid_results = [
            r
            for r in self._crop_grid_results
            if (r.get("row") or {}).get("answer_text") not in source_set
        ]
        self._build_outlier_flat_rows()
        self._render_outlier_table()
        self._render_crop_grid()
