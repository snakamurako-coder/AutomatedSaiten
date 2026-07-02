"""⑧ 本人欄設定ページ（学年・組・番号・ID・氏名の矩形指定）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.identity_repo import IDENTITY_TYPES, get_identity_fields, save_identity_fields
from models.test_repo import get_test_info
from ui_qt import helpers as h
from ui_qt.region_editor import AnswerRegionEditor
from ui_qt.style import set_variant


class Step8Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._selected_type: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("⑧ 本人欄設定"))
        root.addWidget(
            h.muted_label(
                "学年・組・番号・ID・氏名のうち 1 つ以上を選び、模範解答の上をドラッグして枠を設定します。"
                "この枠は ⑨ の照合で切り出し画像として使われます（OCR はしません）。"
            )
        )

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("欄種別"))
        self.type_buttons: dict[str, QPushButton] = {}
        for t in IDENTITY_TYPES:
            btn = QPushButton(t)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, tt=t: self._select_type(tt))
            self.type_buttons[t] = btn
            toolbar.addWidget(btn)
        toolbar.addSpacing(12)
        toolbar.addWidget(h.button("やり直し", self._on_reset, variant="danger-soft"))
        toolbar.addWidget(h.button("本人欄を保存", self._on_save, variant="primary"))
        toolbar.addWidget(h.button("再読込", self.refresh))
        toolbar.addStretch()
        root.addLayout(toolbar)

        self.hint_label = h.caption_label("欄種別を選んでから画像上をドラッグしてください。")
        root.addWidget(self.hint_label)

        self.editor = AnswerRegionEditor(on_change=self._on_regions_changed)
        root.addWidget(self.editor, 1)

        self.status_label = h.caption_label("")
        root.addWidget(self.status_label)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        info = get_test_info(self.app.active_test_id)
        model_path = info.get("modelAnswerPath") or ""
        if model_path and Path(model_path).exists():
            try:
                self.editor.load_image_from_path(model_path)
            except Exception as e:
                self.status_label.setText(f"模範解答の表示に失敗: {e}")
                return
        else:
            self.status_label.setText("模範解答が未登録です。先に ① 回答欄設定で読み込んでください。")
            return
        fields = get_identity_fields(self.app.active_test_id)
        self.editor.set_regions(
            [
                {
                    "id": f["type"],
                    "displayName": f["type"],
                    "x": f["x"],
                    "y": f["y"],
                    "width": f["width"],
                    "height": f["height"],
                }
                for f in fields
            ]
        )
        self._update_type_buttons()
        self.status_label.setText(f"設定済み: {len(fields)} 欄")

    def _select_type(self, type_name: str) -> None:
        self._selected_type = type_name
        for t, btn in self.type_buttons.items():
            btn.setChecked(t == type_name)
        self.editor.set_pending_label(type_name, replace_same=True)
        self.hint_label.setText(f"「{type_name}」欄を画像上でドラッグして指定してください（再ドラッグで上書き）。")

    def _on_regions_changed(self) -> None:
        self._update_type_buttons()

    def _update_type_buttons(self) -> None:
        done_types = {r["id"] for r in self.editor.get_regions()}
        for t, btn in self.type_buttons.items():
            if t in done_types:
                set_variant(btn, "success")
            else:
                btn.setProperty("variant", None)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        done_list = [t for t in IDENTITY_TYPES if t in done_types]
        if done_list:
            self.status_label.setText("設定済み: " + "、".join(done_list))

    def _on_reset(self) -> None:
        if (
            QMessageBox.question(self, "確認", "設定した本人欄をすべてクリアしますか？")
            != QMessageBox.Yes
        ):
            return
        self.editor.clear_all_regions()
        self.status_label.setText("クリアしました。")

    def _on_save(self) -> None:
        if not self.app.require_active_test():
            return
        regions = self.editor.get_regions()
        fields = [
            {
                "type": r["id"],
                "x": r["x"],
                "y": r["y"],
                "width": r["width"],
                "height": r["height"],
            }
            for r in regions
            if r["id"] in IDENTITY_TYPES
        ]
        if not fields:
            h.error(self, "保存エラー", "本人確認欄を 1 つ以上設定してください。")
            return
        try:
            count = save_identity_fields(self.app.active_test_id, fields)
            h.info(self, "保存完了", f"本人確認欄を {count} 件保存しました。")
        except Exception as e:
            h.error(self, "エラー", str(e))
