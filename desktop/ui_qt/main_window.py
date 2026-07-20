"""メインウィンドウ（サイドバー + ステップページ）。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from constants import DESKTOP_READY_STEPS, MANUAL_GRADING_STEP_ID, STEPS
from models.database import connect, get_active_test_id, init_db
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
from ui_qt.pages.step_manual import StepManualPage
from ui_qt.floating_palette.palette_controller import PaletteController
from ui_qt.settings_dialog import open_settings_dialog
from ui_qt.style import COLORS, set_variant


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("模範解答ベース自動採点システム（PC版）")
        self.resize(1280, 820)
        self.setMinimumSize(1000, 660)

        init_db()
        self.active_test_id: str | None = None
        self._current_step_id = 0
        self._apply_startup_test_load()

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        from ui_qt.hover_sidebar import HoverSidebar, OverlayCentral

        content_wrap = QFrame()
        content_wrap.setObjectName("ContentArea")
        content_layout = QVBoxLayout(content_wrap)
        # 左端グラバー分の余白（GAS の main-workspace padding-left 相当）
        content_layout.setContentsMargins(36, 18, 20, 14)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout.addWidget(self.stack, 1)

        sidebar_inner = self._build_sidebar_inner()
        self.sidebar = HoverSidebar(sidebar_inner)
        shell = OverlayCentral(content_wrap, self.sidebar)
        outer.addWidget(shell)

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
        # 手動採点（STEPS リスト外）
        if MANUAL_GRADING_STEP_ID not in self.pages:
            page = StepManualPage(self)
            self.pages[MANUAL_GRADING_STEP_ID] = page
            self.stack.addWidget(page)

        self._refresh_ocr_status()
        self.palette_controller = PaletteController(self)
        self.statusBar().showMessage("準備完了")
        self.load_step(0)

    def show_app_message(self, text: str, *, level: str = "info") -> None:
        """画面下部ステータスバーへ非モーダル通知（システム音なし）。"""
        from ui_qt.app_notify import message_timeout_ms

        color = {
            "info": COLORS["text_secondary"],
            "warn": "#b45309",
            "error": COLORS["danger"],
        }.get(level, COLORS["text_secondary"])
        self.statusBar().setStyleSheet(
            f"QStatusBar {{ color: {color}; font-size: 11px; }}"
        )
        self.statusBar().showMessage(text, message_timeout_ms(level))

    # --- サイドバー ---

    def _build_sidebar_inner(self) -> QWidget:
        sidebar = QWidget()
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 0, 0, 0)
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
            if step["id"] <= 2:
                self._add_nav_button(lay, step)

        fork_lbl = QLabel("▼ 分岐")
        fork_lbl.setObjectName("NavForkLabel")
        fork_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(fork_lbl)

        branch = QFrame()
        branch.setObjectName("NavBranchBlock")
        branch_lay = QHBoxLayout(branch)
        branch_lay.setContentsMargins(0, 0, 0, 0)
        branch_lay.setSpacing(4)

        auto_panel = QFrame()
        auto_panel.setObjectName("NavAutoPath")
        auto_lay = QVBoxLayout(auto_panel)
        auto_lay.setContentsMargins(4, 4, 4, 4)
        auto_lay.setSpacing(2)
        auto_title = QLabel("自動採点")
        auto_title.setObjectName("NavAutoPathTitle")
        auto_title.setAlignment(Qt.AlignCenter)
        auto_lay.addWidget(auto_title)
        for step in STEPS:
            if step["id"] in (3, 4, 5):
                self._add_nav_button(auto_lay, step, compact=True)

        manual_panel = QFrame()
        manual_panel.setObjectName("NavManualPath")
        manual_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        manual_lay = QVBoxLayout(manual_panel)
        manual_lay.setContentsMargins(4, 4, 4, 4)
        manual_lay.setSpacing(2)
        manual_title = QLabel("手動採点")
        manual_title.setObjectName("NavManualPathTitle")
        manual_title.setAlignment(Qt.AlignCenter)
        manual_lay.addWidget(manual_title)
        manual_btn = QPushButton("画像を見ながら\n○△×")
        manual_btn.setObjectName("ManualGradingNav")
        set_variant(manual_btn, "nav")
        manual_btn.setCheckable(True)
        manual_btn.setEnabled(MANUAL_GRADING_STEP_ID in DESKTOP_READY_STEPS)
        manual_btn.setCursor(Qt.PointingHandCursor)
        manual_btn.setToolTip("③④⑤ の代替 — 画像を見ながら ○△× を付ける")
        manual_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        manual_btn.clicked.connect(
            lambda _c=False: self.load_step(MANUAL_GRADING_STEP_ID)
        )
        self.nav_group.addButton(manual_btn)
        self.nav_buttons[MANUAL_GRADING_STEP_ID] = manual_btn
        manual_lay.addWidget(manual_btn, 1)

        branch_lay.addWidget(auto_panel, 3)
        branch_lay.addWidget(manual_panel, 2)
        lay.addWidget(branch)

        merge_lbl = QLabel("▼ 合流")
        merge_lbl.setObjectName("NavMergeLabel")
        merge_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(merge_lbl)

        for step in STEPS:
            if step["id"] >= 6:
                self._add_nav_button(lay, step)

        lay.addSpacing(10)
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #e5e7eb;")
        lay.addWidget(divider)
        lay.addSpacing(6)

        search_btn = QPushButton("文字列検索")
        set_variant(search_btn, "nav")
        search_btn.setCursor(Qt.PointingHandCursor)
        search_btn.setToolTip("OCR・テキストボックス内の文字列を検索し、該当答案へジャンプします")
        search_btn.clicked.connect(self._open_text_search)
        lay.addWidget(search_btn)

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

    def _add_nav_button(
        self, lay: QVBoxLayout, step: dict, *, compact: bool = False
    ) -> None:
        sid = step["id"]
        enabled = sid in DESKTOP_READY_STEPS
        label = step["label"] + ("" if enabled else " …準備中")
        btn = QPushButton(label)
        set_variant(btn, "nav")
        btn.setCheckable(True)
        btn.setEnabled(enabled)
        btn.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        if compact:
            btn.setObjectName("NavBranchStep")
        if enabled:
            btn.clicked.connect(lambda _c=False, s=sid: self.load_step(s))
        self.nav_group.addButton(btn)
        self.nav_buttons[sid] = btn
        lay.addWidget(btn)

    # --- 共通 ---

    def require_active_test(self) -> bool:
        if not self.active_test_id:
            h.warn(self, "テスト未選択", "先にテストを作成または選択してください。")
            return False
        return True

    def _refresh_ocr_status(self) -> None:
        info = check_ocr_config()
        self.ocr_status_label.setText(info.get("message", ""))

    def _refresh_stylus_prefs(self) -> None:
        self.palette_controller.apply_config()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.palette_controller.persist()
        super().closeEvent(event)

    def _open_settings(self) -> None:
        def on_saved() -> None:
            self._refresh_ocr_status()
            self._refresh_stylus_prefs()
            self.palette_controller.refresh_speech_prefs()

        open_settings_dialog(self, on_saved=on_saved)

    def _open_text_search(self) -> None:
        self._sync_active_test()
        if not self.require_active_test():
            return
        from ui_qt.text_search_dialog import TextSearchDialog

        dlg = TextSearchDialog(
            self,
            test_id=str(self.active_test_id),
            on_open_crop=self._text_search_open_crop,
            on_open_full_sheet=self._text_search_open_full_sheet,
            on_open_step4=self._text_search_open_step4,
        )
        dlg.exec()

    def _text_search_open_crop(self, hit: dict) -> None:
        self.load_step(4)
        page = self.pages.get(4)
        if page is not None and hasattr(page, "show_crop_from_search"):
            page.show_crop_from_search(hit)  # type: ignore[attr-defined]

    def _text_search_open_full_sheet(self, hit: dict) -> None:
        from models.ink_repo import is_sheet_field_id

        rid = int(hit.get("resultId") or 0)
        fid = str(hit.get("fieldId") or "")
        if is_sheet_field_id(fid):
            fid = ""
        self.palette_controller.open_full_sheet_grade_dialog(
            result_id=rid, field_id=fid or None
        )

    def _text_search_open_step4(self, hit: dict) -> None:
        self.load_step(4)
        page = self.pages.get(4)
        if page is not None and hasattr(page, "focus_field_from_search"):
            page.focus_field_from_search(str(hit.get("fieldId") or ""))  # type: ignore[attr-defined]

    def load_step(self, step_id: int) -> None:
        self._current_step_id = step_id
        self._sync_active_test()
        page = self.pages.get(step_id)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        btn = self.nav_buttons.get(step_id)
        if btn:
            btn.setChecked(True)
        if step_id != 0 and hasattr(page, "refresh") and self.active_test_id:
            page.refresh()  # type: ignore[attr-defined]
        elif step_id == 0:
            page.refresh()  # type: ignore[attr-defined]
        self._sync_palette(step_id)
        self.palette_controller.set_delete_hotkey_enabled(self._current_step_id not in (1, 8))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (
            self._current_step_id in (1, 8)
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
            and not self._region_delete_hotkey_blocked()
        ):
            page = self.pages.get(self._current_step_id)
            if page is not None and hasattr(page, "handle_delete_key"):
                page.handle_delete_key()  # type: ignore[attr-defined]
                return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _region_delete_hotkey_blocked() -> bool:
        fw = QApplication.focusWidget()
        if fw is None:
            return False
        if isinstance(fw, (QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox)):
            return True
        if isinstance(fw, QComboBox) and fw.isEditable():
            return True
        return False

    def _sync_palette(self, step_id: int) -> None:
        if step_id in PaletteController.ACTIVE_STEPS:
            page = self.pages.get(step_id)
            if page is not None and hasattr(page, "viewer_scroll"):
                self.palette_controller.attach_page(page, step_id)  # type: ignore[arg-type]
                self.palette_controller.show_for_step(step_id)
            return
        self.palette_controller.detach()

    def _apply_startup_test_load(self) -> None:
        """詳細設定の起動時テスト読み出しモードを反映する。"""
        from config import load_config
        from models.test_repo import clear_active_test

        if load_config().get("startup_test_load", "auto") == "blank":
            clear_active_test()
            self.active_test_id = None

    def _sync_active_test(self) -> None:
        """DB のアクティブテストをメモリに同期する。"""
        with connect() as conn:
            tid = get_active_test_id(conn)
        self.active_test_id = tid
