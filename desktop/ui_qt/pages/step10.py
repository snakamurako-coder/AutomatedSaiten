"""⑩ 領域設定ページ（記述欄を大問・範囲・能力にグルーピング）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from constants import MANUAL_GRADING_STEP_ID
from models.criteria_repo import list_question_judgment_disagreements

from models.domain_repo import (
    calculate_domain_scores,
    get_domain_settings_for_ui,
    save_domain_settings,
)
from models.grading_status import field_grading_complete_map
from ui_qt import helpers as h
from ui_qt.layout_helpers import make_expanding
from ui_qt.style import COLORS
from ui_qt.table_cells import make_editable_item, make_readonly_item, wire_excel_edit_columns


def _judgment_count_text(counts: dict[str, int]) -> str:
    return f"○ {int(counts.get('○') or 0)}    △ {int(counts.get('△') or 0)}    × {int(counts.get('×') or 0)}"


class JudgmentMismatchDialog(QDialog):
    """食い違う問いの手動・自動の○△×数を二列で示す。"""

    def __init__(self, parent: QWidget, rows: list[dict[str, Any]]) -> None:
        super().__init__(parent)
        self._app = getattr(parent, "app", None)
        self.setWindowTitle("手動採点と自動採点の判定が食い違っています")
        self.resize(920, 460)
        lay = QVBoxLayout(self)
        note = QLabel(
            "両方に判定がある答案について、"
            "手動採点と自動採点（採点基準）の ○・△・× の数です。"
            "「切り分ける」で、その問いの同じOCRの答案をまとめて比較できます。"
        )
        note.setWordWrap(True)
        lay.addWidget(note)

        table = QTableWidget(len(rows), 4)
        table.setHorizontalHeaderLabels(["問い", "手動採点", "自動採点", ""])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setWordWrap(True)
        for i, row in enumerate(rows):
            name = str(row.get("display_name") or row.get("field_id") or "")
            fid = str(row.get("field_id") or "")
            name_item = QTableWidgetItem(
                f"{name}\n不一致 {int(row.get('mismatch_count') or 0)} 人"
                f" / 比較 {int(row.get('compared_count') or 0)} 人"
            )
            manual_item = QTableWidgetItem(_judgment_count_text(row.get("manual") or {}))
            auto_item = QTableWidgetItem(_judgment_count_text(row.get("auto") or {}))
            for item in (name_item, manual_item, auto_item):
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                )
            table.setItem(i, 0, name_item)
            table.setItem(i, 1, manual_item)
            table.setItem(i, 2, auto_item)
            split_btn = h.button("切り分ける", lambda _c=False, field_id=fid: self._open_split(field_id))
            table.setCellWidget(i, 3, split_btn)
            table.setRowHeight(i, 52)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(False)
        table.setColumnWidth(0, 220)
        table.setColumnWidth(1, 220)
        table.setColumnWidth(3, 120)
        lay.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText("確認")
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

    def _open_split(self, field_id: str) -> None:
        pages = getattr(self._app, "pages", None)
        page = pages.get(MANUAL_GRADING_STEP_ID) if isinstance(pages, dict) else None
        opener = getattr(page, "open_mismatch_ocr_split", None)
        if not callable(opener):
            h.warn(self, "切り分け", "手動採点の画面を開けません。")
            return
        opener(field_id, self)


def show_judgment_mismatch_dialog(host: QWidget) -> None:
    if not host.isVisible():
        return
    app = getattr(host, "app", None)
    test_id = getattr(app, "active_test_id", None)
    if not test_id:
        return
    try:
        rows = list_question_judgment_disagreements(test_id)
    except Exception as e:
        h.warn(host, "判定の確認", str(e))
        return
    if not rows:
        return
    JudgmentMismatchDialog(host, rows).exec()


class Step10Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._rows: list[dict[str, Any]] = []

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("⑩ 領域の設定"))
        root.addWidget(
            h.muted_label(
                "記述欄を「大問」「範囲」「能力」のラベルでグルーピングすると、"
                "領域別の得点が集計され、考査総括・個票に反映されます。"
                "同じラベルを付けた記述欄が 1 つの領域として合算されます。"
                "「採点」列は全回答が ○△× で確定しているかを示します（保留?・未採点があると未完）。"
            )
        )

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["記述欄", "大問", "範囲", "能力", "採点"])
        self.table.setColumnWidth(0, 220)
        for c in (1, 2, 3):
            self.table.setColumnWidth(c, 140)
        self.table.setColumnWidth(4, 72)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectItems)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.verticalHeader().setVisible(False)
        wire_excel_edit_columns(self.table, (1, 2, 3))
        make_expanding(self.table)
        root.addWidget(self.table, 1)

        btns = QHBoxLayout()
        btns.addWidget(
            h.button("領域設定を保存・再計算", self._on_save, variant="primary")
        )
        btns.addWidget(h.button("再読込", self.refresh))
        btns.addStretch()
        root.addLayout(btns)

        self.status_label = h.caption_label("")
        root.addWidget(self.status_label)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        self._rows = get_domain_settings_for_ui(self.app.active_test_id)
        complete_map = field_grading_complete_map(self.app.active_test_id)
        done_bg = QColor(COLORS["selection_soft"])
        self.table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            fid = row["fieldId"]
            done = bool(complete_map.get(fid, False))
            name_item = QTableWidgetItem(row["displayName"])
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, name_item)
            self.table.setItem(i, 1, make_editable_item(row["daiMon"]))
            self.table.setItem(i, 2, make_editable_item(row["hanI"]))
            self.table.setItem(i, 3, make_editable_item(row["noryoku"]))
            self.table.setItem(i, 4, make_readonly_item("完了" if done else "未完", center=True))
            if done:
                for c in range(5):
                    item = self.table.item(i, c)
                    if item is not None:
                        item.setBackground(done_bg)
        if not self._rows:
            self.status_label.setText("記述欄がありません。先に ② 回答欄設定を完了してください。")
        else:
            done_n = sum(1 for r in self._rows if complete_map.get(r["fieldId"]))
            self.status_label.setText(f"採点完了 {done_n} / {len(self._rows)} 記述欄")
        QTimer.singleShot(0, lambda: show_judgment_mismatch_dialog(self))

    def _on_save(self) -> None:
        if not self.app.require_active_test():
            return
        settings = []
        for i, row in enumerate(self._rows):
            settings.append(
                {
                    "fieldId": row["fieldId"],
                    "daiMon": self._cell_text(i, 1),
                    "hanI": self._cell_text(i, 2),
                    "noryoku": self._cell_text(i, 3),
                }
            )
        try:
            save_domain_settings(self.app.active_test_id, settings)
            updated = calculate_domain_scores(self.app.active_test_id)
            self.status_label.setText(f"領域設定を保存し、{updated} 件の得点を再計算しました。")
            h.info(self, "保存完了", f"領域設定を保存し、{updated} 件の得点を再計算しました。")
            self.refresh()
        except Exception as e:
            h.error(self, "エラー", str(e))

    def _cell_text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text().strip() if item else ""
