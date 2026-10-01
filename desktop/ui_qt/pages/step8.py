"""⑧ 採点基準ページ（OCR置換・みなし採点・外れ値画像確認）。"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.criteria_repo import (
    aggregate_manual_grades_by_answer,
    apply_criteria_rules_to_results,
    get_answer_rows_for_pattern,
    get_outlier_answer_groups,
    import_manual_grades_into_criteria,
    merge_unique_with_criteria,
    save_grading_criteria,
    sort_criteria_rows,
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
from models.test_repo import (
    get_answer_fields,
    get_points_conn,
    rewrite_field_texts_for_result_ids,
)
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
    CropTileColumnPanel,
    configure_crop_image_scroll,
    main_table_frame,
    make_expanding,
)
from ui_qt.manual_grading_prefs import manual_auto_grading_link_enabled
from ui_qt.style import COLORS
from ui_qt.table_cells import (
    make_editable_item,
    make_readonly_item,
    make_toggle_item,
    set_toggle_checked,
    start_cell_edit,
    wire_toggle_columns,
)


# 画像選択レベル色（1点目〜）: 紫→青→緑→黄→橙→赤…
_SELECTION_LEVEL_COLORS: list[tuple[str, str]] = [
    ("#7c3aed", "#f3e8ff"),  # 紫
    ("#2563eb", "#dbeafe"),  # 青
    ("#16a34a", "#dcfce7"),  # 緑
    ("#ca8a04", "#fef9c3"),  # 黄
    ("#ea580c", "#ffedd5"),  # 橙
    ("#dc2626", "#fee2e2"),  # 赤
    ("#0891b2", "#cffafe"),  # シアン（7点以上）
    ("#db2777", "#fce7f3"),  # ピンク
]


def _selection_level_colors(level: int) -> tuple[str, str] | None:
    """level 1..N → (border, soft bg)。0 以下は未選択。"""
    if level <= 0:
        return None
    idx = (level - 1) % len(_SELECTION_LEVEL_COLORS)
    return _SELECTION_LEVEL_COLORS[idx]


class _PaneHeightGrip(QWidget):
    """欄の右下グラバー。掴むと高さを2倍にし、離した位置で確定する。"""

    def __init__(self, pane: QWidget, scroll: QScrollArea, *, minimum: int) -> None:
        super().__init__(pane)
        self._pane = pane
        self._scroll = scroll
        self._minimum = minimum
        self._press_y = 0
        self._base_h = minimum
        self.setFixedSize(28, 18)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("掴むとこの欄が2倍の高さになり、離した位置で確定します")

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setPen(QColor("#374151"))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "⠿")
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._press_y = int(event.globalPosition().y())
        self._base_h = max(self._minimum, self._pane.height())
        self._apply_height(self._base_h * 2)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        dy = int(event.globalPosition().y()) - self._press_y
        self._apply_height(self._base_h * 2 + dy)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        dy = int(event.globalPosition().y()) - self._press_y
        self._apply_height(self._base_h * 2 + dy)
        event.accept()

    def _apply_height(self, height: int) -> None:
        height = max(self._minimum, int(height))
        old = self._pane.height()
        if height == old:
            return
        self._pane.setFixedHeight(height)
        host = self._scroll.widget()
        if host is not None:
            host.updateGeometry()
            host.adjustSize()
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.value() + (height - old))


class Step8Page(QWidget):
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
        # result_id → 選択レベル(1..配点満点)。無し＝未選択
        self._selection_levels: dict[int, int] = {}
        # 他解答パターンへ移す: None | {"kind": "selected"|"unselected", "result_ids": set[int]}
        self._pattern_move_pending: dict[str, Any] | None = None

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        make_expanding(self._scroll)
        outer.addWidget(self._scroll, 1)

        body = QWidget()
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(8)

        root.addWidget(h.title_label("⑧ 採点基準の設定"))
        root.addWidget(
            h.muted_label("OCR置換・みなし採点で回答を整えてから、判定・得点の基準を設定します。")
        )

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("記述欄"))
        self.field_combo = QComboBox()
        self.field_combo.setMinimumWidth(240)
        self.field_combo.currentIndexChanged.connect(self._on_field_changed)
        toolbar.addWidget(self.field_combo)
        toolbar.addWidget(h.button("回答を集約", self._on_aggregate))
        toolbar.addWidget(h.button("AI原案", self._on_gemini))
        toolbar.addWidget(h.button("基準を保存", self._on_save_criteria, variant="primary"))
        toolbar.addWidget(
            h.button("手動採点から取込", self._on_import_manual_to_criteria)
        )
        toolbar.addWidget(
            h.button("手動採点へ反映", self._on_export_criteria_to_manual)
        )
        toolbar.addStretch()
        root.addLayout(toolbar)

        root.addWidget(self._build_ocr_replace_section())
        root.addWidget(self._build_deemed_box())

        self._criteria_pane = main_table_frame("回答パターン", self._build_criteria_table())
        self._outlier_pane = self._build_outlier_box()
        self._crop_pane = self._build_crop_box()
        self._attach_height_grip(self._criteria_pane, initial=280, minimum=120)
        self._attach_height_grip(self._outlier_pane, initial=220, minimum=120)
        self._attach_height_grip(self._crop_pane, initial=360, minimum=140)
        root.addWidget(self._criteria_pane)
        root.addWidget(self._outlier_pane)
        root.addWidget(self._crop_pane)
        root.addStretch()

    def _attach_height_grip(self, pane: QWidget, *, initial: int, minimum: int) -> None:
        pane.setFixedHeight(max(minimum, initial))
        lay = pane.layout()
        if lay is None:
            return
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch()
        grip = _PaneHeightGrip(pane, self._scroll, minimum=minimum)
        grip.setStyleSheet(
            f"background: {COLORS['border']}; border: 1px solid {COLORS['border_strong']};"
            " border-radius: 4px;"
        )
        row.addWidget(grip)
        lay.addLayout(row)

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
                "回答",
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
                self._force_incorrect_judgment(ans)
            else:
                self._incorrect_map(fid).pop(ans, None)
        self._sync_checks_to_rows()
        self._apply_criteria_table_styles()
        self.criteria_table.setCurrentCell(row, col)

    def _on_criteria_cell_clicked(self, row: int, col: int) -> None:
        if (
            self._pattern_move_pending is not None
            and col == 2
            and 0 <= row < len(self._criteria_rows)
        ):
            # 移動先選択中は回答パターン一覧の回答文字列タップで確認へ
            self._on_pattern_move_destination_chosen(row)
            return
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

    @staticmethod
    def _coerce_judgment_score(
        judgment: str, score: int, max_score: int
    ) -> tuple[str, int]:
        """判定と配点の整合: ×は必ず0、○/△は0不可。"""
        j = str(judgment or "").strip()
        if j in ("〇", "◯"):
            j = "○"
        elif j in ("x", "X", "✕", "✖"):
            j = "×"
        cap = max(1, int(max_score))
        try:
            sc = int(score)
        except (TypeError, ValueError):
            sc = 0
        sc = max(0, min(cap, sc))
        if j == "×":
            return j, 0
        if j == "○":
            if sc <= 0:
                sc = cap
            return j, sc
        if j == "△":
            if sc <= 0:
                # 部分点の下限。満点欄が1点なら1、それ以外は1〜満点-1の範囲で1
                sc = 1 if cap <= 1 else min(1, cap - 1)
                sc = max(1, sc)
            return j, sc
        return j, sc

    def _set_criteria_judgment(self, row: int, judgment: str) -> None:
        if 0 <= row < len(self._criteria_rows):
            self._criteria_rows[row]["judgment"] = judgment
            # ×以外にしたら不正解チェックを外す（手動採点の×連動と矛盾させない）
            if str(judgment or "").strip() != "×":
                self._clear_incorrect_for_answer(
                    str(self._criteria_rows[row].get("answer_text") or "")
                )

    def _set_criteria_score(self, row: int, score: int) -> None:
        if 0 <= row < len(self._criteria_rows):
            self._criteria_rows[row]["score"] = int(score)
            if int(score) > 0:
                self._clear_incorrect_for_answer(
                    str(self._criteria_rows[row].get("answer_text") or "")
                )

    def _build_outlier_box(self) -> QGroupBox:
        box = QGroupBox("外れ値・少数派回答")
        box.setStyleSheet(
            f"QGroupBox {{ background: #f8fafc; border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
        )
        lay = QVBoxLayout(box)
        lay.addWidget(
            h.caption_label(
                "「みなし」「不正解」「表示」列はクリックで切替。"
                "画像タップで配点段階の色を循環（紫→青→緑→…→解除。段階数＝配点満点）。"
                "みなしは表の列で切替えます。"
            )
        )

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("人数上限 ≤"))
        self.outlier_max_spin = QSpinBox()
        self.outlier_max_spin.setRange(1, 99)
        self.outlier_max_spin.setValue(2)
        ctrl.addWidget(self.outlier_max_spin)
        ctrl.addWidget(h.button("外れ値を検出", self._on_fetch_outliers))
        self.hide_incorrect_check = QCheckBox("不正解対象の回答の画像は表示しない")
        self.hide_incorrect_check.setChecked(True)
        self.hide_incorrect_check.toggled.connect(lambda _c: self._purge_incorrect_from_grid())
        ctrl.addWidget(self.hide_incorrect_check)
        ctrl.addWidget(h.button("なし（未回答）を確認", self._on_show_none_crops))
        ctrl.addWidget(h.button("表示を全選択", lambda: self._select_all_outlier(True)))
        ctrl.addWidget(h.button("表示を解除", lambda: self._select_all_outlier(False)))
        ctrl.addWidget(h.button("選択を画像表示", self._on_show_selected_crops, variant="primary"))
        ctrl.addWidget(
            self._make_soft_orange_button(
                "選択を他解答パターンに移す",
                self._on_start_move_selected_to_pattern,
            )
        )
        ctrl.addWidget(
            self._make_soft_orange_button(
                "非選択を他解答パターンに移す",
                self._on_start_move_unselected_to_pattern,
            )
        )
        ctrl.addStretch()
        lay.addLayout(ctrl)
        self._pattern_move_hint = QLabel("")
        self._pattern_move_hint.setWordWrap(True)
        self._pattern_move_hint.setStyleSheet(
            "color: #9a3412; background: #ffedd5; border: 1px solid #fdba74;"
            " border-radius: 6px; padding: 6px 8px; font-weight: 600;"
        )
        self._pattern_move_hint.hide()
        lay.addWidget(self._pattern_move_hint)

        self.outlier_table = QTableWidget(0, 8)
        self.outlier_table.setHorizontalHeaderLabels(
            ["みなし", "不正解", "回答", "人数", "表示", "生徒ID", "ファイル名", "操作"]
        )
        for i, w in enumerate([52, 52, 220, 48, 48, 90, 200, 60]):
            self.outlier_table.setColumnWidth(i, w)
        self.outlier_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.outlier_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.outlier_table.setMinimumHeight(72)
        make_expanding(self.outlier_table)
        wire_toggle_columns(
            self.outlier_table,
            (0, 1, 4),
            self._on_outlier_toggle,
        )
        self.outlier_table.cellClicked.connect(self._on_outlier_cell_clicked)
        lay.addWidget(self.outlier_table, 1)
        make_expanding(box)
        return box

    def _build_crop_box(self) -> QGroupBox:
        box = QGroupBox("回答欄画像")
        box.setStyleSheet(
            f"QGroupBox {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 8px; }}"
        )
        lay = QVBoxLayout(box)
        zoom_row = QHBoxLayout()
        self.crop_controls = CropDisplayControls()
        self.crop_controls.connect_zoom_changed(self._render_crop_grid)
        self.crop_controls.connect_meta_changed(self._render_crop_grid)
        zoom_row.addWidget(self.crop_controls, 1)
        lay.addLayout(zoom_row)

        self.crop_scroll = QScrollArea()
        self.crop_scroll.setWidgetResizable(True)
        self.crop_scroll.setMinimumHeight(120)
        configure_crop_image_scroll(self.crop_scroll)
        self.crop_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TabletTracking, True)
        self.crop_scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['surface']}; }}"
        )
        self.crop_panel = CropTileColumnPanel(margins=(8, 8, 8, 8), spacing=8)
        self.crop_scroll.setWidget(self.crop_panel)
        lay.addWidget(self.crop_scroll, 1)
        make_expanding(box)
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

    def select_field_id(self, field_id: str) -> bool:
        """記述欄コンボを field_id に合わせる。見つからなければ False。"""
        fid = str(field_id or "").strip()
        if not fid:
            return False
        for i, f in enumerate(self._fields):
            if str(f.get("id")) == fid:
                self.field_combo.blockSignals(True)
                self.field_combo.setCurrentIndex(i)
                self.field_combo.blockSignals(False)
                self._on_field_changed(i)
                return True
        return False

    def focus_field_from_search(self, field_id: str) -> None:
        """文字列検索「⑧採点基準の設定」: 当該記述欄を選択。"""
        from models.ink_repo import is_sheet_field_id

        if not self.app.active_test_id:
            return
        self.refresh()
        fid = str(field_id or "").strip()
        if is_sheet_field_id(fid):
            if self._fields:
                self.select_field_id(str(self._fields[0].get("id") or ""))
            h.info(
                self,
                "答案全体レイヤー",
                "ヒットは答案全体のテキストボックスです。"
                "⑧では先頭の記述欄を選択しました。一枚全容採点で位置を確認できます。",
            )
            return
        if not self.select_field_id(fid):
            h.warn(self, "記述欄なし", f"記述欄が見つかりません: {fid}")

    def show_crop_from_search(self, hit: dict[str, Any]) -> None:
        """文字列検索「記述欄画像」: 該当答案の該当欄クロップを表示。"""
        from models.ink_repo import is_sheet_field_id
        from models.test_repo import get_all_results
        from services.text_search import crop_field_id_for_hit

        if not self.app.active_test_id:
            return
        self.refresh()
        rid = int(hit.get("resultId") or 0)
        if not rid:
            h.warn(self, "エラー", "答案 ID が不正です。")
            return
        fid = crop_field_id_for_hit(hit, self._fields)
        if not fid:
            h.warn(self, "記述欄なし", "表示できる記述欄がありません。")
            return
        if is_sheet_field_id(str(hit.get("fieldId") or "")):
            # シートTBは欄クロップに投影表示される
            pass
        if not self.select_field_id(fid):
            h.warn(self, "記述欄なし", f"記述欄が見つかりません: {fid}")
            return
        row = next(
            (r for r in get_all_results(self.app.active_test_id) if int(r.get("id") or 0) == rid),
            None,
        )
        if row is None:
            h.warn(self, "答案なし", "対象の答案が見つかりません。")
            return
        texts = row.get("textMapping") or {}
        ans = str(texts.get(fid) or "").strip() or "なし"
        crop_row = {
            "rowIndex": rid,
            "studentId": row.get("studentId") or "",
            "fileName": row.get("fileName") or "",
            "fileId": row.get("sourcePath") or row.get("warpedPath") or "",
            "warpedPath": row.get("warpedPath") or "",
            "studentName": row.get("name") or "",
            "answer_text": ans,
        }
        self._load_crops_async([crop_row], allow_incorrect=True)

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

    def _draw_selected_ids(self) -> set[int]:
        """選択レベルが付いている答案 ID（描画・パターン移動用）。"""
        return {rid for rid, lv in self._selection_levels.items() if lv > 0}

    def _selection_level_of(self, result_id: int) -> int:
        return int(self._selection_levels.get(int(result_id), 0) or 0)

    def _cycle_selection_level(self, result_id: int) -> int:
        """配点満点ぶんの色段階＋解除をループ。戻り値は新しいレベル（0=解除）。"""
        rid = int(result_id)
        max_score = max(1, self._field_max_score())
        cur = self._selection_level_of(rid)
        nxt = cur + 1
        if nxt > max_score:
            self._selection_levels.pop(rid, None)
            return 0
        self._selection_levels[rid] = nxt
        return nxt

    def _on_crop_image_clicked(self, fid: str, result_id: int, ans: str) -> None:
        del fid, ans  # 個別選択へ。みなしは表の列で切替
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.set_active_result_id(result_id)
        self._cycle_selection_level(result_id)
        # 全再描画するとスクロールが最上端に戻るため、枠色だけ更新する
        self._apply_crop_tile_selection_styles()
        self._apply_criteria_table_styles()
        self._apply_outlier_row_highlights()
        if ctrl is not None:
            ctrl.notify_draw_selection_changed()

    def _toggle_incorrect(self, fid: str, ans: str) -> None:
        m = self._incorrect_map(fid)
        if m.get(ans):
            m.pop(ans, None)
        else:
            m[ans] = True
            self._force_incorrect_judgment(ans)
        self._sync_checks_to_rows()
        self._refresh_check_views()
        self._purge_incorrect_from_grid()

    def _clear_incorrect_for_answer(self, ans: str) -> None:
        fid = self._selected_field_id()
        if not fid or not ans:
            return
        if ans not in self._incorrect_map(fid):
            return
        self._incorrect_map(fid).pop(ans, None)
        self._sync_checks_to_rows()
        # トグル表示だけ更新（表の全面再構築はしない）
        t = getattr(self, "criteria_table", None)
        if t is not None:
            for i, row in enumerate(self._criteria_rows):
                if str(row.get("answer_text") or "") != ans:
                    continue
                item = t.item(i, 1)
                if item is not None:
                    set_toggle_checked(item, False)

    def _force_incorrect_judgment(self, ans: str) -> None:
        """不正解☑ → 判定×・得点0（手動採点と同一の基準値）。"""
        key = str(ans or "")
        if not key:
            return
        t = getattr(self, "criteria_table", None)
        for i, row in enumerate(self._criteria_rows):
            if str(row.get("answer_text") or "") != key:
                continue
            row["judgment"] = "×"
            row["score"] = 0
            if t is None:
                continue
            combo = find_judgment_combo(t, i)
            if combo is not None:
                combo.blockSignals(True)
                combo.setCurrentText("×")
                combo.blockSignals(False)
            score_w = find_score_widget(t, i)
            if score_w is not None:
                score_w.set_value(0)

    def _apply_criteria_grades_to_results(
        self, rules: list[dict[str, Any]]
    ) -> int:
        """採点基準の判定・配点を、手動採点と同じ results へ回答文字列単位で書き込む。"""
        fid = self._selected_field_id()
        test_id = self.app.active_test_id
        if not fid or not test_id or not rules:
            return 0
        return apply_criteria_rules_to_results(test_id, fid, rules)

    def _sync_checks_to_rows(self) -> None:
        fid = self._selected_field_id()
        if not fid:
            return
        for row in self._criteria_rows:
            ans = row["answer_text"]
            row["deemed"] = self._is_deemed(fid, ans)
            row["incorrect"] = self._is_incorrect(fid, ans)
            if row["incorrect"]:
                row["judgment"] = "×"
                row["score"] = 0

    def _refresh_check_views(self) -> None:
        self._apply_criteria_table_styles()
        self._render_outlier_table()
        self._render_crop_grid()

    def _sync_criteria_from_widgets(self) -> None:
        """画面上の判定・配点を回答文字列で書き戻す。

        みなしやパターン移動で行が消えても、行番号ではなく回答文字列で
        残ったパターン（母体）の判定・配点を維持する。
        """
        t = self.criteria_table
        by_answer: dict[str, dict[str, Any]] = {}
        for i in range(t.rowCount()):
            ans_item = t.item(i, 2)
            if ans_item is None:
                continue
            ans = ans_item.text().strip()
            if not ans:
                continue
            patch: dict[str, Any] = {}
            combo = find_judgment_combo(t, i)
            if combo is not None:
                patch["judgment"] = combo.currentText()
            score_w = find_score_widget(t, i)
            if score_w is not None:
                patch["score"] = score_w.value()
            reason_item = t.item(i, 8)
            if reason_item is not None:
                patch["reason"] = reason_item.text().strip()
            if patch:
                by_answer[ans] = patch
        if not by_answer:
            return
        for row in self._criteria_rows:
            ans = str(row.get("answer_text") or "").strip()
            patch = by_answer.get(ans)
            if patch:
                row.update(patch)

    def _selected_answer_level_map(self) -> dict[str, int]:
        """回答文字列 → その中で最も高い選択レベル。"""
        levels: dict[str, int] = {}
        if not self._selection_levels:
            return levels

        def _note(ans: str, rid: int) -> None:
            lv = self._selection_level_of(rid)
            if lv <= 0:
                return
            key = str(ans or "")
            if lv > levels.get(key, 0):
                levels[key] = lv

        for item in self._crop_grid_results:
            row = item.get("row") or {}
            _note(str(row.get("answer_text") or ""), int(row.get("rowIndex") or 0))
        for row in self._outlier_flat_rows:
            _note(str(row.get("answer_text") or ""), int(row.get("rowIndex") or 0))
        return levels

    def _paint_item_row_bg(
        self,
        table: QTableWidget,
        row: int,
        columns: tuple[int, ...],
        bg: str | None,
    ) -> None:
        color = QColor(bg) if bg else QColor()
        for c in columns:
            item = table.item(row, c)
            if item is None:
                continue
            if bg:
                item.setBackground(color)
            else:
                item.setData(Qt.ItemDataRole.BackgroundRole, None)

    def _apply_criteria_table_styles(self) -> None:
        fid = self._selected_field_id() or ""
        canonical = self._canonical()
        answer_levels = self._selected_answer_level_map()
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

            sel_colors = _selection_level_colors(answer_levels.get(str(ans), 0))
            if sel_colors is not None:
                bg = sel_colors[1]
            elif row.get("deemed") or self._is_deemed(fid, ans):
                bg = COLORS["accent_soft"]
            elif row.get("incorrect") or self._is_incorrect(fid, ans):
                bg = COLORS["danger_soft"]
            else:
                bg = None
            self._paint_item_row_bg(t, i, (0, 1, 2, 3, 6, 7, 8), bg)
        t.blockSignals(False)

    def _apply_outlier_row_highlights(self) -> None:
        """外れ値一覧で、画像選択中の同一答案行を同じ段階色にする。"""
        if not hasattr(self, "outlier_table"):
            return
        t = self.outlier_table
        t.blockSignals(True)
        for i, row in enumerate(self._outlier_flat_rows):
            rid = int(row.get("rowIndex") or 0)
            colors = _selection_level_colors(self._selection_level_of(rid)) if rid else None
            bg = colors[1] if colors else None
            self._paint_item_row_bg(t, i, (0, 1, 2, 3, 4, 5, 6), bg)
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
        self._cancel_pattern_move_mode()
        self._outlier_groups = []
        self._outlier_flat_rows = []
        self._crop_grid_results = []
        self._selection_levels.clear()
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
                self._force_incorrect_judgment(ans)
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
        # 不正解☑は必ず ×/0 として保存・手動採点へ反映
        incorrect_answers = {
            str(row.get("answer_text") or "")
            for row in self._criteria_rows
            if self._is_incorrect(fid, str(row.get("answer_text") or ""))
        }
        for ans in incorrect_answers:
            self._force_incorrect_judgment(ans)
        self._sync_checks_to_rows()

        max_score = self._field_max_score()
        rules = []
        corrected = 0
        for row in self._criteria_rows:
            judgment = str(row.get("judgment") or "").strip()
            if not judgment:
                continue
            try:
                score = int(row.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            ans = str(row.get("answer_text") or "")
            if ans in incorrect_answers:
                judgment = "×"
                score = 0
            before = (judgment, score)
            judgment, score = self._coerce_judgment_score(judgment, score, max_score)
            if (judgment, score) != before:
                corrected += 1
            row["judgment"] = judgment
            row["score"] = score
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
            graded = 0
            if manual_auto_grading_link_enabled():
                graded = self._apply_criteria_grades_to_results(rules)
            sort_criteria_rows(self._criteria_rows)
            self._render_criteria_table()
            msg = f"採点基準を {len(rules)} 件保存しました。"
            if corrected:
                msg += (
                    f"\n判定と配点の不整合 {corrected} 件を是正しました"
                    "（×→0点、○/△で0点→配点を付与）。"
                )
            if graded:
                msg += f"\n手動採点用データへ {graded} 件の判定・配点を反映しました（○/△/×）。"
            h.info(self, "保存完了", msg)
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_import_manual_to_criteria(self) -> None:
        """手動採点の多数決を採点基準へ取り込み（DB保存。手動結果へは書き戻さない）。"""
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        preview = aggregate_manual_grades_by_answer(self.app.active_test_id, fid)
        if not preview.get("winner_count"):
            h.warn(
                self,
                "取込不可",
                "手動採点で確定判定（○△×）が付いた回答パターンがありません。",
            )
            return
        lines = [
            f"確定パターン {preview['winner_count']} 件を採点基準へ取り込みます。",
            "票が割れた回答は既存基準を維持します。",
            "この操作では手動採点結果へは書き戻しません。",
        ]
        if preview.get("tied_count"):
            lines.append(f"票割れ（未決定）: {preview['tied_count']} 件")
            for p in (preview.get("tied") or [])[:5]:
                lines.append(
                    f"・{p.get('answer_text')}: {p.get('vote_summary')}"
                )
        ask = QMessageBox.question(
            self,
            "手動採点から取込",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ask != QMessageBox.StandardButton.Yes:
            return
        try:
            res = import_manual_grades_into_criteria(self.app.active_test_id, fid)
            self._aggregate()
            msg = f"手動採点から {res.get('saved_count', 0)} 件を採点基準へ取り込みました。"
            if res.get("tied_count"):
                msg += f"\n票割れで見送ったパターン: {res['tied_count']} 件"
            h.info(self, "取込完了", msg)
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_export_criteria_to_manual(self) -> None:
        """画面上の採点基準（未保存含む）を手動採点 results へ反映。"""
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        self._sync_criteria_from_widgets()
        max_score = self._field_max_score()
        rules: list[dict[str, Any]] = []
        for row in self._criteria_rows:
            judgment = str(row.get("judgment") or "").strip()
            if not judgment:
                continue
            try:
                score = int(row.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            if self._is_incorrect(fid, str(row.get("answer_text") or "")):
                judgment, score = "×", 0
            judgment, score = self._coerce_judgment_score(judgment, score, max_score)
            row["judgment"] = judgment
            row["score"] = score
            rules.append(
                {
                    "answer_text": row["answer_text"],
                    "judgment": judgment,
                    "score": score,
                }
            )
        if not rules:
            h.warn(self, "反映不可", "判定が入った採点基準がありません。")
            return
        ask = QMessageBox.question(
            self,
            "手動採点へ反映",
            f"採点基準 {len(rules)} 件の判定・配点を、同じ回答文字列の答案へ上書きします。\n"
            "手動で付けた例外判定も基準どおりに置き換わります。続行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ask != QMessageBox.StandardButton.Yes:
            return
        try:
            n = self._apply_criteria_grades_to_results(rules)
            self._render_criteria_table()
            h.info(
                self,
                "反映完了",
                f"手動採点用データへ {n} 件の判定・配点を反映しました。",
            )
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
            h.info(self, "検出完了", f"{len(self._outlier_groups)} 種類の外れ値回答（人数 ≤ {max_count}）")

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
        self._apply_outlier_row_highlights()

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

    @staticmethod
    def _make_soft_orange_button(text: str, on_click) -> QPushButton:
        btn = QPushButton(text)
        btn.setStyleSheet(
            "QPushButton {"
            " background: #ffedd5; color: #9a3412; border: 1px solid #fb923c;"
            " border-radius: 6px; font-weight: 700; padding: 6px 10px;"
            "}"
            "QPushButton:hover { background: #fed7aa; }"
            "QPushButton:pressed { background: #fdba74; }"
        )
        btn.clicked.connect(on_click)
        return btn

    def _cancel_pattern_move_mode(self) -> None:
        self._pattern_move_pending = None
        if hasattr(self, "_pattern_move_hint"):
            self._pattern_move_hint.hide()
            self._pattern_move_hint.clear()

    def _set_pattern_move_hint(self, text: str) -> None:
        self._pattern_move_hint.setText(text)
        self._pattern_move_hint.setVisible(bool(text))

    def _collect_pattern_move_result_ids(self, *, selected: bool) -> list[int]:
        """表示中タイルの選択／非選択を優先。無ければ外れ値表の表示チェックを使う。"""
        if self._crop_grid_results:
            ids: list[int] = []
            for item in self._crop_grid_results:
                if not item.get("ok"):
                    continue
                rid = int((item.get("row") or {}).get("rowIndex") or 0)
                if rid <= 0:
                    continue
                is_sel = rid in self._draw_selected_ids()
                if selected == is_sel:
                    ids.append(rid)
            return ids
        ids = []
        for row in self._outlier_flat_rows:
            if row.get("skip_img"):
                continue
            rid = int(row.get("rowIndex") or 0)
            if rid <= 0:
                continue
            is_sel = bool(row.get("show"))
            if selected == is_sel:
                ids.append(rid)
        return ids

    def _on_start_move_selected_to_pattern(self) -> None:
        self._begin_pattern_move(selected=True)

    def _on_start_move_unselected_to_pattern(self) -> None:
        self._begin_pattern_move(selected=False)

    def _begin_pattern_move(self, *, selected: bool) -> None:
        if not self.app.require_active_test() or not self._selected_field_id():
            return
        kind = "selected" if selected else "unselected"
        if self._pattern_move_pending is not None:
            was = self._pattern_move_pending.get("kind")
            self._cancel_pattern_move_mode()
            if was == kind:
                return
        ids = self._collect_pattern_move_result_ids(selected=selected)
        label = "選択中" if selected else "非選択"
        if not ids:
            if selected:
                h.warn(
                    self,
                    "未選択",
                    "移す回答がありません。\n"
                    "画像を表示してタイルをクリックで選択するか、"
                    "外れ値一覧の「表示」にチェックを入れてください。",
                )
            else:
                h.warn(
                    self,
                    "対象なし",
                    "非選択の回答がありません。\n"
                    "画像表示中なら、残したいタイルだけ選択してから実行してください。",
                )
            return
        if not self._criteria_rows:
            h.warn(self, "一覧なし", "先に回答を集約して回答パターン一覧を表示してください。")
            return
        self._pattern_move_pending = {"kind": kind, "result_ids": set(ids)}
        self._set_pattern_move_hint(
            f"{label} {len(ids)} 件の移動先を選んでください。"
            "上の回答パターン一覧の「回答」列をタップ → 確認後に移動します。"
            "解除するときは同じボタンをもう一度押してください。"
        )
        self._scroll_to_criteria_table()

    def _scroll_to_criteria_table(self) -> None:
        if hasattr(self, "_scroll") and hasattr(self, "criteria_table"):
            self._scroll.ensureWidgetVisible(self.criteria_table, 0, 24)

    def _on_pattern_move_destination_chosen(self, row: int) -> None:
        pending = self._pattern_move_pending
        if not pending or row < 0 or row >= len(self._criteria_rows):
            return
        dest = str(self._criteria_rows[row].get("answer_text") or "").strip() or "なし"
        ids = sorted(int(i) for i in (pending.get("result_ids") or set()) if int(i) > 0)
        kind = pending.get("kind") or "selected"
        label = "選択中" if kind == "selected" else "非選択"
        if not ids:
            self._cancel_pattern_move_mode()
            h.warn(self, "対象なし", "移動対象が空です。")
            return
        ask = QMessageBox.question(
            self,
            "他解答パターンへ移動",
            f"{label}の {len(ids)} 件を、次の回答パターンへ移します。\n\n"
            f"移動先: {dest}\n\n"
            "OCR の語順違いなどで同じグループに混ざった回答を、"
            "別パターンとして集計し直します。よろしいですか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ask != QMessageBox.StandardButton.Yes:
            return
        self._apply_pattern_move(ids, dest)

    def _apply_pattern_move(self, result_ids: list[int], dest_answer: str) -> None:
        fid = self._selected_field_id()
        if not fid or not self.app.active_test_id:
            return
        try:
            n = rewrite_field_texts_for_result_ids(
                self.app.active_test_id, fid, result_ids, dest_answer
            )
        except Exception as e:
            h.error(self, "移動エラー", str(e))
            return
        self._cancel_pattern_move_mode()
        self._selection_levels.clear()
        self._aggregate()
        self._on_fetch_outliers(silent=True)
        # 移動後は移動先パターンの画像を再表示
        if not self._should_skip_crop(dest_answer):
            self._show_answer_pattern_crops(dest_answer)
        h.info(
            self,
            "移動完了",
            f"{n} 件を「{dest_answer}」へ移し、回答パターンを再集約しました。",
        )

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
        self.crop_panel.set_message(f"画像を読み込み中…（{len(rows)}枚）")

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
        if hasattr(self, "_scroll") and hasattr(self, "_crop_pane"):
            self._scroll.ensureWidgetVisible(self._crop_pane, 0, 32)

    def viewer_scroll(self) -> QScrollArea:
        return self.crop_scroll

    def palette_ink_stacks(self) -> list[CropInkImageStack]:
        return self._ink_stacks

    def palette_field_id(self) -> str:
        return self._selected_field_id() or ""

    def palette_draw_selected_ids(self) -> list[int]:
        return sorted(self._draw_selected_ids())

    def palette_set_pen_ui_locked(self, locked: bool) -> None:
        """ペンON＋描画選択ありのときズーム行を無効化（外れ値表より上は可）。"""
        controls = getattr(self, "crop_controls", None)
        if controls is not None:
            controls.setEnabled(not bool(locked))

    def palette_maximize_write_items(self) -> list[dict[str, Any]]:
        fid = self._selected_field_id() or ""
        items: list[dict[str, Any]] = []
        for it in self._crop_grid_results:
            if not it.get("ok"):
                continue
            row = it.get("row") or {}
            rid = int(row.get("rowIndex") or 0)
            if rid not in self._draw_selected_ids():
                continue
            items.append(
                {
                    "result_id": rid,
                    "field_id": fid,
                    "pil": it["pil"],
                    "file_name": str(row.get("fileName") or ""),
                    "student_id": str(row.get("studentId") or ""),
                    "student_name": str(row.get("name") or row.get("studentName") or ""),
                    "ink_strokes": list(it.get("ink_strokes") or []),
                    "sheet_ink_strokes": list(it.get("sheet_ink_strokes") or []),
                    "text_annotations": list(it.get("text_annotations") or []),
                }
            )
        items.sort(
            key=lambda x: (
                str(x.get("file_name") or "").lower(),
                int(x.get("result_id") or 0),
            )
        )
        return items

    def palette_save_ink_strokes(
        self, result_id: int, field_id: str, strokes: list
    ) -> None:
        del field_id
        self._save_ink_strokes(result_id, strokes)

    def palette_refresh_after_maximize_write(self) -> None:
        self._render_crop_grid()

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
        self.crop_panel.clear_tiles()
        self._ink_stacks = []

    def _crop_tile_frame_style(self, *, level: int = 0, deemed: bool = False) -> str:
        colors = _selection_level_colors(level)
        if colors is not None:
            border, bg = colors
            border_w = 3
        elif deemed:
            border = COLORS["selection"]
            bg = COLORS["selection_soft"]
            border_w = 3
        else:
            border = COLORS["border"]
            bg = COLORS["surface"]
            border_w = 2
        return (
            f"QFrame {{ background: {bg}; border: {border_w}px solid {border};"
            f" border-radius: 6px; }}"
        )

    def _apply_crop_tile_selection_styles(self) -> None:
        """選択状態だけ反映（再構築しない＝スクロール位置を維持）。"""
        fid = self._selected_field_id() or ""
        ans_by_id = {
            int((item.get("row") or {}).get("rowIndex") or 0): str(
                (item.get("row") or {}).get("answer_text") or ""
            )
            for item in self._crop_grid_results
            if item.get("ok")
        }
        for stack in self._ink_stacks:
            tile = stack.parentWidget()
            if tile is None:
                continue
            rid = int(getattr(stack, "result_id", 0) or 0)
            ans = ans_by_id.get(rid, "")
            tile.setStyleSheet(
                self._crop_tile_frame_style(
                    level=self._selection_level_of(rid),
                    deemed=self._is_deemed(fid, ans),
                )
            )

    def _render_crop_grid(self) -> None:
        bar = self.crop_scroll.verticalScrollBar()
        hbar = self.crop_scroll.horizontalScrollBar()
        saved_v = bar.value() if bar is not None else 0
        saved_h = hbar.value() if hbar is not None else 0
        had_tiles = bool(self._ink_stacks)

        self._clear_crop_grid()
        if not self._crop_grid_results:
            self._selection_levels.clear()
            self.crop_panel.set_message(
                "「選択を画像表示」または外れ値一覧の「1枚」で回答欄画像を表示します"
            )
            ctrl = getattr(self.app, "palette_controller", None)
            if ctrl is not None:
                ctrl.ensure_palette_visible()
                ctrl.notify_draw_selection_changed()
            self._apply_criteria_table_styles()
            self._apply_outlier_row_highlights()
            return

        visible_ids = {
            int((item.get("row") or {}).get("rowIndex") or 0)
            for item in self._crop_grid_results
            if item.get("ok")
        }
        self._selection_levels = {
            rid: lv for rid, lv in self._selection_levels.items() if rid in visible_ids
        }

        fid = self._selected_field_id() or ""
        zoom = max(30, min(400, self.crop_controls.zoom_value())) / 100.0
        for idx, item in enumerate(self._crop_grid_results):
            tile = self._make_crop_tile(item, fid, zoom)
            self.crop_panel.add_tile(tile, idx)
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.ensure_palette_visible()
            ctrl.notify_draw_selection_changed()
        self._apply_criteria_table_styles()
        self._apply_outlier_row_highlights()

        if had_tiles and (saved_v or saved_h):

            def _restore_scroll() -> None:
                if bar is not None:
                    bar.setValue(saved_v)
                if hbar is not None:
                    hbar.setValue(saved_h)

            QTimer.singleShot(0, _restore_scroll)

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
        row_index = int(row.get("rowIndex") or 0)
        tile.setStyleSheet(
            self._crop_tile_frame_style(
                level=self._selection_level_of(row_index),
                deemed=deemed,
            )
        )
        tile.setCursor(Qt.PointingHandCursor)

        pil = item["pil"]
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
