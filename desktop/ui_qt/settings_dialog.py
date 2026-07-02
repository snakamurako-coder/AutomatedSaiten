"""詳細設定ダイアログ（Qt 版）。"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from config import CONFIG_PATH, load_config, save_config
from services.gemini_rubric import test_gemini_api_key
from services.ocr import test_vision_api_key
from ui_qt import helpers as h


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("詳細設定")
        self.resize(600, 480)
        self._on_saved = on_saved
        cfg = load_config()

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(h.title_label("詳細設定"))
        root.addWidget(
            h.muted_label("API キーは desktop/config.json に保存されます（Git には含めないでください）。")
        )

        # OCR
        ocr_box = QGroupBox("OCR")
        ocr_form = QFormLayout(ocr_box)
        engine_row = QVBoxLayout()
        self.engine_tesseract = QRadioButton("Tesseract（ローカル・無料）")
        self.engine_vision = QRadioButton("Google Vision API（クラウド）")
        if (cfg.get("ocr_engine") or "tesseract") == "vision":
            self.engine_vision.setChecked(True)
        else:
            self.engine_tesseract.setChecked(True)
        engine_row.addWidget(self.engine_tesseract)
        engine_row.addWidget(self.engine_vision)
        ocr_form.addRow("OCR エンジン", engine_row)

        tess_row = QHBoxLayout()
        self.tesseract_edit = QLineEdit(cfg.get("tesseract_cmd") or "")
        tess_row.addWidget(self.tesseract_edit, 1)
        tess_row.addWidget(h.button("参照…", self._browse_tesseract))
        ocr_form.addRow("Tesseract 実行ファイル", tess_row)
        ocr_form.addRow("", h.caption_label("未指定の場合は PATH 上の tesseract を使用します。"))
        root.addWidget(ocr_box)

        # API キー
        api_box = QGroupBox("API キー")
        api_form = QFormLayout(api_box)
        vision_row = QHBoxLayout()
        self.vision_edit = QLineEdit(cfg.get("vision_api_key") or "")
        self.vision_edit.setEchoMode(QLineEdit.Password)
        vision_row.addWidget(self.vision_edit, 1)
        vision_row.addWidget(h.button("接続確認", self._test_vision))
        api_form.addRow("Vision API キー", vision_row)

        gemini_row = QHBoxLayout()
        self.gemini_edit = QLineEdit(cfg.get("gemini_api_key") or "")
        self.gemini_edit.setEchoMode(QLineEdit.Password)
        gemini_row.addWidget(self.gemini_edit, 1)
        gemini_row.addWidget(h.button("接続確認", self._test_gemini))
        api_form.addRow("Gemini API キー", gemini_row)
        api_form.addRow("", h.caption_label("Vision: ③ テキスト化 / Gemini: ④ AI原案 で使用します。"))
        root.addWidget(api_box)

        # その他
        misc_box = QGroupBox("その他")
        misc_form = QFormLayout(misc_box)
        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["landscape", "portrait"])
        self.orientation_combo.setCurrentText(cfg.get("default_orientation") or "landscape")
        misc_form.addRow("用紙向き（デフォルト）", self.orientation_combo)
        root.addWidget(misc_box)

        self.status_label = h.caption_label(f"設定ファイル: {CONFIG_PATH}")
        root.addWidget(self.status_label)
        root.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(h.button("キャンセル", self.reject))
        btn_row.addWidget(h.button("保存", self._on_save, variant="primary"))
        root.addLayout(btn_row)

    def _browse_tesseract(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Tesseract 実行ファイルを選択", "", "実行ファイル (*.exe);;すべて (*.*)"
        )
        if path:
            self.tesseract_edit.setText(path)

    def _collect(self) -> dict:
        return {
            "vision_api_key": self.vision_edit.text().strip(),
            "ocr_engine": "vision" if self.engine_vision.isChecked() else "tesseract",
            "default_orientation": self.orientation_combo.currentText(),
            "tesseract_cmd": self.tesseract_edit.text().strip(),
            "gemini_api_key": self.gemini_edit.text().strip(),
        }

    def _on_save(self) -> None:
        cfg = self._collect()
        if cfg["ocr_engine"] == "vision" and not cfg["vision_api_key"]:
            h.warn(self, "設定エラー", "OCR エンジンが Vision API の場合、Vision API キーを入力してください。")
            return
        try:
            save_config(cfg)
        except OSError as e:
            h.error(self, "保存失敗", str(e))
            return
        if self._on_saved:
            self._on_saved()
        h.info(self, "保存完了", "詳細設定を保存しました。")
        self.accept()

    def _run_api_test(self, label: str, worker: Callable[[], str]) -> None:
        self.status_label.setText(f"{label} を確認中…")

        def done(msg, err):
            if err:
                self.status_label.setText(str(err))
                h.error(self, f"{label} — 失敗", str(err))
            else:
                self.status_label.setText(msg)
                h.info(self, f"{label} — OK", msg)

        h.run_in_thread(self, worker, done)

    def _test_vision(self) -> None:
        key = self.vision_edit.text().strip()
        if not key:
            h.warn(self, "未入力", "Vision API キーを入力してください。")
            return
        self._run_api_test("Vision API", lambda: test_vision_api_key(key))

    def _test_gemini(self) -> None:
        key = self.gemini_edit.text().strip()
        if not key:
            h.warn(self, "未入力", "Gemini API キーを入力してください。")
            return
        self._run_api_test("Gemini API", lambda: test_gemini_api_key(key))


def open_settings_dialog(parent: QWidget | None = None, on_saved: Callable[[], None] | None = None) -> None:
    dlg = SettingsDialog(parent, on_saved=on_saved)
    dlg.exec()
