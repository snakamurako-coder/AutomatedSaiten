"""① 回答欄設定ページ（模範解答 D&D + 領域エディタ）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from models.test_repo import (
    get_answer_fields,
    get_test_info,
    save_answer_fields,
    save_model_answer_image,
)
from services.image_loader import is_supported_input_path
from services.image_warp import warp_image_from_path
from ui_qt import helpers as h
from ui_qt.region_editor import AnswerRegionEditor
from ui_qt.style import COLORS


class Step1Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._field_rows: list[dict[str, Any]] = []
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("① 回答欄設定（模範解答）"))
        root.addWidget(
            h.muted_label(
                "PDF / JPG / PNG をドロップするか「画像を開く」で模範解答を読み込み、"
                "画像上をドラッグして記述欄を指定します。"
            )
        )

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(QLabel("用紙方向"))
        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["landscape", "portrait"])
        toolbar.addWidget(self.orientation_combo)
        toolbar.addWidget(QLabel("二値化"))
        self.thresh_slider = QSlider(Qt.Horizontal)
        self.thresh_slider.setRange(0, 255)
        self.thresh_slider.setValue(128)
        self.thresh_slider.setFixedWidth(120)
        toolbar.addWidget(self.thresh_slider)
        toolbar.addWidget(h.button("画像を開く", self._on_open_file))
        toolbar.addWidget(h.button("記述欄を保存", self._on_save_fields, variant="primary"))
        toolbar.addWidget(h.button("選択欄を削除", self._on_delete_selected, variant="danger-soft"))
        toolbar.addWidget(h.button("再読込", self.refresh))
        toolbar.addStretch()
        root.addLayout(toolbar)

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        self.editor = AnswerRegionEditor(
            on_change=self._refresh_field_panel,
            on_status=self._set_status,
        )
        body.addWidget(self.editor, 1)

        side = QFrame()
        side.setFixedWidth(230)
        side.setStyleSheet(
            f"QFrame {{ background: {COLORS['sidebar']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 8px; }}"
        )
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 8, 8, 8)
        side_title = QLabel("記述欄一覧")
        side_title.setStyleSheet("font-weight: 700; border: none;")
        side_layout.addWidget(side_title)
        self.field_scroll = QScrollArea()
        self.field_scroll.setWidgetResizable(True)
        self.field_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.field_panel = QWidget()
        self.field_panel.setStyleSheet("background: transparent; border: none;")
        self.field_panel_layout = QVBoxLayout(self.field_panel)
        self.field_panel_layout.setContentsMargins(0, 0, 0, 0)
        self.field_panel_layout.setSpacing(6)
        self.field_panel_layout.addStretch()
        self.field_scroll.setWidget(self.field_panel)
        side_layout.addWidget(self.field_scroll)
        body.addWidget(side, 0)

        self.status_label = h.caption_label("PDF / JPG / PNG をドロップするか「画像を開く」で開始")
        root.addWidget(self.status_label)

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    # --- D&D ---

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        files = [p for p in paths if p and is_supported_input_path(p)]
        if not files:
            h.warn(self, "ドロップ", "PDF / JPG / PNG ファイルをドロップしてください。")
            return
        self._load_model_from_path(files[0])

    # --- 読込 ---

    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "模範解答を選択（PDF / JPG / PNG）",
            "",
            "PDF / JPG / PNG (*.pdf *.jpg *.jpeg *.png);;すべて (*.*)",
        )
        if path:
            self._load_model_from_path(path)

    def _load_model_from_path(self, path: str) -> None:
        if not self.app.require_active_test():
            return
        self._set_status("読込・補正中…")
        orientation = self.orientation_combo.currentText() or "landscape"
        thresh = int(self.thresh_slider.value())
        test_id = self.app.active_test_id
        existing_fields = list(self._field_rows)
        ref_w = ref_h = 0
        try:
            info = get_test_info(test_id)
            ref_w = int(info.get("refWidth") or 0)
            ref_h = int(info.get("refHeight") or 0)
        except Exception:
            pass

        def task():
            warped = warp_image_from_path(path, orientation, thresh)
            hh, ww = warped.shape[:2]
            keep = existing_fields if ref_w == ww and ref_h == hh and existing_fields else []
            save_model_answer_image(test_id, warped)
            return warped, keep

        def done(result, err):
            if err:
                self._set_status("")
                h.error(self, "読込エラー", str(err))
                return
            warped, keep = result
            self.editor.set_image(warped)
            if keep:
                self.editor.set_regions(keep)
            self._field_rows = self.editor.get_regions()
            hh, ww = warped.shape[:2]
            self._set_status(f"模範解答を読み込みました（{ww}×{hh}）— 画像上をドラッグして記述欄を追加")
            self._refresh_field_panel()

        h.run_in_thread(self, task, done)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        self._field_rows = get_answer_fields(self.app.active_test_id)
        info = get_test_info(self.app.active_test_id)
        model_path = info.get("modelAnswerPath") or ""
        if model_path and Path(model_path).exists():
            try:
                self.editor.load_image_from_path(model_path)
                self.editor.set_regions(self._field_rows)
                self._field_rows = self.editor.get_regions()
                self._set_status(
                    f"保存済み模範解答を表示（{info.get('refWidth')}×{info.get('refHeight')}）"
                )
            except Exception as e:
                self._set_status(f"模範解答の表示に失敗: {e}")
        else:
            self._set_status("模範解答未登録 — 画像をドロップまたは開いてください")
        self._refresh_field_panel()

    # --- 記述欄一覧 ---

    def _refresh_field_panel(self) -> None:
        while self.field_panel_layout.count():
            item = self.field_panel_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._field_rows = self.editor.get_regions()

        if not self._field_rows:
            self.field_panel_layout.addWidget(
                h.muted_label("画像上をドラッグして\n記述欄を追加")
            )
            self.field_panel_layout.addStretch()
            return

        for idx, row in enumerate(self._field_rows):
            card = QFrame()
            card.setStyleSheet(
                f"QFrame {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']};"
                f" border-radius: 6px; }}"
            )
            lay = QVBoxLayout(card)
            lay.setContentsMargins(8, 6, 8, 6)
            lay.setSpacing(4)
            name = QLabel(f"{row['displayName']}  {row['width']}×{row['height']}")
            name.setStyleSheet("border: none; font-weight: 600; font-size: 12px;")
            lay.addWidget(name)
            ctrl = QHBoxLayout()
            ocr_label = QLabel("OCR")
            ocr_label.setStyleSheet("border: none; font-size: 10px; color: #9ca3af;")
            ctrl.addWidget(ocr_label)
            combo = QComboBox()
            combo.addItems(["en", "ja"])
            combo.setCurrentText(row.get("ocrLang") or "en")
            combo.setFixedWidth(64)
            combo.currentTextChanged.connect(
                lambda lang, i=idx: self._on_lang_changed(i, lang)
            )
            ctrl.addWidget(combo)
            ctrl.addStretch()
            select_btn = h.button("選択", lambda _=False, i=idx: self._select_field(i))
            select_btn.setFixedWidth(52)
            ctrl.addWidget(select_btn)
            lay.addLayout(ctrl)
            self.field_panel_layout.addWidget(card)
        self.field_panel_layout.addStretch()

    def _on_lang_changed(self, index: int, lang: str) -> None:
        self.editor.set_region_ocr_lang(index, lang)
        self._field_rows = self.editor.get_regions()

    def _select_field(self, index: int) -> None:
        self.editor.select_region(index)

    def _on_delete_selected(self) -> None:
        self.editor.delete_selected()

    def _on_save_fields(self) -> None:
        if not self.app.require_active_test():
            return
        self._field_rows = self.editor.get_regions()
        if not self._field_rows:
            h.error(self, "保存エラー", "記述欄がありません。画像上で矩形を指定してください。")
            return
        if not self.editor.has_image():
            h.error(self, "保存エラー", "模範解答画像が読み込まれていません。")
            return
        try:
            warped = self.editor.get_image_bgr()
            if warped is not None:
                save_model_answer_image(self.app.active_test_id, warped)
            save_answer_fields(self.app.active_test_id, self._field_rows)
            h.info(self, "保存完了", "模範解答と記述欄を保存しました。")
            self._set_status("記述欄を保存しました")
        except Exception as e:
            h.error(self, "エラー", str(e))
