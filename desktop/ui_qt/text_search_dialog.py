"""テスト内 OCR／テキストボックスの文字列検索ダイアログ。"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.text_search import search_test_texts
from ui_qt import helpers as h
from ui_qt.style import COLORS, set_variant


class TextSearchDialog(QDialog):
    """サイドバー「文字列検索」から開く専用モーダル。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        test_id: str,
        on_open_crop: Callable[[dict[str, Any]], None],
        on_open_full_sheet: Callable[[dict[str, Any]], None],
        on_open_step4: Callable[[dict[str, Any]], None],
    ) -> None:
        super().__init__(parent)
        self._test_id = str(test_id or "").strip()
        self._on_open_crop = on_open_crop
        self._on_open_full_sheet = on_open_full_sheet
        self._on_open_step4 = on_open_step4
        self._hits: list[dict[str, Any]] = []

        self.setWindowTitle("文字列検索")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(780, 520)
        self.resize(880, 560)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        tip = QLabel(
            "このテストの OCR 読み取り結果と、全テキストボックス内の文字列を検索します。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {COLORS['text_secondary']};")
        root.addWidget(tip)

        row = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("検索文字列…")
        self.query_edit.returnPressed.connect(self._run_search)
        row.addWidget(self.query_edit, 1)
        search_btn = QPushButton("検索")
        set_variant(search_btn, "primary")
        search_btn.clicked.connect(self._run_search)
        row.addWidget(search_btn)
        root.addLayout(row)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        root.addWidget(self.status)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["種別", "生徒ID", "記述欄", "ファイル", "一致箇所"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda _i: self._open_crop())
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.btn_crop = QPushButton("記述欄画像")
        self.btn_crop.setToolTip("ヒットした答案の該当欄クロップを④で表示します")
        self.btn_crop.clicked.connect(self._open_crop)
        actions.addWidget(self.btn_crop)

        self.btn_full = QPushButton("一枚全容採点")
        self.btn_full.setToolTip("ヒットした答案の一枚全容採点を開き、該当欄を選択します")
        self.btn_full.clicked.connect(self._open_full)
        actions.addWidget(self.btn_full)

        self.btn_step4 = QPushButton("④採点基準の設定")
        self.btn_step4.setToolTip("④へ移動し、その記述欄を選択します")
        self.btn_step4.clicked.connect(self._open_step4)
        actions.addWidget(self.btn_step4)

        actions.addStretch(1)
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        actions.addWidget(close_btn)
        root.addLayout(actions)

        self._set_actions_enabled(False)
        self.query_edit.setFocus()

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.btn_crop.setEnabled(enabled)
        self.btn_full.setEnabled(enabled)
        self.btn_step4.setEnabled(enabled)

    def _run_search(self) -> None:
        q = self.query_edit.text().strip()
        if not q:
            h.warn(self, "入力不足", "検索文字列を入力してください。")
            return
        self._hits = search_test_texts(self._test_id, q)
        self.table.setRowCount(0)
        for hit in self._hits:
            r = self.table.rowCount()
            self.table.insertRow(r)
            kind = "OCR" if hit.get("kind") == "ocr" else "TB"
            vals = [
                kind,
                str(hit.get("studentId") or "—"),
                str(hit.get("fieldLabel") or hit.get("fieldId") or ""),
                str(hit.get("fileName") or ""),
                str(hit.get("snippet") or ""),
            ]
            for c, text in enumerate(vals):
                item = QTableWidgetItem(text)
                if c == 4:
                    item.setToolTip(str(hit.get("matchedText") or ""))
                self.table.setItem(r, c, item)
        n = len(self._hits)
        self.status.setText(f"{n} 件ヒット" if n else "一致する文字列はありません。")
        if n:
            self.table.selectRow(0)
            self._set_actions_enabled(True)
        else:
            self._set_actions_enabled(False)

    def _selected_hit(self) -> dict[str, Any] | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            h.warn(self, "未選択", "結果行を選択してください。")
            return None
        idx = rows[0].row()
        if idx < 0 or idx >= len(self._hits):
            return None
        return self._hits[idx]

    def _open_crop(self) -> None:
        hit = self._selected_hit()
        if hit is None:
            return
        self.accept()
        self._on_open_crop(hit)

    def _open_full(self) -> None:
        hit = self._selected_hit()
        if hit is None:
            return
        self.accept()
        self._on_open_full_sheet(hit)

    def _open_step4(self) -> None:
        hit = self._selected_hit()
        if hit is None:
            return
        self.accept()
        self._on_open_step4(hit)
