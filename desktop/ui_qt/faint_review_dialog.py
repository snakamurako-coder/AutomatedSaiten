"""薄い字の目視確認・強調補正・OCR ダイアログ。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from config import (
    delete_enhance_preset,
    list_enhance_presets,
    save_enhance_preset,
    test_warped,
)
from models.test_repo import normalize_file_name
from services.faint_ink import enhance_bgr
from services.image_loader import imread_bgr, imwrite_bgr
from services.image_warp import warped_file_name
from ui_qt.crop_widgets import SliderSpinControls, ZoomControls
from ui_qt.helpers import bgr_to_qpixmap
from ui_qt.style import COLORS


class _PreviewCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = None
        self._fields: list[dict[str, Any]] = []
        self._highlight_id = ""
        self._scale = 1.0
        self._zoom_pct = 100
        self.setMinimumSize(400, 280)

    def set_image(self, bgr: np.ndarray, fields: list[dict[str, Any]], highlight_id: str = "") -> None:
        self._pixmap = bgr_to_qpixmap(bgr)
        self._fields = list(fields or [])
        self._highlight_id = str(highlight_id or "")
        self._recompute_size()
        self.update()

    def set_zoom_pct(self, pct: int) -> None:
        self._zoom_pct = max(25, min(400, int(pct)))
        self._recompute_size()
        self.update()

    def _recompute_size(self) -> None:
        if self._pixmap is None:
            return
        self._scale = self._zoom_pct / 100.0
        w = max(1, int(self._pixmap.width() * self._scale))
        h = max(1, int(self._pixmap.height() * self._scale))
        self.setFixedSize(w, h)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS["sidebar"]))
        if self._pixmap is None:
            painter.setPen(QColor(COLORS["text_secondary"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "画像なし")
            return
        target = self.rect()
        painter.drawPixmap(target, self._pixmap)
        for f in self._fields:
            x = int(float(f.get("x") or 0) * self._scale)
            y = int(float(f.get("y") or 0) * self._scale)
            w = int(float(f.get("width") or 0) * self._scale)
            h = int(float(f.get("height") or 0) * self._scale)
            fid = str(f.get("id") or "")
            hot = fid == self._highlight_id
            pen = QPen(QColor("#f59e0b" if hot else COLORS["accent"]))
            pen.setWidth(3 if hot else 2)
            painter.setPen(pen)
            painter.drawRect(x, y, max(1, w), max(1, h))


class FaintReviewDialog(QDialog):
    """要確認（薄い）答案の目視・強調・OCR。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        test_id: str,
        queue: list[dict[str, Any]],
        fields: list[dict[str, Any]],
        on_ocr: Callable[[list[dict[str, Any]]], None] | None = None,
        selected_file_names: set[str] | frozenset[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("薄い字の目視・強調")
        self.resize(960, 720)
        self._test_id = test_id
        self._queue = list(queue)
        self._fields = list(fields)
        self._on_ocr = on_ocr
        self._selected_names = {
            normalize_file_name(n) for n in (selected_file_names or set()) if n
        }
        self._index = 0
        self._src_bgr: np.ndarray | None = None
        self._preview_bgr: np.ndarray | None = None
        self._pending_ocr: list[dict[str, Any]] = []
        self._highlight_id = ""
        self._did_flush_ocr = False
        self._did_bulk_save = False
        self._presets: list[dict[str, Any]] = []
        self.finished.connect(self._flush_pending_ocr)

        root = QVBoxLayout(self)
        self._title = QLabel("")
        self._title.setStyleSheet(f"font-weight: 600; color: {COLORS['text']};")
        root.addWidget(self._title)
        self._reason = QLabel("")
        self._reason.setWordWrap(True)
        self._reason.setStyleSheet(f"color: {COLORS['text_secondary']};")
        root.addWidget(self._reason)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignCenter)
        self._canvas = _PreviewCanvas()
        scroll.setWidget(self._canvas)
        root.addWidget(scroll, 1)

        self._zoom = ZoomControls(min_pct=25, max_pct=400, value=100)
        self._zoom.connect_zoom_changed(
            lambda: self._canvas.set_zoom_pct(self._zoom.zoom_value())
        )
        root.addWidget(self._zoom)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("プリセット"))
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(160)
        preset_row.addWidget(self._preset_combo, 1)
        apply_btn = QPushButton("適用")
        apply_btn.clicked.connect(self._apply_selected_preset)
        preset_row.addWidget(apply_btn)
        save_btn = QPushButton("現在値を登録…")
        save_btn.setToolTip("現在のスライダー値を新しいプリセットとして保存")
        save_btn.clicked.connect(self._save_current_as_preset)
        preset_row.addWidget(save_btn)
        del_btn = QPushButton("削除")
        del_btn.setToolTip("選択中のユーザープリセットを削除（内蔵は削除不可）")
        del_btn.clicked.connect(self._delete_selected_preset)
        preset_row.addWidget(del_btn)
        root.addLayout(preset_row)

        controls = QVBoxLayout()
        controls.setSpacing(4)
        self._bg_whiten = SliderSpinControls(
            label="地色除去",
            min_val=0,
            max_val=100,
            value=0,
            label_width=72,
            spin_width=64,
        )
        self._bg_whiten.setToolTip("用紙の地色を白に寄せる強度（0=なし）")
        self._bg_whiten.valueChanged.connect(lambda _v: self._refresh_preview())
        controls.addWidget(self._bg_whiten)
        self._contrast = SliderSpinControls(
            label="コントラスト",
            min_val=100,
            max_val=220,
            value=100,
            label_width=72,
            spin_width=64,
        )
        self._contrast.valueChanged.connect(lambda _v: self._refresh_preview())
        controls.addWidget(self._contrast)
        self._clahe = SliderSpinControls(
            label="CLAHE",
            min_val=0,
            max_val=80,
            value=0,
            label_width=72,
            spin_width=64,
        )
        self._clahe.valueChanged.connect(lambda _v: self._refresh_preview())
        controls.addWidget(self._clahe)
        root.addLayout(controls)

        btns = QHBoxLayout()
        self._prev_btn = QPushButton("← 前へ")
        self._prev_btn.clicked.connect(self._go_prev)
        btns.addWidget(self._prev_btn)
        self._next_btn = QPushButton("次へ →")
        self._next_btn.clicked.connect(self._go_next)
        btns.addWidget(self._next_btn)
        btns.addStretch()
        apply_all_btn = QPushButton("この補正を選択中の全てに反映")
        apply_all_btn.setToolTip(
            "⑤一覧でチェックしたファイル（未チェック時はこの一覧の全件）に、"
            "現在のスライダー設定で強調画像を保存します。"
        )
        apply_all_btn.clicked.connect(self._apply_correction_to_all_selected)
        btns.addWidget(apply_all_btn)
        save_only = QPushButton("強調を保存して次へ")
        save_only.setToolTip("強調画像だけ保存し、OCR は行わない")
        save_only.clicked.connect(self._save_and_advance)
        btns.addWidget(save_only)
        ocr_plain = QPushButton("このまま OCR")
        ocr_plain.setToolTip("強調せず現在の補正画像で OCR（比較画面へ）")
        ocr_plain.clicked.connect(lambda: self._run_ocr(enhance=False))
        btns.addWidget(ocr_plain)
        save_ocr = QPushButton("強調を保存して OCR")
        save_ocr.setObjectName("PrimaryBtn")
        save_ocr.clicked.connect(lambda: self._run_ocr(enhance=True))
        btns.addWidget(save_ocr)
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        root.addLayout(btns)

        self._reload_presets(select_name="生画像")
        self._apply_selected_preset()
        self._load_current()

    def _reload_presets(self, select_name: str | None = None) -> None:
        current = select_name or self._preset_combo.currentText()
        self._presets = list_enhance_presets()
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for p in self._presets:
            label = p["name"]
            if p.get("builtin"):
                label = f"{label}（内蔵）"
            self._preset_combo.addItem(label, p["name"])
        self._preset_combo.blockSignals(False)
        if current:
            for i in range(self._preset_combo.count()):
                if self._preset_combo.itemData(i) == current:
                    self._preset_combo.setCurrentIndex(i)
                    break

    def _find_preset(self, name: str) -> dict[str, Any] | None:
        for p in self._presets:
            if p.get("name") == name:
                return p
        return None

    def _set_sliders(self, *, contrast: int, clahe: int, bg_whiten: int) -> None:
        for w in (self._contrast, self._clahe, self._bg_whiten):
            w.blockSignals(True)
        self._contrast.set_value(int(contrast))
        self._clahe.set_value(int(clahe))
        self._bg_whiten.set_value(int(bg_whiten))
        for w in (self._contrast, self._clahe, self._bg_whiten):
            w.blockSignals(False)
        self._refresh_preview()

    def _apply_selected_preset(self) -> None:
        name = self._preset_combo.currentData()
        if not name:
            return
        p = self._find_preset(str(name))
        if not p:
            return
        self._set_sliders(
            contrast=int(p["contrast"]),
            clahe=int(p["clahe"]),
            bg_whiten=int(p["bg_whiten"]),
        )

    def _save_current_as_preset(self) -> None:
        suggested = str(self._preset_combo.currentData() or "") or "マイプリセット"
        if self._find_preset(suggested) and self._find_preset(suggested).get("builtin"):
            suggested = "マイプリセット"
        name, ok = QInputDialog.getText(
            self,
            "プリセット登録",
            "プリセット名:",
            text=suggested,
        )
        if not ok:
            return
        name = str(name or "").strip()
        if not name:
            QMessageBox.warning(self, "登録不可", "名前を入力してください。")
            return
        try:
            self._presets = save_enhance_preset(
                name,
                contrast=self._contrast.value(),
                clahe=self._clahe.value(),
                bg_whiten=self._bg_whiten.value(),
            )
        except ValueError as e:
            QMessageBox.warning(self, "登録不可", str(e))
            return
        self._reload_presets(select_name=name)
        QMessageBox.information(self, "登録完了", f"「{name}」を保存しました。")

    def _delete_selected_preset(self) -> None:
        name = self._preset_combo.currentData()
        if not name:
            return
        p = self._find_preset(str(name))
        if not p:
            return
        if p.get("builtin"):
            QMessageBox.information(self, "削除不可", "内蔵プリセットは削除できません。")
            return
        ans = QMessageBox.question(
            self,
            "プリセット削除",
            f"「{name}」を削除しますか？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        try:
            self._presets = delete_enhance_preset(str(name))
        except ValueError as e:
            QMessageBox.warning(self, "削除不可", str(e))
            return
        self._reload_presets(select_name="生画像")

    def _current_enhance_params(self) -> tuple[float, float, float]:
        return (
            self._contrast.value() / 100.0,
            self._clahe.value() / 10.0,
            self._bg_whiten.value() / 100.0,
        )

    def _enhance_image(self, src_bgr: np.ndarray) -> np.ndarray:
        contrast, clahe, bg = self._current_enhance_params()
        return enhance_bgr(
            src_bgr,
            contrast=contrast,
            brightness=0.0,
            clahe_clip=clahe,
            bg_whiten=bg,
        )

    def _entry_warped_path(self, entry: dict[str, Any]) -> Path:
        name = str(entry.get("fileName") or "")
        warped_path = Path(
            str(
                entry.get("warpedPath")
                or (entry.get("faint") or {}).get("warpedPath")
                or ""
            )
        )
        if not warped_path.name:
            warped_path = test_warped(self._test_id) / warped_file_name(name)
        return warped_path

    def _save_bgr_to_entry(self, entry: dict[str, Any], bgr: np.ndarray) -> str:
        warped_path = self._entry_warped_path(entry)
        warped_path.parent.mkdir(parents=True, exist_ok=True)
        if warped_path.exists():
            backup = warped_path.with_name(warped_path.stem + "_原" + warped_path.suffix)
            if not backup.exists():
                shutil.copy2(str(warped_path), str(backup))
        imwrite_bgr(warped_path, bgr, quality=90)
        resolved = str(warped_path.resolve())
        entry["warpedPath"] = resolved
        return resolved

    def _targets_for_bulk_apply(self) -> list[dict[str, Any]]:
        if self._selected_names:
            return [
                e
                for e in self._queue
                if normalize_file_name(str(e.get("fileName") or "")) in self._selected_names
            ]
        return list(self._queue)

    def _apply_correction_to_all_selected(self) -> None:
        targets = self._targets_for_bulk_apply()
        if not targets:
            QMessageBox.warning(
                self,
                "反映なし",
                "⑤一覧でチェックしたファイルが、この目視一覧にありません。",
            )
            return
        label = (
            f"チェック {len(targets)} 件"
            if self._selected_names
            else f"一覧 {len(targets)} 件"
        )
        ans = QMessageBox.question(
            self,
            "一括反映",
            f"現在の補正設定を {label} すべての補正画像に保存します。\n"
            "よろしいですか？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        saved = 0
        skipped: list[str] = []
        for entry in targets:
            path = self._entry_warped_path(entry)
            if not path.exists():
                src_path = str(
                    entry.get("warpedPath")
                    or (entry.get("faint") or {}).get("warpedPath")
                    or ""
                )
                if not src_path or not Path(src_path).exists():
                    skipped.append(str(entry.get("fileName") or ""))
                    continue
                path = Path(src_path)
            loaded = imread_bgr(path)
            if loaded is None:
                skipped.append(str(entry.get("fileName") or ""))
                continue
            enhanced = self._enhance_image(loaded)
            self._save_bgr_to_entry(entry, enhanced)
            saved += 1
        if self._current() is not None and self._src_bgr is not None:
            self._refresh_preview()
        msg = f"{saved} 件に補正を反映しました。"
        if skipped:
            msg += f"\n\n読込できなかった {len(skipped)} 件:\n" + "\n".join(skipped[:8])
            if len(skipped) > 8:
                msg += f"\n…他 {len(skipped) - 8} 件"
        QMessageBox.information(self, "一括反映", msg)
        if saved:
            self._did_bulk_save = True

    def did_bulk_save(self) -> bool:
        return self._did_bulk_save

    def _current(self) -> dict[str, Any] | None:
        if not self._queue or self._index < 0 or self._index >= len(self._queue):
            return None
        return self._queue[self._index]

    def _go_prev(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._load_current()

    def _go_next(self) -> None:
        if self._index < len(self._queue) - 1:
            self._index += 1
            self._load_current()

    def _load_current(self) -> None:
        entry = self._current()
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < len(self._queue) - 1)
        if entry is None:
            self._title.setText("対象なし")
            self._reason.setText("")
            self._src_bgr = None
            self._preview_bgr = None
            return
        name = str(entry.get("fileName") or "")
        reason = str(entry.get("reason") or (entry.get("faint") or {}).get("reason") or "")
        metrics = entry.get("metrics") or (entry.get("faint") or {}).get("metrics") or {}
        self._title.setText(f"{self._index + 1}/{len(self._queue)}  {name}")
        lines = [reason] if reason else []
        if metrics:
            lines.append(
                f"計測: σ={metrics.get('sigma', '—')} / "
                f"P95−P5={metrics.get('p95_p5', '—')} / "
                f"Δ={metrics.get('bg_delta', '—')}"
            )
        self._reason.setText("\n".join(lines) if lines else "計測理由なし")
        path = str(
            entry.get("warpedPath")
            or (entry.get("faint") or {}).get("warpedPath")
            or ""
        )
        if not path or not Path(path).exists():
            self._src_bgr = None
            self._preview_bgr = None
            self._canvas.set_image(
                np.zeros((120, 200, 3), dtype=np.uint8),
                self._fields,
            )
            base = self._reason.text()
            self._reason.setText(
                (base + "\n" if base else "") + "補正画像が見つかりません。"
            )
            return
        loaded = imread_bgr(path)
        if loaded is None:
            self._src_bgr = None
            self._preview_bgr = None
            base = self._reason.text()
            self._reason.setText(
                (base + "\n" if base else "") + "補正画像を読み込めませんでした。"
            )
            return
        self._src_bgr = loaded
        highlight = str(
            entry.get("fieldId")
            or (entry.get("faint") or {}).get("fieldId")
            or ""
        )
        self._highlight_id = highlight
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if self._src_bgr is None:
            return
        self._preview_bgr = self._enhance_image(self._src_bgr)
        self._canvas.set_image(
            self._preview_bgr,
            self._fields,
            getattr(self, "_highlight_id", ""),
        )

    def _save_enhanced(self, entry: dict[str, Any]) -> str:
        assert self._preview_bgr is not None
        return self._save_bgr_to_entry(entry, self._preview_bgr)

    def did_flush_ocr(self) -> bool:
        return self._did_flush_ocr

    def _save_and_advance(self) -> None:
        entry = self._current()
        if entry is None or self._preview_bgr is None:
            return
        warped = self._save_enhanced(entry)
        entry["warpedPath"] = warped
        self._advance_after_queue()

    def _flush_pending_ocr(self, _result: int = 0) -> None:
        if not self._pending_ocr or not self._on_ocr:
            return
        payload = list(self._pending_ocr)
        self._pending_ocr = []
        self._did_flush_ocr = True
        self._on_ocr(payload)

    def _advance_after_queue(self) -> None:
        if self._index < len(self._queue) - 1:
            self._index += 1
            self._load_current()
        else:
            self.accept()

    def _run_ocr(self, *, enhance: bool) -> None:
        entry = self._current()
        if entry is None:
            return
        if enhance:
            if self._preview_bgr is None:
                return
            warped = self._save_enhanced(entry)
            entry["warpedPath"] = warped
        else:
            warped = str(
                entry.get("warpedPath")
                or (entry.get("faint") or {}).get("warpedPath")
                or ""
            )
            if not warped or not Path(warped).exists():
                return
        self._pending_ocr.append(
            {
                "fileName": str(entry.get("fileName") or ""),
                "sourcePath": str(entry.get("sourcePath") or entry.get("path") or ""),
                "warpedPath": warped,
            }
        )
        self._advance_after_queue()
