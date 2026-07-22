"""詳細設定ダイアログ（API キー・OCR エンジン等）。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from config import CONFIG_PATH, load_config, save_config
from services.gemini_rubric import test_gemini_api_key
from services.ocr import test_openai_api_key, test_vision_api_key
from ui.theme import apply_theme


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, on_saved: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.title("詳細設定")
        self.geometry("580x540")
        self.minsize(500, 440)
        apply_theme(self)
        self.transient(parent)
        self.grab_set()

        self._on_saved = on_saved
        self._loaded = load_config()

        engine = str(self._loaded.get("ocr_engine") or "openai").strip().lower()
        if engine == "tesseract":
            engine = "openai"
        self.ocr_engine_var = tk.StringVar(value=engine)
        self.vision_key_var = tk.StringVar(value=self._loaded.get("vision_api_key") or "")
        self.openai_key_var = tk.StringVar(value=self._loaded.get("openai_api_key") or "")
        self.gemini_key_var = tk.StringVar(value=self._loaded.get("gemini_api_key") or "")
        self.orientation_var = tk.StringVar(value=self._loaded.get("default_orientation") or "landscape")
        self.palm_rejection_var = tk.BooleanVar(
            value=bool(self._loaded.get("stylus_palm_rejection", True))
        )
        self.status_var = tk.StringVar(value=f"設定ファイル: {CONFIG_PATH}")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        ttk.Label(body, text="詳細設定", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text="API キーは desktop/config.json に保存されます（Git には含めないでください）。",
            style="Muted.TLabel",
            wraplength=500,
        ).pack(anchor="w", pady=(6, 14))

        ocr_frame = ttk.LabelFrame(body, text="OCR", padding=8)
        ocr_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(ocr_frame, text="OCR エンジン").grid(row=0, column=0, sticky="w", pady=2)
        engine_row = ttk.Frame(ocr_frame)
        engine_row.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Radiobutton(
            engine_row,
            text="OpenAI API（クラウド・手書き向け）",
            variable=self.ocr_engine_var,
            value="openai",
        ).pack(anchor="w")
        ttk.Label(
            engine_row,
            text="文脈からスペルミスを補正し、不自然な改行による文章崩れも防げる。",
            style="Caption.TLabel",
            wraplength=420,
        ).pack(anchor="w", padx=(22, 0), pady=(0, 6))
        ttk.Radiobutton(
            engine_row,
            text="Google Vision API（クラウド）",
            variable=self.ocr_engine_var,
            value="vision",
        ).pack(anchor="w")
        ttk.Label(
            engine_row,
            text="スペルミスも忠実に拾う反面、改行位置の判断ミスで文章が崩れることがある。",
            style="Caption.TLabel",
            wraplength=420,
        ).pack(anchor="w", padx=(22, 0))

        api_frame = ttk.LabelFrame(body, text="API キー", padding=8)
        api_frame.pack(fill="x", pady=(0, 8))
        api_frame.columnconfigure(1, weight=1)

        ttk.Label(api_frame, text="OpenAI API キー").grid(row=0, column=0, sticky="w", pady=4)
        openai_row = ttk.Frame(api_frame)
        openai_row.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Entry(openai_row, textvariable=self.openai_key_var, show="•", width=42).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(openai_row, text="接続確認", command=self._test_openai).pack(side="left", padx=4)

        ttk.Label(api_frame, text="Vision API キー").grid(row=1, column=0, sticky="w", pady=4)
        vision_row = ttk.Frame(api_frame)
        vision_row.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Entry(vision_row, textvariable=self.vision_key_var, show="•", width=42).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(vision_row, text="接続確認", command=self._test_vision).pack(side="left", padx=4)

        ttk.Label(api_frame, text="Gemini API キー").grid(row=2, column=0, sticky="w", pady=4)
        gemini_row = ttk.Frame(api_frame)
        gemini_row.grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Entry(gemini_row, textvariable=self.gemini_key_var, show="•", width=42).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(gemini_row, text="接続確認", command=self._test_gemini).pack(side="left", padx=4)

        ttk.Label(
            api_frame,
            text="OpenAI / Vision: ③ テキスト化 / Gemini: ④ AI原案 で使用します。",
            style="Caption.TLabel",
        ).grid(row=3, column=1, sticky="w")

        stylus_frame = ttk.LabelFrame(body, text="スタイラス", padding=8)
        stylus_frame.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(
            stylus_frame,
            text="パームリジェクション（指・手のひらを無視）",
            variable=self.palm_rejection_var,
        ).pack(anchor="w")

        misc_frame = ttk.LabelFrame(body, text="その他", padding=8)
        misc_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(misc_frame, text="用紙向き（デフォルト）").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(
            misc_frame,
            textvariable=self.orientation_var,
            values=["landscape", "portrait"],
            state="readonly",
            width=16,
        ).grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(body, textvariable=self.status_var, style="Caption.TLabel", wraplength=500).pack(
            anchor="w", pady=(6, 8)
        )

        btn_row = ttk.Frame(body)
        btn_row.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_row, text="保存", style="Primary.TButton", command=self._on_save).pack(
            side="right", padx=4
        )
        ttk.Button(btn_row, text="キャンセル", command=self._on_cancel).pack(side="right")

    def _collect_config(self) -> dict:
        engine = (self.ocr_engine_var.get() or "openai").strip().lower()
        if engine == "tesseract":
            engine = "openai"
        if engine not in ("openai", "vision"):
            engine = "openai"
        orientation = (self.orientation_var.get() or "landscape").strip().lower()
        if orientation not in ("landscape", "portrait"):
            orientation = "landscape"
        return {
            "vision_api_key": self.vision_key_var.get().strip(),
            "openai_api_key": self.openai_key_var.get().strip(),
            "ocr_engine": engine,
            "default_orientation": orientation,
            "gemini_api_key": self.gemini_key_var.get().strip(),
        }

    def _on_save(self) -> None:
        cfg = load_config()
        cfg.update(self._collect_config())
        cfg["stylus_palm_rejection"] = bool(self.palm_rejection_var.get())
        if cfg["ocr_engine"] == "vision" and not cfg["vision_api_key"]:
            messagebox.showwarning(
                "設定エラー",
                "OCR エンジンが Vision API の場合、Vision API キーを入力してください。",
                parent=self,
            )
            return
        if cfg["ocr_engine"] == "openai" and not cfg["openai_api_key"]:
            messagebox.showwarning(
                "設定エラー",
                "OCR エンジンが OpenAI API の場合、OpenAI API キーを入力してください。",
                parent=self,
            )
            return
        try:
            save_config(cfg)
        except OSError as e:
            messagebox.showerror("保存失敗", str(e), parent=self)
            return
        self.status_var.set("保存しました。")
        if self._on_saved:
            self._on_saved()
        messagebox.showinfo("保存完了", "詳細設定を保存しました。", parent=self)
        self.destroy()

    def _on_cancel(self) -> None:
        self.destroy()

    def _run_api_test(self, label: str, worker: Callable[[], str]) -> None:
        self.status_var.set(f"{label} を確認中…")

        def task() -> None:
            try:
                msg = worker()
                self.after(0, lambda: self._show_test_result(label, msg, None))
            except Exception as e:
                self.after(0, lambda: self._show_test_result(label, str(e), e))

        threading.Thread(target=task, daemon=True).start()

    def _show_test_result(self, label: str, message: str, error: Exception | None) -> None:
        self.status_var.set(message)
        if error:
            messagebox.showerror(f"{label} — 失敗", message, parent=self)
        else:
            messagebox.showinfo(f"{label} — OK", message, parent=self)

    def _test_openai(self) -> None:
        key = self.openai_key_var.get().strip()
        if not key:
            messagebox.showwarning("未入力", "OpenAI API キーを入力してください。", parent=self)
            return
        self._run_api_test("OpenAI API", lambda: test_openai_api_key(key))

    def _test_vision(self) -> None:
        key = self.vision_key_var.get().strip()
        if not key:
            messagebox.showwarning("未入力", "Vision API キーを入力してください。", parent=self)
            return
        self._run_api_test("Vision API", lambda: test_vision_api_key(key))

    def _test_gemini(self) -> None:
        key = self.gemini_key_var.get().strip()
        if not key:
            messagebox.showwarning("未入力", "Gemini API キーを入力してください。", parent=self)
            return
        self._run_api_test("Gemini API", lambda: test_gemini_api_key(key))


def open_settings_dialog(parent: tk.Misc, on_saved: Callable[[], None] | None = None) -> None:
    SettingsDialog(parent, on_saved=on_saved)
