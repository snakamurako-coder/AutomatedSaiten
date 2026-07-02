"""詳細設定ダイアログ（API キー・OCR エンジン等）。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from config import CONFIG_PATH, load_config, save_config
from services.gemini_rubric import test_gemini_api_key
from services.ocr import test_vision_api_key
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

        self.ocr_engine_var = tk.StringVar(value=self._loaded.get("ocr_engine") or "tesseract")
        self.vision_key_var = tk.StringVar(value=self._loaded.get("vision_api_key") or "")
        self.gemini_key_var = tk.StringVar(value=self._loaded.get("gemini_api_key") or "")
        self.tesseract_cmd_var = tk.StringVar(value=self._loaded.get("tesseract_cmd") or "")
        self.orientation_var = tk.StringVar(value=self._loaded.get("default_orientation") or "landscape")
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
            text="Tesseract（ローカル・無料）",
            variable=self.ocr_engine_var,
            value="tesseract",
        ).pack(anchor="w")
        ttk.Radiobutton(
            engine_row,
            text="Google Vision API（クラウド）",
            variable=self.ocr_engine_var,
            value="vision",
        ).pack(anchor="w")

        ttk.Label(ocr_frame, text="Tesseract 実行ファイル").grid(row=1, column=0, sticky="w", pady=2)
        tess_row = ttk.Frame(ocr_frame)
        tess_row.grid(row=1, column=1, sticky="ew", pady=2)
        ocr_frame.columnconfigure(1, weight=1)
        ttk.Entry(tess_row, textvariable=self.tesseract_cmd_var, width=42).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(tess_row, text="参照…", command=self._browse_tesseract).pack(side="left", padx=4)
        ttk.Label(
            ocr_frame,
            text="未指定の場合は PATH 上の tesseract を使用します。",
            style="Caption.TLabel",
        ).grid(row=2, column=1, sticky="w")

        api_frame = ttk.LabelFrame(body, text="API キー", padding=8)
        api_frame.pack(fill="x", pady=(0, 8))
        api_frame.columnconfigure(1, weight=1)

        ttk.Label(api_frame, text="Vision API キー").grid(row=0, column=0, sticky="w", pady=4)
        vision_row = ttk.Frame(api_frame)
        vision_row.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Entry(vision_row, textvariable=self.vision_key_var, show="•", width=42).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(vision_row, text="接続確認", command=self._test_vision).pack(side="left", padx=4)

        ttk.Label(api_frame, text="Gemini API キー").grid(row=1, column=0, sticky="w", pady=4)
        gemini_row = ttk.Frame(api_frame)
        gemini_row.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Entry(gemini_row, textvariable=self.gemini_key_var, show="•", width=42).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(gemini_row, text="接続確認", command=self._test_gemini).pack(side="left", padx=4)

        ttk.Label(
            api_frame,
            text="Vision: ③ テキスト化 / Gemini: ④ AI原案 で使用します。",
            style="Caption.TLabel",
        ).grid(row=2, column=1, sticky="w")

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

    def _browse_tesseract(self) -> None:
        path = filedialog.askopenfilename(
            title="Tesseract 実行ファイルを選択",
            filetypes=[("実行ファイル", "*.exe"), ("すべて", "*.*")],
        )
        if path:
            self.tesseract_cmd_var.set(path)

    def _collect_config(self) -> dict:
        engine = (self.ocr_engine_var.get() or "tesseract").strip().lower()
        if engine not in ("tesseract", "vision"):
            engine = "tesseract"
        orientation = (self.orientation_var.get() or "landscape").strip().lower()
        if orientation not in ("landscape", "portrait"):
            orientation = "landscape"
        return {
            "vision_api_key": self.vision_key_var.get().strip(),
            "ocr_engine": engine,
            "default_orientation": orientation,
            "tesseract_cmd": self.tesseract_cmd_var.get().strip(),
            "gemini_api_key": self.gemini_key_var.get().strip(),
        }

    def _on_save(self) -> None:
        cfg = self._collect_config()
        if cfg["ocr_engine"] == "vision" and not cfg["vision_api_key"]:
            messagebox.showwarning(
                "設定エラー",
                "OCR エンジンが Vision API の場合、Vision API キーを入力してください。",
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
