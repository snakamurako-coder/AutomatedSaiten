"""空DB作成 — 手動採点ルートの先頭（OCR なしで採点用レコードを用意）。"""

from __future__ import annotations

from functools import partial
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from constants import MANUAL_GRADING_STEP_ID
from models.test_repo import normalize_file_name, resolve_student_inbox
from services.manual_bootstrap import run_manual_bootstrap
from services.work_queue import build_file_inventory, find_warped_for_original
from ui_qt import helpers as h
from ui_qt.layout_helpers import main_table_frame
from ui_qt.table_cells import is_toggle_checked, make_toggle_item, set_toggle_checked, wire_toggle_columns

_COL_CHECK = 0
_COL_STATUS = 1
_COL_WARPED = 2
_COL_DB = 3
_COL_FILE = 4


class StepManualBootstrapPage(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._inventory_rows: list[dict[str, Any]] = []
        self._scanned = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        root.addWidget(h.title_label("空DB作成"))
        root.addWidget(
            h.muted_label(
                "OCR なしで採点用レコードを作成します。テキストは全記述欄空欄です。"
                "⑤ トリミングで作成した補正画像パスを紐付けます。"
                "後から自動採点の ⑦ OCR実行 でテキストを注入できます（判定・得点は保持）。"
            )
        )

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("回答フォルダ"))
        self.inbox_edit = QLineEdit()
        self.inbox_edit.setReadOnly(True)
        folder_row.addWidget(self.inbox_edit, 1)
        folder_row.addWidget(
            h.open_folder_button(self._on_open_inbox_folder, text="フォルダを開く")
        )
        self.scan_btn = h.button("フォルダを再認識", self._scan_folder, variant="primary")
        folder_row.addWidget(self.scan_btn)
        root.addLayout(folder_row)

        self.stats_label = h.muted_label("一覧未表示 — 「フォルダを再認識」を押してください。")
        root.addWidget(self.stats_label)

        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("チェック:"))
        for label, mode in [("全て", "all"), ("全解除", "none"), ("＋補正済", "warped"), ("＋未登録", "not_db")]:
            sel_row.addWidget(h.button(label, partial(self._select_by_mode, mode)))
        sel_row.addStretch()
        root.addLayout(sel_row)

        action_row = QHBoxLayout()
        self.bootstrap_btn = h.button(
            "チェックした答案で空DB作成",
            self._on_bootstrap,
            variant="primary",
        )
        action_row.addWidget(self.bootstrap_btn)
        self.manual_btn = h.button("手動採点へ", self._go_manual_grading)
        action_row.addWidget(self.manual_btn)
        action_row.addStretch()
        root.addLayout(action_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["☑", "状態", "補正", "DB", "ファイル名"])
        self.table.horizontalHeader().setSectionResizeMode(_COL_FILE, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        wire_toggle_columns(self.table, (_COL_CHECK,))
        root.addWidget(main_table_frame(self.table), 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        root.addWidget(self.log)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        test_id = self.app.active_test_id
        self.inbox_edit.setText(resolve_student_inbox(test_id))
        if self._scanned:
            self._scan_folder()

    def _on_open_inbox_folder(self) -> None:
        if not self.app.require_active_test():
            return
        h.open_path_in_explorer(resolve_student_inbox(self.app.active_test_id))

    def _scan_folder(self) -> None:
        if not self.app.require_active_test():
            return
        test_id = self.app.active_test_id
        inv = build_file_inventory(test_id, resolve_student_inbox(test_id))
        self._inventory_rows = list(inv.get("rows") or [])
        self._scanned = True
        self._render_table()
        st = inv.get("stats") or {}
        self.stats_label.setText(
            f"全 {st.get('total', len(self._inventory_rows))} 件 / "
            f"DB登録 {st.get('processed', 0)} / 補正済 {sum(1 for r in self._inventory_rows if r.get('warpedPath'))}"
        )

    def _render_table(self) -> None:
        self.table.setRowCount(0)
        test_id = self.app.active_test_id or ""
        for rd in self._inventory_rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, _COL_CHECK, make_toggle_item(True))
            name = str(rd.get("fileName") or "")
            warped = bool(rd.get("warpedPath") or find_warped_for_original(test_id, name))
            in_db = rd.get("status") == "反映済"
            self.table.setItem(row, _COL_STATUS, QTableWidgetItem(str(rd.get("status") or "")))
            self.table.setItem(row, _COL_WARPED, QTableWidgetItem("あり" if warped else "—"))
            self.table.setItem(row, _COL_DB, QTableWidgetItem("済" if in_db else "—"))
            self.table.setItem(row, _COL_FILE, QTableWidgetItem(name))

    def _row_checked(self, row_idx: int) -> bool:
        item = self.table.item(row_idx, _COL_CHECK)
        return is_toggle_checked(item)

    def _select_by_mode(self, mode: str) -> None:
        test_id = self.app.active_test_id or ""
        for i, rd in enumerate(self._inventory_rows):
            if mode == "all":
                checked = True
            elif mode == "none":
                checked = False
            elif mode == "warped":
                name = str(rd.get("fileName") or "")
                checked = bool(rd.get("warpedPath") or find_warped_for_original(test_id, name))
            elif mode == "not_db":
                checked = rd.get("status") != "反映済"
            else:
                checked = False
            set_toggle_checked(self.table.item(i, _COL_CHECK), checked)

    def _checked_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        test_id = self.app.active_test_id or ""
        for i, rd in enumerate(self._inventory_rows):
            if not self._row_checked(i):
                continue
            q = rd.get("queueItem") or {}
            name = str(rd.get("fileName") or "")
            warped = str(rd.get("warpedPath") or q.get("warpedPath") or "")
            if not warped:
                found = find_warped_for_original(test_id, name)
                warped = found or ""
            items.append(
                {
                    "name": name,
                    "fileName": name,
                    "path": str(q.get("path") or rd.get("sourcePath") or ""),
                    "warpedPath": warped,
                }
            )
        return items

    def _on_bootstrap(self) -> None:
        if not self.app.require_active_test():
            return
        if not self._scanned:
            h.warn(self, "未認識", "先に「フォルダを再認識」を実行してください。")
            return
        items = self._checked_items()
        if not items:
            h.warn(self, "選択なし", "空DB作成するファイルにチェックを入れてください。")
            return
        test_id = self.app.active_test_id

        def task():
            return run_manual_bootstrap(test_id, items)

        def done(result, err):
            if err:
                h.error(self, "空DB作成エラー", str(err))
                return
            assert result is not None
            lines = [
                f"新規 {result.get('inserted', 0)} 件 / 更新 {result.get('updated', 0)} 件"
            ]
            skipped = result.get("skippedNoWarp") or []
            if skipped:
                lines.append(f"補正画像なしでスキップ {len(skipped)} 件")
            errs = result.get("errors") or []
            if errs:
                lines.append(f"エラー {len(errs)} 件")
            self.log.appendPlainText("--- 空DB作成: " + " / ".join(lines) + " ---")
            if skipped:
                self.log.appendPlainText("補正なし: " + ", ".join(skipped[:10]))
            self._scan_folder()

        h.run_in_thread(self, task, done)

    def _go_manual_grading(self) -> None:
        mw = self.app
        if hasattr(mw, "load_step"):
            mw.load_step(MANUAL_GRADING_STEP_ID)
