"""⑬ ID・氏名の照合ページ（本人欄画像と結果値の目視照合・手修正）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QByteArray, QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from models.identity_repo import get_identity_coord_basis, get_verification_data
from models.roster_repo import update_student_identities, update_student_identity
from services.crop_preview import load_crops_for_rows
from ui_qt import helpers as h
from ui_qt.helpers import pil_to_qpixmap
from ui_qt.layout_helpers import make_expanding
from ui_qt.style import COLORS

_IDENTITY_MIME = "application/x-automated-saiten-result-id"
_Identity = tuple[str, str]
_Snapshot = list[tuple[int, str, str]]


class _IdentityTile(QFrame):
    """本人欄タイル。中央ドロップで入れ替え、上下端で挿入ずらし。"""

    identityDropped = Signal(int, int, str)

    def __init__(self, result_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.result_id = result_id
        self._press_pos = None
        self._drop_hint = ""
        self.setObjectName(f"idTile{result_id}")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._apply_frame_style("")

    def _apply_frame_style(self, hint: str) -> None:
        accent = COLORS["accent"]
        border = COLORS["border"]
        surface = COLORS["surface"]
        if hint == "swap":
            extra = f"border: 2px solid {accent}; background: {COLORS['accent_soft']};"
        elif hint == "before":
            extra = f"border: 1px solid {border}; border-top: 4px solid {accent}; background: {surface};"
        elif hint == "after":
            extra = f"border: 1px solid {border}; border-bottom: 4px solid {accent}; background: {surface};"
        else:
            extra = f"border: 1px solid {border}; background: {surface};"
        self.setStyleSheet(
            f"QFrame#{self.objectName()} {{ {extra} border-radius: 6px; }}"
        )

    def set_drop_hint(self, hint: str) -> None:
        if hint == self._drop_hint:
            return
        self._drop_hint = hint
        self._apply_frame_style(hint)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if not isinstance(child, QLineEdit):
                self._press_pos = event.position().toPoint()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        self._press_pos = None
        mime = QMimeData()
        mime.setData(_IDENTITY_MIME, QByteArray(str(self.result_id).encode("utf-8")))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def _hint_at(self, y: float) -> str:
        height = max(1, self.height())
        if y < height * 0.28:
            return "before"
        if y > height * 0.72:
            return "after"
        return "swap"

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(_IDENTITY_MIME):
            event.acceptProposedAction()
            self.set_drop_hint(self._hint_at(event.position().y()))
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(_IDENTITY_MIME):
            event.acceptProposedAction()
            self.set_drop_hint(self._hint_at(event.position().y()))
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.set_drop_hint("")
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        hint = self._drop_hint or self._hint_at(event.position().y())
        self.set_drop_hint("")
        if not event.mimeData().hasFormat(_IDENTITY_MIME):
            event.ignore()
            return
        raw = bytes(event.mimeData().data(_IDENTITY_MIME)).decode("utf-8").strip()
        try:
            source_id = int(raw)
        except ValueError:
            event.ignore()
            return
        event.acceptProposedAction()
        self.identityDropped.emit(source_id, self.result_id, hint)


class Step13Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._crop_results: list[dict[str, Any]] = []
        self._edits: dict[int, QLineEdit] = {}
        self._undo_stack: list[_Snapshot] = []

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("⑬ ID・氏名の照合"))
        root.addWidget(
            h.muted_label(
                "切り出し画像は用紙に残したまま、生徒IDと氏名を調整します。"
                "タイルの中央へドロップすると入れ替え、上下の端へドロップするとその位置へ挿入して間を1つずつずらします。"
                "「取り消し」で直前の調整に戻ります。"
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
        self.undo_btn = h.button("取り消し", self._on_undo)
        self.undo_btn.setEnabled(False)
        ctrl.addWidget(self.undo_btn)
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
        make_expanding(scroll)
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
            h.warn(self, "データなし", "採点結果がありません。⑦ OCR実行を先に行ってください。")
            return
        mode = self._current_mode()
        field = next((f for f in identity_fields if f["type"] == mode), None)
        if not field:
            h.warn(
                self,
                "本人欄未設定",
                f"「{mode}」欄が未設定です。⑫ 本人欄設定で枠を指定してください。",
            )
            return
        basis = get_identity_coord_basis(self.app.active_test_id) or "warped"
        field = dict(field)
        field["imageBasis"] = basis
        field["testId"] = self.app.active_test_id
        basis_label = "元画像" if basis == "original" else "補正画像"

        self.status_label.setText(f"画像を読み込み中…（{len(rows)} 件・{basis_label}）")

        def task():
            return load_crops_for_rows(rows, field)

        def done(results, err):
            if err:
                self.status_label.setText("")
                h.error(self, "読込エラー", str(err))
                return
            self._crop_results = results
            self._undo_stack.clear()
            self._refresh_undo_button()
            ok = sum(1 for r in results if r.get("ok"))
            self.status_label.setText(
                f"{ok}/{len(results)} 件を表示中（{basis_label}） — 値を修正したら「修正を保存」"
            )
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
        row = item["row"]

        if not item.get("ok"):
            tile = QFrame()
            lay = QVBoxLayout(tile)
            lay.setContentsMargins(6, 6, 6, 6)
            lay.setSpacing(3)
            tile.setStyleSheet(
                f"QFrame {{ background: {COLORS['danger_soft']}; border: 1px solid #fca5a5;"
                f" border-radius: 6px; }}"
            )
            err = QLabel(f"{row.get('fileName', '—')}\n{item.get('error', '読込失敗')}")
            err.setStyleSheet(f"color: {COLORS['danger']}; border: none; font-size: 10px;")
            err.setWordWrap(True)
            lay.addWidget(err)
            return tile

        tile = _IdentityTile(int(row["id"]))
        tile.identityDropped.connect(self._on_identity_drop)
        tile.setToolTip("中央で入れ替え。上下の端で挿入して、間のIDを1つずらします。")
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(3)
        file_label = QLabel(str(row.get("fileName") or "")[:28])
        file_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        file_label.setStyleSheet(
            f"border: none; font-size: 9px; color: {COLORS['text_secondary']};"
        )
        lay.addWidget(file_label)

        pix = pil_to_qpixmap(item["pil"])
        w = max(60, int(pix.width() * zoom))
        img = QLabel()
        img.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
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

    def _refresh_undo_button(self) -> None:
        self.undo_btn.setEnabled(bool(self._undo_stack))

    def _ok_items(self) -> list[dict[str, Any]]:
        return [item for item in self._crop_results if item.get("ok") and item.get("row")]

    def _sync_edits_to_rows(self) -> None:
        mode = self._current_mode()
        for item in self._ok_items():
            row = item["row"]
            edit = self._edits.get(int(row["id"]))
            if edit is None:
                continue
            value = edit.text().strip()
            if mode == "ID":
                row["studentId"] = value
            else:
                row["name"] = value

    def _show_identity(self, row: dict[str, Any]) -> None:
        edit = self._edits.get(int(row["id"]))
        if edit is None:
            return
        key = "studentId" if self._current_mode() == "ID" else "name"
        edit.setText(str(row.get(key) or ""))

    def _apply_snapshot(self, snapshot: _Snapshot) -> list[tuple[int, str, str]]:
        by_id = {int(item["row"]["id"]): item["row"] for item in self._ok_items()}
        changed: list[tuple[int, str, str]] = []
        for rid, sid, name in snapshot:
            row = by_id.get(rid)
            if row is None:
                continue
            if str(row.get("studentId") or "") == sid and str(row.get("name") or "") == name:
                continue
            row["studentId"] = sid
            row["name"] = name
            self._show_identity(row)
            changed.append((rid, sid, name))
        return changed

    def _commit_identities(
        self,
        items: list[dict[str, Any]],
        new_idents: list[_Identity],
        message: str,
    ) -> None:
        before: _Snapshot = []
        changed: list[tuple[int, str, str]] = []
        for item, (sid, name) in zip(items, new_idents):
            row = item["row"]
            rid = int(row["id"])
            old_sid = str(row.get("studentId") or "")
            old_name = str(row.get("name") or "")
            before.append((rid, old_sid, old_name))
            if old_sid == sid and old_name == name:
                continue
            row["studentId"] = sid
            row["name"] = name
            self._show_identity(row)
            changed.append((rid, sid, name))
        if not changed:
            return
        self._undo_stack.append(before)
        try:
            update_student_identities(self.app.active_test_id, changed)
        except Exception as e:
            self._undo_stack.pop()
            self._apply_snapshot(before)
            h.error(self, "更新エラー", str(e))
            return
        self._refresh_undo_button()
        self.status_label.setText(message)

    def _on_identity_drop(self, source_id: int, target_id: int, action: str) -> None:
        if not self.app.require_active_test():
            return
        self._sync_edits_to_rows()
        items = self._ok_items()
        index = {int(item["row"]["id"]): i for i, item in enumerate(items)}
        src = index.get(int(source_id), -1)
        dst = index.get(int(target_id), -1)
        if src < 0 or dst < 0:
            return
        idents: list[_Identity] = [
            (str(item["row"].get("studentId") or ""), str(item["row"].get("name") or ""))
            for item in items
        ]
        if action == "swap":
            if src == dst:
                return
            idents[src], idents[dst] = idents[dst], idents[src]
            message = "生徒IDと氏名を入れ替えました。"
        else:
            insert_at = dst if action == "before" else dst + 1
            moved = idents.pop(src)
            if src < insert_at:
                insert_at -= 1
            insert_at = max(0, min(insert_at, len(idents)))
            idents.insert(insert_at, moved)
            message = "その位置に挿入し、間の生徒IDを1つずつずらしました。"
        self._commit_identities(items, idents, message)

    def _on_undo(self) -> None:
        if not self._undo_stack or not self.app.require_active_test():
            return
        before = self._undo_stack.pop()
        current: _Snapshot = [
            (
                int(item["row"]["id"]),
                str(item["row"].get("studentId") or ""),
                str(item["row"].get("name") or ""),
            )
            for item in self._ok_items()
        ]
        changed = self._apply_snapshot(before)
        if not changed:
            self._refresh_undo_button()
            self.status_label.setText("直前のID調整を取り消しました。")
            return
        try:
            update_student_identities(self.app.active_test_id, changed)
        except Exception as e:
            self._apply_snapshot(current)
            self._undo_stack.append(before)
            h.error(self, "取り消しエラー", str(e))
            return
        self._refresh_undo_button()
        self.status_label.setText("直前のID調整を取り消しました。")
