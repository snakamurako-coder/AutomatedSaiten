"""⑨ ID・氏名の照合ページ（本人欄画像と結果値の目視照合・手修正）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from models.identity_repo import get_verification_data
from models.roster_repo import update_student_identity
from services.crop_preview import load_crops_for_rows
from ui_qt import helpers as h
from ui_qt.helpers import pil_to_qpixmap
from ui_qt.style import COLORS


class Step9Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._crop_results: list[dict[str, Any]] = []
        self._edits: dict[int, QLineEdit] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("⑨ ID・氏名の照合"))
        root.addWidget(
            h.muted_label(
                "⑧ で設定した氏名欄／ID欄の切り出し画像と採点結果の値を並べて確認し、"
                "必要に応じて修正します（OCR はしません）。"
            )
        )

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("照合対象"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("氏名", "氏名")
        self.mode_combo.addItem("ID", "ID")
        ctrl.addWidget(self.mode_combo)
        ctrl.addWidget(h.button("本人欄画像を表示", self._on_run, variant="primary"))
        ctrl.addWidget(h.button("修正を保存", self._on_save, variant="success"))
        ctrl.addSpacing(16)
        ctrl.addWidget(QLabel("表示倍率"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(30, 400)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(160)
        self.zoom_slider.valueChanged.connect(lambda _v: self._render_grid())
        ctrl.addWidget(self.zoom_slider)
        ctrl.addStretch()
        root.addLayout(ctrl)

        self.status_label = h.caption_label("")
        root.addWidget(self.status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['sidebar']}; }}"
        )
        self.grid_panel = QWidget()
        self.grid_panel.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(self.grid_panel)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.grid.setSpacing(8)
        scroll.setWidget(self.grid_panel)
        root.addWidget(scroll, 1)

    def refresh(self) -> None:
        pass  # 表示はユーザー操作（本人欄画像を表示）で開始

    def _current_mode(self) -> str:
        return self.mode_combo.currentData() or "氏名"

    def _on_run(self) -> None:
        if not self.app.require_active_test():
            return
        data = get_verification_data(self.app.active_test_id)
        rows = data["rows"]
        identity_fields = data["identityFields"]
        if not rows:
            h.warn(self, "データなし", "採点結果がありません。③ テキスト化を先に実行してください。")
            return
        mode = self._current_mode()
        field = next((f for f in identity_fields if f["type"] == mode), None)
        if not field:
            h.warn(
                self,
                "本人欄未設定",
                f"「{mode}」欄が未設定です。⑧ 本人欄設定で枠を指定してください。",
            )
            return

        self.status_label.setText(f"画像を読み込み中…（{len(rows)} 件）")

        def task():
            return load_crops_for_rows(rows, field)

        def done(results, err):
            if err:
                self.status_label.setText("")
                h.error(self, "読込エラー", str(err))
                return
            self._crop_results = results
            ok = sum(1 for r in results if r.get("ok"))
            self.status_label.setText(f"{ok}/{len(results)} 件を表示中 — 値を修正したら「修正を保存」")
            self._render_grid()

        h.run_in_thread(self, task, done)

    def _render_grid(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._edits = {}
        if not self._crop_results:
            self.grid.addWidget(
                h.muted_label("「本人欄画像を表示」で切り出し画像を読み込みます"), 0, 0
            )
            return

        mode = self._current_mode()
        zoom = max(30, min(400, self.zoom_slider.value())) / 100.0
        cols = 4
        for idx, item in enumerate(self._crop_results):
            r, c = divmod(idx, cols)
            self.grid.addWidget(self._make_tile(item, mode, zoom), r, c, Qt.AlignTop | Qt.AlignLeft)
        self.grid.setColumnStretch(cols, 1)

    def _make_tile(self, item: dict[str, Any], mode: str, zoom: float) -> QWidget:
        tile = QFrame()
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(3)
        row = item["row"]

        if not item.get("ok"):
            tile.setStyleSheet(
                f"QFrame {{ background: {COLORS['danger_soft']}; border: 1px solid #fca5a5;"
                f" border-radius: 6px; }}"
            )
            err = QLabel(f"{row.get('fileName', '—')}\n{item.get('error', '読込失敗')}")
            err.setStyleSheet(f"color: {COLORS['danger']}; border: none; font-size: 10px;")
            err.setWordWrap(True)
            lay.addWidget(err)
            return tile

        tile.setStyleSheet(
            f"QFrame {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']};"
            f" border-radius: 6px; }}"
        )
        file_label = QLabel(str(row.get("fileName") or "")[:28])
        file_label.setStyleSheet(
            f"border: none; font-size: 9px; color: {COLORS['text_secondary']};"
        )
        lay.addWidget(file_label)

        pix = pil_to_qpixmap(item["pil"])
        w = max(60, int(pix.width() * zoom))
        img = QLabel()
        img.setPixmap(pix.scaledToWidth(w, Qt.SmoothTransformation))
        img.setStyleSheet("border: none;")
        lay.addWidget(img)

        edit = QLineEdit(str(row.get("studentId" if mode == "ID" else "name") or ""))
        edit.setPlaceholderText(mode)
        self._edits[int(row["id"])] = edit
        lay.addWidget(edit)
        return tile

    def _on_save(self) -> None:
        if not self.app.require_active_test() or not self._crop_results:
            return
        mode = self._current_mode()
        saved = 0
        for item in self._crop_results:
            if not item.get("ok"):
                continue
            row = item["row"]
            rid = int(row["id"])
            edit = self._edits.get(rid)
            if edit is None:
                continue
            value = edit.text().strip()
            student_id = value if mode == "ID" else str(row.get("studentId") or "")
            name = value if mode == "氏名" else str(row.get("name") or "")
            if student_id == str(row.get("studentId") or "") and name == str(row.get("name") or ""):
                continue
            update_student_identity(self.app.active_test_id, rid, student_id, name)
            row["studentId"] = student_id
            row["name"] = name
            saved += 1
        h.info(self, "保存完了", f"{saved} 件の ID・氏名を更新しました。")
        self.status_label.setText(f"{saved} 件を更新しました。")
