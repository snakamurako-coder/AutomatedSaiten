"""手動採点ページ（空DB作成 → 画像を見ながら ○△×。⑦ OCR は任意）。"""

from __future__ import annotations

from typing import Any

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from models.criteria_repo import get_unique_answers
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
from ui_qt.crop_widgets import CropDisplayControls
from ui_qt.helpers import pil_to_qpixmap
from ui_qt.layout_helpers import CropTileColumnPanel, configure_crop_image_scroll, make_expanding
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


class StepManualPage(QWidget):
    """記述欄画像を並べ、複数選択して ○△×/? を一括反映する手動採点。"""

    _MAIN_FILTERS = ("○", "△", "×", "?", "未採点", "未判定", "採点済み", "無回答")
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
        self._feedback_style: dict[str, Any] = get_feedback_style()
        self._show_all_pages = False  # False=指定件数表示 / True=全件表示
        self._page_size = 20
        self._page_index = 0
        self._parallel_palette_mode = False  # False=同一判定連続選択 / True=切り替え平行選択
        self._palette_active_key: str | None = None
        self._palette_btns: dict[str, QPushButton] = {}
        self._ink_stacks: list[CropInkImageStack] = []

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- 上部作業エリア（コンパクトにして画像領域を最大化）---
        work = QWidget()
        work_lay = QVBoxLayout(work)
        work_lay.setContentsMargins(0, 0, 0, 4)
        work_lay.setSpacing(4)

        self._create_page_controls()

        # タイトル行＝全件/指定件数トグルを同じ高さに
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        title_row.setAlignment(Qt.AlignVCenter)
        title_lbl = h.title_label("手動採点")
        title_row.addWidget(title_lbl, 0, Qt.AlignVCenter)
        title_row.addWidget(self._build_page_mode_row(), 1, Qt.AlignVCenter)
        work_lay.addLayout(title_row)

        # 記述欄・並べ替え（直下に選択件数＋判定件数）／右にフィルタ
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
        work_lay.addLayout(header)

        self.crop_scroll = QScrollArea()
        self.crop_scroll.setWidgetResizable(True)
        configure_crop_image_scroll(self.crop_scroll)
        self.crop_scroll.viewport().setAttribute(Qt.WA_TabletTracking, True)
        self.crop_scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['surface']}; }}"
        )
        make_expanding(self.crop_scroll)
        self.crop_panel = CropTileColumnPanel(margins=(6, 6, 6, 6), spacing=6)
        self.crop_scroll.setWidget(self.crop_panel)
        work_lay.addWidget(self.crop_scroll, 1)
        root.addWidget(work, 1)

        # --- 最下部固定オーバーレイ ---
        self.grade_footer = self._build_footer_overlay()
        root.addWidget(self.grade_footer)

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
        self._render_grid()

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
        self._render_grid()

    def _on_page_prev(self) -> None:
        if self._page_index > 0:
            self._page_index -= 1
            self._render_grid()

    def _on_page_next(self) -> None:
        self._page_index += 1
        self._render_grid()

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
        self.crop_controls.connect_zoom_changed(self._render_grid)
        self.crop_controls.connect_meta_changed(self._render_grid)
        left_lay.addWidget(self.crop_controls)
        left_lay.addWidget(self._build_mark_mode_switch())
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
            if btn is self.btn_unjudged:
                btn.setFixedSize(64, 36)
                btn.setStyleSheet(
                    f"QPushButton {{ font-size: 11px; font-weight: 700;"
                    f" padding: 0 8px; min-height: 0; min-width: 0;"
                    f" color: {COLORS['text_secondary']};"
                    f" border: 2px solid {COLORS['border']}; border-radius: 6px;"
                    f" background: {COLORS['surface']}; }}"
                    f"QPushButton:hover {{ background: #f1f5f9; }}"
                )
            else:
                btn.setFixedSize(44, 36)
            btn.setCursor(Qt.PointingHandCursor)
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
        lay.addWidget(h.button("全選択", self._select_all_visible))
        lay.addStretch()
        return box

    # --- データ ---

    def _judgment_button_style(self, stroke: str) -> str:
        """⑫ 出力書式の判定色をボタンに反映。"""
        soft = _mix_hex_with_white(stroke, 0.82)
        return (
            f"QPushButton {{ background: {soft}; color: {stroke}; font-weight: 800;"
            f" font-size: 16px; border: 2px solid {stroke}; border-radius: 6px;"
            f" padding: 0; min-height: 0; min-width: 0; }}"
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
            btn.setFixedHeight(36)
            btn.setMinimumWidth(44)
            btn.setCursor(Qt.PointingHandCursor)
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
        self._render_grid()

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
        if not self.app.require_active_test():
            return
        self._feedback_style = get_feedback_style()
        self._apply_judgment_button_colors()
        self._fields = get_answer_fields(self.app.active_test_id)
        current_fid = self._selected_field_id()
        self._rebuild_field_combo(prefer_fid=current_fid)
        self._rebuild_triangle_filters()
        self._update_judge_buttons()
        self._rebuild_palette_buttons()
        if self._fields:
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
        self._selected_ids.clear()
        self._page_index = 0
        self._filter_snapshot_ids = None
        self._rebuild_triangle_filters()
        self._update_judge_buttons()
        self._load_crops_async()

    def _on_sort_changed(self, _index: int) -> None:
        self._sort_mode = self.sort_combo.currentData() or "file"
        self._page_index = 0
        self._sort_items()
        self._render_grid()

    def _on_filter_toggled(self) -> None:
        self._page_index = 0
        self._refresh_filter_snapshot()
        self._render_grid()

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
            self._render_grid()
            return
        self._tri_filter_key = key
        for k, btn in self._tri_filter_btns.items():
            if k != key:
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        self._page_index = 0
        self._refresh_filter_snapshot()
        self._render_grid()

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
            self._render_grid()
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
            self._refresh_filter_snapshot()
            self._render_grid()
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

    def _reload_grades(self) -> None:
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
        self._render_grid()
        self._update_status_summary()
        self._rebuild_field_combo(prefer_fid=fid)
        h.info(self, "再読込", "自動採点・手動採点で共有している判定を DB から読み直しました。")

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
        return tags

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
        self._render_grid()

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
        self._render_grid()

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
        self._render_grid()

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
        id_set = set(result_ids)
        for item in self._items:
            if item.get("result_id") in id_set:
                item["judgment"] = nj
                item["score"] = score
        if self._filter_snapshot_ids is not None:
            self._filter_snapshot_ids |= id_set
        if self._sort_mode in ("judgment_file", "judgment_id"):
            self._sort_items()
        self._render_grid()
        self._update_status_summary()
        self._rebuild_field_combo(prefer_fid=fid)
        if not silent:
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

    def _render_grid(self) -> None:
        self._clear_grid()
        self._ink_stacks = []
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

    def _make_tile(self, item: dict[str, Any], zoom: float) -> QWidget:
        rid = int(item.get("result_id") or 0)
        selected = rid in self._selected_ids
        j = normalize_judgment(item.get("judgment"))
        sc = item.get("score")
        tile = QFrame()
        pad = 4 if self._print_mark_mode else 6
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(pad, pad, pad, pad)
        lay.setSpacing(2)

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
            lambda rid=rid: self._on_tile_image_clicked(rid)
        )
        self._ink_stacks.append(ink_stack)
        ctrl = getattr(self.app, "palette_controller", None)
        if ctrl is not None:
            ctrl.register_stack(ink_stack)
        lay.addWidget(ink_stack)

        # 文字モードのみ、画像下に判定・得点を表示
        if j and not self._print_mark_mode:
            try:
                sc_txt = f" {int(sc)}点" if sc is not None and sc != "" else ""
            except (TypeError, ValueError):
                sc_txt = ""
            stroke = self._judgment_stroke_color(j) or COLORS["accent"]
            badge = QLabel(f"{j}{sc_txt}")
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
        self._render_grid()
        if ctrl is not None:
            ctrl.notify_draw_selection_changed()
