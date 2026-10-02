"""⑬ ID・氏名の照合ページ（本人欄画像と結果値の目視照合・手修正）。"""

from __future__ import annotations

import unicodedata
from typing import Any

from PySide6.QtCore import QByteArray, QMimeData, Qt, QTimer, Signal
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
from models.roster_repo import (
    _norm_person_name,
    get_roster_rows,
    get_selected_roster_name,
    update_student_identities,
    update_student_identity,
)
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

    def _line_edit_at(self, pos) -> bool:
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child, QLineEdit):
                return True
            nxt = child.childAt(child.mapFrom(self, pos))
            if nxt is None or nxt is child:
                return False
            child = nxt
        return False

    def set_drop_hint(self, hint: str) -> None:
        if hint == self._drop_hint:
            return
        self._drop_hint = hint
        self._apply_frame_style(hint)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._line_edit_at(event.position().toPoint()):
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
        self._edits: dict[int, tuple[QLineEdit, QLineEdit]] = {}
        self._roster_ids_by_name: dict[str, list[str]] = {}
        self._persisted: dict[int, tuple[str, str]] = {}
        self._undo_stack: list[_Snapshot] = []
        self._in_render = False
        self._last_viewport_w = 0

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("⑬ ID・氏名の照合"))
        root.addWidget(
            h.muted_label(
                "切り出し画像は用紙に残したまま、生徒IDと氏名を調整します。"
                "元画像と補正画像は同じ答案なので、IDと氏名も共通です。"
                "タイルの中央へドロップすると入れ替え、上下の端へドロップするとその位置へ挿入して間を1つずつずらします。"
                "入れ替えはすぐに答案へ保存されます。欄の文字を直したときは「修正を保存」で答案に書き込みます。"
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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        make_expanding(scroll)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['sidebar']}; }}"
        )
        self.scroll = scroll
        self.grid_panel = QWidget()
        self.grid_panel.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(self.grid_panel)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.grid.setSpacing(8)
        scroll.setWidget(self.grid_panel)
        root.addWidget(scroll, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._crop_results or self._in_render:
            return
        QTimer.singleShot(0, self._render_grid_if_width_changed)

    def _render_grid_if_width_changed(self) -> None:
        width = self.scroll.viewport().width()
        if abs(width - self._last_viewport_w) < 8:
            return
        self._render_grid()

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
            self._capture_persisted()
            self._undo_stack.clear()
            self._refresh_undo_button()
            self._load_roster_ids()
            ok = sum(1 for r in results if r.get("ok"))
            resolved, missing = self._roster_id_counts()
            note = ""
            if resolved:
                note += f" 名簿からIDを{resolved}件読み出しました。"
            if missing:
                note += f" IDを特定できない氏名が{missing}件あります。"
            self.status_label.setText(
                f"{ok}/{len(results)} 件を表示中（{basis_label}） — 値を修正したら「修正を保存」"
                f"{note}"
            )
            self._render_grid()

        h.run_in_thread(self, task, done)

    def _image_box_width(self, cols: int, zoom: float) -> int:
        """100% のとき、4列が枠内に収まり切り出し全体が見える幅。"""
        viewport_w = self.scroll.viewport().width()
        if viewport_w < 80:
            viewport_w = max(320, self.width() - 24)
        margins = self.grid.contentsMargins()
        spacing = self.grid.spacing()
        inner = viewport_w - margins.left() - margins.right() - spacing * (cols - 1) - 4
        col = max(88, inner // cols)
        tile_pad = 12
        return max(64, int((col - tile_pad) * zoom))

    def _render_grid(self) -> None:
        if self._in_render:
            return
        self._in_render = True
        try:
            self._sync_edits_to_rows()
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._edits = {}
            if not self._crop_results:
                self.grid_panel.setMinimumWidth(0)
                self.grid.addWidget(
                    h.muted_label("「本人欄画像を表示」で切り出し画像を読み込みます"), 0, 0
                )
                return

            zoom = max(30, min(400, self.zoom_slider.value())) / 100.0
            cols = 4
            image_w = self._image_box_width(cols, zoom)
            for idx, item in enumerate(self._crop_results):
                r, c = divmod(idx, cols)
                self.grid.addWidget(
                    self._make_tile(item, image_w),
                    r,
                    c,
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                )
            self.grid.setColumnStretch(cols, 1)
            if zoom > 1.01:
                margins = self.grid.contentsMargins()
                content_w = (
                    cols * (image_w + 12)
                    + self.grid.spacing() * (cols - 1)
                    + margins.left()
                    + margins.right()
                )
                self.grid_panel.setMinimumWidth(content_w)
            else:
                self.grid_panel.setMinimumWidth(0)
            self._last_viewport_w = self.scroll.viewport().width()
        finally:
            self._in_render = False

    def _make_tile(self, item: dict[str, Any], image_w: int) -> QWidget:
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
        file_name = str(row.get("fileName") or "")
        file_label = QLabel(file_name)
        file_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        file_label.setWordWrap(True)
        file_label.setFixedWidth(image_w)
        file_label.setToolTip(file_name)
        file_label.setStyleSheet(
            f"border: none; font-size: 9px; color: {COLORS['text_secondary']};"
        )
        lay.addWidget(file_label)

        pix = pil_to_qpixmap(item["pil"])
        img = QLabel()
        img.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        img.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        if not pix.isNull() and pix.width() > 0:
            img.setPixmap(pix.scaledToWidth(image_w, Qt.SmoothTransformation))
        img.setStyleSheet("border: none;")
        lay.addWidget(img)

        name_edit = QLineEdit(str(row.get("name") or ""))
        name_edit.setPlaceholderText("氏名")
        id_edit = QLineEdit(self._id_text_for_row(row))
        id_edit.setPlaceholderText("ID")
        gap = 4
        id_w = max(28, (image_w - gap) // 3)
        name_w = max(28, image_w - gap - id_w)
        name_edit.setFixedWidth(name_w)
        id_edit.setFixedWidth(id_w)
        edit_row = QWidget()
        edit_row.setFixedWidth(image_w)
        edit_lay = QHBoxLayout(edit_row)
        edit_lay.setContentsMargins(0, 0, 0, 0)
        edit_lay.setSpacing(gap)
        edit_lay.addWidget(name_edit)
        edit_lay.addWidget(id_edit)
        self._edits[int(row["id"])] = (name_edit, id_edit)
        lay.addWidget(edit_row)
        return tile

    def _capture_persisted(self) -> None:
        """答案行に保存されている ID・氏名。画面表示で上書きしない。"""
        saved: dict[int, tuple[str, str]] = {}
        for item in self._crop_results:
            row = item.get("row") or {}
            rid = row.get("id")
            if rid is None:
                continue
            saved[int(rid)] = (
                str(row.get("studentId") or ""),
                str(row.get("name") or ""),
            )
        self._persisted = saved

    def _remember_persisted(self, rows: list[tuple[int, str, str]]) -> None:
        for rid, sid, name in rows:
            self._persisted[int(rid)] = (str(sid or ""), str(name or ""))

    def _load_roster_ids(self) -> None:
        """選択中の名簿を、正規化した氏名 → ID の一覧にする。"""
        grouped: dict[str, list[str]] = {}
        roster_name = get_selected_roster_name(self.app.active_test_id).strip()
        if roster_name:
            for roster_row in get_roster_rows(roster_name):
                key = _norm_person_name(str(roster_row.get("name") or ""))
                sid = str(roster_row.get("studentId") or "").strip()
                if not key or not sid:
                    continue
                bucket = grouped.setdefault(key, [])
                if sid not in bucket:
                    bucket.append(sid)
        self._roster_ids_by_name = grouped

    def _id_text_for_row(self, row: dict[str, Any]) -> str:
        """ID欄に出す値。保存値が氏名そのものなら、名簿のIDを読む。"""
        sid = str(row.get("studentId") or "").strip()
        name = str(row.get("name") or "").strip()
        if not sid or _norm_person_name(sid) != _norm_person_name(name):
            return sid
        matches = self._roster_ids_by_name.get(_norm_person_name(name), [])
        if len(matches) == 1:
            return matches[0]
        return ""

    def _roster_id_counts(self) -> tuple[int, int]:
        resolved = 0
        missing = 0
        for item in self._crop_results:
            row = item.get("row") or {}
            stored = str(row.get("studentId") or "").strip()
            name = str(row.get("name") or "").strip()
            if not stored or _norm_person_name(stored) != _norm_person_name(name):
                continue
            if self._id_text_for_row(row):
                resolved += 1
            else:
                missing += 1
        return resolved, missing

    def _id_key(self, value: str) -> str:
        return unicodedata.normalize("NFKC", str(value or "")).strip()

    def _duplicate_id_edits(self) -> dict[str, list[QLineEdit]]:
        """空欄を除き、2件以上あるIDとその入力欄を返す。"""
        grouped: dict[str, list[QLineEdit]] = {}
        counts: dict[str, int] = {}
        for item in self._crop_results:
            row = item.get("row") or {}
            rid = row.get("id")
            if rid is None:
                continue
            edits = self._edits.get(int(rid))
            if edits is not None:
                sid = self._id_key(edits[1].text())
                edit = edits[1]
            else:
                sid = self._id_key(str(row.get("studentId") or ""))
                edit = None
            if not sid:
                continue
            counts[sid] = counts.get(sid, 0) + 1
            if edit is not None:
                grouped.setdefault(sid, []).append(edit)
        return {sid: grouped.get(sid, []) for sid, count in counts.items() if count > 1}

    def _mark_duplicate_ids(self, duplicates: dict[str, list[QLineEdit]]) -> None:
        flagged = {id(edit) for edits in duplicates.values() for edit in edits if edit is not None}
        for edits in self._edits.values():
            id_edit = edits[1]
            if id(id_edit) in flagged:
                id_edit.setStyleSheet(
                    f"background: {COLORS['danger_soft']}; color: {COLORS['danger']};"
                )
            else:
                id_edit.setStyleSheet("")

    def _on_save(self) -> None:
        if not self.app.require_active_test() or not self._crop_results:
            return
        duplicates = self._duplicate_id_edits()
        self._mark_duplicate_ids(duplicates)
        if duplicates:
            listed = "、".join(sorted(duplicates))
            h.warn(
                self,
                "IDが重複",
                f"同じIDが複数あるため保存できません。\n{listed}",
            )
            self.status_label.setText("同じIDが複数あるため保存できません。")
            return
        saved = 0
        for item in self._crop_results:
            if not item.get("ok"):
                continue
            row = item["row"]
            rid = int(row["id"])
            edits = self._edits.get(rid)
            if edits is None:
                continue
            name_edit, id_edit = edits
            student_id = id_edit.text().strip()
            name = name_edit.text().strip()
            old_sid, old_name = self._persisted.get(rid, ("", ""))
            if student_id == old_sid and name == old_name:
                continue
            update_student_identity(self.app.active_test_id, rid, student_id, name)
            row["studentId"] = student_id
            row["name"] = name
            self._persisted[rid] = (student_id, name)
            saved += 1
        self._refresh_external_match_dialog()
        h.info(self, "保存完了", f"{saved} 件の ID・氏名を更新しました。")
        self.status_label.setText(f"{saved} 件を更新しました。")

    def _refresh_external_match_dialog(self) -> None:
        pages = getattr(self.app, "pages", None) or {}
        page = pages.get(11)
        dialog = getattr(page, "_external_dialog", None) if page is not None else None
        if dialog is None or not dialog.isVisible():
            return
        if str(getattr(dialog, "test_id", "") or "") != str(self.app.active_test_id or ""):
            return
        dialog.reload()

    def _refresh_undo_button(self) -> None:
        self.undo_btn.setEnabled(bool(self._undo_stack))

    def _ok_items(self) -> list[dict[str, Any]]:
        return [item for item in self._crop_results if item.get("ok") and item.get("row")]

    def _sync_edits_to_rows(self) -> None:
        for item in self._ok_items():
            row = item["row"]
            edits = self._edits.get(int(row["id"]))
            if edits is None:
                continue
            name_edit, id_edit = edits
            row["name"] = name_edit.text().strip()
            row["studentId"] = id_edit.text().strip()

    def _show_identity(self, row: dict[str, Any]) -> None:
        edits = self._edits.get(int(row["id"]))
        if edits is None:
            return
        name_edit, id_edit = edits
        name_edit.setText(str(row.get("name") or ""))
        id_edit.setText(str(row.get("studentId") or ""))

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
        self._remember_persisted(changed)
        self._refresh_undo_button()
        self._refresh_external_match_dialog()
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
        self._remember_persisted(changed)
        self._refresh_undo_button()
        self._refresh_external_match_dialog()
        self.status_label.setText("直前のID調整を取り消しました。")
