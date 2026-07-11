"""テキスト書式パネル（テンプレート・文字色・サイズ・装飾）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from models.text_annotation_repo import (
    DEFAULT_TEXT_COLOR,
    DEFAULT_TEXT_STYLE,
    TEXT_PALETTE_COLORS,
    TEXT_STYLE_TEMPLATES,
    resolve_text_style,
)
from ui_qt.floating_palette.text_rich import normalize_text_align
from ui_qt.style import COLORS


def _qt_widget_alive(widget) -> bool:
    if widget is None:
        return False
    try:
        from shiboken6 import isValid

        return bool(isValid(widget))
    except Exception:
        return True


class FormatPalettePanel(QWidget):
    """テキストボックス選択時の書式コントロール。"""

    style_changed = Signal(dict)
    char_format_changed = Signal(dict)
    edit_done_requested = Signal()
    edit_requested = Signal()
    delete_requested = Signal()
    speech_toggled = Signal(bool)
    layout_hint_changed = Signal()
    match_placement_toggled = Signal(bool)

    _SEGMENT_BTN_PAD = 10
    _SEGMENT_BTN_PAD_TALL = 20
    _ALIGN_BTN_HEIGHT_TALL = 22
    _ACTION_BTN_PAD = 8
    _DETAIL_VERTICAL_INTERVAL = 4
    _DETAIL_ALIGN_INTERVAL_TALL = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._align_locked = False
        self._align_tall = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        tpl_row = QHBoxLayout()
        tpl_row.setSpacing(4)
        tpl_lbl = QLabel("背景")
        tpl_lbl.setObjectName("FormatPaletteLabel")
        tpl_lbl.setFixedWidth(48)
        tpl_row.addWidget(tpl_lbl)
        tpl_a = QPushButton("なし")
        tpl_a.setObjectName("ToolSegmentBtn")
        tpl_a.setToolTip("文字のみ（背景・枠なし）")
        tpl_a.clicked.connect(lambda: self.apply_template("A"))
        tpl_b = QPushButton("半透明")
        tpl_b.setObjectName("ToolSegmentBtn")
        tpl_b.setToolTip("文字色の補色を20%で背景表示")
        tpl_b.clicked.connect(lambda: self.apply_template("B"))
        self._tighten_segment_btn(tpl_a, "なし")
        self._tighten_segment_btn(tpl_b, "半透明")
        tpl_row.addWidget(tpl_a)
        tpl_row.addWidget(tpl_b)
        tpl_row.addStretch()
        root.addLayout(tpl_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        color_lbl = QLabel("文字色")
        color_lbl.setObjectName("FormatPaletteLabel")
        color_lbl.setFixedWidth(48)
        color_row.addWidget(color_lbl)
        self._color_row = color_row
        self._color_btns: list[QPushButton] = []
        self._color_group = QButtonGroup(self)
        root.addLayout(color_row)

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(4)
        size_lbl = QLabel("サイズ")
        size_lbl.setObjectName("FormatPaletteLabel")
        size_lbl.setFixedWidth(36)
        metrics_row.addWidget(size_lbl)
        self._size_spin = self._make_pt_spin(
            "選択範囲またはカーソル位置の文字サイズ"
        )
        self._size_spin.valueChanged.connect(self._on_size_changed)
        metrics_row.addWidget(self._size_spin)
        spacing_lbl = QLabel("行間")
        spacing_lbl.setObjectName("FormatPaletteLabel")
        spacing_lbl.setFixedWidth(36)
        metrics_row.addWidget(spacing_lbl)
        self._line_spacing_spin = self._make_pt_spin("改行後の行の高さ（ポイント）")
        self._line_spacing_spin.valueChanged.connect(self._on_line_spacing_changed)
        metrics_row.addWidget(self._line_spacing_spin)
        metrics_row.addStretch()
        root.addLayout(metrics_row)

        align_frame = QFrame()
        self._align_frame = align_frame
        align_frame.setFrameShape(QFrame.Shape.NoFrame)
        align_frame.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        align_lay = QVBoxLayout(align_frame)
        align_lay.setContentsMargins(0, 0, 0, 0)
        align_lay.setSpacing(self._DETAIL_VERTICAL_INTERVAL)

        self._align_h_row_host = QWidget()
        self._align_h_row_host.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        h_align_row = QHBoxLayout(self._align_h_row_host)
        h_align_row.setSpacing(4)
        h_align_row.setContentsMargins(0, 0, 0, 0)
        h_align_lbl = QLabel("横")
        h_align_lbl.setObjectName("FormatPaletteLabel")
        h_align_lbl.setFixedWidth(48)
        h_align_row.addWidget(h_align_lbl)
        self._align_h_group = QButtonGroup(self)
        self._align_h_btns: dict[str, QPushButton] = {}
        for key, label in (("left", "左"), ("center", "中"), ("right", "右")):
            btn = QPushButton(label)
            btn.setObjectName("ToolSegmentBtn")
            btn.setCheckable(True)
            btn.setToolTip({"left": "左寄せ", "center": "中央", "right": "右寄せ"}[key])
            btn.clicked.connect(lambda _c=False, k=key: self._set_align_h(k))
            self._align_h_group.addButton(btn)
            self._tighten_segment_btn(btn, label)
            h_align_row.addWidget(btn)
            self._align_h_btns[key] = btn
        h_align_row.addStretch()

        self._align_v_row_host = QWidget()
        self._align_v_row_host.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        v_align_row = QHBoxLayout(self._align_v_row_host)
        v_align_row.setSpacing(4)
        v_align_row.setContentsMargins(0, 0, 0, 0)
        v_align_lbl = QLabel("縦")
        v_align_lbl.setObjectName("FormatPaletteLabel")
        v_align_lbl.setFixedWidth(48)
        v_align_row.addWidget(v_align_lbl)
        self._align_v_group = QButtonGroup(self)
        self._align_v_btns: dict[str, QPushButton] = {}
        for key, label in (("top", "上"), ("center", "中"), ("bottom", "下")):
            btn = QPushButton(label)
            btn.setObjectName("ToolSegmentBtn")
            btn.setCheckable(True)
            btn.setToolTip({"top": "上寄せ", "center": "中央", "bottom": "下寄せ"}[key])
            btn.clicked.connect(lambda _c=False, k=key: self._set_align_v(k))
            self._align_v_group.addButton(btn)
            self._tighten_segment_btn(btn, label)
            v_align_row.addWidget(btn)
            self._align_v_btns[key] = btn
        v_align_row.addStretch()

        self._detail_format_frame = QWidget()
        detail_lay = QHBoxLayout(self._detail_format_frame)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        detail_lay.setSpacing(3)
        deco_lbl = QLabel("装飾")
        deco_lbl.setObjectName("FormatPaletteLabel")
        deco_lbl.setFixedWidth(48)
        detail_lay.addWidget(deco_lbl)
        self._bold_btn = self._make_deco_btn("太字", "太字", bold=True)
        self._italic_btn = self._make_deco_btn("イタリック", "イタリック", italic=True)
        self._underline_btn = self._make_deco_btn("下線", "下線", underline=True)
        self._bold_btn.clicked.connect(lambda: self._emit_toggle("toggleBold"))
        self._italic_btn.clicked.connect(lambda: self._emit_toggle("toggleItalic"))
        self._underline_btn.clicked.connect(lambda: self._emit_toggle("toggleUnderline"))
        for deco_btn, deco_label in (
            (self._bold_btn, "太字"),
            (self._italic_btn, "イタリック"),
            (self._underline_btn, "下線"),
        ):
            self._tighten_segment_btn(deco_btn, deco_label)
            detail_lay.addWidget(deco_btn)
        detail_lay.addStretch()
        align_lay.addWidget(self._detail_format_frame)

        align_body = QHBoxLayout()
        align_body.setContentsMargins(0, 0, 0, 0)
        align_body.setSpacing(12)
        self._align_cols_host = QWidget()
        self._align_cols_host.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        self._align_cols = QVBoxLayout(self._align_cols_host)
        self._align_cols.setContentsMargins(0, 0, 0, 0)
        self._align_cols.setSpacing(self._DETAIL_VERTICAL_INTERVAL)
        self._align_cols.addWidget(self._align_h_row_host)
        self._align_cols.addWidget(self._align_v_row_host)
        align_body.addWidget(self._align_cols_host, 1)
        self._match_placement_cb = QCheckBox("記述欄画像内配置と揃える")
        self._match_placement_cb.setChecked(True)
        self._match_placement_cb.setToolTip(
            "「記述欄内画像配置」の九分割に合わせて、文字の横・縦位置を同期します"
        )
        self._match_placement_cb.toggled.connect(self._on_match_placement_toggled)
        self._match_placement_cb.hide()
        align_body.addWidget(
            self._match_placement_cb, 0, Qt.AlignmentFlag.AlignVCenter
        )
        align_lay.addLayout(align_body)
        self._detail_format_frame.hide()
        root.addWidget(align_frame)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(2)
        done_btn = self._make_action_btn("編集完了")
        done_btn.clicked.connect(self.edit_done_requested.emit)
        self._done_btn = done_btn
        edit_btn = self._make_action_btn("文字を編集")
        edit_btn.setToolTip("ダブルクリックでも編集を開始できます")
        edit_btn.clicked.connect(self.edit_requested.emit)
        self._edit_text_btn = edit_btn
        self._speech_btn = self._make_action_btn("音声入力")
        self._speech_btn.setCheckable(True)
        self._speech_btn.setToolTip(
            "マイクで音声をテキストに追加"
            "（要ネット・話したあと少し間を空けると認識されます）"
        )
        self._speech_btn.toggled.connect(self._on_speech_toggled)
        del_btn = self._make_action_btn("削除")
        del_btn.setToolTip("選択中のテキストボックスを削除（Del キーでも可）")
        del_btn.setProperty("variant", "danger")
        del_btn.clicked.connect(self.delete_requested.emit)
        self._delete_btn = del_btn
        self._action_btns = (done_btn, edit_btn, self._speech_btn, del_btn)
        speech_w = self._max_action_btn_width(
            ("音声入力", "準備中…", "認識中…", "確認中…", "音声入力中…", "配置待ち…")
        )
        self._speech_btn.setFixedWidth(speech_w)
        for action_btn, action_label in (
            (done_btn, "編集完了"),
            (edit_btn, "文字を編集"),
            (del_btn, "削除"),
        ):
            self._tighten_action_btn(action_btn, action_label)
            btn_row.addWidget(action_btn)
        btn_row.addWidget(self._speech_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._speech_status_label = QLabel("")
        self._speech_status_label.setObjectName("PaletteHintLabel")
        self._speech_status_label.setWordWrap(True)
        self._speech_status_label.hide()
        root.addWidget(self._speech_status_label)

        self._loading = False
        self._loading_char = False
        self._template_edit_mode = False
        self._speech_phase = "idle"
        self._style: dict[str, Any] = resolve_text_style(TEXT_STYLE_TEMPLATES["A"])
        self._text_palette_colors: tuple[str, ...] = TEXT_PALETTE_COLORS
        self._rebuild_color_swatches()
        self._sync_char_format_ui(
            {
                "color": str(DEFAULT_TEXT_STYLE.get("textColor") or DEFAULT_TEXT_COLOR),
                "fontSize": int(DEFAULT_TEXT_STYLE.get("fontSize") or 14),
                "lineSpacing": int(DEFAULT_TEXT_STYLE.get("lineSpacing") or 20),
                "bold": False,
                "italic": False,
                "underline": False,
            }
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        base_h = 160 if self._template_edit_mode else 190
        if self._align_tall:
            base_h += 48
        return QSize(self.content_min_width(), base_h)

    def sizeHint(self) -> QSize:  # noqa: N802
        base_h = 200 if self._template_edit_mode else 230
        if self._align_tall:
            base_h += 48
        return QSize(self.content_min_width(), base_h)

    def content_min_width(self) -> int:
        label_w = 48
        tpl_w = (
            label_w
            + 6
            + self._segment_btn_width("なし")
            + 6
            + self._segment_btn_width("半透明")
        )
        color_w = label_w + 4 + 6 * 24 + 5 * 4
        metrics_w = (
            36
            + self._size_spin.width()
            + 6
            + 36
            + self._line_spacing_spin.width()
        )
        seg = self._segment_btn_width("左")
        align_w = label_w + 6 + 3 * seg + 2 * 6
        deco_w = (
            label_w
            + 6
            + self._segment_btn_width("太字")
            + 6
            + self._segment_btn_width("イタリック")
            + 6
            + self._segment_btn_width("下線")
        )
        action_w = sum(btn.width() for btn in self._action_btns) + 3 * 4
        widths = [tpl_w, color_w, metrics_w, align_w, deco_w]
        if not self._template_edit_mode:
            widths.append(action_w)
        return max(widths)

    def _segment_btn_width(self, label: str, font: QFont | None = None) -> int:
        fm = QFontMetrics(font or self.font())
        return fm.horizontalAdvance(label) + self._SEGMENT_BTN_PAD

    def _action_btn_width(self, label: str) -> int:
        fm = QFontMetrics(self._done_btn.font())
        return fm.horizontalAdvance(label) + self._ACTION_BTN_PAD

    def _max_action_btn_width(self, labels: tuple[str, ...]) -> int:
        return max(self._action_btn_width(label) for label in labels)

    def _tighten_segment_btn(self, btn: QPushButton, label: str) -> None:
        fm = QFontMetrics(btn.font())
        btn.setFixedWidth(self._segment_btn_width(label, btn.font()))
        btn.setFixedHeight(fm.height() + 4)
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _tighten_action_btn(self, btn: QPushButton, label: str) -> None:
        btn.setFixedWidth(self._action_btn_width(label))
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    def _make_action_btn(self, label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("PaletteActionBtn")
        font = QFont(btn.font())
        font.setPointSize(9)
        font.setWeight(QFont.Weight.Medium)
        btn.setFont(font)
        return btn

    def _make_pt_spin(self, tooltip: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(6, 72)
        spin.setSuffix(" pt")
        spin.setToolTip(tooltip)
        fm = QFontMetrics(spin.font())
        frame = spin.style().pixelMetric(QStyle.PixelMetric.PM_SpinBoxFrameWidth, None, spin)
        # "pt" が欠けないよう余裕を持たせる（簡易/詳細/定型文編集で共通）。
        spin.setFixedWidth(fm.horizontalAdvance("144 pt") + frame * 2 + 24)
        return spin

    def _make_deco_btn(
        self,
        label: str,
        tooltip: str,
        *,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
    ) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("ToolSegmentBtn")
        btn.setCheckable(True)
        btn.setToolTip(tooltip)
        font = QFont(btn.font())
        font.setBold(bold)
        font.setItalic(italic)
        font.setUnderline(underline)
        btn.setFont(font)
        return btn

    def set_detailed_controls_visible(self, visible: bool) -> None:
        if _qt_widget_alive(getattr(self, "_align_frame", None)):
            self._align_frame.setVisible(bool(visible))
        if not _qt_widget_alive(self._detail_format_frame):
            return
        self._detail_format_frame.setVisible(bool(visible))

    def set_match_placement_visible(self, visible: bool) -> None:
        if not _qt_widget_alive(getattr(self, "_match_placement_cb", None)):
            return
        self._match_placement_cb.setVisible(bool(visible))
        if visible:
            self._sync_align_buttons_enabled()

    def set_align_buttons_tall(self, enabled: bool) -> None:
        """一律フィードバック向け: 横・縦揃えボタンを読みやすくする。"""
        self._align_tall = bool(enabled)
        root = self.layout()
        if root is not None:
            root.setSpacing(6 if self._align_tall else 2)
        spacing = (
            self._DETAIL_ALIGN_INTERVAL_TALL
            if self._align_tall
            else self._DETAIL_VERTICAL_INTERVAL
        )
        if getattr(self, "_align_cols", None) is not None:
            self._align_cols.setSpacing(spacing)
        if getattr(self, "_align_frame", None) is not None:
            align_lay = self._align_frame.layout()
            if align_lay is not None:
                align_lay.setSpacing(spacing)
                align_lay.setContentsMargins(0, 0, 0, 6 if self._align_tall else 0)

        labels_h = {"left": "左", "center": "中", "right": "右"}
        labels_v = {"top": "上", "center": "中", "bottom": "下"}
        for key, btn in self._align_h_btns.items():
            self._apply_align_btn_size(btn, labels_h[key], tall=self._align_tall)
        for key, btn in self._align_v_btns.items():
            self._apply_align_btn_size(btn, labels_v[key], tall=self._align_tall)

        # 親が縦を潰しても行が重ならないよう、行ホストに高さを固定する
        row_h = self._ALIGN_BTN_HEIGHT_TALL if self._align_tall else 0
        for host in (
            getattr(self, "_align_h_row_host", None),
            getattr(self, "_align_v_row_host", None),
        ):
            if host is None:
                continue
            if self._align_tall:
                host.setFixedHeight(row_h)
            else:
                host.setMinimumHeight(0)
                host.setMaximumHeight(16777215)
        if getattr(self, "_align_cols_host", None) is not None:
            if self._align_tall:
                self._align_cols_host.setMinimumHeight(row_h * 2 + spacing)
            else:
                self._align_cols_host.setMinimumHeight(0)
        if getattr(self, "_align_frame", None) is not None:
            if self._align_tall:
                detail_h = (
                    self._detail_format_frame.sizeHint().height() + spacing
                    if self._detail_format_frame.isVisible()
                    else 0
                )
                self._align_frame.setMinimumHeight(detail_h + row_h * 2 + spacing + 6)
                self._align_frame.setSizePolicy(
                    QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
                )
            else:
                self._align_frame.setMinimumHeight(0)

        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            (
                QSizePolicy.Policy.Minimum
                if self._align_tall
                else (
                    QSizePolicy.Policy.Maximum
                    if self._template_edit_mode
                    else QSizePolicy.Policy.Preferred
                )
            ),
        )
        self._sync_align_buttons_enabled()
        self.updateGeometry()
        self.layout_hint_changed.emit()

    def _apply_align_btn_size(self, btn: QPushButton, label: str, *, tall: bool) -> None:
        fm = QFontMetrics(btn.font())
        if tall:
            pad = self._SEGMENT_BTN_PAD_TALL
            height = self._ALIGN_BTN_HEIGHT_TALL
            width = max(36, fm.horizontalAdvance(label) + pad)
            btn.setObjectName("ToolSegmentBtnAlignTall")
        else:
            height = fm.height() + 4
            width = self._segment_btn_width(label, btn.font())
            btn.setObjectName("ToolSegmentBtn")
        btn.setFixedSize(width, height)
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        btn.style().unpolish(btn)
        btn.style().polish(btn)
        btn.update()

    def is_match_placement_checked(self) -> bool:
        cb = getattr(self, "_match_placement_cb", None)
        if not _qt_widget_alive(cb):
            return False
        return bool(cb.isChecked())

    def set_match_placement_checked(self, checked: bool) -> None:
        cb = getattr(self, "_match_placement_cb", None)
        if not _qt_widget_alive(cb):
            return
        cb.setChecked(bool(checked))

    def apply_text_align(self, align_h: str, align_v: str) -> None:
        """横・縦揃えを外部（配置九分割など）から反映する。"""
        h_key, v_key = normalize_text_align(
            {"textAlignH": align_h, "textAlignV": align_v}
        )
        self._style["textAlignH"] = h_key
        self._style["textAlignV"] = v_key
        self._sync_align_ui()
        self._emit_style()

    def _on_match_placement_toggled(self, checked: bool) -> None:
        self._sync_align_buttons_enabled()
        self.match_placement_toggled.emit(bool(checked))

    def _sync_align_buttons_enabled(self) -> None:
        # disabled にすると文字色が潰れるため、ロックフラグで操作だけ止める
        self._align_locked = (
            self.is_match_placement_checked()
            and _qt_widget_alive(getattr(self, "_match_placement_cb", None))
            and self._match_placement_cb.isVisible()
        )
        tip = (
            "「記述欄画像内配置と揃える」がオンのため、記述欄内画像配置側で変更してください"
            if self._align_locked
            else ""
        )
        for btn in (*self._align_h_btns.values(), *self._align_v_btns.values()):
            btn.setEnabled(True)
            btn.setProperty("locked", "true" if self._align_locked else "false")
            if tip:
                btn.setToolTip(tip)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        # 通常時のツールチップを復元
        if not self._align_locked:
            tips_h = {"left": "左寄せ", "center": "中央", "right": "右寄せ"}
            tips_v = {"top": "上寄せ", "center": "中央", "bottom": "下寄せ"}
            for key, btn in self._align_h_btns.items():
                btn.setToolTip(tips_h[key])
            for key, btn in self._align_v_btns.items():
                btn.setToolTip(tips_v[key])

    def set_template_edit_mode(self, enabled: bool) -> None:
        self._template_edit_mode = bool(enabled)
        # align_tall 時は下辺クリップ・行重なり回避のため Minimum を維持
        v_policy = (
            QSizePolicy.Policy.Minimum
            if self._align_tall
            else (
                QSizePolicy.Policy.Maximum
                if enabled
                else QSizePolicy.Policy.Preferred
            )
        )
        self.setSizePolicy(QSizePolicy.Policy.Preferred, v_policy)
        self.updateGeometry()
        if not _qt_widget_alive(self._speech_btn):
            return
        self._speech_btn.setVisible(not enabled)
        if _qt_widget_alive(self._edit_text_btn):
            if enabled:
                self._edit_text_btn.setToolTip(
                    "プレビュー内で文言を編集（ダブルクリックでも可）"
                )
            else:
                self._edit_text_btn.setToolTip("ダブルクリックでも編集を開始できます")
        if _qt_widget_alive(self._delete_btn):
            if enabled:
                self._delete_btn.setToolTip("この定型文を削除")
            else:
                self._delete_btn.setToolTip("選択中のテキストボックスを削除（Del キーでも可）")
        if _qt_widget_alive(self._done_btn):
            self._done_btn.setText("編集完了")
        self.layout_hint_changed.emit()

    def set_text_palette_colors(self, colors: list[str] | tuple[str, ...]) -> None:
        if len(colors) != 6:
            return
        self._text_palette_colors = tuple(str(c) for c in colors)
        self._rebuild_color_swatches()
        tc = str(self._style.get("textColor") or self._text_palette_colors[0])
        self._sync_color_swatches(tc)

    def _rebuild_color_swatches(self) -> None:
        for btn in self._color_btns:
            self._color_group.removeButton(btn)
            btn.deleteLater()
        self._color_btns.clear()
        while self._color_row.count() > 1:
            item = self._color_row.takeAt(self._color_row.count() - 1)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for i, col in enumerate(self._text_palette_colors):
            b = QPushButton()
            b.setObjectName("ColorSwatchBtn")
            b.setFixedSize(24, 24)
            b.setCheckable(True)
            b.setStyleSheet(
                f"QPushButton#ColorSwatchBtn {{ background: {col}; border-radius: 12px; }}"
            )
            b.clicked.connect(lambda _c=False, idx=i: self._pick_text_color_by_index(idx))
            self._color_group.addButton(b, i)
            self._color_row.addWidget(b)
            self._color_btns.append(b)
        self._color_row.addStretch()

    def _pick_text_color_by_index(self, index: int) -> None:
        if 0 <= index < len(self._text_palette_colors):
            self._pick_text_color(self._text_palette_colors[index])

    def apply_template(self, key: str) -> None:
        tpl = TEXT_STYLE_TEMPLATES.get(key)
        if not tpl:
            return
        merged = {**self._style, **tpl}
        merged["textColor"] = str(
            self._style.get("textColor") or DEFAULT_TEXT_COLOR
        )
        self.load_style(merged)
        self._emit_style()

    def _pick_text_color(self, color: str) -> None:
        if self._loading_char:
            return
        self._sync_color_swatches(color)
        self._style["textColor"] = color
        self.char_format_changed.emit({"color": str(color)})

    def _on_size_changed(self, value: int) -> None:
        if self._loading_char:
            return
        self.char_format_changed.emit({"fontSize": int(value)})

    def _on_line_spacing_changed(self, value: int) -> None:
        if self._loading_char:
            return
        self.char_format_changed.emit({"lineSpacing": int(value)})

    def _emit_toggle(self, key: str) -> None:
        if self._loading_char:
            return
        self.char_format_changed.emit({key: True})

    def _set_align_h(self, key: str) -> None:
        if self._loading or self._align_locked:
            self._sync_align_ui()
            return
        self._style["textAlignH"] = key
        self._sync_align_ui()
        self._emit_style()

    def _set_align_v(self, key: str) -> None:
        if self._loading or self._align_locked:
            self._sync_align_ui()
            return
        self._style["textAlignV"] = key
        self._sync_align_ui()
        self._emit_style()

    def _sync_align_ui(self) -> None:
        h, v = normalize_text_align(self._style)
        for key, btn in self._align_h_btns.items():
            btn.blockSignals(True)
            btn.setChecked(key == h)
            btn.blockSignals(False)
        for key, btn in self._align_v_btns.items():
            btn.blockSignals(True)
            btn.setChecked(key == v)
            btn.blockSignals(False)

    def sync_char_format(self, state: dict[str, Any]) -> None:
        self._sync_char_format_ui(state)

    def _sync_char_format_ui(self, state: dict[str, Any]) -> None:
        self._loading_char = True
        try:
            color = str(state.get("color") or self._text_palette_colors[0])
            self._sync_color_swatches(color)
            self._style["textColor"] = color
            size = int(state.get("fontSize") or self._style.get("fontSize") or 14)
            self._size_spin.blockSignals(True)
            self._size_spin.setValue(max(6, min(72, size)))
            self._size_spin.blockSignals(False)
            spacing = int(
                state.get("lineSpacing")
                or self._style.get("lineSpacing")
                or DEFAULT_TEXT_STYLE.get("lineSpacing")
                or 20
            )
            self._line_spacing_spin.blockSignals(True)
            self._line_spacing_spin.setValue(max(6, min(72, spacing)))
            self._line_spacing_spin.blockSignals(False)
            self._bold_btn.blockSignals(True)
            self._italic_btn.blockSignals(True)
            self._underline_btn.blockSignals(True)
            self._bold_btn.setChecked(bool(state.get("bold")))
            self._italic_btn.setChecked(bool(state.get("italic")))
            self._underline_btn.setChecked(bool(state.get("underline")))
            self._bold_btn.blockSignals(False)
            self._italic_btn.blockSignals(False)
            self._underline_btn.blockSignals(False)
        finally:
            self._loading_char = False

    def _sync_color_swatches(self, color: str) -> None:
        for i, col in enumerate(self._text_palette_colors):
            if i >= len(self._color_btns) or not _qt_widget_alive(self._color_btns[i]):
                continue
            if col.lower() == color.lower():
                self._color_btns[i].setChecked(True)
                return
        for btn in self._color_btns:
            if _qt_widget_alive(btn):
                btn.setChecked(False)

    def load_style(self, style: dict[str, Any]) -> None:
        self._loading = True
        self._style = resolve_text_style(style)
        self._sync_char_format_ui(
            {
                "color": str(
                    self._style.get("textColor")
                    or DEFAULT_TEXT_COLOR
                    or self._text_palette_colors[0]
                ),
                "fontSize": int(self._style.get("fontSize") or 14),
                "lineSpacing": int(
                    self._style.get("lineSpacing")
                    or DEFAULT_TEXT_STYLE.get("lineSpacing")
                    or 20
                ),
                "bold": False,
                "italic": False,
                "underline": False,
            }
        )
        self._sync_align_ui()
        self._loading = False

    def _emit_style(self) -> None:
        if self._loading:
            return
        resolved = resolve_text_style(self._style)
        self._style = resolved
        self.style_changed.emit(resolved)

    def _on_speech_toggled(self, on: bool) -> None:
        self.speech_toggled.emit(bool(on))

    def set_speech_mode(self, mode: str) -> None:
        if mode == "windows":
            self._speech_btn.setToolTip(
                "テキストボックス選択中は Windows 音声入力（Win+H）。"
                "未選択時はアプリ内認識で話し、確定後に配置場所をクリック。"
            )
        else:
            self._speech_btn.setToolTip(
                "マイクで音声をテキストに追加"
                "（無言で区切ると認識。認識中にもう一度押すと終了。"
                " テキストボックス未選択時は配置場所をクリック）"
            )

    def release_speech_button_focus(self) -> None:
        self._speech_btn.clearFocus()
        self.clearFocus()

    def is_speech_checked(self) -> bool:
        return self._speech_btn.isChecked()

    def set_speech_available(self, available: bool) -> None:
        self._speech_btn.setEnabled(bool(available))
        if not available:
            self.set_speech_active(False)

    def set_speech_active(self, on: bool) -> None:
        if not _qt_widget_alive(self._speech_btn):
            return
        if self._speech_btn.isChecked() == on:
            if not on:
                self.set_speech_phase("idle")
            return
        self._speech_btn.blockSignals(True)
        self._speech_btn.setChecked(on)
        self._speech_btn.blockSignals(False)
        if not on:
            self.set_speech_phase("idle")

    def set_speech_phase(self, phase: str) -> None:
        """音声入力の状態表示（idle / preparing / recognizing / paused / windows）。"""
        self._speech_phase = str(phase or "idle")
        btn_text = "音声入力"
        status = ""
        accent = False
        if self._speech_phase == "preparing":
            btn_text = "準備中…"
            status = "マイクを準備しています…"
            accent = True
        elif self._speech_phase == "recognizing":
            btn_text = "認識中…"
            status = "話してください。もう一度押すとその時点まで認識して終了します。"
            accent = True
        elif self._speech_phase == "paused":
            btn_text = "確認中…"
            status = "認識結果の確認中です。"
            accent = True
        elif self._speech_phase == "windows":
            btn_text = "音声入力中…"
            status = "Windows 音声入力を使用中です。"
            accent = True
        elif self._speech_phase == "placing":
            btn_text = "配置待ち…"
            status = "認識したテキストを配置する場所をクリックしてください。"
            accent = True
        self._speech_btn.setText(btn_text)
        if accent:
            self._speech_btn.setStyleSheet(
                "QPushButton#PaletteActionBtn {"
                f" background: {COLORS['accent_soft']};"
                " font-weight: 600;"
                " font-size: 9px;"
                " }"
            )
        else:
            self._speech_btn.setStyleSheet("")
        if status:
            self._speech_status_label.setText(status)
            self._speech_status_label.show()
        else:
            self._speech_status_label.clear()
            self._speech_status_label.hide()
        self.layout_hint_changed.emit()
