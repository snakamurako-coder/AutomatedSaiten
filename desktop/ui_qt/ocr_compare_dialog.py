"""再OCR結果の旧/新比較と欄単位採用ダイアログ。"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui_qt.style import COLORS


def _norm_text(value: Any) -> str:
    s = str(value or "").strip()
    return s if s else "なし"


class OcrCompareDialog(QDialog):
    """答案ナビ付きで旧テキストと新テキストを比較し、欄単位で採用する。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        fields: list[dict[str, Any]],
        previews: list[dict[str, Any]],
        on_commit: Callable[[dict[str, Any]], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("OCR結果の比較・採用")
        self.resize(780, 560)
        self._fields = list(fields)
        self._previews = list(previews)
        self._on_commit = on_commit
        self._index = 0
        self._field_checks: dict[str, QCheckBox] = {}
        self._decisions: dict[str, dict[str, bool]] = {}
        self._committed = 0

        for p in self._previews:
            name = str(p.get("fileName") or "")
            old_t = p.get("oldTexts") or {}
            new_t = p.get("newTexts") or {}
            has_old = bool(p.get("hasExisting")) and bool(old_t)
            adopt: dict[str, bool] = {}
            for f in self._fields:
                fid = str(f.get("id") or "")
                old_v = _norm_text(old_t.get(fid))
                new_v = _norm_text(new_t.get(fid))
                if not has_old:
                    adopt[fid] = True
                else:
                    adopt[fid] = old_v != new_v
            self._decisions[name] = adopt

        root = QVBoxLayout(self)
        self._title = QLabel("")
        self._title.setStyleSheet(f"font-weight: 600; color: {COLORS['text']};")
        root.addWidget(self._title)
        self._hint = QLabel(
            "差分がある欄は初期で「新を採用」です。答案ごとに確定すると DB に反映されます（判定・得点は保持）。"
        )
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {COLORS['text_secondary']};")
        root.addWidget(self._hint)

        sheet_btns = QHBoxLayout()
        all_new = QPushButton("この答案はすべて新")
        all_new.clicked.connect(lambda: self._set_all_adopt(True))
        sheet_btns.addWidget(all_new)
        all_old = QPushButton("この答案はすべて旧")
        all_old.clicked.connect(lambda: self._set_all_adopt(False))
        sheet_btns.addWidget(all_old)
        sheet_btns.addStretch()
        root.addLayout(sheet_btns)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._fields_host = QWidget()
        self._fields_layout = QVBoxLayout(self._fields_host)
        self._fields_layout.setContentsMargins(4, 4, 4, 4)
        self._fields_layout.setSpacing(8)
        scroll.setWidget(self._fields_host)
        root.addWidget(scroll, 1)

        nav = QHBoxLayout()
        self._prev_btn = QPushButton("← 前へ")
        self._prev_btn.clicked.connect(self._go_prev)
        nav.addWidget(self._prev_btn)
        self._next_btn = QPushButton("次へ →")
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._next_btn)
        nav.addStretch()
        commit_btn = QPushButton("この答案の選択を確定")
        commit_btn.setObjectName("PrimaryBtn")
        commit_btn.clicked.connect(self._commit_current)
        nav.addWidget(commit_btn)
        commit_next = QPushButton("確定して次へ")
        commit_next.clicked.connect(self._commit_and_next)
        nav.addWidget(commit_next)
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        nav.addWidget(close_btn)
        root.addLayout(nav)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {COLORS['text_secondary']};")
        root.addWidget(self._status)

        self._load_current()

    def committed_count(self) -> int:
        return self._committed

    def _current(self) -> dict[str, Any] | None:
        if not self._previews or self._index < 0 or self._index >= len(self._previews):
            return None
        return self._previews[self._index]

    def _go_prev(self) -> None:
        self._store_checks()
        if self._index > 0:
            self._index -= 1
            self._load_current()

    def _go_next(self) -> None:
        self._store_checks()
        if self._index < len(self._previews) - 1:
            self._index += 1
            self._load_current()

    def _set_all_adopt(self, use_new: bool) -> None:
        for cb in self._field_checks.values():
            cb.setChecked(use_new)
        self._store_checks()

    def _store_checks(self) -> None:
        entry = self._current()
        if entry is None:
            return
        name = str(entry.get("fileName") or "")
        self._decisions[name] = {
            fid: cb.isChecked() for fid, cb in self._field_checks.items()
        }

    def _clear_fields_ui(self) -> None:
        while self._fields_layout.count():
            item = self._fields_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._field_checks.clear()

    def _load_current(self) -> None:
        self._clear_fields_ui()
        entry = self._current()
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < len(self._previews) - 1)
        if entry is None:
            self._title.setText("対象なし")
            return
        name = str(entry.get("fileName") or "")
        sid = str(entry.get("studentId") or "") or "—"
        self._title.setText(
            f"{self._index + 1}/{len(self._previews)}  {name}  （生徒ID: {sid}）"
        )
        old_t = entry.get("oldTexts") or {}
        new_t = entry.get("newTexts") or {}
        adopt = self._decisions.get(name) or {}
        has_old = bool(entry.get("hasExisting")) and bool(old_t)

        for f in self._fields:
            fid = str(f.get("id") or "")
            label = str(f.get("displayName") or fid)
            old_v = _norm_text(old_t.get(fid)) if has_old else "（未登録）"
            new_v = _norm_text(new_t.get(fid))
            box = QGroupBox(label)
            grid = QGridLayout(box)
            grid.addWidget(QLabel("旧"), 0, 0)
            old_lbl = QLabel(old_v)
            old_lbl.setWordWrap(True)
            old_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(old_lbl, 0, 1)
            grid.addWidget(QLabel("新"), 1, 0)
            new_lbl = QLabel(new_v)
            new_lbl.setWordWrap(True)
            new_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if has_old and old_v != new_v:
                new_lbl.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 600;")
            grid.addWidget(new_lbl, 1, 1)
            cb = QCheckBox("新を採用")
            cb.setChecked(bool(adopt.get(fid, True)))
            if not has_old:
                cb.setChecked(True)
                cb.setEnabled(False)
                cb.setToolTip("未反映のため新テキストを登録します")
            self._field_checks[fid] = cb
            grid.addWidget(cb, 2, 1)
            self._fields_layout.addWidget(box)
        self._fields_layout.addStretch()
        self._status.setText(f"確定済み {self._committed} 件")

    def _merged_texts(self, entry: dict[str, Any]) -> dict[str, str]:
        name = str(entry.get("fileName") or "")
        old_t = dict(entry.get("oldTexts") or {})
        new_t = dict(entry.get("newTexts") or {})
        adopt = self._decisions.get(name) or {}
        merged: dict[str, str] = {}
        for f in self._fields:
            fid = str(f.get("id") or "")
            use_new = bool(adopt.get(fid, True))
            if use_new:
                merged[fid] = _norm_text(new_t.get(fid))
            else:
                merged[fid] = _norm_text(old_t.get(fid))
        return merged

    def _commit_current(self) -> None:
        self._store_checks()
        entry = self._current()
        if entry is None or not self._on_commit:
            return
        payload = {
            "fileName": str(entry.get("fileName") or ""),
            "sourcePath": str(entry.get("sourcePath") or ""),
            "warpedPath": str(entry.get("warpedPath") or ""),
            "studentId": str(entry.get("studentId") or ""),
            "textMapping": self._merged_texts(entry),
        }
        try:
            action = self._on_commit(payload)
        except Exception as e:
            self._status.setText(f"確定失敗: {e}")
            return
        self._committed += 1
        self._status.setText(f"確定済み {self._committed} 件（{action}）")

    def _commit_and_next(self) -> None:
        self._commit_current()
        if self._index < len(self._previews) - 1:
            self._index += 1
            self._load_current()
        else:
            self.accept()
