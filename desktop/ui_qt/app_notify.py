"""非モーダル通知（ステータスバー表示。ポップアップ・システム音なし）。"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget

_LEVEL_MS = {
    "info": 6000,
    "warn": 9000,
    "error": 12000,
}


def _find_message_host(parent: QWidget | None) -> QWidget | None:
    w: QWidget | None = parent
    while w is not None:
        if hasattr(w, "show_app_message"):
            return w
        w = w.parentWidget()
    app = QApplication.instance()
    if app is None:
        return None
    for top in app.topLevelWidgets():
        if hasattr(top, "show_app_message"):
            return top
    return None


def show_app_message(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    level: str = "info",
) -> None:
    host = _find_message_host(parent)
    text = f"{title}: {message}" if title and message else (title or message)
    if host is not None:
        host.show_app_message(text, level=level)  # type: ignore[attr-defined]
        return
    print(f"[{level}] {text}")


def notify_info(parent: QWidget | None, title: str, message: str) -> None:
    show_app_message(parent, title, message, level="info")


def notify_warn(parent: QWidget | None, title: str, message: str) -> None:
    show_app_message(parent, title, message, level="warn")


def notify_error(parent: QWidget | None, title: str, message: str) -> None:
    show_app_message(parent, title, message, level="error")


def message_timeout_ms(level: str) -> int:
    return _LEVEL_MS.get(level, 6000)
