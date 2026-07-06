"""Windows 音声入力（Voice Typing / Win+H）の起動。"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget


def is_windows_voice_typing_available() -> bool:
    return sys.platform == "win32"


def _force_foreground_window(hwnd: int) -> bool:
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if user32.GetForegroundWindow() == hwnd:
        return True
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    cur_tid = kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
    try:
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
    return user32.GetForegroundWindow() == hwnd


def focus_widget_for_voice_input(widget: QWidget) -> bool:
    """音声入力先ウィジェットへ OS レベルでフォーカスを移す。"""
    if widget is None:
        return False
    host = widget.window()
    if host is not None:
        host.showNormal()
        host.raise_()
        host.activateWindow()
    widget.setFocus(Qt.FocusReason.OtherFocusReason)
    if sys.platform == "win32":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            if host is not None:
                _force_foreground_window(int(host.winId()))
            user32.SetFocus(int(widget.winId()))
        except Exception:
            pass
    return widget.hasFocus()


def toggle_windows_voice_typing() -> bool:
    """Win+H を送り Windows 音声入力バーをトグルする。

    タッチキーボードのマイクボタンと同じ音声入力機能です。
    公式 API はなく、ショートカット送信による workaround です。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        keyeventf_keyup = 0x0002
        vk_lwin = 0x5B
        vk_h = 0x48
        user32 = ctypes.windll.user32
        user32.keybd_event(vk_lwin, 0, 0, 0)
        user32.keybd_event(vk_h, 0, 0, 0)
        user32.keybd_event(vk_h, 0, keyeventf_keyup, 0)
        user32.keybd_event(vk_lwin, 0, keyeventf_keyup, 0)
        return True
    except Exception:
        return False
