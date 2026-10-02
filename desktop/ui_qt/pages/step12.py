"""⑫ 本人欄設定ページ（学年・組・番号・ID・氏名の矩形指定）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from models.identity_repo import (
    IDENTITY_BASIS_ORIGINAL,
    IDENTITY_BASIS_WARPED,
    IDENTITY_TYPES,
    clear_identity_fields,
    get_identity_coord_basis,
    get_identity_fields,
    save_identity_fields,
    set_identity_coord_basis,
)
from models.test_repo import get_model_answer_source_path, get_test_info
from ui_qt import helpers as h
from ui_qt.region_editor import AnswerRegionEditor
from ui_qt.region_mode_widgets import RegionDetectModeToggle, _refresh_segment_button
from ui_qt.style import set_variant


class Step12Page(QWidget):
    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._selected_type: str | None = None
        self._coord_basis: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(h.title_label("⑫ 本人欄設定"))
        root.addWidget(
            h.muted_label(
                "先に場所の基準（補正画像か元画像）を選び、その画像上で学年・組・番号・ID・氏名の枠を指定します。"
                "⑬ の照合は、選んだ基準と同じ画像から同じ座標を切り出します。"
                "「自動認識」では欄の内側をクリック、「手動設定」ではドラッグで矩形を指定します。"
            )
        )

        basis_row = QHBoxLayout()
        basis_row.addWidget(QLabel("場所の基準"))
        self.basis_buttons: dict[str, QPushButton] = {}
        for key, label, tip in (
            (
                IDENTITY_BASIS_WARPED,
                "補正画像",
                "ゆがみを直した模範解答上の位置です。生徒の補正画像から同じ座標を切り出します。",
            ),
            (
                IDENTITY_BASIS_ORIGINAL,
                "元画像",
                "読み込んだ原稿上の位置です。生徒の元画像から同じ座標を切り出します。",
            ),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setToolTip(tip)
            set_variant(btn, "nav-segment")
            btn.clicked.connect(lambda _c=False, basis=key: self._on_basis_clicked(basis))
            self.basis_buttons[key] = btn
            basis_row.addWidget(btn)
        basis_row.addStretch()
        root.addLayout(basis_row)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("欄種別"))
        self._type_group = QButtonGroup(self)
        self._type_group.setExclusive(True)
        self.type_buttons: dict[str, QPushButton] = {}
        for t in IDENTITY_TYPES:
            btn = QPushButton(t)
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            set_variant(btn, "nav-segment")
            self._type_group.addButton(btn)
            btn.clicked.connect(lambda _c=False, tt=t: self._select_type(tt))
            self.type_buttons[t] = btn
            toolbar.addWidget(btn)
        toolbar.addSpacing(12)
        toolbar.addWidget(QLabel("設定"))
        self.region_mode_toggle = RegionDetectModeToggle(
            auto_detect=True,
            on_change=self._on_region_mode_changed,
        )
        self.region_mode_toggle.setToolTip(
            "自動認識: 欄の内側をクリックして枠を検出。\n"
            "手動設定: ドラッグで矩形を指定。\n"
            "検出精度は「二値化」しきい値で調整できます。"
        )
        toolbar.addWidget(self.region_mode_toggle)
        toolbar.addWidget(QLabel("二値化"))
        self.thresh_slider = QSlider(Qt.Orientation.Horizontal)
        self.thresh_slider.setRange(0, 255)
        self.thresh_slider.setValue(128)
        self.thresh_slider.setFixedWidth(120)
        self.thresh_slider.valueChanged.connect(self._on_detect_thresh_changed)
        toolbar.addWidget(self.thresh_slider)
        self.delete_btn = h.button("選択欄を削除", self._on_delete_selected, variant="danger-soft")
        self.delete_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.delete_btn.setAutoDefault(False)
        self.delete_btn.setDefault(False)
        toolbar.addWidget(self.delete_btn)
        toolbar.addWidget(h.button("やり直し", self._on_reset, variant="danger-soft"))
        toolbar.addWidget(h.button("本人欄を保存", self._on_save, variant="primary"))
        toolbar.addWidget(h.button("再読込", self.refresh))
        toolbar.addStretch()
        root.addLayout(toolbar)

        self.hint_label = h.caption_label(
            "先に「補正画像」か「元画像」を選んでから、座標を指定してください。"
        )
        root.addWidget(self.hint_label)

        self.editor = AnswerRegionEditor(
            on_change=self._on_regions_changed,
            on_status=self._set_status,
        )
        self.editor.set_detect_threshold(int(self.thresh_slider.value()))
        self.editor.set_detect_roi_margin(480)
        self.editor.set_click_detect_mode(True)
        self.editor.set_require_pending_label_for_detect(True)
        self.editor.set_empty_message("補正画像か元画像を選ぶと、模範解答が表示されます。")
        root.addWidget(self.editor, 1)

        self.status_label = h.caption_label("")
        root.addWidget(self.status_label)

    def handle_delete_key(self) -> None:
        """Del — キャンバスの選択欄を直接削除。"""
        canvas = self.editor._canvas
        if canvas.selected_idx < 0:
            return
        canvas._delete_selected_region()
        self._update_type_buttons()

    def _on_delete_selected(self) -> None:
        self.handle_delete_key()

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _region_action_hint(self) -> str:
        if self.region_mode_toggle.is_auto_detect():
            return "欄の内側をクリックすると自動検出します（再指定で上書き）。"
        return "画像上をドラッグして指定してください（再ドラッグで上書き）。"

    def _basis_label(self) -> str:
        if self._coord_basis == IDENTITY_BASIS_ORIGINAL:
            return "元画像"
        if self._coord_basis == IDENTITY_BASIS_WARPED:
            return "補正画像"
        return ""

    def _refresh_hint(self) -> None:
        if not self._coord_basis:
            self.hint_label.setText(
                "先に「補正画像」か「元画像」を選んでから、座標を指定してください。"
            )
            return
        if self._selected_type:
            self.hint_label.setText(
                f"「{self._selected_type}」欄（{self._basis_label()}） — {self._region_action_hint()}"
            )
            return
        if self.region_mode_toggle.is_auto_detect():
            self.hint_label.setText(
                f"{self._basis_label()}を表示しています。欄種別を選んでから、欄の内側をクリックしてください。"
            )
        else:
            self.hint_label.setText(
                f"{self._basis_label()}を表示しています。欄種別を選んでから、画像上をドラッグして指定してください。"
            )

    def _sync_basis_buttons(self) -> None:
        for key, btn in self.basis_buttons.items():
            btn.setChecked(key == self._coord_basis)
            _refresh_segment_button(btn)

    def _model_path_for_basis(self, basis: str) -> str:
        if basis == IDENTITY_BASIS_ORIGINAL:
            return get_model_answer_source_path(self.app.active_test_id) or ""
        info = get_test_info(self.app.active_test_id)
        return str(info.get("modelAnswerPath") or "")

    def _on_basis_clicked(self, basis: str) -> None:
        if basis == self._coord_basis:
            self._sync_basis_buttons()
            return
        if not self._apply_basis(basis):
            self._sync_basis_buttons()

    def _apply_basis(self, basis: str) -> bool:
        path = self._model_path_for_basis(basis)
        if not path or not Path(path).exists():
            if basis == IDENTITY_BASIS_ORIGINAL:
                h.warn(
                    self,
                    "元画像がありません",
                    "模範解答の原稿が見つかりません。② 回答欄設定で原稿を読み込んでから選んでください。",
                )
            else:
                h.warn(
                    self,
                    "補正画像がありません",
                    "補正済みの模範解答が見つかりません。② 回答欄設定で模範解答を読み込んでから選んでください。",
                )
            return False
        regions = self.editor.get_regions()
        saved = get_identity_fields(self.app.active_test_id)
        if regions or saved:
            if (
                QMessageBox.question(
                    self,
                    "確認",
                    "場所の基準を変えると、指定済みの本人欄は別の画像の座標になるため、"
                    "枠を消してから指定し直します。続けますか？",
                )
                != QMessageBox.Yes
            ):
                return False
        try:
            self.editor.load_image_from_path(path)
        except Exception as e:
            h.error(self, "画像の表示に失敗", str(e))
            return False
        if regions or saved:
            self.editor.clear_all_regions()
            clear_identity_fields(self.app.active_test_id)
            self._update_type_buttons()
        set_identity_coord_basis(self.app.active_test_id, basis)
        self._coord_basis = basis
        self._sync_basis_buttons()
        self._refresh_hint()
        self._set_status(f"{self._basis_label()}を基準にしました。欄種別を選んで座標を指定してください。")
        return True

    def _on_region_mode_changed(self, auto_detect: bool) -> None:
        self.editor.set_click_detect_mode(auto_detect)
        self.editor.focus_canvas()
        self._refresh_hint()
        self._set_status("自動認識モード" if auto_detect else "手動設定モード")

    def _on_detect_thresh_changed(self, value: int) -> None:
        self.editor.set_detect_threshold(value)

    def refresh(self) -> None:
        if not self.app.require_active_test():
            return
        basis = get_identity_coord_basis(self.app.active_test_id)
        self._coord_basis = basis
        self._sync_basis_buttons()
        if not basis:
            self.editor.clear_image()
            self._update_type_buttons()
            self._refresh_hint()
            self.status_label.setText("場所の基準が未選択です。")
            return
        set_identity_coord_basis(self.app.active_test_id, basis)
        path = self._model_path_for_basis(basis)
        if not path or not Path(path).exists():
            self.editor.clear_image()
            self._update_type_buttons()
            self._refresh_hint()
            missing = "元画像" if basis == IDENTITY_BASIS_ORIGINAL else "補正画像"
            self.status_label.setText(
                f"{missing}が見つかりません。② 回答欄設定で模範解答を読み込んでください。"
            )
            return
        try:
            self.editor.load_image_from_path(path)
        except Exception as e:
            self.status_label.setText(f"模範解答の表示に失敗: {e}")
            return
        fields = get_identity_fields(self.app.active_test_id)
        self.editor.set_regions(
            [
                {
                    "id": f["type"],
                    "displayName": f["type"],
                    "x": f["x"],
                    "y": f["y"],
                    "width": f["width"],
                    "height": f["height"],
                }
                for f in fields
            ]
        )
        self._update_type_buttons()
        if self._selected_type:
            self.editor.set_pending_label(self._selected_type, replace_same=True)
        self._refresh_hint()
        self.status_label.setText(f"設定済み（{self._basis_label()}）: {len(fields)} 欄")

    def _select_type(self, type_name: str) -> None:
        self._selected_type = type_name
        for t, btn in self.type_buttons.items():
            btn.setChecked(t == type_name)
            _refresh_segment_button(btn)
        self.editor.set_pending_label(type_name, replace_same=True)
        self.editor.focus_canvas()
        self._refresh_hint()

    def _on_regions_changed(self) -> None:
        QTimer.singleShot(0, self._update_type_buttons)

    def _update_type_buttons(self) -> None:
        done_types = {r["id"] for r in self.editor.get_regions()}
        for t, btn in self.type_buttons.items():
            if t in done_types:
                set_variant(btn, "success")
            else:
                set_variant(btn, "nav-segment")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        done_list = [t for t in IDENTITY_TYPES if t in done_types]
        if done_list and self._coord_basis:
            self.status_label.setText(
                f"設定済み（{self._basis_label()}）: " + "、".join(done_list)
            )

    def _on_reset(self) -> None:
        if (
            QMessageBox.question(self, "確認", "設定した本人欄をすべてクリアしますか？")
            != QMessageBox.Yes
        ):
            return
        self.editor.clear_all_regions()
        self.status_label.setText("クリアしました。")

    def _on_save(self) -> None:
        if not self.app.require_active_test():
            return
        regions = self.editor.get_regions()
        fields = [
            {
                "type": r["id"],
                "x": r["x"],
                "y": r["y"],
                "width": r["width"],
                "height": r["height"],
            }
            for r in regions
            if r["id"] in IDENTITY_TYPES
        ]
        if not self._coord_basis:
            h.error(self, "保存エラー", "先に「補正画像」か「元画像」を選んでください。")
            return
        if not fields:
            h.error(self, "保存エラー", "本人確認欄を 1 つ以上設定してください。")
            return
        try:
            set_identity_coord_basis(self.app.active_test_id, self._coord_basis)
            count = save_identity_fields(self.app.active_test_id, fields)
            h.info(
                self,
                "保存完了",
                f"本人確認欄を {count} 件保存しました（{self._basis_label()}の座標）。",
            )
        except Exception as e:
            h.error(self, "エラー", str(e))
