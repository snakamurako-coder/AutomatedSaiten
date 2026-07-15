"""エクセル出力詳細設定ダイアログ。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui_qt import helpers as h
from models.excel_export_prefs import load_excel_export_prefs, save_excel_export_prefs


class ExcelExportSettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("エクセル出力詳細設定")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(420, 200)
        prefs = load_excel_export_prefs()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        root.addWidget(
            h.caption_label(
                "ヒストグラムの刻みと、ランキングに載せる人数を設定します。"
            )
        )

        form = QFormLayout()
        self.hist_spin = QSpinBox()
        self.hist_spin.setRange(1, 50)
        self.hist_spin.setSuffix(" %")
        self.hist_spin.setValue(int(prefs["hist_bin_pct"]))
        self.hist_spin.setToolTip(
            "満点に対する割合の刻み幅。満点ちょうどは単独の階級になります。"
        )
        form.addRow("ヒストグラム刻み（満点の割合）", self.hist_spin)

        self.overall_spin = QSpinBox()
        self.overall_spin.setRange(1, 500)
        self.overall_spin.setSuffix(" 位")
        self.overall_spin.setValue(int(prefs["rank_overall_limit"]))
        form.addRow("全体ランキング表示人数", self.overall_spin)

        self.class_spin = QSpinBox()
        self.class_spin.setRange(1, 200)
        self.class_spin.setSuffix(" 位")
        self.class_spin.setValue(int(prefs["rank_class_limit"]))
        form.addRow("クラス別ランキング表示人数", self.class_spin)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        save_excel_export_prefs(
            hist_bin_pct=self.hist_spin.value(),
            rank_overall_limit=self.overall_spin.value(),
            rank_class_limit=self.class_spin.value(),
        )
        self.accept()
