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
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QColorDialog,
    QDoubleSpinBox,
)

from config import CONFIG_PATH, default_field_ocr_lang, load_config, save_config
from ui_qt.speech.speech_prefs import (
    DEFAULT_SPEECH_MODE,
    SPEECH_MODE_APP,
    SPEECH_MODE_WINDOWS,
    clamp_speech_pause_seconds,
    load_speech_pause_seconds,
)
from services.gemini_rubric import test_gemini_api_key
from services.ocr import test_openai_api_key, test_vision_api_key
from ui_qt import helpers as h
from ui_qt.floating_palette.palette_prefs import (
    TEXT_BOX_DEFAULT_STYLE_BUILTIN,
    TEXT_PALETTE_COLORS_DEFAULT,
    load_text_box_default_style,
    load_text_palette_colors,
    normalize_text_box_default_style,
    save_text_box_default_style,
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

        self._build_ocr_feature_tab(cfg)
        self._build_criteria_tab(cfg)
        self._build_drawing_tools_tab(cfg)
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

    def _build_ocr_feature_tab(self, cfg: dict) -> None:
        page, lay = self._tab_page(
            "OCR（光学文字認識）は、答案画像に写っている手書き・活字の文字を"
            "テキストデータに変換する機能です。⑦ OCR実行ステップで使用します。"
        )

        ocr_form = QFormLayout()
        ocr_form.setContentsMargins(0, 0, 0, 0)
        ocr_form.setSpacing(10)

        engine_row = QVBoxLayout()
        self.engine_openai = QRadioButton("OpenAI API（クラウド・手書き向け）")
        self.engine_vision = QRadioButton("Google Vision API（クラウド）")
        engine = str(cfg.get("ocr_engine") or "openai").strip().lower()
        if engine == "tesseract":
            engine = "openai"
        if engine == "vision":
            self.engine_vision.setChecked(True)
        else:
            self.engine_openai.setChecked(True)
        engine_row.addWidget(self.engine_openai)
        engine_row.addWidget(self.engine_vision)
        ocr_form.addRow("OCR エンジン", engine_row)

        field_lang_row = QHBoxLayout()
        self.field_ocr_en = QRadioButton("英語")
        self.field_ocr_ja = QRadioButton("日本語")
        if default_field_ocr_lang(cfg) == "ja":
            self.field_ocr_ja.setChecked(True)
        else:
            self.field_ocr_en.setChecked(True)
        field_lang_row.addWidget(self.field_ocr_en)
        field_lang_row.addWidget(self.field_ocr_ja)
        field_lang_row.addStretch()
        ocr_form.addRow("記述欄のデフォルト言語", field_lang_row)
        ocr_form.addRow(
            "",
            h.caption_label("① 回答欄設定で新規記述欄を追加するときの OCR 言語です。"),
        )
        lay.addLayout(ocr_form)

        lay.addWidget(h.caption_label("API キー"))
        lay.addWidget(
            h.caption_label(
                "API キーは desktop/config.json に保存されます（Git には含めないでください）。"
            )
        )

        api_form = QFormLayout()
        api_form.setContentsMargins(0, 0, 0, 0)
        api_form.setSpacing(10)

        vision_row = QHBoxLayout()
        self.vision_edit = QLineEdit(cfg.get("vision_api_key") or "")
        self.vision_edit.setEchoMode(QLineEdit.Password)
        vision_row.addWidget(self.vision_edit, 1)
        vision_row.addWidget(h.button("接続確認", self._test_vision))
        api_form.addRow("Vision API キー", vision_row)

        openai_row = QHBoxLayout()
        self.openai_edit = QLineEdit(cfg.get("openai_api_key") or "")
        self.openai_edit.setEchoMode(QLineEdit.Password)
        openai_row.addWidget(self.openai_edit, 1)
        openai_row.addWidget(h.button("接続確認", self._test_openai))
        api_form.addRow("OpenAI API キー", openai_row)

        gemini_row = QHBoxLayout()
        self.gemini_edit = QLineEdit(cfg.get("gemini_api_key") or "")
        self.gemini_edit.setEchoMode(QLineEdit.Password)
        gemini_row.addWidget(self.gemini_edit, 1)
        gemini_row.addWidget(h.button("接続確認", self._test_gemini))
        api_form.addRow("Gemini API キー", gemini_row)
        api_form.addRow("", h.caption_label("OpenAI / Vision: ⑦ OCR実行 / Gemini: ⑧ 採点基準（AI原案）で使用します。"))
        api_form.addRow(
            "",
            h.caption_label(
                "Vision API キーは「HTTP リファラー（ウェブサイト）」制限では使えません。"
                "制限は「なし」または IP アドレスにし、Cloud Vision API を有効化・課金設定してください。"
                "設定後は「適用して保存」または「保存して閉じる」を押してください。"
            ),
        )
        lay.addLayout(api_form)
        lay.addStretch()
        self._tabs.addTab(page, "OCR機能")

    def _build_criteria_tab(self, cfg: dict) -> None:
        page, lay = self._tab_page(
            "⑥薄字補正の「薄い字を検査」で使う判断基準です。"
            "記述欄クロップの指標がいずれか1つでも基準未満なら「要確認（薄い）」になります。"
            "しきい値は実運用で調整してください。"
        )
        self.faint_enabled = QCheckBox("薄い字の事前検査を有効にする")
        self.faint_enabled.setChecked(bool(cfg.get("faint_check_enabled", True)))
        lay.addWidget(self.faint_enabled)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        self.faint_sigma = QDoubleSpinBox()
        self.faint_sigma.setRange(0.0, 80.0)
        self.faint_sigma.setDecimals(1)
        self.faint_sigma.setSingleStep(0.5)
        self.faint_sigma.setValue(float(cfg.get("faint_min_sigma", 12.0)))
        self.faint_sigma.setToolTip("輝度の標準偏差。この値未満なら薄い疑い")
        form.addRow("最小 σ（標準偏差）", self.faint_sigma)

        self.faint_p95 = QDoubleSpinBox()
        self.faint_p95.setRange(0.0, 255.0)
        self.faint_p95.setDecimals(1)
        self.faint_p95.setSingleStep(1.0)
        self.faint_p95.setValue(float(cfg.get("faint_min_p95_p5", 35.0)))
        self.faint_p95.setToolTip("輝度の 95%点 − 5%点。この値未満なら薄い疑い")
        form.addRow("最小 P95−P5", self.faint_p95)

        self.faint_bg_delta = QDoubleSpinBox()
        self.faint_bg_delta.setRange(0.0, 255.0)
        self.faint_bg_delta.setDecimals(1)
        self.faint_bg_delta.setSingleStep(1.0)
        self.faint_bg_delta.setValue(float(cfg.get("faint_min_bg_delta", 18.0)))
        self.faint_bg_delta.setToolTip("背景輝度（P90）− 字側輝度（P10）。この値未満なら薄い疑い")
        form.addRow("最小 背景との輝度差 Δ", self.faint_bg_delta)

        lay.addLayout(form)
        lay.addWidget(
            h.caption_label(
                "指標はいずれも「この値未満で要確認」。値が大きいほど厳しい判定になります。"
            )
        )
        lay.addStretch()
        self._tabs.addTab(page, "判断基準")

    def _build_drawing_tools_tab(self, cfg: dict) -> None:
        page, lay = self._tab_page(
            "描画ツールで注釈するテキストの色やスタイラス入力の動作を設定します。"
            "各色のサンプルをクリックすると、色選択ダイアログで細かく色を設定できます。"
        )

        lay.addWidget(h.caption_label("テキスト注釈"))
        lay.addWidget(
            h.caption_label(
                "書式タブで選べるテンプレート文字色（6色）。B パターンの背景は文字色の補色になります。"
            )
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
        self._refresh_text_palette_btns()

        lay.addWidget(h.caption_label("テキストボックス配置時の既定書式"))
        lay.addWidget(
            h.caption_label(
                "新規テキストボックスを配置したときの初期値です。"
                "内蔵既定は 赤字・14pt・行間20・左寄せ・上寄せ・背景なし。"
            )
        )
        default_style = load_text_box_default_style()
        self._default_text_style = dict(default_style)

        default_form = QFormLayout()
        default_form.setContentsMargins(0, 0, 0, 0)
        default_form.setSpacing(8)

        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        self._default_color_btn = QPushButton()
        self._default_color_btn.setObjectName("ColorSwatchBtn")
        self._default_color_btn.setFixedSize(28, 28)
        self._default_color_btn.setToolTip("クリックで既定の文字色を変更")
        self._default_color_btn.clicked.connect(self._pick_default_text_color)
        color_row.addWidget(self._default_color_btn)
        color_row.addStretch()
        default_form.addRow("文字色", color_row)
        self._refresh_default_color_btn()

        self._default_size_spin = QSpinBox()
        self._default_size_spin.setRange(6, 72)
        self._default_size_spin.setSuffix(" pt")
        self._default_size_spin.setValue(int(default_style.get("fontSize") or 14))
        default_form.addRow("大きさ", self._default_size_spin)

        self._default_line_spin = QSpinBox()
        self._default_line_spin.setRange(6, 144)
        self._default_line_spin.setSuffix(" pt")
        self._default_line_spin.setValue(int(default_style.get("lineSpacing") or 20))
        default_form.addRow("行間", self._default_line_spin)

        self._default_align_h = QComboBox()
        self._default_align_h.addItem("左寄せ", "left")
        self._default_align_h.addItem("中央", "center")
        self._default_align_h.addItem("右寄せ", "right")
        h_key = str(default_style.get("textAlignH") or "left")
        h_idx = max(0, self._default_align_h.findData(h_key))
        self._default_align_h.setCurrentIndex(h_idx)
        default_form.addRow("横寄せ", self._default_align_h)

        self._default_align_v = QComboBox()
        self._default_align_v.addItem("上寄せ", "top")
        self._default_align_v.addItem("中央", "center")
        self._default_align_v.addItem("下寄せ", "bottom")
        v_key = str(default_style.get("textAlignV") or "top")
        v_idx = max(0, self._default_align_v.findData(v_key))
        self._default_align_v.setCurrentIndex(v_idx)
        default_form.addRow("縦寄せ", self._default_align_v)

        self._default_bg = QComboBox()
        self._default_bg.addItem("なし", "A")
        self._default_bg.addItem("半透明（文字色の補色）", "B")
        bg_key = str(default_style.get("templateId") or "A").upper()
        bg_idx = max(0, self._default_bg.findData(bg_key))
        self._default_bg.setCurrentIndex(bg_idx)
        default_form.addRow("背景", self._default_bg)

        reset_default_btn = h.button(
            "配置既定を初期化", self._reset_text_box_default_style
        )
        reset_default_btn.setToolTip(
            "赤字・14pt・行間20・左寄せ・上寄せ・背景なしに戻す"
        )
        default_form.addRow("", reset_default_btn)
        lay.addLayout(default_form)

        lay.addWidget(h.caption_label("スタイラス"))
        stylus_form = QFormLayout()
        stylus_form.setContentsMargins(0, 0, 0, 0)

        self.palm_rejection_check = QCheckBox("パームリジェクション（指・手のひらを無視）")
        self.palm_rejection_check.setChecked(bool(cfg.get("stylus_palm_rejection", True)))
        self.palm_rejection_check.setToolTip(
            "ON: スタイラスペンのみ手書き（指・マウスは選択操作）\n"
            "OFF: 指・タッチペン・マウスでも手書き可能"
        )
        stylus_form.addRow("", self.palm_rejection_check)

        lay.addLayout(stylus_form)
        lay.addStretch()
        self._tabs.addTab(page, "描画ツール")

    def _build_speech_tab(self, cfg: dict) -> None:
        page, lay = self._tab_page("テキストボックスへの音声入力方法を選びます。")

        self.speech_app = QRadioButton("アプリ内認識（Google・確認ダイアログあり）")
        lay.addWidget(self.speech_app)

        pause_form = QFormLayout()
        pause_form.setContentsMargins(0, 0, 0, 0)
        pause_form.setSpacing(10)
        self.speech_pause_spin = QDoubleSpinBox()
        self.speech_pause_spin.setRange(0.3, 5.0)
        self.speech_pause_spin.setSingleStep(0.1)
        self.speech_pause_spin.setDecimals(1)
        self.speech_pause_spin.setSuffix(" 秒")
        self.speech_pause_spin.setValue(load_speech_pause_seconds())
        self.speech_pause_spin.setToolTip(
            "アプリ内認識で、話したあと何秒無言なら区切って認識するか"
        )
        pause_form.addRow("無言区切り", self.speech_pause_spin)
        self._speech_pause_label = pause_form.labelForField(self.speech_pause_spin)
        lay.addLayout(pause_form)
        lay.addWidget(
            h.caption_label(
                "話したあと何秒無言なら区切って認識するか（0.3〜5.0秒、0.1秒刻み）。"
                "アプリ内認識のときのみ有効です。"
            )
        )

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

        self.speech_app.toggled.connect(self._sync_speech_pause_enabled)
        if self.speech_windows is not None:
            self.speech_windows.toggled.connect(self._sync_speech_pause_enabled)
        self._sync_speech_pause_enabled()

        lay.addWidget(
            h.caption_label("音声入力モードを変えたら「適用して保存」で反映できます（ダイアログは開いたまま）。")
        )
        lay.addStretch()
        self._tabs.addTab(page, "音声入力")

    def _sync_speech_pause_enabled(self) -> None:
        app_mode = self.speech_app.isChecked()
        self.speech_pause_spin.setEnabled(app_mode)
        if self._speech_pause_label is not None:
            self._speech_pause_label.setEnabled(app_mode)

    def _build_misc_tab(self, cfg: dict) -> None:
        page, lay = self._tab_page("その他の既定値を設定します。")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["landscape", "portrait"])
        self.orientation_combo.setCurrentText(cfg.get("default_orientation") or "landscape")
        form.addRow("用紙向き（デフォルト）", self.orientation_combo)

        startup_row = QVBoxLayout()
        self.startup_test_auto = QRadioButton("前回のテストを自動で読み出す")
        self.startup_test_blank = QRadioButton("空欄（未選択）で開始")
        if (cfg.get("startup_test_load") or "auto") == "blank":
            self.startup_test_blank.setChecked(True)
        else:
            self.startup_test_auto.setChecked(True)
        startup_row.addWidget(self.startup_test_auto)
        startup_row.addWidget(self.startup_test_blank)
        form.addRow("⓪ テスト作成（起動時）", startup_row)

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

    def _refresh_default_color_btn(self) -> None:
        col = str(self._default_text_style.get("textColor") or "#dc2626")
        self._default_color_btn.setStyleSheet(
            f"QPushButton#ColorSwatchBtn {{ background: {col}; border-radius: 14px; }}"
        )

    def _pick_default_text_color(self) -> None:
        current = str(self._default_text_style.get("textColor") or "#dc2626")
        picked = QColorDialog.getColor(QColor(current), self, "配置時の既定文字色")
        if not picked.isValid():
            return
        self._default_text_style["textColor"] = picked.name()
        self._refresh_default_color_btn()

    def _collect_text_box_default_style(self) -> dict:
        return normalize_text_box_default_style(
            {
                "textColor": self._default_text_style.get("textColor"),
                "fontSize": self._default_size_spin.value(),
                "lineSpacing": self._default_line_spin.value(),
                "textAlignH": self._default_align_h.currentData(),
                "textAlignV": self._default_align_v.currentData(),
                "templateId": self._default_bg.currentData(),
            }
        )

    def _reset_text_box_default_style(self) -> None:
        style = normalize_text_box_default_style(TEXT_BOX_DEFAULT_STYLE_BUILTIN)
        self._default_text_style = dict(style)
        self._refresh_default_color_btn()
        self._default_size_spin.setValue(int(style.get("fontSize") or 14))
        self._default_line_spin.setValue(int(style.get("lineSpacing") or 20))
        self._default_align_h.setCurrentIndex(
            max(0, self._default_align_h.findData(style.get("textAlignH") or "left"))
        )
        self._default_align_v.setCurrentIndex(
            max(0, self._default_align_v.findData(style.get("textAlignV") or "top"))
        )
        self._default_bg.setCurrentIndex(
            max(0, self._default_bg.findData(str(style.get("templateId") or "A").upper()))
        )

    def _test_openai(self) -> None:
        key = self.openai_edit.text().strip()
        if not key:
            self.status_label.setText("未入力: OpenAI API キーを入力してください。")
            return
        self._run_api_test("OpenAI API", lambda: test_openai_api_key(key))

    def _collect(self) -> dict:
        speech_mode = SPEECH_MODE_APP
        if sys.platform == "win32" and self.speech_windows is not None and self.speech_windows.isChecked():
            speech_mode = SPEECH_MODE_WINDOWS
        return {
            "vision_api_key": self.vision_edit.text().strip(),
            "openai_api_key": self.openai_edit.text().strip(),
            "ocr_engine": "vision" if self.engine_vision.isChecked() else "openai",
            "default_field_ocr_lang": "ja" if self.field_ocr_ja.isChecked() else "en",
            "default_orientation": self.orientation_combo.currentText(),
            "startup_test_load": "blank" if self.startup_test_blank.isChecked() else "auto",
            "gemini_api_key": self.gemini_edit.text().strip(),
            "speech_input_mode": speech_mode,
            "speech_pause_seconds": clamp_speech_pause_seconds(self.speech_pause_spin.value()),
            "faint_check_enabled": self.faint_enabled.isChecked(),
            "faint_min_sigma": float(self.faint_sigma.value()),
            "faint_min_p95_p5": float(self.faint_p95.value()),
            "faint_min_bg_delta": float(self.faint_bg_delta.value()),
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
        if cfg["ocr_engine"] == "openai" and not cfg["openai_api_key"]:
            self.status_label.setText(
                "設定エラー: OCR エンジンが OpenAI API の場合、OpenAI API キーを入力してください。"
            )
            return False
        try:
            save_config(cfg)
            save_text_palette_colors(self._text_palette_colors)
            save_text_box_default_style(self._collect_text_box_default_style())
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
