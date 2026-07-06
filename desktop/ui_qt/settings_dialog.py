"""詳細設定ダイアログ（Qt 版）。"""

from __future__ import annotations

import sys
from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QColorDialog,
)

from config import CONFIG_PATH, load_config, save_config
from ui_qt.speech.speech_prefs import (
    DEFAULT_SPEECH_MODE,
    SPEECH_MODE_APP,
    SPEECH_MODE_WINDOWS,
)
from services.gemini_rubric import test_gemini_api_key
from services.ocr import test_vision_api_key
from ui_qt import helpers as h
from ui_qt.floating_palette.palette_prefs import (
    TEXT_PALETTE_COLORS_DEFAULT,
    load_text_palette_colors,
    save_text_palette_colors,
)
from ui_qt.style import COLORS


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("詳細設定")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(560, 420)
        self._on_saved = on_saved
        self._api_test_token = 0
        cfg = load_config()

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            f"""
            QTabWidget::pane {{
                border: 1px solid {COLORS["border"]};
                background: {COLORS["surface"]};
                border-radius: 4px;
            }}
            QTabBar::tab {{
                padding: 8px 14px;
                margin-right: 2px;
                background: {COLORS["sidebar"]};
                border: 1px solid {COLORS["border"]};
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
            QTabBar::tab:selected {{
                background: {COLORS["surface"]};
                font-weight: 600;
            }}
            """
        )
        root.addWidget(self._tabs, 1)

        self._build_ocr_tab(cfg)
        self._build_api_tab(cfg)
        self._build_stylus_tab(cfg)
        self._build_text_tab()
        self._build_speech_tab(cfg)
        self._build_misc_tab(cfg)

        self.status_label = h.caption_label(f"設定ファイル: {CONFIG_PATH}")
        root.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(h.button("キャンセル", self.reject))
        btn_row.addWidget(h.button("適用して保存", self._on_apply_save, variant="primary"))
        btn_row.addWidget(h.button("保存して閉じる", self._on_save))
        root.addLayout(btn_row)

    def _tab_page(self, caption: str | None = None) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        if caption:
            lay.addWidget(h.caption_label(caption))
        return page, lay

    def _build_ocr_tab(self, cfg: dict) -> None:
        page, lay = self._tab_page(
            "OCR エンジンと Tesseract のパスを設定します。Vision API は③テキスト化で使用します。"
        )
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        engine_row = QVBoxLayout()
        self.engine_tesseract = QRadioButton("Tesseract（ローカル・無料）")
        self.engine_vision = QRadioButton("Google Vision API（クラウド）")
        if (cfg.get("ocr_engine") or "tesseract") == "vision":
            self.engine_vision.setChecked(True)
        else:
            self.engine_tesseract.setChecked(True)
        engine_row.addWidget(self.engine_tesseract)
        engine_row.addWidget(self.engine_vision)
        form.addRow("OCR エンジン", engine_row)

        tess_row = QHBoxLayout()
        self.tesseract_edit = QLineEdit(cfg.get("tesseract_cmd") or "")
        tess_row.addWidget(self.tesseract_edit, 1)
        tess_row.addWidget(h.button("参照…", self._browse_tesseract))
        form.addRow("Tesseract 実行ファイル", tess_row)
        form.addRow("", h.caption_label("未指定の場合は PATH 上の tesseract を使用します。"))

        lay.addLayout(form)
        lay.addStretch()
        self._tabs.addTab(page, "OCR")

    def _build_api_tab(self, cfg: dict) -> None:
        page, lay = self._tab_page(
            "API キーは desktop/config.json に保存されます（Git には含めないでください）。"
        )
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        vision_row = QHBoxLayout()
        self.vision_edit = QLineEdit(cfg.get("vision_api_key") or "")
        self.vision_edit.setEchoMode(QLineEdit.Password)
        vision_row.addWidget(self.vision_edit, 1)
        vision_row.addWidget(h.button("接続確認", self._test_vision))
        form.addRow("Vision API キー", vision_row)

        gemini_row = QHBoxLayout()
        self.gemini_edit = QLineEdit(cfg.get("gemini_api_key") or "")
        self.gemini_edit.setEchoMode(QLineEdit.Password)
        gemini_row.addWidget(self.gemini_edit, 1)
        gemini_row.addWidget(h.button("接続確認", self._test_gemini))
        form.addRow("Gemini API キー", gemini_row)
        form.addRow("", h.caption_label("Vision: ③ テキスト化 / Gemini: ④ AI原案 で使用します。"))
        form.addRow(
            "",
            h.caption_label(
                "Vision API キーは「HTTP リファラー（ウェブサイト）」制限では使えません。"
                "制限は「なし」または IP アドレスにし、Cloud Vision API を有効化・課金設定してください。"
                "設定後は「適用して保存」または「保存して閉じる」を押してください。"
            ),
        )

        lay.addLayout(form)
        lay.addStretch()
        self._tabs.addTab(page, "API キー")

    def _build_stylus_tab(self, cfg: dict) -> None:
        page, lay = self._tab_page("スタイラス入力と手書きレイヤーの動作を設定します。")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)

        self.palm_rejection_check = QCheckBox("パームリジェクション（指・手のひらを無視）")
        self.palm_rejection_check.setChecked(bool(cfg.get("stylus_palm_rejection", True)))
        self.palm_rejection_check.setToolTip(
            "ON: スタイラスペンのみ手書き（指・マウスは選択操作）\n"
            "OFF: 指・タッチペン・マウスでも手書き可能"
        )
        form.addRow("", self.palm_rejection_check)

        lay.addLayout(form)
        lay.addStretch()
        self._tabs.addTab(page, "スタイラス")

    def _build_text_tab(self) -> None:
        page, lay = self._tab_page(
            "書式タブで選べるテンプレート文字色（6色）。B パターンの背景は文字色の補色になります。"
        )

        palette_row = QHBoxLayout()
        palette_row.setSpacing(6)
        self._text_palette_colors: list[str] = list(load_text_palette_colors())
        self._text_palette_btns: list[QPushButton] = []
        for i in range(6):
            btn = QPushButton()
            btn.setObjectName("ColorSwatchBtn")
            btn.setFixedSize(28, 28)
            btn.setToolTip("クリックで色を変更")
            btn.clicked.connect(lambda _c=False, idx=i: self._pick_text_palette_color(idx))
            palette_row.addWidget(btn)
            self._text_palette_btns.append(btn)
        reset_btn = h.button("初期化", self._reset_text_palette_colors)
        reset_btn.setToolTip("6色をデフォルトに戻す")
        palette_row.addWidget(reset_btn)
        palette_row.addStretch()
        lay.addLayout(palette_row)
        lay.addStretch()
        self._refresh_text_palette_btns()
        self._tabs.addTab(page, "テキスト注釈")

    def _build_speech_tab(self, cfg: dict) -> None:
        page, lay = self._tab_page("テキストボックスへの音声入力方法を選びます。")

        self.speech_app = QRadioButton("アプリ内認識（Google・確認ダイアログあり）")
        lay.addWidget(self.speech_app)
        self.speech_windows: QRadioButton | None = None
        if sys.platform == "win32":
            self.speech_windows = QRadioButton(
                "Windows 音声入力（Win+H・タッチキーボードのマイクと同等）"
            )
            lay.addWidget(self.speech_windows)
            lay.addWidget(
                h.caption_label(
                    "Windows モードは Win+H で音声入力バーを開き、"
                    "認識結果をテキストボックスへ直接入力します（確認ダイアログなし）。"
                )
            )
        else:
            lay.addWidget(
                h.caption_label("Windows 音声入力は Windows 版でのみ利用できます。")
            )
        speech_mode = str(cfg.get("speech_input_mode") or DEFAULT_SPEECH_MODE).strip().lower()
        if sys.platform == "win32" and speech_mode == SPEECH_MODE_WINDOWS and self.speech_windows:
            self.speech_windows.setChecked(True)
        else:
            self.speech_app.setChecked(True)
        lay.addWidget(
            h.caption_label("音声入力モードを変えたら「適用して保存」で反映できます（ダイアログは開いたまま）。")
        )
        lay.addStretch()
        self._tabs.addTab(page, "音声入力")

    def _build_misc_tab(self, cfg: dict) -> None:
        page, lay = self._tab_page("その他の既定値を設定します。")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["landscape", "portrait"])
        self.orientation_combo.setCurrentText(cfg.get("default_orientation") or "landscape")
        form.addRow("用紙向き（デフォルト）", self.orientation_combo)

        lay.addLayout(form)
        lay.addStretch()
        self._tabs.addTab(page, "その他")

    def _refresh_text_palette_btns(self) -> None:
        for i, btn in enumerate(self._text_palette_btns):
            col = self._text_palette_colors[i]
            btn.setStyleSheet(
                f"QPushButton#ColorSwatchBtn {{ background: {col}; border-radius: 14px; }}"
            )

    def _pick_text_palette_color(self, index: int) -> None:
        if index < 0 or index >= len(self._text_palette_colors):
            return
        picked = QColorDialog.getColor(QColor(self._text_palette_colors[index]), self, "テンプレート文字色")
        if not picked.isValid():
            return
        self._text_palette_colors[index] = picked.name()
        self._refresh_text_palette_btns()

    def _reset_text_palette_colors(self) -> None:
        self._text_palette_colors = list(TEXT_PALETTE_COLORS_DEFAULT)
        self._refresh_text_palette_btns()

    def _browse_tesseract(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Tesseract 実行ファイルを選択", "", "実行ファイル (*.exe);;すべて (*.*)"
        )
        if path:
            self.tesseract_edit.setText(path)

    def _collect(self) -> dict:
        speech_mode = SPEECH_MODE_APP
        if sys.platform == "win32" and self.speech_windows is not None and self.speech_windows.isChecked():
            speech_mode = SPEECH_MODE_WINDOWS
        return {
            "vision_api_key": self.vision_edit.text().strip(),
            "ocr_engine": "vision" if self.engine_vision.isChecked() else "tesseract",
            "default_orientation": self.orientation_combo.currentText(),
            "tesseract_cmd": self.tesseract_edit.text().strip(),
            "gemini_api_key": self.gemini_edit.text().strip(),
            "speech_input_mode": speech_mode,
        }

    def _persist_settings(self) -> bool:
        cfg = load_config()
        cfg.update(self._collect())
        cfg["stylus_palm_rejection"] = self.palm_rejection_check.isChecked()
        if cfg["ocr_engine"] == "vision" and not cfg["vision_api_key"]:
            self.status_label.setText(
                "設定エラー: OCR エンジンが Vision API の場合、Vision API キーを入力してください。"
            )
            return False
        try:
            save_config(cfg)
            save_text_palette_colors(self._text_palette_colors)
        except OSError as e:
            self.status_label.setText(f"保存失敗: {e}")
            return False
        if self._on_saved:
            self._on_saved()
        return True

    def _on_apply_save(self) -> None:
        if not self._persist_settings():
            return
        self.status_label.setText("設定を保存し、適用しました。")

    def _on_save(self) -> None:
        if not self._persist_settings():
            return
        self.status_label.setText("詳細設定を保存しました。")
        self.accept()

    def _run_api_test(self, label: str, worker: Callable[[], str]) -> None:
        self._api_test_token += 1
        token = self._api_test_token
        self.status_label.setText(f"{label} を確認中…")

        def done(msg, err):
            if token != self._api_test_token:
                return
            if err:
                self.status_label.setText(f"{label} — 失敗: {err}")
            else:
                self.status_label.setText(f"{label} — OK: {msg}")

        h.run_in_thread(self, worker, done)

        def watchdog() -> None:
            if token != self._api_test_token:
                return
            if self.status_label.text().endswith("を確認中…"):
                self.status_label.setText(
                    f"{label} — タイムアウト: 40 秒以内に応答がありませんでした。"
                    " インターネット接続、ファイアウォール、プロキシ、API キー制限を確認してください。"
                )

        QTimer.singleShot(40_000, watchdog)

    def _test_vision(self) -> None:
        key = self.vision_edit.text().strip()
        if not key:
            self.status_label.setText("未入力: Vision API キーを入力してください。")
            return
        self._run_api_test("Vision API", lambda: test_vision_api_key(key))

    def _test_gemini(self) -> None:
        key = self.gemini_edit.text().strip()
        if not key:
            self.status_label.setText("未入力: Gemini API キーを入力してください。")
            return
        self._run_api_test("Gemini API", lambda: test_gemini_api_key(key))


def open_settings_dialog(parent: QWidget | None = None, on_saved: Callable[[], None] | None = None) -> None:
    palette = getattr(parent, "palette_controller", None) if parent is not None else None
    if palette is not None:
        palette.set_settings_overlay_active(True)
    try:
        dlg = SettingsDialog(parent, on_saved=on_saved)
        dlg.exec()
    finally:
        if palette is not None:
            palette.set_settings_overlay_active(False)
