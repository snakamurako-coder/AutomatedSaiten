"""⑩ 個票出力ページ（合計欄配置・書式設定・プレビュー・一括生成）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from models.output_repo import (
    get_available_output_slot_keys,
    get_feedback_style,
    get_output_slots,
    reset_feedback_style,
    save_feedback_style,
    save_output_slots,
)
from models.test_repo import get_test_info
from services.feedback_renderer import (
    _load_rows_with_extras,
    batch_generate_feedback,
    render_feedback_for_row,
)
from ui_qt import helpers as h
from ui_qt.helpers import ProgressBridge, pil_to_qpixmap
from ui_qt.region_editor import AnswerRegionEditor
from ui_qt.style import COLORS, set_variant


class Step10Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._slot_print_modes: dict[str, str] = {}
        self._rows: list[dict[str, Any]] = []
        self._preview_image = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(8)

        root.addWidget(h.title_label("⑩ 個票出力"))
        root.addWidget(
            h.muted_label(
                "補正済み解答画像に判定マーク（○/△/×）・小問得点・合計欄を合成した個票を生成します。"
            )
        )
        root.addWidget(self._build_slots_box())
        root.addWidget(self._build_style_box())
        root.addWidget(self._build_preview_box())
        root.addWidget(self._build_batch_box())

    # ---------- 合計欄の配置 ----------

    def _build_slots_box(self) -> QGroupBox:
        box = QGroupBox("合計欄の配置（出力欄設定）")
        lay = QVBoxLayout(box)
        lay.addWidget(
            h.caption_label(
                "項目を選んで模範解答の上をドラッグすると合計欄が配置されます（同じ項目は上書き）。"
                "候補は ⑥ 領域設定から生成されます。"
            )
        )

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("印字形式"))
        self.print_mode_combo = QComboBox()
        self.print_mode_combo.addItem("数字のみ", "number")
        self.print_mode_combo.addItem("ラベル付き", "label")
        ctrl.addWidget(self.print_mode_combo)
        ctrl.addWidget(h.button("選択欄を削除", self._on_delete_slot, variant="danger-soft"))
        ctrl.addWidget(h.button("合計欄を保存", self._on_save_slots, variant="primary"))
        ctrl.addStretch()
        lay.addLayout(ctrl)

        self.slot_btn_row = QHBoxLayout()
        self.slot_btn_row.setSpacing(6)
        lay.addLayout(self.slot_btn_row)
        self._slot_buttons: dict[str, QPushButton] = {}

        self.slot_hint = h.caption_label("")
        lay.addWidget(self.slot_hint)

        self.slot_editor = AnswerRegionEditor(on_change=self._on_slots_changed)
        self.slot_editor.setMinimumHeight(340)
        lay.addWidget(self.slot_editor)

        self.slot_status = h.caption_label("")
        lay.addWidget(self.slot_status)
        return box

    def _build_style_box(self) -> QGroupBox:
        box = QGroupBox("出力書式設定（全テスト共通）")
        lay = QVBoxLayout(box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("マーク余白率"))
        self.style_inset = QDoubleSpinBox()
        self.style_inset.setRange(0.0, 0.45)
        self.style_inset.setSingleStep(0.01)
        row1.addWidget(self.style_inset)
        row1.addWidget(QLabel("○ 色"))
        self.style_maru_color = QLineEdit()
        self.style_maru_color.setFixedWidth(90)
        row1.addWidget(self.style_maru_color)
        row1.addWidget(QLabel("○ 塗り透明度"))
        self.style_maru_fill = QDoubleSpinBox()
        self.style_maru_fill.setRange(0.0, 1.0)
        self.style_maru_fill.setSingleStep(0.02)
        row1.addWidget(self.style_maru_fill)
        row1.addWidget(QLabel("△ 色"))
        self.style_sankaku_color = QLineEdit()
        self.style_sankaku_color.setFixedWidth(90)
        row1.addWidget(self.style_sankaku_color)
        row1.addWidget(QLabel("× 色"))
        self.style_batsu_color = QLineEdit()
        self.style_batsu_color.setFixedWidth(90)
        row1.addWidget(self.style_batsu_color)
        row1.addStretch()
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("得点文字色"))
        self.style_score_color = QLineEdit()
        self.style_score_color.setFixedWidth(90)
        row2.addWidget(self.style_score_color)
        row2.addWidget(QLabel("得点サイズ比"))
        self.style_score_size = QDoubleSpinBox()
        self.style_score_size.setRange(0.1, 1.0)
        self.style_score_size.setSingleStep(0.05)
        row2.addWidget(self.style_score_size)
        row2.addWidget(QLabel("合計文字色"))
        self.style_total_color = QLineEdit()
        self.style_total_color.setFixedWidth(90)
        row2.addWidget(self.style_total_color)
        row2.addWidget(QLabel("合計サイズ比"))
        self.style_total_size = QDoubleSpinBox()
        self.style_total_size.setRange(0.1, 1.0)
        self.style_total_size.setSingleStep(0.05)
        row2.addWidget(self.style_total_size)
        row2.addWidget(QLabel("合計最小フォント"))
        self.style_total_min = QSpinBox()
        self.style_total_min.setRange(6, 72)
        row2.addWidget(self.style_total_min)
        row2.addWidget(h.button("書式を保存", self._on_save_style, variant="success"))
        row2.addWidget(h.button("デフォルトに戻す", self._on_reset_style))
        row2.addStretch()
        lay.addLayout(row2)
        return box

    def _build_preview_box(self) -> QGroupBox:
        box = QGroupBox("プレビュー")
        lay = QVBoxLayout(box)
        ctrl = QHBoxLayout()
        self.preview_row_combo = QComboBox()
        self.preview_row_combo.setMinimumWidth(320)
        ctrl.addWidget(self.preview_row_combo)
        ctrl.addWidget(h.button("1件プレビュー", self._on_preview, variant="primary"))
        ctrl.addWidget(QLabel("表示倍率"))
        self.preview_zoom = QSlider(Qt.Horizontal)
        self.preview_zoom.setRange(10, 200)
        self.preview_zoom.setValue(40)
        self.preview_zoom.setFixedWidth(160)
        self.preview_zoom.valueChanged.connect(lambda _v: self._update_preview_pixmap())
        ctrl.addWidget(self.preview_zoom)
        ctrl.addStretch()
        lay.addLayout(ctrl)

        preview_scroll = QScrollArea()
        preview_scroll.setWidgetResizable(True)
        preview_scroll.setMinimumHeight(380)
        preview_scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['sidebar']}; }}"
        )
        self.preview_label = QLabel("「1件プレビュー」で個票の合成結果を確認できます")
        self.preview_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.preview_label.setStyleSheet("background: transparent; padding: 8px;")
        preview_scroll.setWidget(self.preview_label)
        lay.addWidget(preview_scroll)
        return box

    def _build_batch_box(self) -> QGroupBox:
        box = QGroupBox("一括生成")
        lay = QVBoxLayout(box)
        ctrl = QHBoxLayout()
        self.batch_btn = h.button("全員分の個票を生成", self._on_batch, variant="primary")
        ctrl.addWidget(self.batch_btn)
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 100)
        ctrl.addWidget(self.batch_progress, 1)
        lay.addLayout(ctrl)
        self.batch_status = h.caption_label("")
        lay.addWidget(self.batch_status)
        return box

    # ---------- 再読込 ----------

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        test_id = self.app.active_test_id

        # 模範解答 + 保存済みスロット
        info = get_test_info(test_id)
        model_path = info.get("modelAnswerPath") or ""
        if model_path and Path(model_path).exists():
            try:
                self.slot_editor.load_image_from_path(model_path)
            except Exception as e:
                self.slot_status.setText(f"模範解答の表示に失敗: {e}")
        else:
            self.slot_status.setText("模範解答が未登録です。先に ① 回答欄設定で読み込んでください。")

        slots = get_output_slots(test_id)
        self._slot_print_modes = {s["slotKey"]: s["printMode"] for s in slots}
        self.slot_editor.set_regions(
            [
                {
                    "id": s["slotKey"],
                    "displayName": s["slotKey"],
                    "x": s["x"],
                    "y": s["y"],
                    "width": s["width"],
                    "height": s["height"],
                }
                for s in slots
            ]
        )

        # 項目ボタン
        while self.slot_btn_row.count():
            item = self.slot_btn_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._slot_buttons = {}
        keys = get_available_output_slot_keys(test_id)
        for key in keys:
            btn = QPushButton(key)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, k=key: self._select_slot_key(k))
            self._slot_buttons[key] = btn
            self.slot_btn_row.addWidget(btn)
        self.slot_btn_row.addStretch()
        if len(keys) <= 2:
            self.slot_hint.setText("⑥ 領域設定で大問・範囲・能力を設定すると候補が増えます。")
        self._update_slot_status()

        # 書式
        self._load_style_to_form(get_feedback_style())

        # プレビュー行
        self._rows = _load_rows_with_extras(test_id)
        self.preview_row_combo.clear()
        for r in self._rows:
            label = f"{r.get('studentId') or '-'} / {r.get('name') or '-'} / {r.get('fileName')}"
            self.preview_row_combo.addItem(label)

    # ---------- 合計欄 ----------

    def _select_slot_key(self, key: str) -> None:
        for k, btn in self._slot_buttons.items():
            btn.setChecked(k == key)
        self.slot_editor.set_pending_label(key, replace_same=True)
        self._slot_print_modes.setdefault(key, self.print_mode_combo.currentData())
        self.slot_hint.setText(f"「{key}」の欄を画像上でドラッグして配置してください（再ドラッグで上書き）。")

    def _on_slots_changed(self) -> None:
        # 新規配置されたスロットに現在の印字形式を割り当てる
        for r in self.slot_editor.get_regions():
            self._slot_print_modes.setdefault(r["id"], self.print_mode_combo.currentData())
        self._update_slot_status()

    def _update_slot_status(self) -> None:
        regions = self.slot_editor.get_regions()
        if not regions:
            self.slot_status.setText("配置済みの合計欄はありません。")
            return
        parts = []
        for r in regions:
            mode = self._slot_print_modes.get(r["id"], "number")
            parts.append(f"{r['id']}（{'ラベル付き' if mode == 'label' else '数字のみ'}）")
        self.slot_status.setText("配置済み: " + "、".join(parts))

    def _on_delete_slot(self) -> None:
        self.slot_editor.delete_selected()

    def _on_save_slots(self) -> None:
        if not self.app.require_active_test():
            return
        regions = self.slot_editor.get_regions()
        slots = []
        current_mode = self.print_mode_combo.currentData()
        for r in regions:
            slots.append(
                {
                    "slotKey": r["id"],
                    "x": r["x"],
                    "y": r["y"],
                    "width": r["width"],
                    "height": r["height"],
                    "printMode": self._slot_print_modes.get(r["id"], current_mode),
                }
            )
        try:
            count = save_output_slots(self.app.active_test_id, slots)
            h.info(self, "保存完了", f"合計欄を {count} 件保存しました。")
            self._update_slot_status()
        except Exception as e:
            h.error(self, "エラー", str(e))

    # ---------- 書式 ----------

    def _load_style_to_form(self, style: dict[str, Any]) -> None:
        mark = style["mark"]
        self.style_inset.setValue(float(mark["insetRatio"]))
        self.style_maru_color.setText(mark["maru"]["strokeColor"])
        self.style_maru_fill.setValue(float(mark["maru"]["fillOpacity"]))
        self.style_sankaku_color.setText(mark["sankaku"]["strokeColor"])
        self.style_batsu_color.setText(mark["batsu"]["strokeColor"])
        self.style_score_color.setText(mark["score"]["color"])
        self.style_score_size.setValue(float(mark["score"]["sizeRatio"]))
        self.style_total_color.setText(style["total"]["color"])
        self.style_total_size.setValue(float(style["total"]["sizeRatio"]))
        self.style_total_min.setValue(int(style["total"]["minFontSize"]))

    def _collect_style(self) -> dict[str, Any]:
        style = get_feedback_style()
        style["mark"]["insetRatio"] = self.style_inset.value()
        style["mark"]["maru"]["strokeColor"] = self.style_maru_color.text().strip() or "#dc2626"
        style["mark"]["maru"]["fillOpacity"] = self.style_maru_fill.value()
        style["mark"]["sankaku"]["strokeColor"] = self.style_sankaku_color.text().strip() or "#ea580c"
        style["mark"]["batsu"]["strokeColor"] = self.style_batsu_color.text().strip() or "#2563eb"
        style["mark"]["score"]["color"] = self.style_score_color.text().strip() or "#111827"
        style["mark"]["score"]["sizeRatio"] = self.style_score_size.value()
        style["total"]["color"] = self.style_total_color.text().strip() or "#111827"
        style["total"]["sizeRatio"] = self.style_total_size.value()
        style["total"]["minFontSize"] = self.style_total_min.value()
        return style

    def _on_save_style(self) -> None:
        try:
            save_feedback_style(self._collect_style())
            h.info(self, "保存完了", "出力書式を保存しました（全テスト共通）。")
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _on_reset_style(self) -> None:
        self._load_style_to_form(reset_feedback_style())
        h.info(self, "リセット", "出力書式をデフォルトに戻しました。")

    # ---------- プレビュー ----------

    def _on_preview(self) -> None:
        if not self.app.require_active_test():
            return
        idx = self.preview_row_combo.currentIndex()
        if idx < 0 or idx >= len(self._rows):
            h.warn(self, "行未選択", "プレビューする行を選択してください。")
            return
        row = self._rows[idx]
        test_id = self.app.active_test_id
        self.preview_label.setText("合成中…")

        def done(img, err):
            if err:
                self.preview_label.setText("")
                h.error(self, "プレビューエラー", str(err))
                return
            self._preview_image = img
            self._update_preview_pixmap()

        h.run_in_thread(self, lambda: render_feedback_for_row(test_id, row), done)

    def _update_preview_pixmap(self) -> None:
        if self._preview_image is None:
            return
        zoom = max(10, min(200, self.preview_zoom.value())) / 100.0
        pix = pil_to_qpixmap(self._preview_image)
        w = max(100, int(pix.width() * zoom))
        self.preview_label.setPixmap(pix.scaledToWidth(w, Qt.SmoothTransformation))

    # ---------- 一括生成 ----------

    def _on_batch(self) -> None:
        if not self.app.require_active_test():
            return
        test_id = self.app.active_test_id
        self.batch_btn.setEnabled(False)
        self.batch_progress.setValue(0)
        self.batch_status.setText("個票を生成中…")

        bridge = ProgressBridge(self)
        bridge.updated.connect(self._on_batch_progress)

        def task():
            def on_progress(current: int, total: int, name: str) -> None:
                bridge.updated.emit(current, total, name)

            return batch_generate_feedback(test_id, on_progress=on_progress)

        h.run_in_thread(self, task, self._on_batch_done)

    def _on_batch_progress(self, current: int, total: int, name: str) -> None:
        pct = int(current / total * 100) if total else 0
        self.batch_progress.setValue(pct)
        self.batch_status.setText(f"{current}/{total} {name}")

    def _on_batch_done(self, result: dict[str, Any] | None, err: Exception | None) -> None:
        self.batch_btn.setEnabled(True)
        if err:
            self.batch_status.setText("")
            h.error(self, "一括生成エラー", str(err))
            return
        assert result is not None
        msg = f"生成 {result['saved']} 件 / スキップ {len(result['skipped'])} 件 / エラー {len(result['errors'])} 件\n保存先: {result['outputDir']}"
        self.batch_status.setText(msg.replace("\n", " — "))
        h.info(self, "一括生成完了", msg)
