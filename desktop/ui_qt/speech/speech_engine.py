"""非表示 QWebEngineView で Web Speech API を利用する音声入力エンジン。"""

from __future__ import annotations

from PySide6.QtCore import QUrl, Signal, Slot, QTimer
from PySide6.QtWidgets import QWidget

from ui_qt.speech.speech_bridge import SpeechRecognitionBridge

_WEBENGINE_AVAILABLE = False
try:
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView

    _WEBENGINE_AVAILABLE = True
except ImportError:
    QWebChannel = None  # type: ignore[assignment,misc]
    QWebEnginePage = None  # type: ignore[assignment,misc]
    QWebEngineProfile = None  # type: ignore[assignment,misc]
    QWebEngineView = None  # type: ignore[assignment,misc]

_SPEECH_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Speech</title>
</head>
<body>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
(function () {
  var bridge = null;
  var rec = null;
  var listening = false;

  function postError(msg) {
    if (bridge) bridge.onError(String(msg || "error"));
  }

  function ensureRecognition() {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      postError("Web Speech API が利用できません");
      return null;
    }
    var r = new SR();
    r.lang = "ja-JP";
    r.interimResults = false;
    r.continuous = true;
    r.maxAlternatives = 1;
    r.onresult = function (e) {
      var text = "";
      for (var i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) {
          text += e.results[i][0].transcript;
        }
      }
      if (text && bridge) bridge.onFinalText(text);
    };
    r.onerror = function (e) {
      postError(e.error || "recognition-error");
    };
    r.onend = function () {
      listening = false;
      if (bridge) bridge.onEnded();
    };
    return r;
  }

  window.speechStart = function () {
    if (listening) return;
    rec = ensureRecognition();
    if (!rec) return;
    try {
      rec.start();
      listening = true;
    } catch (err) {
      postError(err.message || "start-failed");
    }
  };

  window.speechStop = function () {
    if (!rec || !listening) return;
    try {
      rec.stop();
    } catch (err) {
      postError(err.message || "stop-failed");
    }
    listening = false;
  };

  new QWebChannel(qt.webChannelTransport, function (channel) {
    bridge = channel.objects.bridge;
    bridge.onReady();
  });
})();
</script>
</body>
</html>
"""


if _WEBENGINE_AVAILABLE:

    class _SpeechWebPage(QWebEnginePage):
        def __init__(self, profile: QWebEngineProfile, parent=None) -> None:
            super().__init__(profile, parent)
            try:
                from PySide6.QtWebEngineCore import QWebEnginePermission

                self.permissionRequested.connect(self._on_permission)  # type: ignore[attr-defined]
                self._PermissionType = QWebEnginePermission.PermissionType
            except (ImportError, AttributeError):
                self._PermissionType = None

        def _on_permission(self, permission) -> None:
            if self._PermissionType is None:
                return
            if permission.permissionType() == self._PermissionType.MediaAudioCapture:
                permission.grant()


class SpeechEngine(QWidget):
    """Chromium Web Speech API を使った音声認識（Google 無料 STT・要ネット）。"""

    transcript_received = Signal(str)
    error = Signal(str)
    listening_changed = Signal(bool)
    availability_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = SpeechRecognitionBridge(self)
        self._bridge.ready.connect(self._on_bridge_ready)
        self._bridge.final_text.connect(self._on_final_text)
        self._bridge.error.connect(self._on_error)
        self._bridge.ended.connect(self._on_ended)

        self._view = None
        self._channel = None
        self._ready = False
        self._listening = False
        self._start_pending = False
        self._want_listening = False

        if _WEBENGINE_AVAILABLE:
            self._init_webview()
        else:
            self.availability_changed.emit(False)

    @staticmethod
    def is_available() -> bool:
        return _WEBENGINE_AVAILABLE

    def is_listening(self) -> bool:
        return self._listening

    def _init_webview(self) -> None:
        profile = QWebEngineProfile.defaultProfile()
        self._view = QWebEngineView(self)
        page = _SpeechWebPage(profile, self._view)
        self._view.setPage(page)
        self._view.setFixedSize(1, 1)
        self._view.hide()

        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)
        page.setWebChannel(self._channel)

        page.loadFinished.connect(self._on_load_finished)
        base = QUrl("https://local.automatedsaiten/speech/")
        page.setHtml(_SPEECH_HTML, base)
        self.availability_changed.emit(True)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self.error.emit("音声認識ページの読み込みに失敗しました")
            self.availability_changed.emit(False)

    @Slot()
    def _on_bridge_ready(self) -> None:
        self._ready = True
        if self._start_pending or self._want_listening:
            self._start_pending = False
            if not self._listening:
                self._begin_recognition()

    def _run_js(self, script: str) -> None:
        if self._view is None:
            return
        page = self._view.page()
        if page is not None:
            page.runJavaScript(script)

    def start(self) -> None:
        if not _WEBENGINE_AVAILABLE or self._view is None:
            self.error.emit(
                "PySide6-Addons が未インストールです。"
                " pip install PySide6-Addons を実行してください。"
            )
            return
        self._want_listening = True
        if self._listening:
            return
        if not self._ready:
            self._start_pending = True
            return
        self._begin_recognition()

    def _begin_recognition(self) -> None:
        self._listening = True
        self.listening_changed.emit(True)
        self._run_js("window.speechStart && window.speechStart();")

    def stop(self) -> None:
        self._want_listening = False
        self._start_pending = False
        self._run_js("window.speechStop && window.speechStop();")
        self._set_listening(False)

    def _set_listening(self, on: bool) -> None:
        if self._listening == on:
            return
        self._listening = on
        self.listening_changed.emit(on)

    @Slot(str)
    def _on_final_text(self, text: str) -> None:
        chunk = str(text or "").strip()
        if chunk:
            self.transcript_received.emit(chunk)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        msg = str(message or "").strip()
        if msg in ("aborted", "no-speech"):
            return
        self._set_listening(False)
        if msg == "not-allowed":
            self.error.emit("マイクの使用が許可されていません")
            return
        if msg == "network":
            self.error.emit("音声認識にはネットワーク接続が必要です")
            return
        if msg == "service-not-allowed":
            self.error.emit("Web Speech API が利用できません（Chromium 設定を確認）")
            return
        self.error.emit(f"音声認識エラー: {msg}")

    @Slot()
    def _on_ended(self) -> None:
        self._set_listening(False)
        if self._want_listening and self._ready:
            QTimer.singleShot(120, self._restart_if_wanted)

    def _restart_if_wanted(self) -> None:
        if not self._want_listening or self._listening:
            return
        self._begin_recognition()
