"""④回答集約向け 一律フィードバック設定ダイアログ。"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from services.uniform_feedback_apply import apply_uniform_feedback
from services.uniform_feedback_placement import resolve_uniform_feedback_placement
from ui_qt import helpers as h
from ui_qt.helpers import enable_dialog_maximize
from ui_qt.floating_palette.format_palette_panel import FormatPalettePanel
from ui_qt.floating_palette.phrase_edit_preview_panel import PhraseEditPreviewPanel
from models.text_annotation_repo import TEXT_STYLE_TEMPLATE_A, resolve_text_style
from ui_qt.floating_palette.phrase_template_prefs import (
    add_phrase_template,
    clone_phrase_content_with_new_group_id,
    load_phrase_templates,
    template_dict_from_uniform_config,
    update_phrase_template,
)


class UniformFeedbackDialog(QDialog):
    applied = Signal(int, dict)

    def __init__(
        self,
        parent: QWidget | None,
        *,
        test_id: str,
        field: dict[str, Any],
        answer_text: str,
        initial_config: dict[str, Any] | None,
    ) -> None:
        super().__init__(parent)
        self._test_id = str(test_id or "")
        self._field = dict(field or {})
        self._answer_text = str(answer_text or "").strip() or "なし"
        self._initial = dict(initial_config or {})
        self._templates = load_phrase_templates()
        self._active_template: dict[str, Any] | None = None

        self.setWindowTitle("一律フィードバック")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumSize(760, 620)
        enable_dialog_maximize(self)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"対象回答: {self._answer_text}"))
        root.addWidget(QLabel("※適用直後に対象画像へ反映されます"))

        source_box = QGroupBox("文言ソース")
        source_lay = QVBoxLayout(source_box)
        src_row = QHBoxLayout()
        self._src_existing = QRadioButton("登録済み定型文")
        self._src_new = QRadioButton("新規追加・編集")
        self._source_group = QButtonGroup(self)
        self._source_group.addButton(self._src_existing)
        self._source_group.addButton(self._src_new)
        self._src_new.setChecked(True)
        src_row.addWidget(self._src_existing)
        src_row.addWidget(self._src_new)
        src_row.addStretch(1)
        source_lay.addLayout(src_row)

        self._tpl_combo = QComboBox()
        for tpl in self._templates:
            self._tpl_combo.addItem(str(tpl.get("label") or tpl.get("text") or "（無題）"), str(tpl.get("id")))
        source_lay.addWidget(self._tpl_combo)

        id_row = QHBoxLayout()
        self._id_same = QRadioButton("既存IDを使う")
        self._id_new = QRadioButton("新しいIDを発行")
        self._id_group = QButtonGroup(self)
        self._id_group.addButton(self._id_same)
        self._id_group.addButton(self._id_new)
        self._id_new.setChecked(True)
        id_row.addWidget(self._id_same)
        id_row.addWidget(self._id_new)
        id_row.addStretch(1)
        source_lay.addLayout(id_row)
        root.addWidget(source_box)

        edit_box = QGroupBox("内容編集")
        edit_lay = QVBoxLayout(edit_box)
        self._preview = PhraseEditPreviewPanel(self)
        # ダイアログは親ウィンドウ追従リサイズがないため、編集時の高さ切替を行わない
        self._preview.set_expand_on_edit(False)
        self._format = FormatPalettePanel(self)
        self._format.set_template_edit_mode(True)
        self._format.set_detailed_controls_visible(True)
        self._format.set_match_placement_visible(True)
        self._format.set_match_placement_checked(True)
        self._format.set_align_buttons_tall(True)
        self._format.style_changed.connect(self._preview.apply_style_dict)
        self._format.char_format_changed.connect(self._preview.apply_char_format)
        self._format.edit_requested.connect(self._preview.start_text_editing)
        self._format.edit_done_requested.connect(self._preview.finish_text_editing)
        self._format.match_placement_toggled.connect(self._on_match_placement_toggled)
        self._preview.char_format_state_changed.connect(self._format.sync_char_format)
        self._preview.set_focus_guard_widgets(())
        edit_lay.addWidget(self._preview, 0)
        edit_lay.addWidget(self._format, 0)
        self._format.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )

        save_row = QHBoxLayout()
        self._save_phrase_btn = QPushButton("この内容を定型文として保存")
        self._save_phrase_btn.clicked.connect(self._on_save_phrase)
        save_row.addWidget(self._save_phrase_btn)
        save_row.addStretch(1)
        edit_lay.addLayout(save_row)
        root.addWidget(edit_box, 1)

        placement_box = QGroupBox("記述欄内画像配置")
        placement_lay = QVBoxLayout(placement_box)
        self._placement_group = QButtonGroup(self)
        self._placement_group.setExclusive(True)
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        labels = [
            ("left", "top", "左上"),
            ("center", "top", "上中央"),
            ("right", "top", "右上"),
            ("left", "center", "左中央"),
            ("center", "center", "中央"),
            ("right", "center", "右中央"),
            ("left", "bottom", "左下"),
            ("center", "bottom", "下中央"),
            ("right", "bottom", "右下"),
        ]
        for i, (hpos, vpos, label) in enumerate(labels):
            btn = QPushButton(label)
            btn.setObjectName("UniformPlacementBtn")
            btn.setCheckable(True)
            btn.setProperty("placement_h", hpos)
            btn.setProperty("placement_v", vpos)
            # PhrasePlacementBtn(40px高・全幅) の約半分
            btn.setFixedSize(120, 20)
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._placement_group.addButton(btn, i)
            r, c = divmod(i, 3)
            grid.addWidget(btn, r, c)
        grid_wrap = QHBoxLayout()
        grid_wrap.addStretch(1)
        grid_wrap.addLayout(grid)
        grid_wrap.addStretch(1)
        placement_lay.addLayout(grid_wrap)
        root.addWidget(placement_box)

        actions = QHBoxLayout()
        actions.addStretch(1)
        apply_btn = h.button("適用", self._on_apply, variant="primary")
        close_btn = h.button("閉じる", self.accept)
        actions.addWidget(apply_btn)
        actions.addWidget(close_btn)
        root.addLayout(actions)

        self._src_existing.toggled.connect(self._on_source_mode_changed)
        self._tpl_combo.currentIndexChanged.connect(self._on_template_changed)
        self._placement_group.buttonClicked.connect(self._on_placement_changed)
        self._load_initial_state()

    def _find_template(self, tpl_id: str) -> dict[str, Any] | None:
        target = str(tpl_id or "")
        for tpl in self._templates:
            if str(tpl.get("id")) == target:
                return tpl
        return None

    def _load_initial_state(self) -> None:
        cfg = self._initial
        placement_h = str(cfg.get("placementH") or "center")
        placement_v = str(cfg.get("placementV") or "center")
        for btn in self._placement_group.buttons():
            if btn.property("placement_h") == placement_h and btn.property("placement_v") == placement_v:
                btn.setChecked(True)
                break
        else:
            if self._placement_group.buttons():
                self._placement_group.buttons()[4].setChecked(True)

        self._src_existing.blockSignals(True)
        self._src_new.blockSignals(True)
        try:
            if cfg:
                pid = str(cfg.get("phraseTemplateId") or "")
                tpl = self._find_template(pid)
                if tpl is not None:
                    self._src_existing.setChecked(True)
                    self._tpl_combo.setCurrentIndex(max(0, self._tpl_combo.findData(pid)))
                    if str(cfg.get("phraseGroupId") or "") == str(tpl.get("phraseGroupId") or ""):
                        self._id_same.setChecked(True)
                    else:
                        self._id_new.setChecked(True)
                    self._load_preview_template(copy.deepcopy(tpl))
                else:
                    self._src_new.setChecked(True)
                    self._load_preview_template(template_dict_from_uniform_config(cfg))
            else:
                self._src_new.setChecked(True)
                self._load_new_blank_template()
        finally:
            self._src_existing.blockSignals(False)
            self._src_new.blockSignals(False)
        self._sync_source_mode()
        self._sync_align_from_placement()

    def _preview_style(self, style: dict[str, Any] | None) -> dict[str, Any]:
        merged = resolve_text_style(dict(style or TEXT_STYLE_TEMPLATE_A))
        if self._format.is_match_placement_checked():
            placement_h, placement_v = self._selected_placement()
            merged["textAlignH"] = placement_h
            merged["textAlignV"] = placement_v
        return merged

    def _blank_template(self) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "label": "",
            "text": "",
            "textHtml": "",
            "textFormat": "plain",
            "style": self._preview_style(TEXT_STYLE_TEMPLATE_A),
            "width": 120.0,
            "height": 36.0,
        }

    def _load_preview_template(self, tpl: dict[str, Any]) -> None:
        loaded = copy.deepcopy(tpl)
        loaded["style"] = self._preview_style(loaded.get("style"))
        self._active_template = loaded
        self._preview.load_template(loaded)
        self._format.load_style(loaded.get("style") or {})
        if self._format.is_match_placement_checked():
            self._sync_align_from_placement()

    def _sync_align_from_placement(self) -> None:
        if not self._format.is_match_placement_checked():
            return
        placement_h, placement_v = self._selected_placement()
        self._format.apply_text_align(placement_h, placement_v)

    def _on_placement_changed(self, _btn: QPushButton | None = None) -> None:
        self._sync_align_from_placement()

    def _on_match_placement_toggled(self, checked: bool) -> None:
        if checked:
            self._sync_align_from_placement()

    def _load_new_blank_template(self) -> None:
        self._load_preview_template(self._blank_template())

    def _sync_source_mode(self) -> None:
        existing = self._src_existing.isChecked()
        self._tpl_combo.setEnabled(existing)
        self._id_same.setEnabled(existing)
        self._id_new.setEnabled(existing)

    def _on_source_mode_changed(self, existing_selected: bool) -> None:
        self._sync_source_mode()
        if existing_selected:
            self._id_same.setChecked(True)
            self._on_template_changed(self._tpl_combo.currentIndex())
        else:
            self._id_new.setChecked(True)
            self._load_new_blank_template()

    def _on_template_changed(self, _index: int) -> None:
        if not self._src_existing.isChecked():
            return
        tpl_id = str(self._tpl_combo.currentData() or "")
        tpl = self._find_template(tpl_id)
        if tpl is None:
            return
        self._load_preview_template(tpl)

    def _current_template(self) -> dict[str, Any] | None:
        if self._active_template is None:
            return None
        self._preview.finish_text_editing()
        updates = self._preview.export_updates()
        current = {**self._active_template, **updates}
        return current

    def _template_for_apply(self, tpl: dict[str, Any]) -> dict[str, Any]:
        merged = copy.deepcopy(tpl)
        if self._src_existing.isChecked() and self._id_new.isChecked():
            return clone_phrase_content_with_new_group_id(merged)
        if not str(merged.get("phraseGroupId") or "").strip():
            return clone_phrase_content_with_new_group_id(merged)
        return merged

    def _on_save_phrase(self) -> None:
        tpl = self._current_template()
        if tpl is None:
            h.warn(self, "保存", "定型文が選択されていません。")
            return
        if self._src_existing.isChecked():
            saved = update_phrase_template(str(tpl.get("id") or ""), **tpl)
            if saved is None:
                h.warn(self, "保存", "定型文の保存に失敗しました。")
                return
            self._active_template = saved
            h.info(self, "保存", "定型文を更新しました。")
            return
        saved = add_phrase_template(tpl)
        self._templates = load_phrase_templates()
        self._tpl_combo.blockSignals(True)
        self._tpl_combo.clear()
        for item in self._templates:
            self._tpl_combo.addItem(str(item.get("label") or item.get("text") or "（無題）"), str(item.get("id")))
        self._tpl_combo.blockSignals(False)
        self._src_existing.setChecked(True)
        self._tpl_combo.setCurrentIndex(max(0, self._tpl_combo.findData(str(saved.get("id") or ""))))
        self._load_preview_template(saved)
        h.info(self, "保存", "新規定型文として登録しました。")

    def _selected_placement(self) -> tuple[str, str]:
        btn = self._placement_group.checkedButton()
        if btn is None:
            return "center", "center"
        return str(btn.property("placement_h") or "center"), str(btn.property("placement_v") or "center")

    def _confirm_correction(self, template: dict[str, Any], placement_h: str, placement_v: str) -> bool:
        resolved = resolve_uniform_feedback_placement(
            field_width=float(self._field.get("width") or 1),
            field_height=float(self._field.get("height") or 1),
            box_width=float(template.get("width") or 120),
            box_height=float(template.get("height") or 36),
            placement_h=placement_h,
            placement_v=placement_v,
        )
        if not resolved.get("needsCorrection"):
            return False
        dirs = "・".join(resolved.get("overflowDirections") or [])
        ans = QMessageBox.question(
            self,
            "配置補正",
            f"現在の設定では回答欄の外へはみ出す可能性があります（{dirs}）。\n補正して配置しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return ans == QMessageBox.StandardButton.Yes

    def _on_apply(self) -> None:
        tpl = self._current_template()
        if tpl is None:
            h.warn(self, "一律フィードバック", "文言が選択されていません。")
            return
        tpl = self._template_for_apply(tpl)
        placement_h, placement_v = self._selected_placement()
        use_correction = self._confirm_correction(tpl, placement_h, placement_v)
        count = apply_uniform_feedback(
            self._test_id,
            str(self._field.get("id") or ""),
            self._answer_text,
            {
                "template": tpl,
                "placementH": placement_h,
                "placementV": placement_v,
            },
            use_correction=use_correction,
        )
        if count <= 0:
            h.warn(self, "一律フィードバック", "適用対象がありませんでした。")
            return
        config = {
            "phraseGroupId": str(tpl.get("phraseGroupId") or ""),
            "phraseTemplateId": str(tpl.get("id") or ""),
            "label": str(tpl.get("label") or ""),
            "text": str(tpl.get("text") or ""),
            "textHtml": str(tpl.get("textHtml") or ""),
            "textFormat": str(tpl.get("textFormat") or "plain"),
            "style": copy.deepcopy(tpl.get("style") or {}),
            "width": float(tpl.get("width") or 120),
            "height": float(tpl.get("height") or 36),
            "placementH": placement_h,
            "placementV": placement_v,
            "useCorrection": bool(use_correction),
        }
        self.applied.emit(count, config)
        h.info(self, "一律フィードバック", f"{count} 件に適用しました。")

