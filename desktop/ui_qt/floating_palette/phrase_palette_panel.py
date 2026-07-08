"""定型文（フレーズシール）パネル。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from ui_qt.floating_palette.palette_prefs import VIEW_DETAILED, VIEW_SIMPLE
from ui_qt.floating_palette.phrase_template_prefs import (
    PHRASE_SIMPLE_TEXT_WIDTH,
    delete_phrase_template,
    phrase_detail_body_text,
    phrase_display_label,
    phrase_has_content,
    phrase_palette_content_html,
    phrase_palette_detail_html,
    phrase_preview_text,
    phrase_simple_button_label,
    phrase_templates_mru,
)
from ui_qt.floating_palette.text_rich import (
    palette_border_css,
    palette_fill_background,
    qt_label_alignment,
)
from ui_qt.style import COLORS

_DETAIL_PLACEMENT_BTN_WIDTH = 40
_DETAIL_TEXT_MIN_WIDTH = 80
# 高さ調整はカード本体のみ。ウィンドウ伸長の余白はカード間に入れない。
_DETAIL_BODY_MIN_HEIGHT = 60
_DETAIL_CARD_MIN_HEIGHT = 72
_DETAIL_PLACEMENT_BTN_HEIGHT = 60
_DETAIL_CARD_SPACING = 8
_DETAIL_VIEWPORT_MAX_CARDS = 4
_DETAIL_ACTION_BTN_HEIGHT = 22
_DETAIL_FONT_PX = 11
_DETAIL_LINE_HEIGHT_PX = 15


def _detail_action_btn_width(font) -> int:
    fm = QFontMetrics(font)
    return max(fm.horizontalAdvance("編集"), fm.horizontalAdvance("削除")) + 14


class PhrasePalettePanel(QWidget):
    """定型文の選択・登録 UI。"""

    phrase_selected = Signal(str)
    phrase_edit_requested = Signal(str)
    phrase_deleted = Signal(str)
    copy_from_textbox_requested = Signal()
    placement_cancel_requested = Signal()
    layout_hint_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_mode = VIEW_SIMPLE
        self._pending_id: str | None = None
        self._editing_id: str | None = None
        self._compact_edit = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)

        self._pending_label = QLabel("")
        self._pending_label.setObjectName("PhrasePendingLabel")
        self._pending_label.setWordWrap(False)
        self._pending_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self._pending_label.hide()
        root.addWidget(self._pending_label)

        self._simple_frame = QFrame()
        self._simple_lay = QVBoxLayout(self._simple_frame)
        self._simple_lay.setContentsMargins(0, 0, 0, 0)
        self._simple_lay.setSpacing(0)
        self._simple_lay.addStretch()
        self._simple_frame.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        root.addWidget(self._simple_frame)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._scroll_host = QWidget()
        self._scroll_host.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        self._detailed_lay = QVBoxLayout(self._scroll_host)
        self._detailed_lay.setContentsMargins(0, 0, 0, 0)
        self._detailed_lay.setSpacing(_DETAIL_CARD_SPACING)
        self._detailed_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._detailed_lay.addStretch(1)
        self._scroll.setWidget(self._scroll_host)
        root.addWidget(self._scroll, 1)
        self._scroll.hide()

        self._edit_single_frame = QFrame()
        self._edit_single_frame.setObjectName("PhraseDetailRow")
        self._edit_single_frame.setProperty("editing", True)
        self._edit_single_lay = QVBoxLayout(self._edit_single_frame)
        self._edit_single_lay.setContentsMargins(8, 8, 8, 8)
        self._edit_single_lay.setSpacing(4)
        root.addWidget(self._edit_single_frame)
        self._edit_single_frame.hide()

        # コピー行は固定高。縦伸長の余りは上の定型文一覧(_scroll)だけが吸収する。
        self._copy_wrap = QWidget()
        self._copy_wrap.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        action_row = QHBoxLayout(self._copy_wrap)
        action_row.setSpacing(4)
        action_row.setContentsMargins(0, 0, 0, 0)
        self._copy_btn = QPushButton("テキストボックスからコピー")
        self._copy_btn.setObjectName("PhraseCopyBtn")
        self._copy_btn.setToolTip(
            "選択中のテキストボックスを書式込みで定型文として登録"
        )
        self._copy_btn.clicked.connect(self.copy_from_textbox_requested.emit)
        self._sync_copy_btn_width()
        self._copy_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        action_row.addWidget(self._copy_btn)
        action_row.addStretch()
        root.addWidget(self._copy_wrap, 0)

        self._select_group = QButtonGroup(self)
        self._select_group.setExclusive(True)
        self._phrase_btns: dict[str, QPushButton] = {}
        self._detail_rows: dict[str, QFrame] = {}
        self.reload_templates()

    def _sync_copy_btn_width(self) -> None:
        btn = self._copy_btn
        font = QFont(btn.font())
        font.setPixelSize(11)
        btn.setFont(font)
        opt = QStyleOptionButton()
        opt.initFrom(btn)
        opt.text = btn.text()
        contents_w = btn.style().sizeFromContents(
            QStyle.ContentsType.CT_PushButton,
            opt,
            QSize(0, 0),
            btn,
        ).width()
        btn.setFixedWidth(max(contents_w + 4, btn.sizeHint().width() + 4))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_copy_btn_width()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.content_min_width(), self.content_height_hint())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.content_min_width(), self.content_height_hint())

    def content_height_hint(self) -> int:
        lay = self.layout()
        margins = lay.contentsMargins()
        spacing = lay.spacing()
        blocks: list[int] = []
        if self._pending_label.isVisible():
            blocks.append(self._pending_label.sizeHint().height())
        if self._compact_edit:
            blocks.append(self._edit_single_frame.sizeHint().height())
        elif self._view_mode == VIEW_SIMPLE:
            blocks.append(self._simple_list_height())
        elif self._scroll.isVisible():
            blocks.append(self._detailed_viewport_height())
        if self._copy_btn.isVisible():
            blocks.append(self._copy_btn.sizeHint().height())
        total = margins.top() + margins.bottom() + sum(blocks)
        if len(blocks) > 1:
            total += spacing * (len(blocks) - 1)
        return max(36, total)

    _SIMPLE_BTN_HEIGHT = 36

    def _simple_list_height(self) -> int:
        if not self._phrase_btns:
            return 0
        btn_h = self._SIMPLE_BTN_HEIGHT
        count = len(self._phrase_btns)
        gap = self._simple_lay.spacing()
        return count * btn_h + gap * max(0, count - 1)

    def _detailed_list_height(self) -> int:
        count = len(self._detail_rows)
        if count <= 0:
            return _DETAIL_CARD_MIN_HEIGHT
        return (
            count * _DETAIL_CARD_MIN_HEIGHT
            + _DETAIL_CARD_SPACING * max(0, count - 1)
        )

    def _detailed_viewport_height(self) -> int:
        """ウィンドウ側はカード数に応じて拡げ、超過分だけスクロール."""
        full = self._detailed_list_height()
        max_h = (
            _DETAIL_VIEWPORT_MAX_CARDS * _DETAIL_CARD_MIN_HEIGHT
            + _DETAIL_CARD_SPACING * max(0, _DETAIL_VIEWPORT_MAX_CARDS - 1)
        )
        return max(_DETAIL_CARD_MIN_HEIGHT, min(full, max_h))

    def content_min_width(self) -> int:
        self._sync_copy_btn_width()
        copy_w = self._copy_btn.width()
        action_w = _detail_action_btn_width(self.font())
        if self._compact_edit:
            return max(220, copy_w)
        if self._view_mode == VIEW_DETAILED:
            row_w = (
                16
                + _DETAIL_PLACEMENT_BTN_WIDTH
                + 8
                + _DETAIL_TEXT_MIN_WIDTH
                + 8
                + action_w * 2
                + 4
            )
            return max(220, copy_w, row_w)
        simple_w = max(
            (
                self._simple_btn_width_hint(
                    self._template_for_id(pid) or {}
                )
                for pid in self._phrase_btns
            ),
            default=0,
        )
        return max(220, copy_w, simple_w)

    def set_compact_edit_mode(self, active: bool) -> None:
        self._compact_edit = bool(active)
        self._apply_view_mode()
        self.reload_templates()
        self.layout_hint_changed.emit()

    def set_view_mode(self, mode: str) -> None:
        self._view_mode = mode if mode in (VIEW_SIMPLE, VIEW_DETAILED) else VIEW_SIMPLE
        self._apply_view_mode()
        self._sync_copy_btn_width()
        self.reload_templates()
        self.layout_hint_changed.emit()

    def _apply_view_mode(self) -> None:
        if self._compact_edit:
            self._simple_frame.hide()
            self._scroll.hide()
            self._edit_single_frame.show()
            self._copy_wrap.hide()
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            self.layout_hint_changed.emit()
            return
        self._edit_single_frame.hide()
        detailed = self._view_mode == VIEW_DETAILED
        self._simple_frame.setVisible(not detailed)
        self._scroll.setVisible(detailed)
        # 簡易版からは「テキストボックスからコピー」を除外し、詳細版のみ表示する。
        self._copy_wrap.setVisible(detailed)
        # 詳細一覧: パネル自体を縦に伸ばし、余白は _scroll(定型文エリア) だけが取る。
        # 簡易版は Maximum のまま（下余白が増えない）。
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding if detailed else QSizePolicy.Policy.Maximum,
        )
        root = self.layout()
        if root is not None:
            root.setStretchFactor(self._scroll, 1 if detailed else 0)
            root.setStretchFactor(self._copy_wrap, 0)
        self.layout_hint_changed.emit()

    def reload_templates(self) -> None:
        templates = self._templates_for_display(phrase_templates_mru())
        self._rebuild_buttons(templates)
        if self._pending_id and self._pending_id not in self._phrase_btns:
            self.set_pending_phrase(None)
        if (
            self._editing_id
            and not self._compact_edit
            and self._editing_id not in self._phrase_btns
        ):
            self.set_editing_phrase(None)

    def set_pending_phrase(self, phrase_id: str | None) -> None:
        self._pending_id = str(phrase_id) if phrase_id else None
        for pid, btn in self._phrase_btns.items():
            btn.blockSignals(True)
            btn.setChecked(pid == self._pending_id)
            btn.blockSignals(False)
        if self._pending_id:
            tpl_label = phrase_display_label(
                self._template_for_id(self._pending_id) or {}, compact=False
            )
            self._pending_label.setText(
                f"配置待ち: {tpl_label} — 貼り付け位置を指定"
            )
            self._pending_label.show()
        else:
            self._pending_label.clear()
            self._pending_label.hide()
        self.layout_hint_changed.emit()

    def set_editing_phrase(self, phrase_id: str | None) -> None:
        self._editing_id = str(phrase_id) if phrase_id else None
        self._apply_view_mode()
        self.reload_templates()

    def _templates_for_display(self, templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._compact_edit and self._editing_id:
            return [t for t in templates if str(t.get("id")) == self._editing_id]
        return templates

    def _template_for_id(self, phrase_id: str) -> dict[str, Any] | None:
        for tpl in phrase_templates_mru():
            if str(tpl.get("id")) == phrase_id:
                return tpl
        return None

    def _clear_layout_widgets(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _rebuild_buttons(self, templates: list[dict[str, Any]]) -> None:
        for btn in self._phrase_btns.values():
            self._select_group.removeButton(btn)
            btn.deleteLater()
        self._phrase_btns.clear()
        self._detail_rows.clear()
        self._clear_layout_widgets(self._edit_single_lay)

        if self._compact_edit:
            if templates:
                self._fill_edit_single_card(templates[0])
            self.set_pending_phrase(self._pending_id)
            return

        if self._view_mode == VIEW_SIMPLE:
            self._clear_layout_widgets(self._simple_lay)
            self._scroll_host.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored
            )
            self._simple_lay.addStretch()
            for tpl in templates:
                btn = self._make_simple_select_btn(tpl)
                self._simple_lay.insertWidget(self._simple_lay.count() - 1, btn)
        else:
            self._clear_layout_widgets(self._detailed_lay)
            self._detailed_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
            self._detailed_lay.addStretch(1)
            self._scroll_host.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
            )
            for tpl in templates:
                frame = self._make_detailed_row(tpl)
                self._detailed_lay.insertWidget(self._detailed_lay.count() - 1, frame)
            # カード高さは固定。余白は末尾 stretch だけが吸収する。
            self._scroll_host.adjustSize()

        self.set_pending_phrase(self._pending_id)
        self.updateGeometry()
        self.layout_hint_changed.emit()

    def _fill_edit_single_card(self, tpl: dict[str, Any]) -> None:
        body = QLabel()
        body.setObjectName("PhraseDetailBody")
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        body.setAlignment(qt_label_alignment(tpl.get("style")))
        body.setText(
            phrase_palette_detail_html(tpl)
            if phrase_has_content(tpl)
            else phrase_detail_body_text(tpl)
        )
        body.setWordWrap(False)
        body.setFixedHeight(_DETAIL_BODY_MIN_HEIGHT)
        body.setFont(self._palette_detail_label_font())
        body.setStyleSheet("background: transparent; border: none;")
        if not phrase_has_content(tpl):
            body.setStyleSheet(f"color: {COLORS['text_muted']}; background: transparent; border: none;")
        self._edit_single_frame.setMinimumHeight(_DETAIL_CARD_MIN_HEIGHT)
        self._edit_single_frame.setStyleSheet(self._phrase_detail_row_qss(tpl, editing=True))
        self._edit_single_lay.addWidget(body)

    def _phrase_simple_btn_qss(self, tpl: dict[str, Any]) -> str:
        if not phrase_has_content(tpl):
            return f'QPushButton#PhraseSimpleBtn {{ color: {COLORS["danger"]}; }}'
        bg = palette_fill_background(tpl.get("style") or {})
        border = palette_border_css(tpl.get("style") or {})
        if border == "none":
            border = f'1px solid {COLORS["border"]}'
        return (
            f"QPushButton#PhraseSimpleBtn {{ background: {bg}; border: {border}; }}"
            f"QPushButton#PhraseSimpleBtn:checked {{"
            f' background: {COLORS["accent_soft"]};'
            f' border-color: {COLORS["accent"]};'
            f" }}"
        )

    def _phrase_detail_row_qss(self, tpl: dict[str, Any], *, editing: bool) -> str:
        if not phrase_has_content(tpl):
            bg = COLORS["surface"]
        else:
            fill = palette_fill_background(tpl.get("style") or {})
            bg = fill if fill != "transparent" else COLORS["surface"]
        border = palette_border_css(tpl.get("style") or {})
        if border == "none":
            border = f'1px solid {COLORS["border"]}'
        if editing:
            border = f'1px solid {COLORS["accent"]}'
            if bg in (COLORS["surface"], "transparent"):
                bg = COLORS["accent_soft"]
        return (
            f"QFrame#PhraseDetailRow {{ background: {bg}; border: {border};"
            f" border-radius: 8px; }}"
        )

    def _palette_simple_label_font(self) -> QFont:
        font = QFont("Meiryo")
        font.setPixelSize(12)
        return font

    def _palette_detail_label_font(self) -> QFont:
        font = QFont("Meiryo")
        font.setPixelSize(_DETAIL_FONT_PX)
        return font

    def _palette_placement_btn_font(self) -> QFont:
        font = QFont("Meiryo")
        font.setPixelSize(10)
        return font

    def _simple_btn_width_hint(self, tpl: dict[str, Any]) -> int:
        fm = QFontMetrics(self._palette_simple_label_font())
        text = phrase_simple_button_label(tpl)
        return fm.horizontalAdvance(text) + 20

    def _make_rich_label(
        self,
        tpl: dict[str, Any],
        *,
        one_line: bool,
        truncate_width: int | None = None,
    ) -> QLabel:
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(False)
        if one_line:
            label.setObjectName("PhraseSimpleBody")
            label.setFont(self._palette_simple_label_font())
            label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            label.setMaximumHeight(self._SIMPLE_BTN_HEIGHT - 4)
        else:
            label.setObjectName("PhraseDetailBody")
            label.setMinimumHeight(_DETAIL_BODY_MIN_HEIGHT)
            label.setMaximumHeight(_DETAIL_BODY_MIN_HEIGHT)
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            label.setAlignment(qt_label_alignment(tpl.get("style")))
            label.setFont(self._palette_detail_label_font())
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if phrase_has_content(tpl):
            label.setText(
                phrase_palette_content_html(
                    tpl, one_line=True, truncate_width=truncate_width
                )
                if one_line
                else phrase_palette_detail_html(tpl)
            )
            if not one_line:
                label.setStyleSheet("background: transparent; border: none;")
        else:
            label.setText(phrase_detail_body_text(tpl))
            label.setStyleSheet(
                f"color: {COLORS['text_muted']}; background: transparent; border: none;"
            )
        return label

    def _style_select_btn(self, btn: QPushButton, tpl: dict[str, Any]) -> None:
        if not phrase_has_content(tpl):
            btn.setStyleSheet(f"color: {COLORS['danger']};")
        else:
            btn.setStyleSheet("")

    def _make_simple_select_btn(self, tpl: dict[str, Any]) -> QPushButton:
        pid = str(tpl.get("id") or "")
        btn = QPushButton()
        btn.setObjectName("PhraseSimpleBtn")
        btn.setCheckable(True)
        btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        btn.setMinimumHeight(self._SIMPLE_BTN_HEIGHT)
        btn.setMaximumHeight(self._SIMPLE_BTN_HEIGHT)
        btn.setFixedHeight(self._SIMPLE_BTN_HEIGHT)
        lay = QHBoxLayout(btn)
        lay.setContentsMargins(6, 4, 8, 4)
        lay.setSpacing(0)
        label = self._make_rich_label(
            tpl,
            one_line=True,
            truncate_width=PHRASE_SIMPLE_TEXT_WIDTH,
        )
        lay.addWidget(label, 1)
        preview = phrase_preview_text(tpl)
        btn.setToolTip(preview or phrase_simple_button_label(tpl))
        btn.setStyleSheet(self._phrase_simple_btn_qss(tpl))
        btn.clicked.connect(lambda _c=False, p=pid: self._on_phrase_clicked(p))
        self._select_group.addButton(btn)
        self._phrase_btns[pid] = btn
        return btn

    def _make_placement_btn(self, tpl: dict[str, Any]) -> QPushButton:
        pid = str(tpl.get("id") or "")
        btn = QPushButton("選択")
        btn.setObjectName("PhrasePlacementBtn")
        btn.setCheckable(True)
        btn.setFixedWidth(_DETAIL_PLACEMENT_BTN_WIDTH)
        btn.setFixedHeight(_DETAIL_PLACEMENT_BTN_HEIGHT)
        btn.setFont(self._palette_placement_btn_font())
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        preview = phrase_preview_text(tpl)
        btn.setToolTip(f"選択して配置\n{preview or '（未登録）'}")
        self._style_select_btn(btn, tpl)
        btn.clicked.connect(lambda _c=False, p=pid: self._on_phrase_clicked(p))
        self._select_group.addButton(btn)
        self._phrase_btns[pid] = btn
        return btn

    def _make_select_btn(self, tpl: dict[str, Any], *, compact: bool) -> QPushButton:
        if compact:
            return self._make_simple_select_btn(tpl)
        return self._make_placement_btn(tpl)

    def _make_action_btn(self, label: str, *, danger: bool = False) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("PaletteActionBtn")
        if danger:
            btn.setProperty("variant", "danger")
        btn.setFixedWidth(_detail_action_btn_width(self.font()))
        btn.setFixedHeight(_DETAIL_ACTION_BTN_HEIGHT)
        btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return btn

    def _make_detailed_row(self, tpl: dict[str, Any]) -> QFrame:
        pid = str(tpl.get("id") or "")
        frame = QFrame()
        frame.setObjectName("PhraseDetailRow")
        frame.setProperty("editing", pid == self._editing_id)
        frame.setMinimumHeight(_DETAIL_CARD_MIN_HEIGHT)
        frame.setMaximumHeight(_DETAIL_CARD_MIN_HEIGHT)
        frame.setFixedHeight(_DETAIL_CARD_MIN_HEIGHT)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        select_btn = self._make_placement_btn(tpl)
        # カード内で選択ボタンを高さ中央に配置
        lay.addWidget(select_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        body = self._make_rich_label(tpl, one_line=False)
        body.setMinimumWidth(_DETAIL_TEXT_MIN_WIDTH)
        body.setFixedHeight(_DETAIL_BODY_MIN_HEIGHT)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(body, 1, Qt.AlignmentFlag.AlignVCenter)

        action_row = QHBoxLayout()
        action_row.setSpacing(4)
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        edit_btn = self._make_action_btn("編集")
        edit_btn.setToolTip("書式・文言をテキストボックスと同様に編集")
        edit_btn.clicked.connect(lambda _c=False, p=pid: self.phrase_edit_requested.emit(p))
        del_btn = self._make_action_btn("削除", danger=True)
        del_btn.clicked.connect(lambda _c=False, p=pid: self._on_delete(p))
        action_row.addWidget(edit_btn)
        action_row.addWidget(del_btn)
        lay.addLayout(action_row, 0)

        frame.setStyleSheet(
            self._phrase_detail_row_qss(tpl, editing=pid == self._editing_id)
        )

        self._detail_rows[pid] = frame
        return frame

    def _on_phrase_clicked(self, phrase_id: str) -> None:
        if self._pending_id == phrase_id:
            self._select_group.setExclusive(False)
            btn = self._phrase_btns.get(phrase_id)
            if btn is not None:
                btn.setChecked(False)
            self._select_group.setExclusive(True)
            self.set_pending_phrase(None)
            self.placement_cancel_requested.emit()
            return
        self.set_pending_phrase(phrase_id)
        self.phrase_selected.emit(phrase_id)

    def _on_delete(self, phrase_id: str) -> None:
        if self._pending_id == phrase_id:
            self.set_pending_phrase(None)
            self.placement_cancel_requested.emit()
        if self._editing_id == phrase_id:
            self.set_editing_phrase(None)
        delete_phrase_template(phrase_id)
        self.phrase_deleted.emit(phrase_id)
        self.reload_templates()
