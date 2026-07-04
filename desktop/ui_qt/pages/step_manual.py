"""手動採点ページ（② から分岐・③④⑤ の代替）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from models.database import connect
from models.test_repo import (
    get_all_results,
    get_answer_fields,
    get_points_conn,
    update_results_field_grades,
)
from services.crop_preview import load_crops_for_rows
from ui_qt import helpers as h
from ui_qt.crop_widgets import CropDisplayControls
from ui_qt.helpers import pil_to_qpixmap
from ui_qt.layout_helpers import make_expanding
from ui_qt.style import COLORS, set_variant


class StepManualPage(QWidget):
    """記述欄画像を並べ、複数選択して ○△× を一括反映する手動採点。"""

    _MAIN_FILTERS = ("○", "△", "×", "未採点", "採点済み")

    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._fields: list[dict[str, Any]] = []
        self._items: list[dict[str, Any]] = []
        self._selected_ids: set[int] = set()
        self._filter_btns: dict[str, QPushButton] = {}
        self._tri_filter_btns: dict[str, QPushButton] = {}
        self._tri_filter_key = "all"
        self._sort_mode = "file"

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- 上部作業エリア（フィルタ・画像）---
        work = QWidget()
        work_lay = QVBoxLayout(work)
        work_lay.setContentsMargins(0, 0, 0, 8)
        work_lay.setSpacing(6)

        work_lay.addWidget(h.title_label("手動採点"))
        work_lay.addWidget(
            h.muted_label(
                "② 配点決定のあと、記述欄ごとに解答画像を見ながら ○△× を付けます。"
                "（③ テキスト化・④ 採点基準・⑤ 一括採点の代替フロー）"
            )
        )

        header = QHBoxLayout()
        header.setSpacing(8)
        left_hdr = QVBoxLayout()
        left_hdr.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(QLabel("採点する記述欄"))
        self.field_combo = QComboBox()
        self.field_combo.setMinimumWidth(240)
        self.field_combo.currentIndexChanged.connect(self._on_field_changed)
        top.addWidget(self.field_combo)
        top.addSpacing(8)
        top.addWidget(QLabel("並べ替え"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("ファイル名", "file")
        self.sort_combo.addItem("ID", "id")
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        top.addWidget(self.sort_combo)
        top.addStretch()
        left_hdr.addLayout(top)
        self.selection_label = h.caption_label("0 件を選択中")
        left_hdr.addWidget(self.selection_label)
        header.addLayout(left_hdr, 1)
        header.addWidget(self._build_filter_box(), 0)
        work_lay.addLayout(header)

        self.crop_scroll = QScrollArea()
        self.crop_scroll.setWidgetResizable(True)
        self.crop_scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {COLORS['border']}; border-radius: 6px;"
            f" background: {COLORS['surface']}; }}"
        )
        make_expanding(self.crop_scroll)
        self.crop_panel = QWidget()
        self.crop_grid = QGridLayout(self.crop_panel)
        self.crop_grid.setContentsMargins(8, 8, 8, 8)
        self.crop_grid.setSpacing(8)
        self.crop_scroll.setWidget(self.crop_panel)
        work_lay.addWidget(self.crop_scroll, 1)

        self.status_label = h.caption_label("")
        work_lay.addWidget(self.status_label)
        root.addWidget(work, 1)

        # --- 最下部固定オーバーレイ ---
        root.addWidget(self._build_footer_overlay())

    def _build_filter_box(self) -> QGroupBox:
        box = QGroupBox("表示フィルタ")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        row1 = QHBoxLayout()
        row1.addWidget(h.caption_label("判定:"))
        for key in self._MAIN_FILTERS:
            btn = QPushButton(key)
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.toggled.connect(lambda _c, k=key: self._on_filter_toggled())
            self._filter_btns[key] = btn
            row1.addWidget(btn)
        row1.addStretch()
        lay.addLayout(row1)

        self.tri_filter_row = QHBoxLayout()
        self.tri_filter_row.addWidget(h.caption_label("△の部分点:"))
        lay.addLayout(self.tri_filter_row)
        return box

    def _build_footer_overlay(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("ManualGradeFooter")
        footer.setStyleSheet(
            f"#ManualGradeFooter {{ background: {COLORS['surface']};"
            f" border-top: 2px solid {COLORS['border_strong']}; }}"
        )
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(12)

        self.crop_controls = CropDisplayControls()
        self.crop_controls.connect_zoom_changed(self._render_grid)
        self.crop_controls.connect_meta_changed(self._render_grid)
        lay.addWidget(self.crop_controls, 2)

        judge = QGroupBox("選択への判定反映")
        judge_lay = QHBoxLayout(judge)
        judge_lay.setContentsMargins(8, 6, 8, 6)
        judge_lay.addWidget(h.caption_label("画像をタップで複数選択 →"))
        self.btn_maru = QPushButton("○")
        self.btn_sankaku = QPushButton("△")
        self.btn_batsu = QPushButton("×")
        for btn, handler in (
            (self.btn_maru, lambda: self._apply_judgment("○")),
            (self.btn_sankaku, lambda: self._apply_judgment("△")),
            (self.btn_batsu, lambda: self._apply_judgment("×")),
        ):
            btn.setFixedSize(44, 36)
            btn.setCursor(Qt.PointingHandCursor)
            set_variant(btn, "primary")
            btn.clicked.connect(handler)
            judge_lay.addWidget(btn)
        judge_lay.addWidget(h.button("選択を解除", self._clear_selection))
        lay.addWidget(judge, 1)
        return footer

    # --- データ ---

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        self._fields = get_answer_fields(self.app.active_test_id)
        current = self.field_combo.currentIndex()
        self.field_combo.blockSignals(True)
        self.field_combo.clear()
        self.field_combo.addItems([f"{f['displayName']} ({f['id']})" for f in self._fields])
        if self._fields:
            self.field_combo.setCurrentIndex(current if 0 <= current < len(self._fields) else 0)
        self.field_combo.blockSignals(False)
        self._rebuild_triangle_filters()
        self._update_judge_buttons()
        if self._fields:
            self._load_crops_async()
        else:
            self._items = []
            self._selected_ids.clear()
            self._render_grid()

    def _selected_field_id(self) -> str | None:
        idx = self.field_combo.currentIndex()
        if idx < 0 or idx >= len(self._fields):
            return None
        return self._fields[idx]["id"]

    def _field_max_score(self) -> int:
        fid = self._selected_field_id()
        test_id = self.app.active_test_id
        if not fid or not test_id:
            return 1
        with connect() as conn:
            pts = get_points_conn(conn, test_id)
        return max(1, int(pts.get(fid, 1)))

    def _on_field_changed(self, _index: int) -> None:
        self._selected_ids.clear()
        self._rebuild_triangle_filters()
        self._update_judge_buttons()
        self._load_crops_async()

    def _on_sort_changed(self, _index: int) -> None:
        self._sort_mode = self.sort_combo.currentData() or "file"
        self._sort_items()
        self._render_grid()

    def _on_filter_toggled(self) -> None:
        self._render_grid()

    def _rebuild_triangle_filters(self) -> None:
        while self.tri_filter_row.count() > 1:
            item = self.tri_filter_row.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._tri_filter_btns.clear()
        self._tri_filter_key = "all"
        max_score = self._field_max_score()
        if max_score <= 1:
            return
        specs = [("all", "△すべて")]
        if max_score == 2:
            specs.append(("1", "△(1)"))
        else:
            for s in range(1, max_score):
                specs.append((str(s), f"△({s})"))
        for key, label in specs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.setCursor(Qt.PointingHandCursor)
            btn.toggled.connect(lambda checked, k=key: self._on_tri_filter(k, checked))
            self._tri_filter_btns[key] = btn
            self.tri_filter_row.addWidget(btn)
        self.tri_filter_row.addStretch()

    def _on_tri_filter(self, key: str, checked: bool) -> None:
        if not checked:
            if self._tri_filter_key == key:
                all_btn = self._tri_filter_btns.get("all")
                if all_btn:
                    all_btn.blockSignals(True)
                    all_btn.setChecked(True)
                    all_btn.blockSignals(False)
                    self._tri_filter_key = "all"
            return
        self._tri_filter_key = key
        for k, btn in self._tri_filter_btns.items():
            if k != key:
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        self._render_grid()

    def _update_judge_buttons(self) -> None:
        max_score = self._field_max_score()
        self.btn_sankaku.setVisible(max_score > 1)
        tri_btn = self._filter_btns.get("△")
        if tri_btn is not None:
            tri_btn.setVisible(max_score > 1)
        self.btn_sankaku.setToolTip(
            "1点（配点2点時）" if max_score == 2 else "部分点を指定して一括反映"
        )

    def _load_crops_async(self) -> None:
        fid = self._selected_field_id()
        test_id = self.app.active_test_id
        if not fid or not test_id:
            return
        field = next((f for f in self._fields if f["id"] == fid), None)
        if not field:
            return
        results = get_all_results(test_id)
        if not results:
            self._items = []
            self._render_grid()
            self.status_label.setText("採点結果がありません。③ テキスト化で画像を登録してください。")
            return
        rows = [
            {
                "rowIndex": r["id"],
                "studentId": r.get("studentId") or "",
                "fileName": r.get("fileName") or "",
                "fileId": r.get("sourcePath") or "",
                "warpedPath": r.get("warpedPath") or "",
                "answer_text": str(r.get("textMapping", {}).get(fid, "") or "").strip() or "なし",
                "judgment": str(r.get("judgments", {}).get(fid, "") or "").strip(),
                "score": r.get("scores", {}).get(fid),
            }
            for r in results
        ]
        self._clear_grid()
        self.crop_grid.addWidget(h.muted_label(f"画像を読み込み中…（{len(rows)}枚）"), 0, 0)
        self.status_label.setText(f"{len(rows)} 件を読み込み中…")

        def done(crop_results, err):
            if err:
                h.error(self, "画像読込エラー", str(err))
                return
            self._items = []
            for cr, src in zip(crop_results, rows, strict=False):
                self._items.append(
                    {
                        **cr,
                        "result_id": src["rowIndex"],
                        "judgment": src["judgment"],
                        "score": src["score"],
                    }
                )
            self._sort_items()
            self._render_grid()
            ok = sum(1 for i in self._items if i.get("ok"))
            self.status_label.setText(f"{ok}/{len(self._items)} 枚を表示 — タップで選択し ○△× を反映")

        h.run_in_thread(self, lambda: load_crops_for_rows(rows, field), done)

    def _sort_items(self) -> None:
        if self._sort_mode == "id":
            self._items.sort(
                key=lambda i: (
                    str((i.get("row") or {}).get("studentId") or "").strip().lower(),
                    str((i.get("row") or {}).get("fileName") or "").lower(),
                )
            )
        else:
            self._items.sort(
                key=lambda i: str((i.get("row") or {}).get("fileName") or "").lower()
            )

    def _item_passes_filter(self, item: dict[str, Any]) -> bool:
        j = str(item.get("judgment") or "").strip()
        sc = item.get("score")
        has_j = j in ("○", "△", "×")
        tags: list[str] = []
        if has_j:
            tags.append("採点済み")
            tags.append(j)
        else:
            tags.append("未採点")
        active = [k for k, btn in self._filter_btns.items() if btn.isChecked()]
        if not active:
            return True
        if not any(t in active for t in tags):
            return False
        if j == "△" and self._tri_filter_key != "all" and self._field_max_score() > 1:
            try:
                return int(sc) == int(self._tri_filter_key)
            except (TypeError, ValueError):
                return False
        return True

    def _clear_selection(self) -> None:
        self._selected_ids.clear()
        self._render_grid()

    def _apply_judgment(self, judgment: str) -> None:
        if not self.app.require_active_test():
            return
        fid = self._selected_field_id()
        if not fid:
            h.warn(self, "記述欄未選択", "記述欄を選んでください。")
            return
        if not self._selected_ids:
            h.warn(self, "未選択", "画像をタップして選択してください。")
            return
        max_score = self._field_max_score()
        if judgment == "○":
            score = max_score
        elif judgment == "×":
            score = 0
        elif judgment == "△":
            if max_score <= 1:
                return
            if max_score == 2:
                score = 1
            else:
                score, ok = QInputDialog.getInt(
                    self,
                    "部分点",
                    f"選択 {len(self._selected_ids)} 件の得点（1〜{max_score - 1}）",
                    1,
                    1,
                    max_score - 1,
                )
                if not ok:
                    return
        else:
            return
        try:
            n = update_results_field_grades(
                self.app.active_test_id,
                fid,
                list(self._selected_ids),
                judgment,
                score,
            )
        except Exception as e:
            h.error(self, "保存エラー", str(e))
            return
        id_set = set(self._selected_ids)
        for item in self._items:
            if item.get("result_id") in id_set:
                item["judgment"] = judgment
                item["score"] = score
        self._selected_ids.clear()
        self._render_grid()
        h.info(self, "反映完了", f"{n} 件に {judgment}（{score}点）を反映しました。")

    # --- グリッド ---

    def _clear_grid(self) -> None:
        while self.crop_grid.count():
            item = self.crop_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_grid(self) -> None:
        self._clear_grid()
        visible = [i for i in self._items if self._item_passes_filter(i)]
        self.selection_label.setText(f"{len(self._selected_ids)} 件を選択中（表示 {len(visible)} 枚）")
        if not visible:
            self.crop_grid.addWidget(
                h.muted_label("表示する画像がありません。フィルタまたは記述欄を確認してください。"),
                0,
                0,
            )
            return
        zoom = max(30, min(400, self.crop_controls.zoom_value())) / 100.0
        cols = 4
        col_idx = 0
        row_idx = 0
        for item in visible:
            tile = self._make_tile(item, zoom)
            self.crop_grid.addWidget(tile, row_idx, col_idx, Qt.AlignTop | Qt.AlignLeft)
            col_idx += 1
            if col_idx >= cols:
                col_idx = 0
                row_idx += 1
        self.crop_grid.setColumnStretch(cols, 1)

    def _make_tile(self, item: dict[str, Any], zoom: float) -> QWidget:
        rid = int(item.get("result_id") or 0)
        selected = rid in self._selected_ids
        j = str(item.get("judgment") or "").strip()
        sc = item.get("score")
        tile = QFrame()
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(2)

        if not item.get("ok"):
            tile.setStyleSheet(
                f"QFrame {{ background: {COLORS['danger_soft']}; border: 2px solid #fca5a5;"
                f" border-radius: 6px; }}"
            )
            err = QLabel(str(item.get("error") or "読込失敗"))
            err.setWordWrap(True)
            lay.addWidget(err)
            return tile

        if selected:
            border = COLORS["accent"]
            bg = COLORS["accent_soft"]
        else:
            border = COLORS["border"]
            bg = COLORS["surface"]
        tile.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 2px solid {border}; border-radius: 6px; }}"
        )
        tile.setCursor(Qt.PointingHandCursor)

        row = item["row"]
        pil = item["pil"]
        w = max(40, int(pil.width * zoom))
        pix: QPixmap = pil_to_qpixmap(pil).scaledToWidth(w, Qt.SmoothTransformation)
        img = QLabel()
        img.setPixmap(pix)
        img.setStyleSheet("border: none;")
        lay.addWidget(img)

        if j:
            try:
                sc_txt = f" {int(sc)}点" if sc is not None and sc != "" else ""
            except (TypeError, ValueError):
                sc_txt = ""
            badge = QLabel(f"{j}{sc_txt}")
            badge.setStyleSheet(
                f"border: none; font-size: 11px; font-weight: 700; color: {COLORS['accent']};"
            )
            lay.addWidget(badge)

        if self.crop_controls.show_id():
            lay.addWidget(QLabel(f"ID: {row.get('studentId') or '-'}"))
        if self.crop_controls.show_file_name():
            fn = QLabel(str(row.get("fileName") or ""))
            fn.setWordWrap(True)
            fn.setStyleSheet(f"font-size: 9px; color: {COLORS['text_secondary']}; border: none;")
            lay.addWidget(fn)
        if self.crop_controls.show_ocr_text():
            ans = QLabel(str(row.get("answer_text") or ""))
            ans.setWordWrap(True)
            ans.setStyleSheet(
                f"font-size: 10px; color: {COLORS['text_secondary']}; border: none;"
            )
            lay.addWidget(ans)

        def click_handler(_event, result_id=rid):
            if result_id in self._selected_ids:
                self._selected_ids.discard(result_id)
            else:
                self._selected_ids.add(result_id)
            self._render_grid()

        tile.mousePressEvent = click_handler  # type: ignore[method-assign]
        return tile
