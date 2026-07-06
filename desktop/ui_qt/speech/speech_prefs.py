"""音声入力モード（config.json）。"""

from __future__ import annotations

import sys

from config import load_config, save_config

SPEECH_MODE_APP = "app"
SPEECH_MODE_WINDOWS = "windows"
DEFAULT_SPEECH_MODE = SPEECH_MODE_APP


def load_speech_input_mode() -> str:
    mode = str(load_config().get("speech_input_mode") or DEFAULT_SPEECH_MODE).strip().lower()
    if mode == SPEECH_MODE_WINDOWS and sys.platform == "win32":
        return SPEECH_MODE_WINDOWS
    return SPEECH_MODE_APP


def save_speech_input_mode(mode: str) -> None:
    cfg = load_config()
    if mode == SPEECH_MODE_WINDOWS and sys.platform == "win32":
        cfg["speech_input_mode"] = SPEECH_MODE_WINDOWS
    else:
        cfg["speech_input_mode"] = SPEECH_MODE_APP
    save_config(cfg)


def is_speech_input_available(mode: str | None = None) -> bool:
    mode = mode or load_speech_input_mode()
    if mode == SPEECH_MODE_WINDOWS:
        from ui_qt.speech.windows_voice_typing import is_windows_voice_typing_available

        return is_windows_voice_typing_available()
    from ui_qt.speech.speech_engine import SpeechEngine

    return SpeechEngine.is_available()
