"""メインウィンドウ（サイドバー + ステップページ）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from constants import DESKTOP_READY_STEPS, STEPS
from models.database import init_db
from services.ocr import check_ocr_config
from ui_qt import helpers as h
from ui_qt.pages.step0 import Step0Page
from ui_qt.pages.step1 import Step1Page
from ui_qt.pages.step2 import Step2Page
from ui_qt.pages.step3 import Step3Page
from ui_qt.pages.step4 import Step4Page
from ui_qt.pages.step5 import Step5Page
from ui_qt.pages.step6 import Step6Page
from ui_qt.pages.step7 import Step7Page
from ui_qt.pages.step8 import Step8Page
from ui_qt.pages.step9 import Step9Page
from ui_qt.pages.step10 import Step10Page
from ui_qt.settings_dialog import open_settings_dialog
from ui_qt.style import set_variant


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("模範解答ベース自動採点システム（PC版）")
        self.resize(1280, 820)
        self.setMinimumSize(1000, 660)

        init_db()
        self.active_test_id: str | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        content_wrap = QFrame()
        content_wrap.setObjectName("ContentArea")
        content_layout = QVBoxLayout(content_wrap)
        content_layout.setContentsMargins(20, 18, 20, 14)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack)
        root.addWidget(content_wrap, 1)

        self.pages: dict[int, QWidget] = {}
        page_classes = {
            0: Step0Page,
            1: Step1Page,
            2: Step2Page,
            3: Step3Page,
            4: Step4Page,
            5: Step5Page,
            6: Step6Page,
            7: Step7Page,
            8: Step8Page,
            9: Step9Page,
            10: Step10Page,
        }
        for step in STEPS:
            sid = step["id"]
            if sid in page_classes:
                page = page_classes[sid](self)
            else:
                page = QWidget()
                lay = QVBoxLayout(page)
                lay.addWidget(
                    h.muted_label(f"{step['label']} — このステップは今後のバージョンで追加予定です。")
                )
                lay.addStretch()
            self.pages[sid] = page
            self.stack.addWidget(page)

        self._refresh_ocr_status()
        self.load_step(0)
        self.pages[0].refresh()  # type: ignore[attr-defined]

    # --- サイドバー ---

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(232)
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(14, 16, 14, 12)
        lay.setSpacing(3)

        title = QLabel("自動採点")
        title.setObjectName("SidebarTitle")
        lay.addWidget(title)
        sub = QLabel("PC版")
        sub.setObjectName("SidebarCaption")
        lay.addWidget(sub)
        lay.addSpacing(12)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[int, QPushButton] = {}
        for step in STEPS:
            sid = step["id"]
            enabled = sid in DESKTOP_READY_STEPS
            btn = QPushButton(step["label"] + ("" if enabled else " …準備中"))
            set_variant(btn, "nav")
            btn.setCheckable(True)
            btn.setEnabled(enabled)
            btn.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
            if enabled:
                btn.clicked.connect(lambda _c=False, s=sid: self.load_step(s))
            self.nav_group.addButton(btn)
            self.nav_buttons[sid] = btn
            lay.addWidget(btn)

        lay.addSpacing(10)
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #e5e7eb;")
        lay.addWidget(divider)
        lay.addSpacing(6)

        settings_btn = QPushButton("詳細設定")
        set_variant(settings_btn, "nav")
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.clicked.connect(self._open_settings)
        lay.addWidget(settings_btn)

        lay.addStretch()
        self.ocr_status_label = QLabel("")
        self.ocr_status_label.setObjectName("SidebarCaption")
        self.ocr_status_label.setWordWrap(True)
        lay.addWidget(self.ocr_status_label)
        return sidebar

    # --- 共通 ---

    def require_active_test(self) -> bool:
        if not self.active_test_id:
            h.warn(self, "テスト未選択", "先にテストを作成または選択してください。")
            return False
        return True

    def _refresh_ocr_status(self) -> None:
        info = check_ocr_config()
        self.ocr_status_label.setText(info.get("message", ""))

    def _open_settings(self) -> None:
        open_settings_dialog(self, on_saved=self._refresh_ocr_status)

    def load_step(self, step_id: int) -> None:
        self.stack.setCurrentWidget(self.pages[step_id])
        btn = self.nav_buttons.get(step_id)
        if btn:
            btn.setChecked(True)
        page = self.pages[step_id]
        if step_id != 0 and hasattr(page, "refresh") and self.active_test_id:
            page.refresh()  # type: ignore[attr-defined]
        elif step_id == 0:
            page.refresh()  # type: ignore[attr-defined]
