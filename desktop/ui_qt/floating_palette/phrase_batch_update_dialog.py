"""定型文一括更新ダイアログ。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.text_annotation_repo import (
    bulk_update_phrase_boxes,
    find_phrase_placements,
)
from ui_qt import helpers as h
from ui_qt.floating_palette.phrase_template_prefs import (
    phrase_display_label,
    phrase_palette_detail_html,
    phrase_preview_text,
)
from ui_qt.floating_palette.text_rich import qt_label_alignment
from ui_qt.style import COLORS


class PhraseBatchUpdateDialog(QDialog):
    """同一 phraseGroupId の配置を現在のテスト内で一括更新する。"""

    applied = Signal(int)

    def __init__(
        self,
        parent: QWidget | None,
        *,
        test_id: str,
        template: dict[str, Any],
    ) -> None:
        super().__init__(parent)
        self._test_id = str(test_id or "").strip()
        self._template = dict(template or {})
        self._group_id = str(self._template.get("phraseGroupId") or "").strip()
        self._changed_count = 0

        self.setWindowTitle("定型文一括更新")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumSize(520, 480)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        header = QLabel(f"グループ ID: {self._group_id}")
        header.setStyleSheet("font-weight: 700; font-size: 12px;")
        root.addWidget(header)

        tpl_label = QLabel(phrase_display_label(self._template))
        tpl_label.setWordWrap(True)
        root.addWidget(tpl_label)

        preview = QLabel()
        preview.setWordWrap(True)
        preview.setTextFormat(Qt.TextFormat.RichText)
        preview.setAlignment(qt_label_alignment(self._template.get("style")))
        preview.setText(phrase_palette_detail_html(self._template))
        preview.setStyleSheet(
            f"padding: 8px; background: {COLORS['surface']};"
            f" border: 1px solid {COLORS['border']}; border-radius: 6px;"
        )
        root.addWidget(preview)

        self._count_label = h.muted_label("")
        root.addWidget(self._count_label)

        table_box = QGroupBox("配置一覧（現在のテスト）")
        table_lay = QVBoxLayout(table_box)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["記述欄", "受験者ID", "氏名", "ファイル名"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        table_lay.addWidget(self._table)
        root.addWidget(table_box, 1)

        ops = QGroupBox("操作")
        ops_lay = QVBoxLayout(ops)

        append_row = QHBoxLayout()
        append_row.addWidget(QLabel("一斉追加:"))
        self._append_text = QPlainTextEdit()
        self._append_text.setPlaceholderText("追加する文言")
        self._append_text.setMaximumHeight(72)
        append_row.addWidget(self._append_text, 1)
        pos_col = QVBoxLayout()
        self._append_before = QRadioButton("前に追加")
        self._append_after = QRadioButton("後に追加")
        self._append_after.setChecked(True)
        pos_group = QButtonGroup(self)
        pos_group.addButton(self._append_before)
        pos_group.addButton(self._append_after)
        pos_col.addWidget(self._append_before)
        pos_col.addWidget(self._append_after)
        append_row.addLayout(pos_col)
        append_btn = h.button("一斉追加を実行")
        append_btn.clicked.connect(self._on_append)
        append_row.addWidget(append_btn)
        ops_lay.addLayout(append_row)

        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("一斉変更:"))
        self._replace_text = QPlainTextEdit()
        self._replace_text.setPlainText(phrase_preview_text(self._template))
        self._replace_text.setMaximumHeight(72)
        replace_row.addWidget(self._replace_text, 1)
        replace_btn = h.button("一斉変更を実行")
        replace_btn.clicked.connect(self._on_replace)
        replace_row.addWidget(replace_btn)
        ops_lay.addLayout(replace_row)

        del_row = QHBoxLayout()
        del_btn = h.button("一斉削除", variant="danger")
        del_btn.clicked.connect(self._on_delete)
        del_row.addWidget(del_btn)
        del_row.addStretch(1)
        ops_lay.addLayout(del_row)

        root.addWidget(ops)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = h.button("閉じる")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

        self._reload_placements()

    @property
    def changed_count(self) -> int:
        return self._changed_count

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.raise_()
        self.activateWindow()

    def _notify_applied(self, count: int) -> None:
        if count > 0:
            self.applied.emit(count)

    def _reload_placements(self) -> None:
        placements = find_phrase_placements(self._test_id, self._group_id)
        self._table.setRowCount(len(placements))
        for i, p in enumerate(placements):
            self._table.setItem(i, 0, QTableWidgetItem(str(p.get("fieldName") or "")))
            self._table.setItem(i, 1, QTableWidgetItem(str(p.get("studentId") or "")))
            self._table.setItem(i, 2, QTableWidgetItem(str(p.get("studentName") or "")))
            self._table.setItem(i, 3, QTableWidgetItem(str(p.get("fileName") or "")))
        self._count_label.setText(f"対象: {len(placements)} 件")

    def _on_delete(self) -> None:
        n = self._table.rowCount()
        if n <= 0:
            h.info(self, "一斉削除", "対象の配置がありません。")
            return
        ans = QMessageBox.question(
            self,
            "一斉削除",
            f"同一 ID の定型文テキストボックス {n} 件をすべて削除しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        count = bulk_update_phrase_boxes(
            self._test_id, self._group_id, "delete"
        )
        self._changed_count += count
        self._reload_placements()
        self._notify_applied(count)
        h.info(self, "一斉削除", f"{count} 件を削除しました。")

    def _on_append(self) -> None:
        text = self._append_text.toPlainText()
        if not str(text or "").strip():
            h.warn(self, "一斉追加", "追加する文言を入力してください。")
            return
        if self._table.rowCount() <= 0:
            h.info(self, "一斉追加", "対象の配置がありません。")
            return
        position = "before" if self._append_before.isChecked() else "after"
        count = bulk_update_phrase_boxes(
            self._test_id,
            self._group_id,
            "append",
            text=text,
            position=position,
        )
        self._changed_count += count
        self._reload_placements()
        self._notify_applied(count)
        h.info(self, "一斉追加", f"{count} 件を更新しました。")

    def _on_replace(self) -> None:
        if self._table.rowCount() <= 0:
            h.info(self, "一斉変更", "対象の配置がありません。")
            return
        text = self._replace_text.toPlainText()
        tpl = {
            **self._template,
            "text": text,
            "textHtml": "",
            "textFormat": "plain",
        }
        count = bulk_update_phrase_boxes(
            self._test_id,
            self._group_id,
            "replace",
            template=tpl,
        )
        self._changed_count += count
        self._reload_placements()
        self._notify_applied(count)
        h.info(self, "一斉変更", f"{count} 件を更新しました。")
