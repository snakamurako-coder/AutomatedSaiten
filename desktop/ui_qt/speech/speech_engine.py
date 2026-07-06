"""プラットフォーム別マイク音声認識エンジン（Windows: WinRT / その他: SpeechRecognition）。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading

from PySide6.QtCore import Q_ARG, QMetaObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import QWidget

_HRESULT_PRIVACY_DECLINED = 0x80045509
_PRIVACY_ERROR_MESSAGE = (
    "Windows の音声認識を使うには、以下の設定が必要です。\n\n"
    "設定 → プライバシーとセキュリティ → 音声\n"
    "で「オンライン音声認識」をオンにし、"
    "Microsoft のプライバシーポリシーを確認・同意してください。\n\n"
    "（従来の設定名: 「入力とカスタム入力の設定を使う」／「私を理解する」）\n\n"
    "設定画面を開きました。有効にしたら、もう一度音声入力をお試しください。"
)

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


def _is_speech_privacy_error(exc: BaseException) -> bool:
    for attr in ("winerror", "hresult", "HRESULT"):
        value = getattr(exc, attr, None)
        if value is not None and int(value) & 0xFFFFFFFF == _HRESULT_PRIVACY_DECLINED:
            return True
    message = str(exc).lower()
    return "privacy policy" in message or "speech privacy" in message


def _open_speech_privacy_settings() -> None:
    if sys.platform != "win32":
        return
    for uri in (
        "ms-settings:privacy-speech",
        "ms-settings:privacy-speechtyping",
        "ms-settings:privacy-accounts",
    ):
        try:
            os_startfile = getattr(__import__("os"), "startfile", None)
            if os_startfile is not None:
                os_startfile(uri)
                return
        except OSError:
            continue
    subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:privacy-speech"], shell=False)


def _emit_speech_error(worker: _SpeechWorkerBase, exc: BaseException) -> None:
    if _is_speech_privacy_error(exc):
        _open_speech_privacy_settings()
        worker.error.emit(_PRIVACY_ERROR_MESSAGE)
        return
    worker.error.emit(f"音声認識エラー: {exc}")


def _init_worker_com() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        hr = ctypes.windll.ole32.CoInitializeEx(None, 2)
        return hr in (0, 1)
    except Exception:
        return False


def _uninit_worker_com(initialized: bool) -> None:
    if not initialized or sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.ole32.CoUninitialize()
    except Exception:
        pass


def _winrt_status_message(status) -> str | None:
    from winrt.windows.media.speechrecognition import SpeechRecognitionResultStatus

    silent_statuses = {
        SpeechRecognitionResultStatus.SUCCESS,
        SpeechRecognitionResultStatus.TIMEOUT_EXCEEDED,
        SpeechRecognitionResultStatus.UNKNOWN,
        SpeechRecognitionResultStatus.USER_CANCELED,
    }
    if status in silent_statuses:
        return None
    messages = {
        SpeechRecognitionResultStatus.MICROPHONE_UNAVAILABLE: (
            "マイクが利用できません。Windows の設定 → プライバシー → マイクを確認してください。"
        ),
        SpeechRecognitionResultStatus.NETWORK_FAILURE: (
            "音声認識にネットワーク接続が必要です。"
        ),
        SpeechRecognitionResultStatus.AUDIO_QUALITY_FAILURE: (
            "音声品質が低いため認識できませんでした。マイク位置を調整してください。"
        ),
        SpeechRecognitionResultStatus.TOPIC_LANGUAGE_NOT_SUPPORTED: (
            "日本語の音声認識がサポートされていません。Windows の言語パックを確認してください。"
        ),
    }
    return messages.get(status)


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
        com_initialized = _init_worker_com()
        try:
            asyncio.run(self._async_main())
        except Exception as exc:
            if not self._stop_event.is_set():
                _emit_speech_error(self, exc)
        finally:
            _uninit_worker_com(com_initialized)

    @Slot(str)
    def _deliver_transcript(self, text: str) -> None:
        chunk = str(text or "").strip()
        if chunk:
            self.transcript_received.emit(chunk)

    @Slot(str)
    def _deliver_error(self, message: str) -> None:
        msg = str(message or "").strip()
        if msg:
            self.error.emit(msg)

    def _schedule_transcript(self, text: str) -> None:
        QMetaObject.invokeMethod(
            self,
            "_deliver_transcript",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, text),
        )

    def _schedule_error(self, message: str) -> None:
        QMetaObject.invokeMethod(
            self,
            "_deliver_error",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, message),
        )

    async def _async_main(self) -> None:
        from winrt.windows.globalization import Language
        from winrt.windows.media.speechrecognition import (
            SpeechRecognitionConfidence,
            SpeechRecognitionResultStatus,
            SpeechRecognizer,
        )

        recognizer = None
        try:
            try:
                recognizer = SpeechRecognizer(Language("ja-JP"))
            except Exception:
                recognizer = SpeechRecognizer()

            compile_result = await recognizer.compile_constraints_async()
            if int(compile_result.status) != 0:
                self._schedule_error(
                    "日本語の音声認識を初期化できません（Windows の音声パックを確認）"
                )
                return

            while not self._stop_event.is_set():
                if not self._want_listening or self._pause_event.is_set():
                    await asyncio.sleep(0.05)
                    continue

                try:
                    result = await recognizer.recognize_async()
                except Exception as exc:
                    if self._stop_event.is_set():
                        break
                    if _is_speech_privacy_error(exc):
                        _open_speech_privacy_settings()
                        self._schedule_error(_PRIVACY_ERROR_MESSAGE)
                        break
                    self._schedule_error(f"音声認識エラー: {exc}")
                    await asyncio.sleep(0.3)
                    continue

                if self._stop_event.is_set() or self._pause_event.is_set():
                    continue

                text = str(getattr(result, "text", "") or "").strip()
                confidence = getattr(result, "confidence", None)
                status = result.status

                if text and confidence != SpeechRecognitionConfidence.REJECTED:
                    self._schedule_transcript(text)
                    continue

                err_msg = _winrt_status_message(status)
                if err_msg:
                    self._schedule_error(err_msg)
                    if status in (
                        SpeechRecognitionResultStatus.MICROPHONE_UNAVAILABLE,
                        SpeechRecognitionResultStatus.NETWORK_FAILURE,
                        SpeechRecognitionResultStatus.TOPIC_LANGUAGE_NOT_SUPPORTED,
                    ):
                        break
        finally:
            if recognizer is not None:
                try:
                    recognizer.close()
                except Exception:
                    pass


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
