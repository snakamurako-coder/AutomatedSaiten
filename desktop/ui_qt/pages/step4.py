"""④ 採点基準ページ（OCR置換・みなし採点・外れ値画像確認）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
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
    QSlider,
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
from models.test_repo import get_answer_fields
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
from ui_qt.helpers import pil_to_qpixmap
from ui_qt.style import COLORS

_CHECK = "☑"
_UNCHECK = "☐"


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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
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

        root.addWidget(self._build_ocr_replace_box())
        root.addWidget(self._build_deemed_box())
        root.addWidget(self._build_criteria_table())
        root.addWidget(self._build_edit_box())
        root.addWidget(self._build_outlier_box())

    # ==================== UI 構築 ====================

    def _build_ocr_replace_box(self) -> QGroupBox:
        box = QGroupBox("OCRテキスト置換")
        lay = QVBoxLayout(box)
        lay.addWidget(
            h.caption_label(
                "置換ルール保存はルールのみ。「置換を適用して再集約」で採点結果のテキスト列を書き換えます。"
            )
        )
        self.ocr_table = QTableWidget(0, 3)
        self.ocr_table.setHorizontalHeaderLabels(["検索", "置換後", "正規表現"])
        self.ocr_table.setColumnWidth(0, 240)
        self.ocr_table.setColumnWidth(1, 240)
        self.ocr_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.ocr_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.ocr_table.setFixedHeight(140)
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
        return box

    def _build_deemed_box(self) -> QGroupBox:
        box = QGroupBox("みなし採点")
        lay = QVBoxLayout(box)
        lay.addWidget(
            h.caption_label(
                "正答例を指定し、表の「みなし」「不正解」列をダブルクリックで選択 → 適用で正答例に統一します。"
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
        self.criteria_table = QTableWidget(0, 7)
        self.criteria_table.setHorizontalHeaderLabels(
            ["みなし", "不正解", "解答", "人数", "判定", "得点", "備考"]
        )
        widths = [52, 52, 280, 52, 52, 52, 260]
        for i, w in enumerate(widths):
            self.criteria_table.setColumnWidth(i, w)
        self.criteria_table.horizontalHeader().setStretchLastSection(True)
        self.criteria_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.criteria_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.criteria_table.setMinimumHeight(240)
        self.criteria_table.currentCellChanged.connect(lambda *_: self._on_criteria_select())
        self.criteria_table.cellDoubleClicked.connect(self._on_criteria_double_click)
        return self.criteria_table

    def _build_edit_box(self) -> QGroupBox:
        box = QGroupBox("選択行の編集")
        row = QHBoxLayout(box)
        row.addWidget(QLabel("判定(○/△/×)"))
        self.edit_judgment = QLineEdit()
        self.edit_judgment.setFixedWidth(90)
        row.addWidget(self.edit_judgment)
        row.addWidget(QLabel("得点"))
        self.edit_score = QLineEdit()
        self.edit_score.setFixedWidth(70)
        row.addWidget(self.edit_score)
        row.addWidget(QLabel("備考"))
        self.edit_reason = QLineEdit()
        row.addWidget(self.edit_reason, 1)
        row.addWidget(h.button("選択行に適用", self._on_apply_edit))
        return box

    def _build_outlier_box(self) -> QGroupBox:
        box = QGroupBox("外れ値・少数派解答の確認（回答欄画像）")
        lay = QVBoxLayout(box)
        lay.addWidget(
            h.caption_label(
                "「みなし」「不正解」列はダブルクリックで切替。画像タイルクリックでもみなしを切替えられます。"
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
        zoom_row.addWidget(QLabel("表示倍率"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(30, 400)
        self.zoom_slider.setValue(100)
        self.zoom_slider.valueChanged.connect(lambda _v: self._render_crop_grid())
        zoom_row.addWidget(self.zoom_slider, 1)
        self.zoom_label = QLabel("100")
        self.zoom_slider.valueChanged.connect(lambda v: self.zoom_label.setText(str(v)))
        zoom_row.addWidget(self.zoom_label)
        lay.addLayout(zoom_row)

        self.outlier_table = QTableWidget(0, 8)
        self.outlier_table.setHorizontalHeaderLabels(
            ["みなし", "不正解", "解答", "人数", "表示", "生徒ID", "ファイル名", "操作"]
        )
        for i, w in enumerate([52, 52, 220, 48, 48, 90, 200, 60]):
            self.outlier_table.setColumnWidth(i, w)
        self.outlier_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.outlier_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.outlier_table.setFixedHeight(190)
        self.outlier_table.cellDoubleClicked.connect(self._on_outlier_double_click)
        lay.addWidget(self.outlier_table)

        self.crop_scroll = QScrollArea()
        self.crop_scroll.setWidgetResizable(True)
        self.crop_scroll.setMinimumHeight(320)
        self.crop_scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['sidebar']}; }}"
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
        self._render_criteria_table()
        self._render_outlier_table()
        self._render_crop_grid()

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

    def _render_criteria_table(self) -> None:
        fid = self._selected_field_id() or ""
        canonical = self._canonical()
        t = self.criteria_table
        t.blockSignals(True)
        t.setRowCount(len(self._criteria_rows))
        for i, row in enumerate(self._criteria_rows):
            ans = row.get("answer_text", "")
            deemed_mark = (
                "—"
                if canonical and ans == canonical
                else (_CHECK if self._is_deemed(fid, ans) else _UNCHECK)
            )
            incorrect_mark = _CHECK if self._is_incorrect(fid, ans) else _UNCHECK
            values = [
                deemed_mark,
                incorrect_mark,
                ans,
                str(row.get("count", 0)),
                str(row.get("judgment", "") or ""),
                str(row.get("score", "") or ""),
                str(row.get("reason", "") or ""),
            ]
            bg = None
            if row.get("deemed") or self._is_deemed(fid, ans):
                bg = COLORS["accent_soft"]
            elif row.get("incorrect") or self._is_incorrect(fid, ans):
                bg = COLORS["danger_soft"]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c in (0, 1, 3, 4, 5):
                    item.setTextAlignment(Qt.AlignCenter)
                if bg:
                    from PySide6.QtGui import QColor

                    item.setBackground(QColor(bg))
                t.setItem(i, c, item)
        t.blockSignals(False)

    def _on_criteria_double_click(self, row: int, col: int) -> None:
        if col not in (0, 1) or row >= len(self._criteria_rows):
            return
        fid = self._selected_field_id()
        if not fid:
            return
        ans = self._criteria_rows[row]["answer_text"]
        if col == 0:
            self._toggle_deemed(fid, ans)
        else:
            self._toggle_incorrect(fid, ans)
        self.criteria_table.selectRow(row)

    def _on_criteria_select(self) -> None:
        row = self.criteria_table.currentRow()
        if row < 0 or row >= len(self._criteria_rows):
            return
        r = self._criteria_rows[row]
        self.edit_judgment.setText(str(r.get("judgment", "") or ""))
        self.edit_score.setText(str(r.get("score", "") or ""))
        self.edit_reason.setText(str(r.get("reason", "") or ""))

    def _on_apply_edit(self) -> None:
        row = self.criteria_table.currentRow()
        if row < 0 or row >= len(self._criteria_rows):
            return
        self._criteria_rows[row]["judgment"] = self.edit_judgment.text().strip()
        score_val = self.edit_score.text().strip()
        try:
            self._criteria_rows[row]["score"] = int(score_val) if score_val else ""
        except ValueError:
            h.error(self, "入力エラー", "得点は整数で入力してください。")
            return
        self._criteria_rows[row]["reason"] = self.edit_reason.text().strip()
        self._render_criteria_table()
        self.criteria_table.selectRow(row)

    def _on_save_criteria(self) -> None:
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
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
        t.setRowCount(len(self._outlier_flat_rows))
        for i, row in enumerate(self._outlier_flat_rows):
            ans = row["answer_text"]
            values = [
                _CHECK if self._is_deemed(fid, ans) else _UNCHECK,
                _CHECK if self._is_incorrect(fid, ans) else _UNCHECK,
                ans,
                str(row["group_count"]),
                _CHECK if row.get("show") and not row.get("skip_img") else _UNCHECK,
                str(row.get("studentId") or "-"),
                str(row.get("fileName") or ""),
                "—" if row.get("skip_img") else "1枚",
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c in (0, 1, 3, 4, 7):
                    item.setTextAlignment(Qt.AlignCenter)
                t.setItem(i, c, item)

    def _on_outlier_double_click(self, row: int, col: int) -> None:
        if row >= len(self._outlier_flat_rows):
            return
        flat = self._outlier_flat_rows[row]
        fid = self._selected_field_id()
        if not fid:
            return
        ans = flat["answer_text"]
        if col == 0:
            self._toggle_deemed(fid, ans)
        elif col == 1:
            self._toggle_incorrect(fid, ans)
        elif col == 4:
            if flat.get("skip_img"):
                return
            flat["show"] = not flat.get("show")
            self._render_outlier_table()
        elif col == 7 and not flat.get("skip_img"):
            self._load_crops_async([flat], allow_incorrect=True)

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
            self._crop_grid_results = results
            self._render_crop_grid()

        h.run_in_thread(self, lambda: load_crops_for_rows(rows, field), done)

    # ==================== 画像タイル ====================

    def _clear_crop_grid(self) -> None:
        while self.crop_grid.count():
            item = self.crop_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_crop_grid(self) -> None:
        self._clear_crop_grid()
        if not self._crop_grid_results:
            self.crop_grid.addWidget(
                h.muted_label("「選択を画像表示」または外れ値一覧の「1枚」で回答欄画像を表示します"),
                0,
                0,
            )
            return

        fid = self._selected_field_id() or ""
        zoom = max(30, min(400, self.zoom_slider.value())) / 100.0
        cols = 4
        for idx, item in enumerate(self._crop_grid_results):
            r, c = divmod(idx, cols)
            tile = self._make_crop_tile(item, fid, zoom)
            self.crop_grid.addWidget(tile, r, c, Qt.AlignTop | Qt.AlignLeft)
        # 余白を埋めるダミー
        self.crop_grid.setColumnStretch(cols, 1)

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
            err = QLabel(f"{item['row'].get('fileName', '—')}\n{item.get('error', '読込失敗')}")
            err.setStyleSheet(f"color: {COLORS['danger']}; border: none; font-size: 10px;")
            err.setWordWrap(True)
            lay.addWidget(err)
            return tile

        row = item["row"]
        ans = row.get("answer_text") or ""
        deemed = self._is_deemed(fid, ans)
        border = COLORS["accent"] if deemed else COLORS["border"]
        bg = COLORS["accent_soft"] if deemed else COLORS["surface"]
        tile.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 2px solid {border}; border-radius: 6px; }}"
        )
        tile.setCursor(Qt.PointingHandCursor)

        pil = item["pil"]
        w = max(40, int(pil.width * zoom))
        pix: QPixmap = pil_to_qpixmap(pil).scaledToWidth(w, Qt.SmoothTransformation)
        img = QLabel()
        img.setPixmap(pix)
        img.setStyleSheet("border: none;")
        lay.addWidget(img)

        id_label = QLabel(f"ID: {row.get('studentId') or '-'}")
        id_label.setStyleSheet("border: none; font-size: 10px; font-weight: 700;")
        lay.addWidget(id_label)
        file_label = QLabel(str(row.get("fileName") or "")[:28])
        file_label.setStyleSheet(
            f"border: none; font-size: 9px; color: {COLORS['text_secondary']};"
        )
        lay.addWidget(file_label)
        ans_label = QLabel(ans[:40])
        ans_label.setStyleSheet(
            f"border: none; font-size: 10px; color: {COLORS['accent']}; font-family: Consolas;"
        )
        ans_label.setWordWrap(True)
        lay.addWidget(ans_label)

        def click_handler(_event, a=ans):
            f = self._selected_field_id()
            if f:
                self._toggle_deemed(f, a)

        tile.mousePressEvent = click_handler  # type: ignore[method-assign]
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
