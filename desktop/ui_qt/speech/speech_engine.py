"""プラットフォーム別マイク音声認識（Windows: soundcard + Google / その他: PyAudio + Google）。"""

from __future__ import annotations

import importlib.util
import sys
import threading
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

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

_COINIT_MULTITHREADED = 0
_RPC_E_CHANGED_MODE = -2147417850


def _soundcard_installed() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return importlib.util.find_spec("soundcard") is not None
    except Exception:
        return False


def _availability_message() -> tuple[bool, str]:
    if sys.platform == "win32":
        if _soundcard_installed() and _SR_AVAILABLE:
            return True, ""
        if not _SR_AVAILABLE:
            return False, "SpeechRecognition が未インストールです（pip install SpeechRecognition）"
        return False, "soundcard が未インストールです（pip install soundcard）"
    if _SR_AVAILABLE and _PYAUDIO_AVAILABLE:
        return True, ""
    if not _SR_AVAILABLE:
        return False, "SpeechRecognition が未インストールです（pip install SpeechRecognition）"
    return False, "PyAudio が未インストールです（pip install PyAudio）"


def _init_worker_com() -> bool:
    """ワーカースレッド用 COM 初期化。自前で初期化した場合のみ True（Uninitialize 要）。"""
    if sys.platform != "win32":
        return False
    try:
        import pythoncom

        hr = pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        if hr == pythoncom.RPC_E_CHANGED_MODE:
            return False
        return hr in (0, 1)
    except ImportError:
        import ctypes

        hr = ctypes.windll.ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
        if hr == _RPC_E_CHANGED_MODE:
            return False
        return hr in (0, 1)
    except Exception:
        return False


def _uninit_worker_com(should_uninit: bool) -> None:
    if not should_uninit or sys.platform != "win32":
        return
    try:
        import pythoncom

        pythoncom.CoUninitialize()
    except ImportError:
        import ctypes

        ctypes.windll.ole32.CoUninitialize()
    except Exception:
        pass


class _SpeechBridge(QObject):
    transcript_received = Signal(str)
    error = Signal(str)
    finished = Signal()


class _SpeechWorkerBase:
    def __init__(self, bridge: _SpeechBridge) -> None:
        self._bridge = bridge
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._want_listening = False

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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

    def start(self) -> None:
        if self.is_running():
            self.request_start()
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._thread_main, name="SpeechWorker", daemon=True)
        self._thread.start()

    def wait(self, timeout_ms: int) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout=max(timeout_ms, 0) / 1000)
        return not self.is_running()

    def _thread_main(self) -> None:
        com_owned = _init_worker_com()
        try:
            self._run_loop()
        except Exception as exc:
            if not self._stop_event.is_set():
                self._bridge.error.emit(f"音声認識エラー: {exc}")
        finally:
            _uninit_worker_com(com_owned)
            self._bridge.finished.emit()

    def _run_loop(self) -> None:
        raise NotImplementedError


