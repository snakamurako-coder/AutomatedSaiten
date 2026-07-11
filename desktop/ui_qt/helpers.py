"""Qt UI 共通ヘルパー（スレッド実行・画像変換・ダイアログ）。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from ui_qt.app_notify import notify_error, notify_info, notify_warn
from ui_qt.style import set_role, set_variant

class _AsyncBridge(QObject):
    """Python threading から UI スレッドへ結果を渡すブリッジ。"""

    finished = Signal(object, object)  # (result, error)


def run_in_thread(
    parent: QObject,
    fn: Callable[..., Any],
    on_done: Callable[[Any, Exception | None], None],
    *args: Any,
    **kwargs: Any,
) -> _AsyncBridge:
    """バックグラウンドスレッドで fn を実行し、完了時に UI スレッドで on_done を呼ぶ。

    QThread では PySide6 環境で完了コールバックが届かないことがあるため、
    threading + Signal で UI に戻す。
    """
    bridge = _AsyncBridge()

    def _handle(result: Any, error: Exception | None) -> None:
        on_done(result, error)
        workers = getattr(parent, "_async_workers", None)
        if workers is not None and bridge in workers:
            workers.remove(bridge)

    bridge.finished.connect(_handle, Qt.ConnectionType.QueuedConnection)

    if not hasattr(parent, "_async_workers"):
        parent._async_workers = []  # type: ignore[attr-defined]
    parent._async_workers.append(bridge)  # type: ignore[attr-defined]

    def work() -> None:
        try:
            result = fn(*args, **kwargs)
            bridge.finished.emit(result, None)
        except Exception as e:  # noqa: BLE001 - UI に表示するため全捕捉
            bridge.finished.emit(None, e)

    threading.Thread(target=work, daemon=True, name="AsyncWorker").start()
    return bridge


class ProgressBridge(QObject):
    """ワーカースレッドから UI スレッドへ進捗を渡すためのシグナル橋。"""

    updated = Signal(int, int, str)  # (current, total, name)
    detailed = Signal(object)  # dict — ファイル単位の詳細進捗


def bgr_to_qimage(image_bgr: np.ndarray) -> QImage:
    """OpenCV BGR ndarray → QImage（コピーを返す）。"""
    if image_bgr.ndim == 2:
        h, w = image_bgr.shape
        return QImage(image_bgr.data, w, h, w, QImage.Format_Grayscale8).copy()
    h, w, ch = image_bgr.shape
    if ch == 4:
        return QImage(image_bgr.data, w, h, w * 4, QImage.Format_ARGB32).copy()
    rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])
    return QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888).copy()


def bgr_to_qpixmap(image_bgr: np.ndarray) -> QPixmap:
    return QPixmap.fromImage(bgr_to_qimage(image_bgr))


def pil_to_qpixmap(pil_image) -> QPixmap:
    from PIL.ImageQt import ImageQt

    return QPixmap.fromImage(QImage(ImageQt(pil_image.convert("RGBA"))))


# --- 通知（非モーダル・音声なし） ---

def info(parent: QWidget | None, title: str, message: str) -> None:
    notify_info(parent, title, message)


def warn(parent: QWidget | None, title: str, message: str) -> None:
    notify_warn(parent, title, message)


def error(parent: QWidget | None, title: str, message: str) -> None:
    notify_error(parent, title, message)


# --- ウィジェット生成ショートカット ---

def title_label(text: str) -> QLabel:
    lbl = QLabel(text)
    set_role(lbl, "title")
    return lbl


def muted_label(text: str, wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    set_role(lbl, "muted")
    lbl.setWordWrap(wrap)
    return lbl


def caption_label(text: str, wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    set_role(lbl, "caption")
    lbl.setWordWrap(wrap)
    return lbl


def button(text: str, on_click: Callable[[], None] | None = None, variant: str | None = None) -> QPushButton:
    btn = QPushButton(text)
    if variant:
        set_variant(btn, variant)
    if on_click:
        btn.clicked.connect(on_click)
    return btn


def open_in_file_manager(path: str | Path, *, parent: QWidget | None = None) -> bool:
    """ファイルまたはフォルダを OS のファイルマネージャで開く。

    ファイルの場合は親フォルダを開く（Windows では可能なら当該ファイルを選択）。
    フォルダが無ければ作成してから開く。
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    target = Path(path)
    try:
        if target.suffix and not target.exists() and target.parent:
            folder = target.parent
            select = None
        elif target.is_file():
            folder = target.parent
            select = target
        else:
            folder = target
            select = None
        folder.mkdir(parents=True, exist_ok=True)
        folder = folder.resolve()

        import sys

        if sys.platform == "win32" and select is not None and select.exists():
            import subprocess

            subprocess.Popen(["explorer", f"/select,{select}"])
            return True

        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        if not ok and parent is not None:
            error(parent, "フォルダを開けません", f"次の場所を開けませんでした:\n{folder}")
        return bool(ok)
    except Exception as e:
        if parent is not None:
            error(parent, "フォルダを開けません", str(e))
        return False


def open_folder_button(
    on_click: Callable[[], None],
    *,
    text: str = "出力フォルダを開く",
) -> QPushButton:
    """データ出力 UI の横に置く確認用ボタン。"""
    btn = button(text, on_click)
    btn.setToolTip("出力先フォルダをエクスプローラーで開いて確認します")
    return btn
