"""Qt UI 共通ヘルパー（スレッド実行・画像変換・ダイアログ）。"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QWidget

from ui_qt.style import set_role, set_variant


class _Worker(QObject):
    finished = Signal(object, object)  # (result, error)
    progress = Signal(object)

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished.emit(result, None)
        except Exception as e:  # noqa: BLE001 - UI に表示するため全捕捉
            self.finished.emit(None, e)


def run_in_thread(
    parent: QObject,
    fn: Callable[..., Any],
    on_done: Callable[[Any, Exception | None], None],
    *args: Any,
    on_progress: Callable[[Any], None] | None = None,
    **kwargs: Any,
) -> QThread:
    """バックグラウンドスレッドで fn を実行し、完了時に UI スレッドで on_done を呼ぶ。

    fn へ progress コールバックを渡したい場合は、kwargs 側で
    `progress_emitter` を受け取る設計にせず、Signal 経由で on_progress を使う。
    """
    thread = QThread(parent)
    worker = _Worker(fn, *args, **kwargs)
    worker.moveToThread(thread)
    if on_progress:
        worker.progress.connect(on_progress)

    def _done(result: Any, error: Exception | None) -> None:
        on_done(result, error)
        thread.quit()

    worker.finished.connect(_done)
    thread.started.connect(worker.run)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    # 親に参照を保持させて GC を防ぐ
    if not hasattr(parent, "_bg_threads"):
        parent._bg_threads = []  # type: ignore[attr-defined]
    parent._bg_threads.append(thread)  # type: ignore[attr-defined]
    thread.finished.connect(lambda: parent._bg_threads.remove(thread))  # type: ignore[attr-defined]
    thread.start()
    return thread


class ProgressBridge(QObject):
    """ワーカースレッドから UI スレッドへ進捗を渡すためのシグナル橋。"""

    updated = Signal(int, int, str)  # (current, total, name)


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


# --- ダイアログ ---

def info(parent: QWidget | None, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def warn(parent: QWidget | None, title: str, message: str) -> None:
    QMessageBox.warning(parent, title, message)


def error(parent: QWidget | None, title: str, message: str) -> None:
    QMessageBox.critical(parent, title, message)


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
