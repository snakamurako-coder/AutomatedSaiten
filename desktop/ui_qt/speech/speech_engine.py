"""プラットフォーム別マイク音声認識エンジン（Windows: WinRT / その他: SpeechRecognition）。"""

from __future__ import annotations

import asyncio
import sys
import threading

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

_WINRT_AVAILABLE = False
if sys.platform == "win32":
    try:
        import winrt.windows.foundation  # noqa: F401
        import winrt.windows.globalization  # noqa: F401
        import winrt.windows.media.speechrecognition  # noqa: F401

        _WINRT_AVAILABLE = True
    except ImportError:
        pass

_SR_AVAILABLE = False
try:
    import speech_recognition  # noqa: F401

    _SR_AVAILABLE = True
except ImportError:
    pass

_PYAUDIO_AVAILABLE = False
if _SR_AVAILABLE:
    try:
        import pyaudio  # noqa: F401

        _PYAUDIO_AVAILABLE = True
    except ImportError:
        pass


def _availability_message() -> tuple[bool, str]:
    if _WINRT_AVAILABLE:
        return True, ""
    if _SR_AVAILABLE and _PYAUDIO_AVAILABLE:
        return True, ""
    if sys.platform == "win32":
        return (
            False,
            "音声認識パッケージが未インストールです。"
            " pip install winrt-Windows.Media.SpeechRecognition "
            "winrt-Windows.Foundation winrt-Windows.Globalization",
        )
    if not _SR_AVAILABLE:
        return False, "SpeechRecognition が未インストールです（pip install SpeechRecognition）"
    return False, "PyAudio が未インストールです（pip install PyAudio）"


class _SpeechWorkerBase(QThread):
    transcript_received = Signal(str)
    error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._want_listening = False

    def request_start(self) -> None:
        self._want_listening = True
        self._pause_event.clear()

    def request_stop(self) -> None:
        self._want_listening = False
        self._pause_event.clear()
        self._stop_event.set()

    def request_pause(self) -> None:
        self._pause_event.set()

    def request_resume(self) -> None:
        self._pause_event.clear()


class _WinrtSpeechWorker(_SpeechWorkerBase):
    def run(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as exc:
            if not self._stop_event.is_set():
                self.error.emit(f"音声認識エラー: {exc}")

    async def _async_main(self) -> None:
        from winrt.windows.globalization import Language
        from winrt.windows.media.speechrecognition import (
            SpeechRecognitionResultStatus,
            SpeechRecognizer,
        )

        try:
            recognizer = SpeechRecognizer(Language("ja-JP"))
        except Exception:
            recognizer = SpeechRecognizer()

        compile_result = await recognizer.compile_constraints_async()
        if int(compile_result.status) != 0:
            self.error.emit("日本語の音声認識を初期化できません（Windows の音声パックを確認）")
            recognizer.close()
            return

        session = recognizer.continuous_recognition_session
        session_active = False

        def on_result(_sender, args) -> None:
            try:
                result = args.result
                if result.status != SpeechRecognitionResultStatus.SUCCESS:
                    return
                text = str(result.text or "").strip()
                if text:
                    self.transcript_received.emit(text)
            except Exception as exc:
                self.error.emit(f"音声認識エラー: {exc}")

        session.result_generated += on_result

        try:
            while not self._stop_event.is_set():
                if self._want_listening and not self._pause_event.is_set():
                    if not session_active:
                        await session.start_async()
                        session_active = True
                elif session_active:
                    await session.stop_async()
                    session_active = False
                await asyncio.sleep(0.05)
        finally:
            if session_active:
                try:
                    await session.stop_async()
                except Exception:
                    pass
            recognizer.close()


class _SrSpeechWorker(_SpeechWorkerBase):
    def run(self) -> None:
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.8

        try:
            microphone = sr.Microphone()
        except OSError as exc:
            self.error.emit(f"マイクを開けません: {exc}")
            return

        with microphone as source:
            try:
                recognizer.adjust_for_ambient_noise(source, duration=0.4)
            except Exception:
                pass

            while not self._stop_event.is_set():
                if not self._want_listening or self._pause_event.is_set():
                    self.msleep(50)
                    continue
                try:
                    audio = recognizer.listen(source, timeout=0.8, phrase_time_limit=20)
                except sr.WaitTimeoutError:
                    continue
                except Exception as exc:
                    if not self._stop_event.is_set():
                        self.error.emit(f"録音エラー: {exc}")
                    break

                if self._stop_event.is_set() or self._pause_event.is_set():
                    continue

                try:
                    text = recognizer.recognize_google(audio, language="ja-JP")
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as exc:
                    self.error.emit(f"音声認識エラー: {exc}")
                    continue

                chunk = str(text or "").strip()
                if chunk:
                    self.transcript_received.emit(chunk)


def _create_worker(parent: QWidget | None) -> _SpeechWorkerBase:
    if _WINRT_AVAILABLE:
        return _WinrtSpeechWorker(parent)
    return _SrSpeechWorker(parent)


class SpeechEngine(QWidget):
    """マイク音声認識（Windows: オフライン WinRT / その他: Google STT・要ネット）。"""

    transcript_received = Signal(str)
    error = Signal(str)
    listening_changed = Signal(bool)
    availability_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: _SpeechWorkerBase | None = None
        self._listening = False
        self._paused = False
        self._want_listening = False
        self.availability_changed.emit(self.is_available())

    @staticmethod
    def is_available() -> bool:
        ok, _ = _availability_message()
        return ok

    def is_listening(self) -> bool:
        return self._listening

    def start(self) -> None:
        ok, message = _availability_message()
        if not ok:
            self.error.emit(message)
            return
        self._want_listening = True
        self._paused = False
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_start()
            self._set_listening(True)
            return
        self._spawn_worker()

    def _spawn_worker(self) -> None:
        self._worker = _create_worker(self)
        self._worker.transcript_received.connect(self.transcript_received)
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.request_start()
        self._worker.start()
        self._set_listening(True)

    def stop(self) -> None:
        self._want_listening = False
        self._paused = False
        if self._worker is not None:
            self._worker.request_stop()
            if not self._worker.wait(5000):
                self._worker.terminate()
                self._worker.wait(1000)
            self._worker = None
        self._set_listening(False)

    def pause(self) -> None:
        """確認ダイアログ表示中など、認識だけ一時停止（音声入力トグルはオンのまま）。"""
        self._paused = True
        if self._worker is not None:
            self._worker.request_pause()
        self._set_listening(False)

    def resume(self) -> None:
        """一時停止後に認識を再開。"""
        self._paused = False
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_resume()
        if self._want_listening:
            self._set_listening(True)

    def _set_listening(self, on: bool) -> None:
        if self._listening == on:
            return
        self._listening = on
        self.listening_changed.emit(on)

    def _on_worker_error(self, message: str) -> None:
        msg = str(message or "").strip()
        if msg:
            self.error.emit(msg)

    def _on_worker_finished(self) -> None:
        self._set_listening(False)
        if self._want_listening and not self._paused:
            self._spawn_worker()
