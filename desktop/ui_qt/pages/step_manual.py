"""手動採点ページ（空DB作成 → 画像を見ながら ○△×。⑦ OCR は任意）。"""

from __future__ import annotations

from typing import Any

from PIL import Image
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from models.criteria_repo import (
    manual_grade_decisions,
    apply_saved_criteria_to_field,
    get_unique_answers,
    import_manual_grades_into_criteria,
    manual_criteria_mismatch_ids,
    sync_committed_grades_to_criteria,
)
from models.database import connect
from models.grading_status import (
    PENDING_JUDGMENT,
    field_grading_complete_map,
    normalize_judgment,
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
from models.output_repo import get_feedback_style
from models.test_repo import (
    get_all_results,
    get_answer_fields,
    get_points_conn,
    update_results_field_grades,
)
from services.crop_preview import load_crops_for_rows
from services.feedback_renderer import composite_mark_on_image
from ui_qt import helpers as h
from ui_qt.grade_transfer_undo import (
    confirm_saved_then_transfer,
    has_transfer_undo,
    undo_last_transfer,
)
from ui_qt.crop_widgets import CropDisplayControls
from ui_qt.helpers import pil_to_qpixmap
from ui_qt.hover_top_toolbar import HoverTopToolbar, ManualGradingWorkOverlay
from ui_qt.layout_helpers import CropTileColumnPanel, configure_crop_image_scroll, make_expanding
from ui_qt.manual_grading_prefs import (
    load_field_view_prefs,
    load_field_zoom_pct,
    load_manual_grading_display_prefs,
    group_grade_hide_decided_enabled,
    manual_auto_grading_link_enabled,
    manual_grading_hover_toolbar_enabled,
    save_field_view_prefs,
    save_field_zoom_pct,
    save_manual_grading_display_prefs,
)
from ui_qt.stylus_overlay import CropInkImageStack
from ui_qt.style import COLORS


def _mix_hex_with_white(hex_color: str, white_ratio: float = 0.82) -> str:
    """判定色を白と混ぜた薄い背景色。"""
    raw = str(hex_color or "").lstrip("#")
    if len(raw) != 6:
        return COLORS["surface"]
    r, g, b = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    w = max(0.0, min(1.0, white_ratio))
    r = int(r + (255 - r) * w)
    g = int(g + (255 - g) * w)
    b = int(b + (255 - b) * w)
    return f"#{r:02x}{g:02x}{b:02x}"


class GroupGradeDialog(QDialog):
    """同じOCRの他回答へ、直前の判定を付けるか確認する一時ウィンドウ。"""

    def __init__(
        self,
        parent: "StepManualPage",
        answer_text: str,
        group_items: list[dict],
        *,
        judgment: str,
        score: int,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"同じ判定でよいか確認（OCR: {answer_text}）")
        self.setModal(True)
        self.resize(900, 640)
        self.page = parent
        self.answer_text = answer_text
        self.items = group_items
        self._proposed_judgment = normalize_judgment(judgment) or judgment
        self._proposed_score = int(score)
        self._selected_ids: set[int] = {
            int(i.get("result_id") or 0)
            for i in group_items
            if int(i.get("result_id") or 0)
        }
        self._ink_stacks: list = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        tip = h.caption_label(
            f"⑧採点基準の「{answer_text}」は {self._proposed_judgment}"
            f"（{self._proposed_score}点）にしました。"
            "下は同じOCRの他の回答です。同じ判定でよいものは選択したまま"
            "「選択中に同じ判定」を押してください。"
            "違う答案は選んで ○△× で個別に付けられます。"
            "閉じるだけなら他の回答は変更しません。"
            + (
                " 判定を付けるとその答案は一覧から消えます。"
                if group_grade_hide_decided_enabled()
                else ""
            )
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)

        ctrl_lay = QHBoxLayout()
        self._info_lbl = QLabel()
        self._info_lbl.setTextFormat(Qt.RichText)
        self._refresh_info_label()
        ctrl_lay.addWidget(self._info_lbl)
        ctrl_lay.addStretch()
        btn_all = QPushButton("すべて選択")
        btn_none = QPushButton("選択解除")
        btn_same = QPushButton(
            f"選択中に同じ判定（{self._proposed_judgment} {self._proposed_score}点）"
        )
        btn_all.clicked.connect(self._select_all)
        btn_none.clicked.connect(self._select_none)
        btn_same.clicked.connect(self._apply_same_to_selected)
        ctrl_lay.addWidget(btn_all)
        ctrl_lay.addWidget(btn_none)
        ctrl_lay.addWidget(btn_same)
        lay.addLayout(ctrl_lay)

        mark_lay = QHBoxLayout()
        mark_lay.addWidget(QLabel("個別に変える:"))
        btn_maru = QPushButton("○ (1)")
        btn_sankaku = QPushButton("△ (2)")
        btn_batsu = QPushButton("× (3)")
        btn_clear = QPushButton("判定解除 (BackSpace)")
        btn_maru.clicked.connect(lambda: self._apply_judgment("○"))
        btn_sankaku.clicked.connect(lambda: self._apply_judgment("△"))
        btn_batsu.clicked.connect(lambda: self._apply_judgment("×"))
        btn_clear.clicked.connect(lambda: self._apply_judgment(""))
        for btn in (btn_maru, btn_sankaku, btn_batsu, btn_clear):
            self.page._configure_judgment_mark_button(btn)
            mark_lay.addWidget(btn)
        if self.page._field_max_score() <= 1:
            btn_sankaku.hide()
        mark_lay.addStretch()
        lay.addLayout(mark_lay)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.panel = CropTileColumnPanel(margins=(4, 4, 0, 4), spacing=4)
        self.scroll.setWidget(self.panel)
        lay.addWidget(self.scroll, 1)

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        close_btn = btn_box.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.setText("閉じる")
        lay.addWidget(btn_box)

        self._render_grid(preserve_scroll=False)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_1:
            self._apply_judgment("○")
        elif event.key() == Qt.Key_2:
            self._apply_judgment("△")
        elif event.key() == Qt.Key_3:
            self._apply_judgment("×")
        elif event.key() == Qt.Key_Backspace:
            self._apply_judgment("")
        else:
            super().keyPressEvent(event)

    def _on_tile_clicked(self, rid: int) -> None:
        if rid in self._selected_ids:
            self._selected_ids.discard(rid)
        else:
            self._selected_ids.add(rid)
        self._apply_selection_styles()

    def _select_all(self) -> None:
        self._selected_ids = {
            int(i.get("result_id") or 0)
            for i in self.items
            if int(i.get("result_id") or 0)
        }
        self._apply_selection_styles()

    def _select_none(self) -> None:
        self._selected_ids.clear()
        self._apply_selection_styles()

    def _apply_same_to_selected(self) -> None:
        if not self._selected_ids:
            h.warn(self, "未選択", "同じ判定を付ける画像を選択してください。")
            return
        ids = list(self._selected_ids)
        self._selected_ids.clear()
        was_in_dialog = getattr(self.page, "_in_group_dialog", False)
        self.page._in_group_dialog = True
        try:
            ok = self.page._commit_grades(
                ids,
                self._proposed_judgment,
                self._proposed_score,
                silent=True,
            )
        finally:
            self.page._in_group_dialog = was_in_dialog
        self._after_applied(ids, hide=bool(ok))

    def _apply_judgment(self, judgment: str) -> None:
        if not self._selected_ids:
            h.warn(self, "未選択", "画像をタップして選択してください。")
            return
        resolved = self.page._resolve_judgment_score(judgment)
        if not resolved:
            return
        nj, score = resolved
        ids = list(self._selected_ids)
        self._selected_ids.clear()
        was_in_dialog = getattr(self.page, "_in_group_dialog", False)
        self.page._in_group_dialog = True
        try:
            ok = self.page._commit_grades(ids, nj, score, silent=True)
        finally:
            self.page._in_group_dialog = was_in_dialog
        # 判定解除は「決めた」扱いにせず、一覧に残す
        self._after_applied(ids, hide=bool(ok and nj))

    def _refresh_info_label(self) -> None:
        self._info_lbl.setText(
            f"OCRテキスト: <b>{self.answer_text}</b>"
            f" （残り {len(self.items)} 枚 / 提案 {self._proposed_judgment}"
            f" {self._proposed_score}点）"
        )

    def _after_applied(self, ids: list[int], *, hide: bool) -> None:
        if hide and group_grade_hide_decided_enabled():
            drop = {int(x) for x in ids if int(x or 0)}
            self.items = [
                i for i in self.items if int(i.get("result_id") or 0) not in drop
            ]
            self._selected_ids -= drop
            if not self.items:
                self.accept()
                return
        self._refresh_info_label()
        self._render_grid()

    def _apply_selection_styles(self) -> None:
        by_id = {int(i.get("result_id") or 0): i for i in self.items}
        for stack in self._ink_stacks:
            tile = stack.parentWidget()
            if tile is None:
                continue
            rid = int(getattr(stack, "result_id", 0) or 0)
            item = by_id.get(rid)
            if item is None:
                continue
            j = normalize_judgment(item.get("judgment"))
            selected = rid in self._selected_ids
            bg, border = self.page._tile_colors(j, selected=selected)
            border_w = 3 if selected else 2
            tile.setStyleSheet(
                f"QFrame {{ background: {bg}; border: {border_w}px solid {border};"
                f" border-radius: 6px; }}"
            )

    def _render_grid(self, preserve_scroll: bool = True) -> None:
        v_bar = self.scroll.verticalScrollBar()
        h_bar = self.scroll.horizontalScrollBar()
        saved_v = v_bar.value() if v_bar is not None else 0
        saved_h = h_bar.value() if h_bar is not None else 0

        self.panel.clear_tiles()
        self._ink_stacks = []
        zoom = max(30, min(400, self.page.crop_controls.zoom_value())) / 100.0
        # ページ本体のスタックを汚さないよう一時退避
        saved_stacks = list(self.page._ink_stacks)
        self.page._ink_stacks = []
        try:
            for idx, item in enumerate(self.items):
                tile = self.page._make_tile(
                    item,
                    zoom,
                    selected_ids=self._selected_ids,
                    on_click=self._on_tile_clicked,
                )
                self.panel.add_tile(tile, idx)
            self._ink_stacks = list(self.page._ink_stacks)
        finally:
            self.page._ink_stacks = saved_stacks

        if preserve_scroll and (saved_v or saved_h):
            self.page._schedule_scroll_restore(self.scroll, saved_v, saved_h)

class StepManualPage(QWidget):
    """記述欄画像を並べ、複数選択して ○△×/? を一括反映する手動採点。"""

    _MAIN_FILTERS = (
        "○",
        "△",
        "×",
        "?",
        "未採点",
        "未判定",
        "採点済み",
        "無回答",
        "基準不一致",
    )
    _CLEAR_JUDGMENT_KEY = "none"

    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._fields: list[dict[str, Any]] = []
        self._items: list[dict[str, Any]] = []
        self._selected_ids: set[int] = set()
        self._filter_btns: dict[str, QPushButton] = {}
        self._tri_filter_btns: dict[str, QPushButton] = {}
        self._tri_filter_key = "all"
        self._sort_mode = "file"
        self._filter_snapshot_ids: set[int] | None = None
        self._print_mark_mode = False  # False=文字情報 / True=個票と同じ印字
        self._zoom_field_id: str | None = None  # 現在 UI 設定が紐づく記述欄
        self._feedback_style: dict[str, Any] = get_feedback_style()
        self._show_all_pages = False  # False=指定件数表示 / True=全件表示
        self._page_size = 20
        self._page_index = 0
        self._parallel_palette_mode = False  # False=同一判定連続選択 / True=切り替え平行選択
        self._palette_active_key: str | None = None
        self._palette_btns: dict[str, QPushButton] = {}
        self._ink_stacks: list[CropInkImageStack] = []
        self._scroll_restore_token: object | None = None
        self._criteria_mismatch_ids: set[int] = set()
        self._in_group_dialog = False

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._create_page_controls()

        self._work_host = QWidget()
        self._work_host_layout = QVBoxLayout(self._work_host)
        self._work_host_layout.setContentsMargins(0, 0, 0, 4)
        self._work_host_layout.setSpacing(4)

        self._top_toolbar_content = QWidget()
        top_lay = QVBoxLayout(self._top_toolbar_content)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        title_row.setAlignment(Qt.AlignVCenter)
        title_lbl = h.title_label("手動採点")
        title_row.addWidget(title_lbl, 0, Qt.AlignVCenter)
        title_row.addWidget(self._build_page_mode_row(), 1, Qt.AlignVCenter)
        top_lay.addLayout(title_row)

        header = QHBoxLayout()
        header.setSpacing(8)
        left_hdr = QVBoxLayout()
        left_hdr.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("採点する記述欄"))
        self.field_combo = QComboBox()
        self.field_combo.setMinimumWidth(200)
        self.field_combo.currentIndexChanged.connect(self._on_field_changed)
        top.addWidget(self.field_combo)
        top.addWidget(QLabel("並べ替え"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("ファイル名", "file")
        self.sort_combo.addItem("ID", "id")
        self.sort_combo.addItem("判定－ファイル名", "judgment_file")
        self.sort_combo.addItem("判定－ID", "judgment_id")
        self.sort_combo.addItem("自動採点：回答の集約順（ファイル名）", "agg_file")
        self.sort_combo.addItem("自動採点：回答の集約順（ID）", "agg_id")
        self.sort_combo.setMinimumWidth(200)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        top.addWidget(self.sort_combo)
        top.addWidget(h.button("判定を再読込", self._reload_grades))
        self.btn_export_to_criteria = h.button(
            "採点基準へ反映", self._on_export_manual_to_criteria
        )
        self.btn_import_from_criteria = h.button(
            "採点基準から取込", self._on_import_criteria_to_manual
        )
        self.btn_undo_transfer = h.button("取り消す", self._on_undo_transfer)
        self.btn_undo_transfer.setToolTip(
            "直前の取込・反映の前に保存した状態へ戻します。"
        )
        top.addWidget(self.btn_export_to_criteria)
        top.addWidget(self.btn_import_from_criteria)
        top.addWidget(self.btn_undo_transfer)
        top.addStretch()
        left_hdr.addLayout(top)

        info_row = QHBoxLayout()
        info_row.setSpacing(16)
        self.selection_label = h.caption_label("0 件を選択中")
        self.selection_label.setWordWrap(False)
        self.status_label = h.caption_label("○0 △0 ×0 ?0 未採点0")
        self.status_label.setWordWrap(False)
        info_row.addWidget(self.selection_label, 0)
        info_row.addWidget(self.status_label, 0)
        info_row.addWidget(self._build_selection_mode_switch(), 0)
        info_row.addStretch()
        left_hdr.addLayout(info_row)
        header.addLayout(left_hdr, 1)
        header.addWidget(self._build_filter_box(), 0)
        top_lay.addLayout(header)

        self.crop_scroll = QScrollArea()
        self.crop_scroll.setWidgetResizable(True)
        configure_crop_image_scroll(self.crop_scroll)
        self.crop_scroll.viewport().setAttribute(Qt.WA_TabletTracking, True)
        self._crop_scroll_style_normal = (
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['surface']}; }}"
        )
        self._crop_scroll_style_hover = (
            f"QScrollArea {{ border: none; border-radius: 0;"
            f" background: {COLORS['surface']}; }}"
        )
        self.crop_scroll.setStyleSheet(self._crop_scroll_style_normal)
        self.crop_scroll.setViewportMargins(0, 0, 0, 0)
        self.crop_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        make_expanding(self.crop_scroll)
        self._crop_panel_margins_normal = (4, 4, 0, 4)
        self._crop_panel_margins_hover = (4, 2, 0, 4)
        self._crop_panel_spacing_normal = 4
        self._crop_panel_spacing_hover = 3
        self.crop_panel = CropTileColumnPanel(
            margins=self._crop_panel_margins_normal,
            spacing=self._crop_panel_spacing_normal,
        )
        self.crop_scroll.setWidget(self.crop_panel)

        self._hover_toolbar_mode: bool | None = None
        self._hover_toolbar: HoverTopToolbar | None = None
        self._work_overlay: ManualGradingWorkOverlay | None = None
        self._apply_toolbar_layout_mode()

        make_expanding(self._work_host)
        root.addWidget(self._work_host, 1)

        # --- 最下部固定オーバーレイ ---
        self.grade_footer = self._build_footer_overlay()
        root.addWidget(self.grade_footer)
        self._update_link_dependent_ui()

    def _update_link_dependent_ui(self) -> None:
        """常時リンクONのときは移行用ボタン・基準不一致フィルタを隠す。"""
        linked = manual_auto_grading_link_enabled()
        for btn in (
            getattr(self, "btn_export_to_criteria", None),
            getattr(self, "btn_import_from_criteria", None),
        ):
            if btn is None:
                continue
            btn.setVisible(not linked)
            btn.setEnabled(not linked)
        undo = getattr(self, "btn_undo_transfer", None)
        if undo is not None:
            undo.setVisible(not linked)
            fid = self._selected_field_id() if self.app.active_test_id else ""
            undo.setEnabled(
                (not linked)
                and bool(fid)
                and has_transfer_undo(str(self.app.active_test_id or ""), str(fid))
            )
        mismatch_btn = self._filter_btns.get("基準不一致")
        if mismatch_btn is not None:
            if linked and mismatch_btn.isChecked():
                mismatch_btn.blockSignals(True)
                mismatch_btn.setChecked(False)
                mismatch_btn.blockSignals(False)
                self._refresh_filter_snapshot()
                if self._items:
                    self._render_grid(preserve_scroll=True)
            mismatch_btn.setVisible(not linked)
            mismatch_btn.setEnabled(not linked)

    def apply_layout_prefs(self) -> None:
        """詳細設定の手動採点レイアウト変更を反映する。"""
        self._apply_toolbar_layout_mode(force=True)
        self._update_link_dependent_ui()

    def _apply_toolbar_layout_mode(self, *, force: bool = False) -> None:
        enabled = manual_grading_hover_toolbar_enabled()
        if not force and enabled == self._hover_toolbar_mode:
            self._apply_toolbar_visuals(enabled)
            self._sync_content_area_margins(enabled)
            return
        self._hover_toolbar_mode = enabled

        while self._work_host_layout.count():
            item = self._work_host_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        if enabled:
            if self._hover_toolbar is None:
                self._hover_toolbar = HoverTopToolbar(self._top_toolbar_content)
                self._work_overlay = ManualGradingWorkOverlay(
                    self.crop_scroll, self._hover_toolbar
                )
            else:
                self._top_toolbar_content.setParent(None)
                host_lay = self._hover_toolbar._content_host.layout()  # noqa: SLF001
                if host_lay.indexOf(self._top_toolbar_content) < 0:
                    host_lay.addWidget(self._top_toolbar_content)
                self.crop_scroll.setParent(self._work_overlay)
                self._hover_toolbar.setParent(self._work_overlay)
            self._work_host_layout.setContentsMargins(0, 0, 0, 0)
            self._work_host_layout.setSpacing(0)
            self._work_host_layout.addWidget(self._work_overlay, 1)
            make_expanding(self._work_overlay)
        else:
            self._top_toolbar_content.setParent(self._work_host)
            self.crop_scroll.setParent(self._work_host)
            self._work_host_layout.setContentsMargins(0, 0, 0, 4)
            self._work_host_layout.setSpacing(4)
            self._work_host_layout.addWidget(self._top_toolbar_content)
            self._work_host_layout.addWidget(self.crop_scroll, 1)

        self._apply_toolbar_visuals(enabled)
        self._sync_content_area_margins(enabled)

    def _apply_toolbar_visuals(self, hover: bool) -> None:
        if hover:
            self.crop_scroll.setStyleSheet(self._crop_scroll_style_hover)
            self.crop_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        else:
            self.crop_scroll.setStyleSheet(self._crop_scroll_style_normal)
            self.crop_scroll.setFrameShape(QScrollArea.Shape.StyledPanel)
        self.crop_scroll.setViewportMargins(0, 0, 0, 0)
        if hover:
            self.crop_panel.configure_layout(
                margins=self._crop_panel_margins_hover,
                spacing=self._crop_panel_spacing_hover,
            )
        else:
            self.crop_panel.configure_layout(
                margins=self._crop_panel_margins_normal,
                spacing=self._crop_panel_spacing_normal,
            )

    def _sync_content_area_margins(self, hover: bool) -> None:
        del hover
        fn = getattr(self.app, "apply_manual_grading_content_margins", None)
        if callable(fn):
            fn()

    def _build_mark_mode_switch(self) -> QWidget:
        """判定表示（文字/印字）— 下部固定メニュー用。"""
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(h.caption_label("判定表示"))
        self._mode_lbl_text = QLabel("文字")
        self._mode_lbl_print = QLabel("印字")
        self.mark_mode_switch = QCheckBox()
        self.mark_mode_switch.setObjectName("MarkModeSwitch")
        self.mark_mode_switch.setCursor(Qt.PointingHandCursor)
        self.mark_mode_switch.setToolTip(
            "文字: 画像下に判定・得点を表示（タイル余白は⑫の判定色）\n"
            "印字: ⑫ 個票プレビューと同じ ○△×・得点を画像上に重ねる"
        )
        self.mark_mode_switch.setStyleSheet(
            f"""
            QCheckBox#MarkModeSwitch {{
                spacing: 0px;
            }}
            QCheckBox#MarkModeSwitch::indicator {{
                width: 40px;
                height: 22px;
                border-radius: 11px;
                border: 1px solid {COLORS["border_strong"]};
                background: #e5e7eb;
            }}
            QCheckBox#MarkModeSwitch::indicator:checked {{
                background: {COLORS["accent"]};
                border-color: {COLORS["accent_hover"]};
            }}
            """
        )
        self.mark_mode_switch.toggled.connect(self._on_mark_mode_toggled)
        lay.addWidget(self._mode_lbl_text)
        lay.addWidget(self.mark_mode_switch)
        lay.addWidget(self._mode_lbl_print)
        self._update_mode_labels()
        return wrap

    def _on_mark_mode_toggled(self, checked: bool) -> None:
        self._print_mark_mode = bool(checked)
        self._update_mode_labels()
        self._save_manual_display_prefs()
        self._render_grid()

    def _apply_manual_display_prefs(self) -> None:
        if not hasattr(self, "crop_controls"):
            return
        prefs = load_manual_grading_display_prefs()
        self.crop_controls.apply_display_prefs(
            show_id=prefs["showId"],
            show_file=prefs["showFileName"],
            show_ocr=prefs["showOcrText"],
        )
        self._print_mark_mode = prefs["printMarkMode"]
        self.mark_mode_switch.blockSignals(True)
        self.mark_mode_switch.setChecked(self._print_mark_mode)
        self.mark_mode_switch.blockSignals(False)
        self._update_mode_labels()

    def _save_manual_display_prefs(self) -> None:
        if not hasattr(self, "crop_controls"):
            return
        save_manual_grading_display_prefs(
            {
                **self.crop_controls.display_prefs(),
                "printMarkMode": self._print_mark_mode,
            }
        )

    def _persist_field_prefs(self) -> None:
        fid = self._zoom_field_id
        test_id = self.app.active_test_id
        if not fid or not test_id:
            return
        if hasattr(self, "crop_controls"):
            save_field_zoom_pct(test_id, fid, self.crop_controls.zoom_value())
        save_field_view_prefs(
            test_id,
            fid,
            show_all_pages=self._show_all_pages,
            page_size=self._page_size,
            parallel_palette_mode=self._parallel_palette_mode,
        )

    def _apply_saved_field_prefs(self) -> None:
        fid = self._selected_field_id()
        test_id = self.app.active_test_id
        if not fid or not test_id:
            return
        if hasattr(self, "crop_controls"):
            pct = load_field_zoom_pct(test_id, fid, 100)
            self.crop_controls.set_zoom_value(pct, block_signals=True)
        view = load_field_view_prefs(test_id, fid)
        self._show_all_pages = bool(view["showAllPages"])
        self._page_size = int(view["pageSize"])
        self._parallel_palette_mode = bool(view["parallelPaletteMode"])
        if hasattr(self, "page_mode_switch"):
            self.page_mode_switch.blockSignals(True)
            self.page_mode_switch.setChecked(not self._show_all_pages)
            self.page_mode_switch.blockSignals(False)
            self._update_page_mode_labels()
            self._update_page_controls_enabled()
        if hasattr(self, "page_size_spin"):
            self.page_size_spin.blockSignals(True)
            self.page_size_spin.setValue(self._page_size)
            self.page_size_spin.blockSignals(False)
        if hasattr(self, "selection_mode_switch"):
            self.selection_mode_switch.blockSignals(True)
            self.selection_mode_switch.setChecked(self._parallel_palette_mode)
            self.selection_mode_switch.blockSignals(False)
            self._update_selection_mode_labels()
        if hasattr(self, "judge_stack"):
            self.judge_stack.setCurrentIndex(1 if self._parallel_palette_mode else 0)
        self._zoom_field_id = fid

    def _on_zoom_changed(self) -> None:
        fid = self._selected_field_id()
        if fid:
            self._zoom_field_id = fid
            self._persist_field_prefs()
        self._render_grid()

    def _on_display_meta_changed(self) -> None:
        self._save_manual_display_prefs()
        self._render_grid()

    def _update_mode_labels(self) -> None:
        active = "font-weight: 700; color: #111827;"
        idle = f"font-weight: 400; color: {COLORS['text_muted']};"
        if self._print_mark_mode:
            self._mode_lbl_text.setStyleSheet(idle)
            self._mode_lbl_print.setStyleSheet(active)
        else:
            self._mode_lbl_text.setStyleSheet(active)
            self._mode_lbl_print.setStyleSheet(idle)

    def _create_page_controls(self) -> None:
        self.page_size_spin = QSpinBox()
        self.page_size_spin.setRange(1, 500)
        self.page_size_spin.setValue(self._page_size)
        self.page_size_spin.setSuffix(" 件ごと")
        self.page_size_spin.setMinimumWidth(108)
        self.page_size_spin.setToolTip("一度に表示する件数（クリックして直接入力可）")
        self.page_size_spin.valueChanged.connect(self._on_page_size_changed)

        self.page_prev_btn = h.button("◀ 前", self._on_page_prev)
        self.page_prev_btn.setObjectName("PageNavBtn")
        self.page_next_btn = h.button("次 ▶", self._on_page_next)
        self.page_next_btn.setObjectName("PageNavBtn")
        self.page_prev_btn.setStyleSheet(
            "QPushButton#PageNavBtn { padding: 4px 10px; min-width: 52px; }"
        )
        self.page_next_btn.setStyleSheet(
            "QPushButton#PageNavBtn { padding: 4px 10px; min-width: 52px; }"
        )

        self.page_info_label = h.caption_label("", wrap=False)
        self.page_info_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        )
        sample = "999 / 999 ページ（全 9999 件）"
        self.page_info_label.setMinimumWidth(
            self.page_info_label.fontMetrics().horizontalAdvance(sample) + 8
        )

    def _build_page_mode_row(self) -> QWidget:
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._page_lbl_all = QLabel("全件表示")
        self._page_lbl_chunk = QLabel("指定件数表示")
        self.page_mode_switch = QCheckBox()
        self.page_mode_switch.setObjectName("PageModeSwitch")
        self.page_mode_switch.setChecked(True)  # 指定件数表示がデフォルト
        self.page_mode_switch.setCursor(Qt.PointingHandCursor)
        self.page_mode_switch.setToolTip(
            "全件表示: フィルタ後の画像をすべて並べる\n"
            "指定件数表示: 一度に表示する件数を区切る（ページ送り）"
        )
        self.page_mode_switch.setStyleSheet(
            f"""
            QCheckBox#PageModeSwitch::indicator {{
                width: 40px; height: 22px; border-radius: 11px;
                border: 1px solid {COLORS["border_strong"]}; background: #e5e7eb;
            }}
            QCheckBox#PageModeSwitch::indicator:checked {{
                background: {COLORS["accent"]}; border-color: {COLORS["accent_hover"]};
            }}
            """
        )
        self.page_mode_switch.toggled.connect(self._on_page_mode_toggled)
        lay.addWidget(self._page_lbl_all)
        lay.addWidget(self.page_mode_switch)
        lay.addWidget(self._page_lbl_chunk)

        self._page_nav_wrap = QWidget()
        nav_lay = QHBoxLayout(self._page_nav_wrap)
        nav_lay.setContentsMargins(0, 0, 0, 0)
        nav_lay.setSpacing(6)
        nav_lay.addWidget(self.page_size_spin)
        nav_lay.addWidget(self.page_prev_btn)
        nav_lay.addWidget(self.page_info_label)
        nav_lay.addWidget(self.page_next_btn)
        self._page_nav_wrap.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        lay.addWidget(self._page_nav_wrap)

        lay.addStretch()
        self._update_page_mode_labels()
        self._update_page_controls_enabled()
        wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return wrap

    def _on_page_mode_toggled(self, checked: bool) -> None:
        # checked=True → 指定件数表示, False → 全件表示
        self._show_all_pages = not checked
        self._page_index = 0
        self._update_page_mode_labels()
        self._update_page_controls_enabled()
        fid = self._selected_field_id()
        if fid:
            self._zoom_field_id = fid
            self._persist_field_prefs()
        self._render_grid(preserve_scroll=False)

    def _update_page_mode_labels(self) -> None:
        active = "font-weight: 700; color: #111827;"
        idle = f"font-weight: 400; color: {COLORS['text_muted']};"
        if self._show_all_pages:
            self._page_lbl_all.setStyleSheet(active)
            self._page_lbl_chunk.setStyleSheet(idle)
        else:
            self._page_lbl_all.setStyleSheet(idle)
            self._page_lbl_chunk.setStyleSheet(active)

    def _update_page_controls_enabled(self) -> None:
        chunk = not self._show_all_pages
        if hasattr(self, "_page_nav_wrap"):
            self._page_nav_wrap.setVisible(chunk)

    def _on_page_size_changed(self, value: int) -> None:
        self._page_size = max(1, int(value))
        self._page_index = 0
        fid = self._selected_field_id()
        if fid:
            self._zoom_field_id = fid
            self._persist_field_prefs()
        self._render_grid(preserve_scroll=False)

    def _on_page_prev(self) -> None:
        if self._page_index > 0:
            self._page_index -= 1
            self._render_grid(preserve_scroll=False)

    def _on_page_next(self) -> None:
        self._page_index += 1
        self._render_grid(preserve_scroll=False)

    def _build_filter_box(self) -> QGroupBox:
        box = QGroupBox("表示フィルタ（オフ＝全件／ON＝該当のみ）")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)
        row1 = QHBoxLayout()
        for key in self._MAIN_FILTERS:
            btn = QPushButton(key)
            btn.setCheckable(True)
            btn.setChecked(False)  # デフォルト全オフ＝全件表示
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(self._filter_tooltip(key))
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    padding: 4px 8px;
                    border: 1px solid {COLORS["border_strong"]};
                    border-radius: 6px;
                    background: {COLORS["surface"]};
                }}
                QPushButton:checked {{
                    background: {COLORS["accent"]};
                    color: white;
                    font-weight: 700;
                    border-color: {COLORS["accent_hover"]};
                }}
                """
            )
            btn.toggled.connect(lambda _c=False: self._on_filter_toggled())
            self._filter_btns[key] = btn
            row1.addWidget(btn)
        row1.addStretch()
        lay.addLayout(row1)

        self.tri_filter_row = QHBoxLayout()
        self.tri_filter_row.addWidget(h.caption_label("△の部分点:"))
        lay.addLayout(self.tri_filter_row)
        return box

    @staticmethod
    def _filter_tooltip(key: str) -> str:
        return {
            "○": "ON にすると ○ 判定のみ表示（他も ON なら OR）",
            "△": "ON にすると △ 判定のみ表示",
            "×": "ON にすると × 判定のみ表示",
            "?": "ON にすると 保留（?）のみ表示",
            "未採点": "ON にすると 判定がまだない回答を表示",
            "未判定": "ON にすると 判定なし（初期状態）の回答を表示",
            "採点済み": "ON にすると 確定判定（○△×）を表示（保留は含まない）",
            "無回答": "ON にすると OCR/集約で「なし」の無回答を表示",
            "基準不一致": (
                "ON にすると ⑧採点基準と手動採点の判定・配点が食い違う答案だけ表示"
                "（両方に確定判定がある答案のみ）"
            ),
        }.get(key, "")

    def _build_footer_overlay(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("ManualGradeFooter")
        footer.setStyleSheet(
            f"#ManualGradeFooter {{ background: {COLORS['surface']};"
            f" border-top: 2px solid {COLORS['border_strong']}; }}"
        )
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)

        # 左: 短い拡大率 + メタ表示 + 判定表示切替
        left = QWidget()
        left.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(2)
        self.crop_controls = CropDisplayControls(slider_max_width=140)
        self.crop_controls.connect_zoom_changed(self._on_zoom_changed)
        self.crop_controls.connect_meta_changed(self._on_display_meta_changed)
        left_lay.addWidget(self.crop_controls)
        left_lay.addWidget(self._build_mark_mode_switch())
        self._apply_manual_display_prefs()
        lay.addWidget(left, 0)

        # 右: 採点モードに応じて「選択への判定反映」または「判定パレット」
        self.judge_stack = QStackedWidget()
        self.judge_stack.addWidget(self._build_continuous_judge_panel())  # 0
        self.judge_stack.addWidget(self._build_palette_judge_panel())  # 1
        lay.addWidget(self.judge_stack, 1)
        return footer

    def _build_selection_mode_switch(self) -> QWidget:
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(4)
        self._sel_lbl_continuous = QLabel("同一判定連続選択")
        self._sel_lbl_parallel = QLabel("切り替え平行選択")
        self.selection_mode_switch = QCheckBox()
        self.selection_mode_switch.setObjectName("SelectionModeSwitch")
        self.selection_mode_switch.setChecked(False)
        self.selection_mode_switch.setCursor(Qt.PointingHandCursor)
        self.selection_mode_switch.setToolTip(
            "同一判定連続選択: 画像を複数選択してから ○△× を一括反映\n"
            "切り替え平行選択: 判定パレットで判定を選び、画像タップで即反映"
        )
        self.selection_mode_switch.setStyleSheet(
            f"""
            QCheckBox#SelectionModeSwitch::indicator {{
                width: 40px; height: 22px; border-radius: 11px;
                border: 1px solid {COLORS["border_strong"]}; background: #e5e7eb;
            }}
            QCheckBox#SelectionModeSwitch::indicator:checked {{
                background: {COLORS["accent"]}; border-color: {COLORS["accent_hover"]};
            }}
            """
        )
        self.selection_mode_switch.toggled.connect(self._on_selection_mode_toggled)
        lay.addWidget(self._sel_lbl_continuous)
        lay.addWidget(self.selection_mode_switch)
        lay.addWidget(self._sel_lbl_parallel)
        self._update_selection_mode_labels()
        return wrap

    def _on_selection_mode_toggled(self, checked: bool) -> None:
        self._parallel_palette_mode = bool(checked)
        self._selected_ids.clear()
        self._palette_active_key = None
        self._update_selection_mode_labels()
        self.judge_stack.setCurrentIndex(1 if self._parallel_palette_mode else 0)
        self._rebuild_palette_buttons()
        fid = self._selected_field_id()
        if fid:
            self._zoom_field_id = fid
            self._persist_field_prefs()
        self._render_grid()

    def _update_selection_mode_labels(self) -> None:
        active = "font-weight: 700; color: #111827;"
        idle = f"font-weight: 400; color: {COLORS['text_muted']};"
        if self._parallel_palette_mode:
            self._sel_lbl_continuous.setStyleSheet(idle)
            self._sel_lbl_parallel.setStyleSheet(active)
        else:
            self._sel_lbl_continuous.setStyleSheet(active)
            self._sel_lbl_parallel.setStyleSheet(idle)

    def _configure_judgment_mark_button(self, btn: QPushButton) -> None:
        """判定パレットと同じボタンサイズ（高さ固定・幅はラベルに応じて拡張）。"""
        btn.setFixedHeight(36)
        btn.setMinimumWidth(44)
        btn.setCursor(Qt.PointingHandCursor)

    def _build_continuous_judge_panel(self) -> QGroupBox:
        judge = QGroupBox("選択への判定反映")
        judge_lay = QHBoxLayout(judge)
        judge_lay.setContentsMargins(8, 4, 8, 4)
        judge_lay.setSpacing(6)
        judge_lay.addWidget(h.caption_label("画像をタップで複数選択 →"))
        self.btn_maru = QPushButton("○")
        self.btn_sankaku = QPushButton("△")
        self.btn_batsu = QPushButton("×")
        self.btn_pending = QPushButton("?")
        self.btn_pending.setToolTip("保留（あとで確認）")
        self.btn_unjudged = QPushButton("未判定")
        self.btn_unjudged.setToolTip("判定を解除（初期状態・判定なし）")
        for btn, handler in (
            (self.btn_maru, lambda: self._apply_judgment("○")),
            (self.btn_sankaku, lambda: self._apply_judgment("△")),
            (self.btn_batsu, lambda: self._apply_judgment("×")),
            (self.btn_pending, lambda: self._apply_judgment(PENDING_JUDGMENT)),
            (self.btn_unjudged, lambda: self._apply_judgment("")),
        ):
            self._configure_judgment_mark_button(btn)
            btn.clicked.connect(handler)
            judge_lay.addWidget(btn)
        self._apply_judgment_button_colors()
        judge_lay.addWidget(h.button("全選択", self._select_all_visible))
        judge_lay.addWidget(
            h.button("未採点を一括選択", self._select_ungraded, variant="success")
        )
        judge_lay.addWidget(h.button("選択を解除", self._clear_selection))
        judge_lay.addStretch()
        return judge

    def _build_palette_judge_panel(self) -> QGroupBox:
        box = QGroupBox("判定パレット")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)
        lay.addWidget(
            h.caption_label("有効な判定をタップ → 画像タップで即反映")
        )
        self.palette_btn_row = QHBoxLayout()
        self.palette_btn_row.setSpacing(4)
        lay.addLayout(self.palette_btn_row)
        lay.addStretch()
        return box

    # --- データ ---

    def _judgment_button_style(self, stroke: str) -> str:
        """⑫ 出力書式の判定色をボタンに反映。"""
        soft = _mix_hex_with_white(stroke, 0.82)
        return (
            f"QPushButton {{ background: {soft}; color: {stroke}; font-weight: 800;"
            f" font-size: 16px; border: 2px solid {stroke}; border-radius: 6px;"
            f" padding: 0 12px; min-height: 0; min-width: 44px; }}"
            f"QPushButton:hover {{ background: {_mix_hex_with_white(stroke, 0.7)}; }}"
            f"QPushButton:pressed {{ background: {stroke}; color: white; }}"
        )

    def _apply_judgment_button_colors(self) -> None:
        """○△× は個票出力と同じ判定記号色。? は保留用の琥珀色。"""
        mark = (self._feedback_style or {}).get("mark") or {}
        maru = str((mark.get("maru") or {}).get("strokeColor") or "#dc2626")
        sankaku = str((mark.get("sankaku") or {}).get("strokeColor") or "#ea580c")
        batsu = str((mark.get("batsu") or {}).get("strokeColor") or "#2563eb")
        pending = "#a16207"
        if hasattr(self, "btn_maru"):
            self.btn_maru.setStyleSheet(self._judgment_button_style(maru))
            self.btn_sankaku.setStyleSheet(self._judgment_button_style(sankaku))
            self.btn_batsu.setStyleSheet(self._judgment_button_style(batsu))
            self.btn_pending.setStyleSheet(self._judgment_button_style(pending))
        if hasattr(self, "btn_unjudged"):
            self.btn_unjudged.setStyleSheet(
                self._palette_button_style(self._CLEAR_JUDGMENT_KEY, active=False)
            )
        self._update_palette_button_styles()

    def _palette_specs(self) -> list[tuple[str, str, str, int]]:
        """(key, label, judgment, score) — 配点の高い順（○→△部分点→×）。"""
        max_score = self._field_max_score()
        specs: list[tuple[str, str, str, int]] = [("○", "○", "○", max_score)]
        if max_score > 1:
            for s in range(max_score - 1, 0, -1):
                specs.append((f"△:{s}", f"△({s})", "△", s))
        specs.append(("×", "×", "×", 0))
        specs.append(("?", "?", PENDING_JUDGMENT, 0))
        specs.append((self._CLEAR_JUDGMENT_KEY, "未判定", "", 0))
        return specs

    def _palette_stroke_for_key(self, key: str) -> str:
        mark = (self._feedback_style or {}).get("mark") or {}
        if key == "○":
            return str((mark.get("maru") or {}).get("strokeColor") or "#dc2626")
        if key.startswith("△:"):
            return str((mark.get("sankaku") or {}).get("strokeColor") or "#ea580c")
        if key == "×":
            return str((mark.get("batsu") or {}).get("strokeColor") or "#2563eb")
        if key == "?":
            return "#a16207"
        if key == self._CLEAR_JUDGMENT_KEY:
            return COLORS["text_secondary"]
        return COLORS["text_secondary"]

    def _palette_button_style(self, key: str, *, active: bool) -> str:
        stroke = self._palette_stroke_for_key(key)
        if active:
            return self._judgment_button_style(stroke)
        return (
            f"QPushButton {{ background: {COLORS['surface']}; color: {COLORS['text_muted']};"
            f" font-weight: 700; font-size: 14px; border: 2px solid {COLORS['border']};"
            f" border-radius: 6px; padding: 0 6px; min-height: 0; min-width: 0; }}"
            f"QPushButton:hover {{ background: #f9fafb; }}"
        )

    def _rebuild_palette_buttons(self) -> None:
        if not hasattr(self, "palette_btn_row"):
            return
        while self.palette_btn_row.count():
            item = self.palette_btn_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._palette_btns.clear()
        valid_keys = {spec[0] for spec in self._palette_specs()}
        if self._palette_active_key not in valid_keys:
            self._palette_active_key = None
        for key, label, _j, _s in self._palette_specs():
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == self._palette_active_key)
            self._configure_judgment_mark_button(btn)
            btn.toggled.connect(lambda checked, k=key: self._on_palette_toggled(k, checked))
            self._palette_btns[key] = btn
            self.palette_btn_row.addWidget(btn)
        self._update_palette_button_styles()

    def _on_palette_toggled(self, key: str, checked: bool) -> None:
        if checked:
            self._palette_active_key = key
            for k, btn in self._palette_btns.items():
                if k != key:
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)
        elif self._palette_active_key == key:
            self._palette_active_key = None
        self._update_palette_button_styles()
        # タイル内容は変わらないので再描画しない（スクロール維持）
        self._update_selection_label()

    def _update_palette_button_styles(self) -> None:
        for key, btn in self._palette_btns.items():
            btn.setStyleSheet(
                self._palette_button_style(key, active=(key == self._palette_active_key))
            )

    def _palette_active_spec(self) -> tuple[str, str, int] | None:
        if not self._palette_active_key:
            return None
        for key, _label, judgment, score in self._palette_specs():
            if key == self._palette_active_key:
                return judgment, self._palette_active_key, score
        return None

    def refresh(self) -> None:
        self._update_link_dependent_ui()
        if not self.app.require_active_test():
            return
        self._feedback_style = get_feedback_style()
        self._apply_judgment_button_colors()
        self._fields = get_answer_fields(self.app.active_test_id)
        current_fid = self._selected_field_id()
        self._rebuild_field_combo(prefer_fid=current_fid)
        self._rebuild_triangle_filters()
        self._update_judge_buttons()
        if self._fields:
            self._apply_saved_field_prefs()
            self._rebuild_palette_buttons()
            self._load_crops_async()
        else:
            self._items = []
            self._selected_ids.clear()
            self._render_grid()

    def _rebuild_field_combo(self, prefer_fid: str | None = None) -> None:
        """未：/完：接頭辞と完了行の薄紫背景で記述欄プルダウンを再構築。"""
        prefer = prefer_fid or self._selected_field_id()
        complete_map = (
            field_grading_complete_map(self.app.active_test_id)
            if self.app.active_test_id
            else {}
        )
        model = QStandardItemModel(self.field_combo)
        select_idx = 0
        for i, f in enumerate(self._fields):
            done = bool(complete_map.get(f["id"], False))
            prefix = "完：" if done else "未："
            item = QStandardItem(f"{prefix}{f['displayName']} ({f['id']})")
            item.setData(f["id"], Qt.UserRole)
            if done:
                item.setBackground(QBrush(QColor(COLORS["selection_soft"])))
            model.appendRow(item)
            if prefer and f["id"] == prefer:
                select_idx = i
        self.field_combo.blockSignals(True)
        self.field_combo.setModel(model)
        if self._fields:
            self.field_combo.setCurrentIndex(select_idx)
        self.field_combo.blockSignals(False)

    def _selected_field_id(self) -> str | None:
        idx = self.field_combo.currentIndex()
        if idx < 0 or idx >= len(self._fields):
            return None
        data = self.field_combo.currentData()
        if data:
            return str(data)
        return self._fields[idx]["id"]

    def _field_max_score(self) -> int:
        fid = self._selected_field_id()
        test_id = self.app.active_test_id
        if not fid or not test_id:
            return 1
        with connect() as conn:
            pts = get_points_conn(conn, test_id)
        return max(1, int(pts.get(fid, 1)))

    def _on_field_changed(self, _index: int) -> None:
        self._persist_field_prefs()
        self._selected_ids.clear()
        self._page_index = 0
        self._filter_snapshot_ids = None
        self._rebuild_triangle_filters()
        self._update_judge_buttons()
        self._apply_saved_field_prefs()
        self._rebuild_palette_buttons()
        self._load_crops_async()

    def _on_sort_changed(self, _index: int) -> None:
        self._sort_mode = self.sort_combo.currentData() or "file"
        self._page_index = 0
        self._sort_items()
        self._render_grid(preserve_scroll=False)

    def _on_filter_toggled(self) -> None:
        self._page_index = 0
        self._refresh_filter_snapshot()
        self._render_grid(preserve_scroll=False)

    def _rebuild_triangle_filters(self) -> None:
        while self.tri_filter_row.count() > 1:
            item = self.tri_filter_row.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._tri_filter_btns.clear()
        self._tri_filter_key = "all"
        max_score = self._field_max_score()
        if max_score <= 1:
            return
        specs = [("all", "△すべて")]
        if max_score == 2:
            specs.append(("1", "△(1)"))
        else:
            for s in range(1, max_score):
                specs.append((str(s), f"△({s})"))
        for key, label in specs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.setCursor(Qt.PointingHandCursor)
            btn.toggled.connect(lambda checked, k=key: self._on_tri_filter(k, checked))
            self._tri_filter_btns[key] = btn
            self.tri_filter_row.addWidget(btn)
        self.tri_filter_row.addStretch()

    def _on_tri_filter(self, key: str, checked: bool) -> None:
        if not checked:
            if self._tri_filter_key == key:
                all_btn = self._tri_filter_btns.get("all")
                if all_btn:
                    all_btn.blockSignals(True)
                    all_btn.setChecked(True)
                    all_btn.blockSignals(False)
                    self._tri_filter_key = "all"
            self._page_index = 0
            self._refresh_filter_snapshot()
            self._render_grid(preserve_scroll=False)
            return
        self._tri_filter_key = key
        for k, btn in self._tri_filter_btns.items():
            if k != key:
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        self._page_index = 0
        self._refresh_filter_snapshot()
        self._render_grid(preserve_scroll=False)

    def _update_judge_buttons(self) -> None:
        max_score = self._field_max_score()
        if hasattr(self, "btn_sankaku"):
            self.btn_sankaku.setVisible(max_score > 1)
            self.btn_sankaku.setToolTip(
                "1点（配点2点時）" if max_score == 2 else "部分点を指定して一括反映"
            )
        tri_btn = self._filter_btns.get("△")
        if tri_btn is not None:
            tri_btn.setVisible(max_score > 1)
        self._rebuild_palette_buttons()

    def _load_crops_async(self) -> None:
        fid = self._selected_field_id()
        test_id = self.app.active_test_id
        if not fid or not test_id:
            return
        field = next((f for f in self._fields if f["id"] == fid), None)
        if not field:
            return
        results = get_all_results(test_id)
        if not results:
            self._items = []
            self._render_grid(preserve_scroll=False)
            self.status_label.setText(
                "採点結果がありません。手動採点の「空DB作成」を実行するか、"
                "自動採点の ⑦ OCR実行 でテキスト化してください。"
            )
            return
        rows = [
            {
                "rowIndex": r["id"],
                "studentId": r.get("studentId") or "",
                "fileName": r.get("fileName") or "",
                "fileId": r.get("sourcePath") or "",
                "warpedPath": r.get("warpedPath") or "",
                "answer_text": str(r.get("textMapping", {}).get(fid, "") or "").strip() or "なし",
                "judgment": normalize_judgment(r.get("judgments", {}).get(fid, "")),
                "score": r.get("scores", {}).get(fid),
            }
            for r in results
        ]
        self._clear_grid()
        self.crop_panel.set_message(f"画像を読み込み中…（{len(rows)}枚）")
        self.status_label.setText(f"{len(rows)} 件を読み込み中…")

        def done(crop_results, err):
            if err:
                h.error(self, "画像読込エラー", str(err))
                return
            test_id = self.app.active_test_id
            result_ids = [int(src["rowIndex"]) for src in rows if src.get("rowIndex")]
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
            self._items = []
            for cr, src in zip(crop_results, rows, strict=False):
                rid = int(src["rowIndex"])
                local_tb = text_map.get(rid, [])
                sheet_tb = project_sheet_text_to_field_local(
                    sheet_text_map.get(rid, []), field
                )
                self._items.append(
                    {
                        **cr,
                        "result_id": rid,
                        "judgment": src["judgment"],
                        "score": src["score"],
                        "ink_strokes": ink_map.get(rid, []),
                        "sheet_ink_strokes": project_sheet_ink_to_field_local(
                            sheet_ink_map.get(rid, []), field
                        ),
                        "text_annotations": list(local_tb) + list(sheet_tb),
                    }
                )
            self._sort_items()
            self._refresh_criteria_mismatch_ids()
            self._refresh_filter_snapshot()
            self._render_grid(preserve_scroll=False)
            self._update_status_summary()

        h.run_in_thread(self, lambda: load_crops_for_rows(rows, field), done)

    def viewer_scroll(self) -> QScrollArea:
        return self.crop_scroll

    def palette_ink_stacks(self) -> list[CropInkImageStack]:
        return self._ink_stacks

    def palette_field_id(self) -> str:
        return self._selected_field_id() or ""

    def palette_focus_result_id(self) -> int | None:
        """描画ツールが参照する選択中画像（1件のみ選択時）。"""
        if len(self._selected_ids) == 1:
            return next(iter(self._selected_ids))
        return None

    def palette_draw_selected_ids(self) -> list[int]:
        return list(self._selected_ids)

    def palette_set_pen_ui_locked(self, locked: bool) -> None:
        """ペンON＋選択ありのときフッタ（ズーム／判定操作）を無効化。"""
        footer = getattr(self, "grade_footer", None)
        if footer is not None:
            footer.setEnabled(not bool(locked))

    def palette_maximize_write_items(self) -> list[dict[str, Any]]:
        fid = self._selected_field_id() or ""
        items: list[dict[str, Any]] = []
        for it in self._items:
            rid = int(it.get("result_id") or 0)
            if rid not in self._selected_ids or not it.get("ok"):
                continue
            row = it.get("row") or {}
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
        self._render_grid()

    def palette_test_id(self) -> str | None:
        tid = getattr(self.app, "active_test_id", None)
        return str(tid) if tid else None

    def palette_refresh_annotation_cache(self) -> None:
        test_id = self.palette_test_id()
        fid = self._selected_field_id() or ""
        if not test_id or not fid:
            return
        field = next((f for f in self._fields if str(f.get("id")) == fid), None)
        for item in self._items:
            rid = int(item.get("result_id") or 0)
            local = get_text_annotations(test_id, rid, fid)
            sheet = get_text_annotations(test_id, rid, SHEET_FIELD_ID)
            sheet_tb = (
                project_sheet_text_to_field_local(sheet, field) if field else []
            )
            item["text_annotations"] = list(local) + list(sheet_tb)

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
        for item in self._items:
            if int(item.get("result_id") or 0) == int(result_id):
                prev = item.get("text_annotations") or []
                sheet = [a for a in prev if str(a.get("source") or "") == "sheet"]
                item["text_annotations"] = list(items) + sheet
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
        for item in self._items:
            if int(item.get("result_id") or 0) == int(result_id):
                item["text_annotations"] = [
                    a
                    for a in (item.get("text_annotations") or [])
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
        for item in self._items:
            if int(item.get("result_id") or 0) == int(result_id):
                item["ink_strokes"] = list(strokes)
                break

    def _reload_grades(self, *, silent: bool = False) -> None:
        """DB の判定を再読込（⑦ 一括採点後の確認用。画像は再取得しない）。"""
        fid = self._selected_field_id()
        test_id = self.app.active_test_id
        if not fid or not test_id or not self._items:
            self._load_crops_async()
            return
        by_id = {r["id"]: r for r in get_all_results(test_id)}
        for item in self._items:
            rid = int(item.get("result_id") or 0)
            row = by_id.get(rid)
            if not row:
                continue
            item["judgment"] = normalize_judgment(row.get("judgments", {}).get(fid, ""))
            item["score"] = row.get("scores", {}).get(fid)
            if item.get("row") is not None:
                item["row"]["answer_text"] = (
                    str(row.get("textMapping", {}).get(fid, "") or "").strip() or "なし"
                )
        self._selected_ids.clear()
        self._refresh_criteria_mismatch_ids()
        if self._filter_btns.get("基準不一致") and self._filter_btns["基準不一致"].isChecked():
            self._refresh_filter_snapshot()
        self._render_grid(preserve_scroll=False)
        self._update_status_summary()
        self._rebuild_field_combo(prefer_fid=fid)
        if not silent:
            h.info(self, "再読込", "自動採点・手動採点で共有している判定を DB から読み直しました。")

    def _on_export_manual_to_criteria(self) -> None:
        """この記述欄の手動採点を多数決で採点基準へ反映（⑧へ）。"""
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        decisions = manual_grade_decisions(self.app.active_test_id, fid)
        if not decisions:
            h.warn(
                self,
                "反映不可",
                "確定判定（○△×）が付いた答案がありません。",
            )
            return
        lines = [
            f"この記述欄の手動採点 {len(decisions)} 件を、一件ずつ採点基準へ上書きします。",
            "採点基準と違っていても、その判定・配点で置き換えます。",
            "この操作では手動採点結果へは書き戻しません。",
        ]
        if not confirm_saved_then_transfer(
            self,
            self.app.active_test_id,
            fid,
            criteria_rules=None,
            title="採点基準へ反映",
            detail_lines=lines,
        ):
            return
        try:
            res = import_manual_grades_into_criteria(self.app.active_test_id, fid)
            msg = (
                f"採点基準へ {res.get('decision_count', 0)} 件を上書きしました"
                f"（回答文字列 {res.get('saved_count', 0)} 件）。"
            )
            h.info(self, "反映完了", msg)
            self._reload_grade_views()
        except Exception as e:
            h.error(self, "エラー", str(e))

    def reload_field_grades_from_db(self) -> None:
        if not self.app.active_test_id or not self._selected_field_id():
            return
        self._reload_grades(silent=True)

    def _reload_grade_views(self) -> None:
        pages = getattr(self.app, "pages", None) or {}
        for page in pages.values():
            reload = getattr(page, "reload_field_grades_from_db", None)
            if callable(reload):
                reload()
            refresh_btn = getattr(page, "refresh_transfer_undo_button", None)
            if callable(refresh_btn):
                refresh_btn()

    def refresh_transfer_undo_button(self) -> None:
        self._update_link_dependent_ui()

    def _on_undo_transfer(self) -> None:
        fid = self._selected_field_id()
        if not self.app.active_test_id or not fid:
            return
        try:
            if not undo_last_transfer(self, self.app.active_test_id, fid):
                return
            self._reload_grade_views()
            h.info(self, "取り消しました", "保存した状態に戻しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_import_criteria_to_manual(self) -> None:
        """保存済み採点基準を、この記述欄の手動採点結果へ取込。"""
        fid = self._selected_field_id()
        if not self.app.require_active_test() or not fid:
            return
        if not confirm_saved_then_transfer(
            self,
            self.app.active_test_id,
            fid,
            criteria_rules=None,
            title="採点基準から取込",
            detail_lines=[
                "⑧で保存済みの採点基準を、同じ回答文字列の答案へ反映します。",
                "すでに付いている判定が基準と違う答案は、手動採点のまま残します。",
            ],
        ):
            return
        try:
            n = apply_saved_criteria_to_field(self.app.active_test_id, fid)
            self._reload_grades(silent=True)
            self._reload_grade_views()
            h.info(
                self,
                "取込完了",
                f"採点基準から {n} 件の判定・配点を取り込みました。",
            )
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _update_status_summary(self) -> None:
        counts = {"○": 0, "△": 0, "×": 0, "?": 0, "未採点": 0}
        for item in self._items:
            j = normalize_judgment(item.get("judgment"))
            if j in ("○", "△", "×", "?"):
                counts[j] += 1
            else:
                counts["未採点"] += 1
        self.status_label.setText(
            f"○{counts['○']} △{counts['△']} ×{counts['×']} "
            f"?{counts['?']} 未採点{counts['未採点']}"
        )

    def _answer_aggregate_order(self) -> dict[str, int]:
        """⑧ 回答の集約と同じ順（人数降順・回答テキスト昇順）の順位マップ。"""
        fid = self._selected_field_id()
        test_id = self.app.active_test_id
        if not fid or not test_id:
            return {}
        unique = get_unique_answers(test_id, fid)
        return {str(u.get("answer_text") or "なし"): i for i, u in enumerate(unique)}

    def _sort_items(self) -> None:
        mode = self._sort_mode
        if mode in ("agg_file", "agg_id"):
            order = self._answer_aggregate_order()

            def key_fn(item: dict[str, Any]) -> tuple:
                row = item.get("row") or {}
                ans = str(row.get("answer_text") or "なし")
                rank = order.get(ans, 10**9)
                if mode == "agg_id":
                    sec = str(row.get("studentId") or "").strip().lower()
                else:
                    sec = str(row.get("fileName") or "").lower()
                return (rank, sec, str(row.get("fileName") or "").lower())

            self._items.sort(key=key_fn)
        elif mode == "judgment_file":
            self._items.sort(
                key=lambda i: (
                    *self._judgment_sort_rank(i),
                    str((i.get("row") or {}).get("fileName") or "").lower(),
                )
            )
        elif mode == "judgment_id":
            self._items.sort(
                key=lambda i: (
                    *self._judgment_sort_rank(i),
                    str((i.get("row") or {}).get("studentId") or "").strip().lower(),
                    str((i.get("row") or {}).get("fileName") or "").lower(),
                )
            )
        elif mode == "id":
            self._items.sort(
                key=lambda i: (
                    str((i.get("row") or {}).get("studentId") or "").strip().lower(),
                    str((i.get("row") or {}).get("fileName") or "").lower(),
                )
            )
        else:
            self._items.sort(
                key=lambda i: str((i.get("row") or {}).get("fileName") or "").lower()
            )

    def _item_filter_tags(self, item: dict[str, Any]) -> list[str]:
        """この回答が属するフィルタタグ（トグル ON のいずれかと一致すれば表示）。"""
        j = normalize_judgment(item.get("judgment"))
        ans = str((item.get("row") or {}).get("answer_text") or "").strip() or "なし"
        tags: list[str] = []
        if j == "○":
            tags.append("○")
        elif j == "△":
            tags.append("△")
        elif j == "×":
            tags.append("×")
        elif j == PENDING_JUDGMENT:
            tags.append("?")
        else:
            tags.append("未採点")
            tags.append("未判定")
        if j in ("○", "△", "×"):
            tags.append("採点済み")
        if ans == "なし":
            tags.append("無回答")
        rid = int(item.get("result_id") or 0)
        if rid and rid in self._criteria_mismatch_ids:
            tags.append("基準不一致")
        return tags

    def _refresh_criteria_mismatch_ids(self) -> None:
        """⑧採点基準と手動採点の不一致 ID を再計算。"""
        fid = self._selected_field_id()
        test_id = self.app.active_test_id
        if not fid or not test_id:
            self._criteria_mismatch_ids = set()
            return
        try:
            self._criteria_mismatch_ids = manual_criteria_mismatch_ids(
                test_id, fid, max_score=self._field_max_score()
            )
        except Exception:
            self._criteria_mismatch_ids = set()

    def _filters_active(self) -> bool:
        if any(btn.isChecked() for btn in self._filter_btns.values()):
            return True
        return (
            self._tri_filter_key != "all"
            and self._field_max_score() > 1
            and bool(self._tri_filter_btns)
        )

    def _refresh_filter_snapshot(self) -> None:
        """フィルタ操作時のみ表示対象を確定（判定変更では再計算しない）。"""
        if self._filter_btns.get("基準不一致") and self._filter_btns["基準不一致"].isChecked():
            self._refresh_criteria_mismatch_ids()
        if not self._filters_active():
            self._filter_snapshot_ids = None
            return
        self._filter_snapshot_ids = {
            int(i.get("result_id") or 0)
            for i in self._items
            if self._item_passes_filter(i) and int(i.get("result_id") or 0)
        }

    def _judgment_sort_rank(self, item: dict[str, Any]) -> tuple[int, int]:
        """○ → △(部分点降順) → × → ? → 未採点。"""
        j = normalize_judgment(item.get("judgment"))
        if j == "○":
            return 0, 0
        if j == "△":
            try:
                partial = -int(float(item.get("score") or 0))
            except (TypeError, ValueError):
                partial = 0
            return 1, partial
        if j == "×":
            return 2, 0
        if j == PENDING_JUDGMENT:
            return 3, 0
        return 4, 0

    def _item_passes_filter(self, item: dict[str, Any]) -> bool:
        """すべて OFF＝全件表示。1つでも ON なら、ON のタグに該当するものだけ（OR）。"""
        active = {k for k, btn in self._filter_btns.items() if btn.isChecked()}
        if not active:
            return True
        tags = self._item_filter_tags(item)
        if not any(t in active for t in tags):
            return False
        j = normalize_judgment(item.get("judgment"))
        sc = item.get("score")
        if j == "△" and self._tri_filter_key != "all" and self._field_max_score() > 1:
            # △ ボタンが OFF でも「採点済み」だけで見ている場合は部分点フィルタを適用
            try:
                return int(float(sc)) == int(self._tri_filter_key)
            except (TypeError, ValueError):
                return False
        return True

    def _filtered_items(self) -> list[dict[str, Any]]:
        if self._filter_snapshot_ids is None:
            return list(self._items)
        visible_ids = set(self._filter_snapshot_ids)
        # 判定順ソート時: スナップショットで「消えない」を維持しつつ、
        # 現在フィルタに合致する件は即時表示（再フィルタ不要）
        if self._sort_mode in ("judgment_file", "judgment_id"):
            visible_ids.update(
                int(i.get("result_id") or 0)
                for i in self._items
                if self._item_passes_filter(i) and int(i.get("result_id") or 0)
            )
        return [
            i
            for i in self._items
            if int(i.get("result_id") or 0) in visible_ids
        ]

    def _current_page_items(self) -> list[dict[str, Any]]:
        """全件表示ならフィルタ後すべて、指定件数表示なら現在ページのみ。"""
        visible = self._filtered_items()
        if self._show_all_pages:
            return visible
        size = max(1, self._page_size)
        total_vis = len(visible)
        pages = max(1, (total_vis + size - 1) // size) if total_vis else 1
        if self._page_index >= pages:
            self._page_index = pages - 1
        if self._page_index < 0:
            self._page_index = 0
        start = self._page_index * size
        return visible[start : start + size]

    def _clear_selection(self) -> None:
        self._selected_ids.clear()
        self._apply_tile_selection_styles()
        self._update_selection_label()
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.notify_draw_selection_changed()

    def _select_all_visible(self) -> None:
        """表示中の画像をすべて選択（フィルタ適用時は表示分、指定件数表示時は現在ページ）。"""
        ids = {
            int(i.get("result_id") or 0)
            for i in self._current_page_items()
            if int(i.get("result_id") or 0)
        }
        if not ids:
            h.warn(self, "表示なし", "選択できる表示中の画像がありません。")
            return
        self._selected_ids = ids
        self._apply_tile_selection_styles()
        self._update_selection_label()
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.notify_draw_selection_changed()

    def _select_ungraded(self) -> None:
        """表示中の画像のうち、判定なし（未採点）を一括選択する。"""
        ids = {
            int(i.get("result_id") or 0)
            for i in self._current_page_items()
            if not normalize_judgment(i.get("judgment"))
            and int(i.get("result_id") or 0)
        }
        if not ids:
            h.warn(self, "未採点なし", "表示中の未採点（判定なし）はありません。")
            return
        self._selected_ids = ids
        self._apply_tile_selection_styles()
        self._update_selection_label()
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.notify_draw_selection_changed()

    def _resolve_judgment_score(self, judgment: str) -> tuple[str, int] | None:
        raw = str(judgment or "").strip()
        if raw in ("", "未判定", self._CLEAR_JUDGMENT_KEY):
            return "", 0
        max_score = self._field_max_score()
        nj = normalize_judgment(judgment)
        if nj == "○":
            return nj, max_score
        if nj == "×":
            return nj, 0
        if nj == PENDING_JUDGMENT:
            return nj, 0
        if nj == "△":
            if max_score <= 1:
                return None
            if max_score == 2:
                return nj, 1
            score, ok = QInputDialog.getInt(
                self,
                "部分点",
                f"選択 {len(self._selected_ids)} 件の得点（1〜{max_score - 1}）",
                1,
                1,
                max_score - 1,
            )
            if not ok:
                return None
            return nj, score
        return None

    @staticmethod
    def _item_answer_text(item: dict[str, Any]) -> str:
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        return str(row.get("answer_text") or item.get("answer_text") or "").strip() or "なし"

    def _peer_items_for_answer(self, answer_text: str) -> list[dict[str, Any]]:
        key = str(answer_text or "").strip() or "なし"
        return [i for i in self._items if self._item_answer_text(i) == key]

    def _answers_needing_peer_check(
        self, result_ids: set[int], judgment: str, score: int
    ) -> list[str]:
        """今付けた回答以外に、同じ判定・配点でない同OCRがある回答文字列。"""
        if self._in_group_dialog:
            return []
        nj = normalize_judgment(judgment)
        if nj not in ("○", "△", "×"):
            return []
        try:
            target_score = int(score)
        except (TypeError, ValueError):
            target_score = 0
        found: list[str] = []
        seen: set[str] = set()
        for item in self._items:
            rid = int(item.get("result_id") or 0)
            if rid not in result_ids:
                continue
            ans = self._item_answer_text(item)
            if ans in seen:
                continue
            others = [
                i
                for i in self._peer_items_for_answer(ans)
                if int(i.get("result_id") or 0) not in result_ids
            ]
            if not others:
                continue
            needs = False
            for other in others:
                oj = normalize_judgment(other.get("judgment"))
                try:
                    os = int(other.get("score") or 0)
                except (TypeError, ValueError):
                    os = 0
                if oj != nj or os != target_score:
                    needs = True
                    break
            if needs:
                seen.add(ans)
                found.append(ans)
        return found

    def _push_criteria_to_step8(
        self, field_id: str, answers: set[str], judgment: str, score: int
    ) -> None:
        pages = getattr(self.app, "pages", None)
        step8 = pages.get(8) if isinstance(pages, dict) else None
        fn = getattr(step8, "apply_live_criteria_grades", None)
        if fn is None:
            return
        for ans in answers:
            fn(field_id, ans, judgment, score)

    def _open_peer_checks(
        self,
        answers: list[str],
        judgment: str,
        score: int,
        exclude_ids: set[int],
    ) -> None:
        for ans in answers:
            others = [
                i
                for i in self._peer_items_for_answer(ans)
                if int(i.get("result_id") or 0) not in exclude_ids
            ]
            if not others:
                continue
            self._in_group_dialog = True
            try:
                dlg = GroupGradeDialog(
                    self,
                    ans,
                    others,
                    judgment=judgment,
                    score=score,
                )
                dlg.exec()
            finally:
                self._in_group_dialog = False
        if self._print_mark_mode or self._sort_mode in ("judgment_file", "judgment_id"):
            self._render_grid(preserve_scroll=True)
        else:
            self._sync_tile_judgment_chrome()
            self._update_selection_label()
        self._update_status_summary()
        self._rebuild_field_combo(prefer_fid=self._selected_field_id())

    def _commit_grades(
        self,
        result_ids: list[int],
        judgment: str,
        score: int,
        *,
        silent: bool = False,
    ) -> bool:
        if not self.app.require_active_test():
            return False
        fid = self._selected_field_id()
        if not fid or not result_ids:
            return False
        nj = normalize_judgment(judgment)
        id_set = {int(x) for x in result_ids if int(x or 0)}
        # 当該回答の基準反映後に、同OCRの他回答を確認する（先に一括では付けない）
        graded_ids = set(id_set)
        peer_answers = self._answers_needing_peer_check(graded_ids, nj, score)

        try:
            n = update_results_field_grades(
                self.app.active_test_id,
                fid,
                result_ids,
                nj,
                score,
            )
        except Exception as e:
            h.error(self, "保存エラー", str(e))
            return False

        linked_answers: set[str] = set()
        linked_judgment = nj
        linked_score = score
        # リンクONのときだけ、今決めた判定を⑧の当該回答へ即反映する。
        # 同じOCRの他答案へは自動で広げない（確認ポップアップで一件ずつ決める）。
        if manual_auto_grading_link_enabled() and nj in ("○", "△", "×"):
            try:
                sync_res = sync_committed_grades_to_criteria(
                    self.app.active_test_id,
                    fid,
                    result_ids,
                    nj,
                    score,
                    max_score=self._field_max_score(),
                    propagate_to_results=False,
                )
                if sync_res.get("judgment"):
                    linked_judgment = str(sync_res["judgment"])
                    linked_score = int(sync_res.get("score") or score)
                criteria_answers = set(sync_res.get("answers") or ())
                if criteria_answers:
                    self._push_criteria_to_step8(
                        fid, criteria_answers, linked_judgment, linked_score
                    )
            except Exception as e:
                h.warn(self, "採点基準への同期失敗", str(e))

        for item in self._items:
            rid = int(item.get("result_id") or 0)
            ans = self._item_answer_text(item)
            if rid in id_set or (linked_answers and ans in linked_answers):
                item["judgment"] = linked_judgment
                item["score"] = linked_score
                if linked_answers and ans in linked_answers and rid:
                    id_set.add(rid)

        mismatch_filter_on = bool(
            self._filter_btns.get("基準不一致")
            and self._filter_btns["基準不一致"].isChecked()
        )
        if mismatch_filter_on:
            self._refresh_criteria_mismatch_ids()
            self._refresh_filter_snapshot()
        elif self._filter_snapshot_ids is not None:
            self._filter_snapshot_ids |= id_set

        needs_rebuild = (
            self._print_mark_mode
            or self._sort_mode in ("judgment_file", "judgment_id")
            or mismatch_filter_on
        )
        v_bar = self.crop_scroll.verticalScrollBar()
        h_bar = self.crop_scroll.horizontalScrollBar()
        saved_v = v_bar.value() if v_bar is not None else 0
        saved_h = h_bar.value() if h_bar is not None else 0
        if needs_rebuild:
            if self._sort_mode in ("judgment_file", "judgment_id"):
                self._sort_items()
            self._render_grid(preserve_scroll=True)
        else:
            self._sync_tile_judgment_chrome()
            self._update_selection_label()
        self._update_status_summary()
        self._rebuild_field_combo(prefer_fid=fid)
        if needs_rebuild and (saved_v or saved_h):
            self._schedule_scroll_restore(self.crop_scroll, saved_v, saved_h)

        if peer_answers:
            QTimer.singleShot(
                0,
                lambda a=list(peer_answers), j=linked_judgment, s=linked_score, ids=set(graded_ids): (
                    self._open_peer_checks(a, j, s, ids)
                ),
            )
        elif not silent:
            if not nj:
                h.info(self, "反映完了", f"{n} 件の判定を解除しました（未判定）。")
            else:
                label = "保留" if nj == PENDING_JUDGMENT else nj
                h.info(self, "反映完了", f"{n} 件に {label}（{score}点）を反映しました。")

        return True

    def _apply_judgment(self, judgment: str) -> None:
        if not self.app.require_active_test():
            return
        if not self._selected_field_id():
            h.warn(self, "記述欄未選択", "記述欄を選んでください。")
            return
        if not self._selected_ids:
            h.warn(self, "未選択", "画像をタップして選択してください。")
            return
        resolved = self._resolve_judgment_score(judgment)
        if not resolved:
            return
        nj, score = resolved
        ids = list(self._selected_ids)
        self._selected_ids.clear()
        self._commit_grades(ids, nj, score)

    def _apply_palette_to_image(self, result_id: int) -> None:
        spec = self._palette_active_spec()
        if not spec or not result_id:
            return
        judgment, _key, score = spec
        self._commit_grades([result_id], judgment, score, silent=True)

    # --- グリッド ---

    def _clear_grid(self) -> None:
        self.crop_panel.clear_tiles()

    def _update_selection_label(self) -> None:
        visible = self._filtered_items()
        total_vis = len(visible)
        page_items = self._current_page_items()
        if self._show_all_pages:
            self.page_info_label.setText("")
        else:
            size = max(1, self._page_size)
            pages = max(1, (total_vis + size - 1) // size) if total_vis else 1
            self.page_info_label.setText(
                f"{self._page_index + 1} / {pages} ページ（全 {total_vis} 件）"
            )
            self.page_prev_btn.setEnabled(self._page_index > 0)
            self.page_next_btn.setEnabled(self._page_index < pages - 1)

        sel = f"{len(self._selected_ids)} 件を選択中（該当 {total_vis} 枚"
        if not self._show_all_pages:
            sel += f"・このページ {len(page_items)} 枚"
        sel += "）"
        if self._parallel_palette_mode:
            spec = self._palette_active_spec()
            if spec:
                _j, key, score = spec
                label = next(
                    (lbl for k, lbl, _j2, _s in self._palette_specs() if k == key),
                    key,
                )
                sel = f"判定パレット: {label}（{score}点）— 画像タップで即反映"
            else:
                sel = "判定パレット: 判定を選んでから画像をタップ"
        self.selection_label.setText(sel)

    def _apply_tile_selection_styles(self) -> None:
        """選択枠だけ更新（再構築しない＝スクロール位置を維持）。"""
        self._sync_tile_judgment_chrome(update_badges=False)

    def _sync_tile_judgment_chrome(self, *, update_badges: bool = True) -> None:
        """判定色の枠・バッジをデータに合わせて更新（タイル再構築なし）。"""
        by_id = {
            int(i.get("result_id") or 0): i
            for i in self._current_page_items()
            if int(i.get("result_id") or 0)
        }
        for stack in self._ink_stacks:
            tile = stack.parentWidget()
            if tile is None:
                continue
            rid = int(getattr(stack, "result_id", 0) or 0)
            item = by_id.get(rid)
            if item is None:
                continue
            j = normalize_judgment(item.get("judgment"))
            selected = rid in self._selected_ids
            bg, border = self._tile_colors(j, selected=selected)
            border_w = 3 if selected else 2
            tile.setStyleSheet(
                f"QFrame {{ background: {bg}; border: {border_w}px solid {border};"
                f" border-radius: 6px; }}"
            )
            if not update_badges or self._print_mark_mode:
                continue
            sc = item.get("score")
            badge = tile.findChild(QLabel, "judgment_badge")
            if j:
                try:
                    sc_txt = f" {int(sc)}点" if sc is not None and sc != "" else ""
                except (TypeError, ValueError):
                    sc_txt = ""
                stroke = self._judgment_stroke_color(j) or COLORS["accent"]
                text = f"{j}{sc_txt}"
                style = (
                    f"border: none; font-size: 11px; font-weight: 700; color: {stroke};"
                    f" background: transparent;"
                )
                if badge is None:
                    badge = QLabel(text)
                    badge.setObjectName("judgment_badge")
                    badge.setStyleSheet(style)
                    lay = tile.layout()
                    if lay is not None:
                        # 画像スタックの直後に挿入
                        lay.insertWidget(1, badge)
                else:
                    badge.setText(text)
                    badge.setStyleSheet(style)
                    badge.show()
            elif badge is not None:
                badge.hide()
                badge.clear()

    def _schedule_scroll_restore(
        self, scroll: QScrollArea, saved_v: int, saved_h: int = 0
    ) -> None:
        """FlowLayout 再計算で一時的に range=0 になっても、見ていた位置へ戻す。"""
        if saved_v <= 0 and saved_h <= 0:
            return
        v_bar = scroll.verticalScrollBar()
        h_bar = scroll.horizontalScrollBar()
        token = object()
        self._scroll_restore_token = token

        def _apply() -> None:
            if getattr(self, "_scroll_restore_token", None) is not token:
                return
            if v_bar is not None and saved_v > 0:
                mx = v_bar.maximum()
                if mx > 0:
                    v_bar.setValue(min(saved_v, mx))
            if h_bar is not None and saved_h > 0:
                mxh = h_bar.maximum()
                if mxh > 0:
                    h_bar.setValue(min(saved_h, mxh))

        def _on_v_range(_mn: int, mx: int) -> None:
            if getattr(self, "_scroll_restore_token", None) is not token:
                if v_bar is not None:
                    try:
                        v_bar.rangeChanged.disconnect(_on_v_range)
                    except RuntimeError:
                        pass
                return
            if mx <= 0:
                return
            v_bar.setValue(min(saved_v, mx))
            if mx >= saved_v:
                try:
                    v_bar.rangeChanged.disconnect(_on_v_range)
                except RuntimeError:
                    pass

        if v_bar is not None and saved_v > 0:
            v_bar.rangeChanged.connect(_on_v_range)
        for ms in (0, 16, 50, 100, 200):
            QTimer.singleShot(ms, _apply)

        def _cleanup() -> None:
            if getattr(self, "_scroll_restore_token", None) is token:
                self._scroll_restore_token = None
            if v_bar is not None:
                try:
                    v_bar.rangeChanged.disconnect(_on_v_range)
                except RuntimeError:
                    pass
            _apply()

        QTimer.singleShot(300, _cleanup)

    def _render_grid(self, preserve_scroll: bool = True) -> None:
        if not preserve_scroll:
            self._scroll_restore_token = None
        v_bar = self.crop_scroll.verticalScrollBar()
        h_bar = self.crop_scroll.horizontalScrollBar()
        saved_v = v_bar.value() if v_bar is not None else 0
        saved_h = h_bar.value() if h_bar is not None else 0
        had_tiles = bool(self._ink_stacks)

        self._clear_grid()
        self._ink_stacks = []
        page_items = self._current_page_items()
        self._update_selection_label()
        if self._items:
            self._update_status_summary()
        if not page_items:
            self.crop_panel.set_message(
                "表示する画像がありません。フィルタまたは記述欄を確認してください。"
            )
            ctrl = getattr(self.app, "palette_controller", None)
            if ctrl is not None:
                ctrl.notify_draw_selection_changed()
            return
        zoom = max(30, min(400, self.crop_controls.zoom_value())) / 100.0
        for idx, item in enumerate(page_items):
            tile = self._make_tile(item, zoom)
            self.crop_panel.add_tile(tile, idx)
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.ensure_palette_visible()
            ctrl.notify_draw_selection_changed()

        if preserve_scroll and had_tiles and (saved_v or saved_h):
            self._schedule_scroll_restore(self.crop_scroll, saved_v, saved_h)

    def _judgment_stroke_color(self, judgment: str) -> str | None:
        mark = (self._feedback_style or {}).get("mark") or {}
        j = normalize_judgment(judgment)
        if j == "○":
            return str((mark.get("maru") or {}).get("strokeColor") or "#dc2626")
        if j == "△":
            return str((mark.get("sankaku") or {}).get("strokeColor") or "#ea580c")
        if j == "×":
            return str((mark.get("batsu") or {}).get("strokeColor") or "#2563eb")
        if j == PENDING_JUDGMENT:
            return "#a16207"  # 保留（琥珀色）
        return None

    def _tile_colors(self, judgment: str, *, selected: bool) -> tuple[str, str]:
        """タイル余白の背景色・枠色（⑫の判定色ベース。選択時は紫）。"""
        if selected:
            return COLORS["selection_soft"], COLORS["selection"]
        stroke = self._judgment_stroke_color(judgment)
        if stroke:
            return _mix_hex_with_white(stroke, 0.82), stroke
        return COLORS["surface"], COLORS["border"]

    def _pil_with_mark(self, pil: Image.Image, judgment: str, score: Any) -> Image.Image:
        """⑫ 個票プレビューと同じ判定マーク・得点を画像上に重ねる。"""
        return composite_mark_on_image(
            pil, judgment, score, self._feedback_style, supersample=4
        )

    def _make_tile(
        self,
        item: dict[str, Any],
        zoom: float,
        selected_ids: set[int] | None = None,
        on_click: Any = None,
    ) -> QWidget:
        selected_set = self._selected_ids if selected_ids is None else selected_ids
        click_handler = self._on_tile_image_clicked if on_click is None else on_click

        rid = int(item.get("result_id") or 0)
        selected = rid in selected_set
        j = normalize_judgment(item.get("judgment"))
        sc = item.get("score")
        tile = QFrame()
        pad = 2 if self._print_mark_mode else 3
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(pad, pad, pad, pad)
        lay.setSpacing(1)

        if not item.get("ok"):
            tile.setStyleSheet(
                f"QFrame {{ background: {COLORS['danger_soft']}; border: 2px solid #fca5a5;"
                f" border-radius: 6px; }}"
            )
            err = QLabel(str(item.get("error") or "読込失敗"))
            err.setWordWrap(True)
            lay.addWidget(err)
            return tile

        bg, border = self._tile_colors(j, selected=selected)
        border_w = 3 if selected else 2
        tile.setStyleSheet(
            f"QFrame {{ background: {bg}; border: {border_w}px solid {border};"
            f" border-radius: 6px; }}"
        )
        tile.setCursor(Qt.PointingHandCursor)

        row = item["row"]
        pil = item["pil"]
        if self._print_mark_mode and j:
            pil = self._pil_with_mark(pil, j, sc)

        fid = self._selected_field_id() or ""
        placement_meta = {
            "resultId": rid,
            "fieldId": fid,
            "studentId": row.get("studentId"),
            "studentName": str(row.get("name") or row.get("studentName") or ""),
        }
        ink_stack = CropInkImageStack(
            pil_image=pil,
            field_id=fid,
            result_id=rid,
            strokes=item.get("ink_strokes") or [],
            sheet_strokes=item.get("sheet_ink_strokes") or [],
            annotations=item.get("text_annotations") or [],
            zoom=zoom,
            placement_meta=placement_meta,
            on_strokes_changed=lambda s, rid=rid: self._save_ink_strokes(rid, s),
            on_annotations_changed=lambda s, rid=rid: self.palette_save_annotations(
                rid, fid, s
            ),
            on_sheet_box_deleted=lambda bid, rid=rid: self._on_sheet_box_deleted(
                rid, bid
            ),
        )
        ink_stack.image_clicked.connect(
            lambda rid=rid: click_handler(rid)
        )
        self._ink_stacks.append(ink_stack)
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.register_stack(ink_stack)
        lay.addWidget(ink_stack)

        # 文字モードのみ、画像下に判定・得点を表示（後から in-place 更新できるよう命名）
        if j and not self._print_mark_mode:
            try:
                sc_txt = f" {int(sc)}点" if sc is not None and sc != "" else ""
            except (TypeError, ValueError):
                sc_txt = ""
            stroke = self._judgment_stroke_color(j) or COLORS["accent"]
            badge = QLabel(f"{j}{sc_txt}")
            badge.setObjectName("judgment_badge")
            badge.setStyleSheet(
                f"border: none; font-size: 11px; font-weight: 700; color: {stroke};"
                f" background: transparent;"
            )
            lay.addWidget(badge)

        if self.crop_controls.show_id():
            id_lbl = QLabel(f"ID: {row.get('studentId') or '-'}")
            id_lbl.setStyleSheet("border: none; background: transparent;")
            lay.addWidget(id_lbl)
        if self.crop_controls.show_file_name():
            fn = QLabel(str(row.get("fileName") or ""))
            fn.setWordWrap(True)
            fn.setStyleSheet(
                f"font-size: 9px; color: {COLORS['text_secondary']};"
                f" border: none; background: transparent;"
            )
            lay.addWidget(fn)
        if self.crop_controls.show_ocr_text():
            ans = QLabel(str(row.get("answer_text") or ""))
            ans.setWordWrap(True)
            ans.setStyleSheet(
                f"font-size: 10px; color: {COLORS['text_secondary']};"
                f" border: none; background: transparent;"
            )
            lay.addWidget(ans)

        return tile

    def _on_tile_image_clicked(self, result_id: int) -> None:
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.set_active_result_id(result_id)
        if self._parallel_palette_mode:
            if self._palette_active_key:
                self._apply_palette_to_image(result_id)
            return
        if ctrl is not None and ctrl.is_pen_draw_lock_active():
            # ペンON＋選択あり: 選択の変更・未選択タップを無視
            return
        if result_id in self._selected_ids:
            self._selected_ids.discard(result_id)
        else:
            self._selected_ids.add(result_id)
        self._apply_tile_selection_styles()
        self._update_selection_label()
        if ctrl is not None:
            ctrl.notify_draw_selection_changed()