class _SoundcardSpeechWorker(_SpeechWorkerBase):
    """Windows: soundcard で録音し Google STT で認識（要ネット）。"""

    _SAMPLE_RATE = 16000
    _BLOCK_SIZE = _SAMPLE_RATE // 10
    _SILENCE_BLOCKS = 18
    _MIN_SPEECH_BLOCKS = 3
    _SILENCE_LEVEL = 0.006
    _MAX_PHRASE_BLOCKS = _SAMPLE_RATE // _BLOCK_SIZE * 25
    _NO_SPEECH_HINT_AFTER = 10

    def _run_loop(self) -> None:
        import numpy as np
        import speech_recognition as sr

        # soundcard は COM を使うため、メインスレッドでは import しない
        import soundcard as sc

        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True

        try:
            microphone = sc.default_microphone()
        except Exception as exc:
            self._bridge.error.emit(f"マイクを開けません: {exc}")
            return

        empty_streak = 0
        with microphone.recorder(samplerate=self._SAMPLE_RATE, channels=1) as recorder:
            while not self._stop_event.is_set():
                if not self._want_listening or self._pause_event.is_set():
                    time.sleep(0.05)
                    continue

                frames: list = []
                silent_run = 0
                speech_blocks = 0
                idle_blocks = 0

                while (
                    not self._stop_event.is_set()
                    and self._want_listening
                    and not self._pause_event.is_set()
                ):
                    block = recorder.record(numframes=self._BLOCK_SIZE)
                    level = float(np.abs(block).mean())
                    if level >= self._SILENCE_LEVEL:
                        frames.append(block)
                        speech_blocks += 1
                        silent_run = 0
                        idle_blocks = 0
                    elif frames:
                        frames.append(block)
                        silent_run += 1
                        if silent_run >= self._SILENCE_BLOCKS:
                            break
                    else:
                        idle_blocks += 1
                        if idle_blocks >= 40:
                            break
                    if speech_blocks >= self._MAX_PHRASE_BLOCKS:
                        break

                if self._stop_event.is_set() or not self._want_listening or self._pause_event.is_set():
                    continue

                if not frames or speech_blocks < self._MIN_SPEECH_BLOCKS:
                    empty_streak += 1
                    if empty_streak >= self._NO_SPEECH_HINT_AFTER:
                        empty_streak = 0
                        self._bridge.error.emit(
                            "音声が検出されません。"
                            "マイクに向かって話し、区切りで少し黙ってください。"
                        )
                    continue

                empty_streak = 0
                audio = np.concatenate(frames, axis=0)
                pcm = (np.clip(audio[:, 0], -1.0, 1.0) * 32767).astype(np.int16)
                audio_data = sr.AudioData(pcm.tobytes(), self._SAMPLE_RATE, 2)
                try:
                    text = recognizer.recognize_google(audio_data, language="ja-JP")
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as exc:
                    self._bridge.error.emit(f"音声認識エラー: {exc}")
                    continue

                chunk = str(text or "").strip()
                if chunk:
                    self._bridge.transcript_received.emit(chunk)


class _SrSpeechWorker(_SpeechWorkerBase):
    def _run_loop(self) -> None:
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.8

        try:
            microphone = sr.Microphone()
        except OSError as exc:
            self._bridge.error.emit(f"マイクを開けません: {exc}")
            return

        with microphone as source:
            try:
                recognizer.adjust_for_ambient_noise(source, duration=0.4)
            except Exception:
                pass

            while not self._stop_event.is_set():
                if not self._want_listening or self._pause_event.is_set():
                    time.sleep(0.05)
                    continue
                try:
                    audio = recognizer.listen(source, timeout=0.8, phrase_time_limit=20)
                except sr.WaitTimeoutError:
                    continue
                except Exception as exc:
                    if not self._stop_event.is_set():
                        self._bridge.error.emit(f"録音エラー: {exc}")
                    break

                if self._stop_event.is_set() or self._pause_event.is_set():
                    continue

                try:
                    text = recognizer.recognize_google(audio, language="ja-JP")
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as exc:
                    self._bridge.error.emit(f"音声認識エラー: {exc}")
                    continue

                chunk = str(text or "").strip()
                if chunk:
                    self._bridge.transcript_received.emit(chunk)


def _create_worker(bridge: _SpeechBridge) -> _SpeechWorkerBase:
    if sys.platform == "win32" and _soundcard_installed():
        return _SoundcardSpeechWorker(bridge)
    return _SrSpeechWorker(bridge)


class SpeechEngine(QWidget):
    """マイク音声認識（Google STT・要ネット接続）。"""

    transcript_received = Signal(str)
    error = Signal(str)
    listening_changed = Signal(bool)
    availability_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = _SpeechBridge(self)
        self._bridge.transcript_received.connect(self.transcript_received)
        self._bridge.error.connect(self._on_worker_error)
        self._bridge.finished.connect(self._on_worker_finished)
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
        if self._worker is not None and self._worker.is_running():
            self._worker.request_start()
            self._set_listening(True)
            return
        self._spawn_worker()

    def _spawn_worker(self) -> None:
        self._worker = _create_worker(self._bridge)
        self._worker.request_start()
        self._worker.start()
        self._set_listening(True)

    def stop(self) -> None:
        self._want_listening = False
        self._paused = False
        if self._worker is not None:
            self._worker.request_stop()
            self._worker.wait(5000)
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
        if self._worker is not None and self._worker.is_running():
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
