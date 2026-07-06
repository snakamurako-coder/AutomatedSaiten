"""Windows 音声入力（Voice Typing / Win+H）の起動。"""

from __future__ import annotations

import sys


def is_windows_voice_typing_available() -> bool:
    return sys.platform == "win32"


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
