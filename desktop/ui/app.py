"""メインウィンドウとステップ画面。"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from constants import DESKTOP_READY_STEPS, STEPS
from models.database import init_db
from models.criteria_repo import merge_unique_with_criteria, save_grading_criteria
from models.text_processing import (
    apply_deemed_scoring_to_field,
    apply_text_replacements_to_field,
    get_deemed_draft,
    get_ocr_replacements,
    save_deemed_scoring_draft,
    save_ocr_replacements,
)
from models.test_repo import (
    create_test,
    export_results_to_excel,
    get_answer_fields,
    get_result_preview,
    get_test_info,
    list_tests,
    save_answer_fields,
    save_model_answer_image,
    save_points,
    save_student_folder,
    set_active_test,
)
from services.batch_processor import run_batch_ocr
from services.gemini_rubric import generate_rubric_with_gemini
from services.grading import execute_grading, get_summary_data
from services.image_warp import warp_image_from_path
from services.ocr import check_ocr_config
from services.work_queue import build_ocr_work_queue
from ui.region_editor import AnswerRegionEditor
from ui.settings_dialog import open_settings_dialog
from ui.step4_outlier import Step4OutlierMixin
from ui.theme import COLORS, apply_theme, style_listbox, style_text


class AutomatedSaitenApp(Step4OutlierMixin, tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("模範解答ベース自動採点システム（PC版）")
        self.geometry("1200x780")
        self.minsize(960, 640)
        apply_theme(self)

        init_db()
        self.active_test_id: str | None = None
        self.current_step = 0
        self._field_rows: list[dict[str, Any]] = []
        self._criteria_rows: list[dict[str, Any]] = []
        self._ocr_replace_rows: list[dict[str, Any]] = []
        self._init_step4_state()

        self._build_layout()
        self._refresh_ocr_status()
        self._load_step(0)
        self._refresh_test_list()

    def _build_layout(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        nav_wrap = tk.Frame(self, bg=COLORS["sidebar_border"], width=252)
        nav_wrap.grid(row=0, column=0, sticky="ns")
        nav_wrap.grid_propagate(False)

        nav = ttk.Frame(nav_wrap, style="Sidebar.TFrame", padding=(14, 18))
        nav.pack(fill="both", expand=True, padx=(0, 1))

        ttk.Label(nav, text="自動採点", style="SidebarTitle.TLabel").pack(anchor="w")
        ttk.Label(nav, text="PC版", style="SidebarMuted.TLabel").pack(anchor="w", pady=(0, 14))

        self.step_buttons: dict[int, ttk.Button] = {}
        for step in STEPS:
            sid = step["id"]
            enabled = sid in DESKTOP_READY_STEPS
            label = step["label"] + ("" if enabled else " …準備中")
            btn_style = "Nav.TButton" if enabled else "NavDisabled.TButton"
            btn = ttk.Button(
                nav,
                text=label,
                style=btn_style,
                command=lambda s=sid: self._load_step(s) if s in DESKTOP_READY_STEPS else None,
                state="normal" if enabled else "disabled",
            )
            btn.pack(fill="x", pady=3)
            self.step_buttons[sid] = btn

        ttk.Separator(nav, orient="horizontal").pack(fill="x", pady=14)
        ttk.Button(nav, text="詳細設定", style="Nav.TButton", command=self._open_settings).pack(
            fill="x", pady=3
        )
        self.ocr_status_var = tk.StringVar(value="")
        ttk.Label(nav, textvariable=self.ocr_status_var, style="SidebarMuted.TLabel").pack(
            anchor="w", pady=(12, 0)
        )

        self.content = ttk.Frame(self, style="Content.TFrame", padding=(20, 18))
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        self.frames: dict[int, ttk.Frame] = {}
        for step in STEPS:
            frame = ttk.Frame(self.content)
            frame.grid(row=0, column=0, sticky="nsew")
            self.frames[step["id"]] = frame
            if step["id"] not in DESKTOP_READY_STEPS:
                ttk.Label(
                    frame,
                    text=f"{step['label']} — このステップは今後のバージョンで追加予定です。",
                    style="Muted.TLabel",
                ).pack(anchor="w")

        self._build_step0()
        self._build_step1()
        self._build_step2()
        self._build_step3()
        self._build_step4()
        self._build_step5()

    def _show_frame(self, step_id: int) -> None:
        self.frames[step_id].tkraise()
        for sid, btn in self.step_buttons.items():
            if sid not in DESKTOP_READY_STEPS:
                continue
            btn.configure(style="NavActive.TButton" if sid == step_id else "Nav.TButton")
        self.current_step = step_id

    def _require_active_test(self) -> bool:
        if not self.active_test_id:
            messagebox.showwarning("テスト未選択", "先にテストを作成または選択してください。")
            return False
        return True

    def _refresh_ocr_status(self) -> None:
        info = check_ocr_config()
        self.ocr_status_var.set(info.get("message", ""))

    def _open_settings(self) -> None:
        open_settings_dialog(self, on_saved=self._refresh_ocr_status)

    def _refresh_test_list(self) -> None:
        tests = list_tests()
        self.test_listbox.delete(0, tk.END)
        for t in tests:
            mark = "● " if t.get("isActive") else "  "
            self.test_listbox.insert(
                tk.END,
                f"{mark}{t['testName']}  [{t['status']}] step={t['currentStep']}",
            )
        self.test_listbox._tests = tests  # type: ignore[attr-defined]

        if tests:
            active = next((t for t in tests if t.get("isActive")), tests[0])
            self.active_test_id = active["testSsId"]
            self.active_test_label.config(text=f"選択中: {active['testName']}")
        else:
            self.active_test_id = None
            self.active_test_label.config(text="選択中: （なし）")

    def _load_step(self, step_id: int) -> None:
        self._show_frame(step_id)
        if step_id == 1:
            self._reload_fields()
        elif step_id == 2:
            self._reload_points()
        elif step_id == 3:
            self._reload_ocr_panel()
        elif step_id == 4:
            self._reload_criteria_panel()
        elif step_id == 5:
            self._reload_summary_panel()

    # --- Step 0 ---
    def _build_step0(self) -> None:
        f = self.frames[0]
        ttk.Label(f, text="⓪ テスト作成", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")

        form = ttk.LabelFrame(f, text="新規テスト", padding=12)
        form.grid(row=1, column=0, sticky="nw", pady=8)

        ttk.Label(form, text="テスト名").grid(row=0, column=0, sticky="w")
        self.new_test_name = ttk.Entry(form, width=32)
        self.new_test_name.grid(row=0, column=1, padx=4, pady=2)

        ttk.Label(form, text="科目名").grid(row=1, column=0, sticky="w")
        self.new_test_subject = ttk.Entry(form, width=32)
        self.new_test_subject.grid(row=1, column=1, padx=4, pady=2)

        ttk.Label(form, text="実施日時").grid(row=2, column=0, sticky="w")
        self.new_test_datetime = ttk.Entry(form, width=32)
        self.new_test_datetime.grid(row=2, column=1, padx=4, pady=2)

        ttk.Button(form, text="テストを作成", style="Primary.TButton", command=self._on_create_test).grid(
            row=3, column=0, columnspan=2, pady=(10, 0)
        )

        list_frame = ttk.LabelFrame(f, text="テスト一覧", padding=12)
        list_frame.grid(row=1, column=1, sticky="nsew", padx=(16, 0), pady=8)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(1, weight=1)

        self.test_listbox = tk.Listbox(list_frame, height=14)
        style_listbox(self.test_listbox)
        self.test_listbox.pack(fill="both", expand=True)
        btns = ttk.Frame(list_frame)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="選択", command=self._on_select_test).pack(side="left", padx=2)
        ttk.Button(btns, text="更新", command=self._refresh_test_list).pack(side="left", padx=2)

        self.active_test_label = ttk.Label(f, text="選択中: （なし）", style="Muted.TLabel")
        self.active_test_label.grid(row=2, column=0, columnspan=2, sticky="w")

    def _on_create_test(self) -> None:
        name = self.new_test_name.get().strip()
        if not name:
            messagebox.showerror("入力エラー", "テスト名を入力してください。")
            return
        try:
            res = create_test(name, self.new_test_subject.get(), self.new_test_datetime.get())
            self.active_test_id = res["testSsId"]
            messagebox.showinfo("作成完了", f"テスト「{name}」を作成しました。")
            self._refresh_test_list()
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    def _on_select_test(self) -> None:
        sel = self.test_listbox.curselection()
        if not sel:
            return
        tests = getattr(self.test_listbox, "_tests", [])
        if sel[0] >= len(tests):
            return
        test = tests[sel[0]]
        set_active_test(test["testSsId"])
        self.active_test_id = test["testSsId"]
        self.active_test_label.config(text=f"選択中: {test['testName']}")
        self._refresh_test_list()

    # --- Step 1 ---
    def _build_step1(self) -> None:
        f = self.frames[1]
        f.columnconfigure(0, weight=1)

        ttk.Label(f, text="① 回答欄設定（模範解答）", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            f,
            text="画像をドロップするか「画像を開く」で模範解答を読み込み、Canvas 上をドラッグして記述欄を指定します。",
            style="Muted.TLabel",
            wraplength=900,
        ).grid(row=1, column=0, sticky="w", pady=(6, 12))

        toolbar = ttk.Frame(f)
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(toolbar, text="用紙方向").pack(side="left")
        self.step1_orientation_var = tk.StringVar(value="landscape")
        ttk.Combobox(
            toolbar,
            textvariable=self.step1_orientation_var,
            values=["landscape", "portrait"],
            state="readonly",
            width=10,
        ).pack(side="left", padx=(4, 12))
        ttk.Label(toolbar, text="二値化").pack(side="left")
        self.step1_thresh_var = tk.IntVar(value=128)
        ttk.Scale(
            toolbar,
            from_=0,
            to=255,
            variable=self.step1_thresh_var,
            orient="horizontal",
            length=120,
        ).pack(side="left", padx=(4, 12))
        ttk.Button(toolbar, text="画像を開く", command=self._on_open_model_file).pack(
            side="left", padx=2
        )
        ttk.Button(toolbar, text="記述欄を保存", style="Primary.TButton", command=self._on_save_fields).pack(
            side="left", padx=6
        )
        ttk.Button(toolbar, text="選択欄を削除", command=self._on_delete_selected_field).pack(
            side="left", padx=2
        )
        ttk.Button(toolbar, text="再読込", command=self._reload_fields).pack(side="left", padx=2)

        body = ttk.Frame(f)
        body.grid(row=3, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        f.rowconfigure(3, weight=1)

        self.region_editor = AnswerRegionEditor(body, on_change=self._refresh_field_list_panel)
        self.region_editor.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        side = ttk.LabelFrame(body, text="記述欄一覧", padding=6, width=220)
        side.grid(row=0, column=1, sticky="ns")
        side.grid_propagate(False)
        self.field_list_inner = ttk.Frame(side)
        self.field_list_inner.pack(fill="both", expand=True)
        self.step1_status_var = tk.StringVar(value="画像をドロップするか「画像を開く」で開始")
        ttk.Label(f, textvariable=self.step1_status_var, style="Caption.TLabel").grid(
            row=4, column=0, sticky="w", pady=(8, 0)
        )

        self._hook_file_drop(f, self._on_drop_files)
        self._hook_file_drop(self.region_editor, self._on_drop_files)
        self._hook_file_drop(self.region_editor._canvas, self._on_drop_files)

    def _hook_file_drop(self, widget: tk.Misc, callback) -> None:
        try:
            import windnd

            def handler(files: list) -> None:
                paths = [self._decode_drop_path(f) for f in files]
                callback(paths)

            windnd.hook_dropfiles(widget, func=handler)
        except ImportError:
            pass

    @staticmethod
    def _decode_drop_path(raw) -> str:
        if isinstance(raw, bytes):
            for enc in ("utf-8", "mbcs"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    @staticmethod
    def _is_image_path(path: str) -> bool:
        return Path(path).suffix.lower() in {
            ".jpg",
            ".jpeg",
            ".png",
            ".bmp",
            ".webp",
            ".tif",
            ".tiff",
        }

    def _on_drop_files(self, paths: list[str]) -> None:
        images = [p for p in paths if self._is_image_path(p)]
        if not images:
            messagebox.showwarning("ドロップ", "画像ファイル（jpg/png 等）をドロップしてください。")
            return
        self._load_model_from_path(images[0])

    def _on_open_model_file(self) -> None:
        path = filedialog.askopenfilename(
            title="模範解答画像を選択",
            filetypes=[
                ("画像", "*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff"),
                ("すべて", "*.*"),
            ],
        )
        if path:
            self._load_model_from_path(path)

    def _load_model_from_path(self, path: str) -> None:
        if not self._require_active_test():
            return
        self.step1_status_var.set("読込・補正中…")

        orientation = self.step1_orientation_var.get() or "landscape"
        thresh = int(self.step1_thresh_var.get() or 128)
        test_id = self.active_test_id
        existing_fields = list(self._field_rows)
        ref_w = ref_h = 0
        try:
            info = get_test_info(test_id)
            ref_w = int(info.get("refWidth") or 0)
            ref_h = int(info.get("refHeight") or 0)
        except Exception:
            pass

        def task() -> None:
            try:
                warped = warp_image_from_path(path, orientation, thresh)  # type: ignore[arg-type]
                h, w = warped.shape[:2]
                keep_fields = (
                    existing_fields if ref_w == w and ref_h == h and existing_fields else []
                )
                save_model_answer_image(test_id, warped)
                self.after(
                    0, lambda w=warped, ef=keep_fields: self._apply_warped_model(w, ef, None)
                )
            except Exception as e:
                self.after(
                    0, lambda err=e, ef=existing_fields: self._apply_warped_model(None, ef, err)
                )

        threading.Thread(target=task, daemon=True).start()

    def _apply_warped_model(
        self,
        warped,
        existing_fields: list[dict[str, Any]],
        error: Exception | None,
    ) -> None:
        if error:
            self.step1_status_var.set("")
            messagebox.showerror("読込エラー", str(error))
            return
        self.region_editor.set_image(warped)
        if existing_fields:
            self.region_editor.set_regions(existing_fields)
            self._field_rows = self.region_editor.get_regions()
        else:
            self._field_rows = []
        h, w = warped.shape[:2]
        self.step1_status_var.set(f"模範解答を読み込みました（{w}×{h}）")
        self._refresh_field_list_panel()

    def _reload_fields(self) -> None:
        if not self._require_active_test():
            return
        self._field_rows = get_answer_fields(self.active_test_id)
        info = get_test_info(self.active_test_id)
        model_path = info.get("modelAnswerPath") or ""
        if model_path and Path(model_path).exists():
            try:
                self.region_editor.load_image_from_path(model_path)
                self.region_editor.set_regions(self._field_rows)
                self._field_rows = self.region_editor.get_regions()
                self.step1_status_var.set(f"保存済み模範解答を表示（{info.get('refWidth')}×{info.get('refHeight')}）")
            except Exception as e:
                self.step1_status_var.set(f"模範解答の表示に失敗: {e}")
        else:
            self.step1_status_var.set("模範解答未登録 — 画像をドロップまたは開いてください")
        self._refresh_field_list_panel()

    def _refresh_field_list_panel(self) -> None:
        for child in self.field_list_inner.winfo_children():
            child.destroy()
        self._field_rows = self.region_editor.get_regions()
        if not self._field_rows:
            ttk.Label(
                self.field_list_inner,
                text="Canvas 上をドラッグして\n記述欄を追加",
                style="Muted.TLabel",
                justify="center",
            ).pack(pady=16)
            return
        for idx, row in enumerate(self._field_rows):
            item = ttk.Frame(self.field_list_inner, padding=4)
            item.pack(fill="x", pady=2)
            ttk.Label(
                item,
                text=f"{row['displayName']}\n{row['width']}×{row['height']}",
                style="Body.TLabel",
            ).pack(anchor="w")
            lang_row = ttk.Frame(item)
            lang_row.pack(anchor="w", pady=(4, 0))
            ttk.Label(lang_row, text="OCR", style="Caption.TLabel").pack(side="left")
            lang_var = tk.StringVar(value=row.get("ocrLang") or "en")

            def make_lang_handler(i: int, var: tk.StringVar):
                def _on_change(_event=None) -> None:
                    self.region_editor.set_region_ocr_lang(i, var.get())
                    self._field_rows = self.region_editor.get_regions()

                return _on_change

            combo = ttk.Combobox(
                lang_row,
                textvariable=lang_var,
                values=["ja", "en"],
                state="readonly",
                width=6,
            )
            combo.pack(side="left", padx=4)
            combo.bind("<<ComboboxSelected>>", make_lang_handler(idx, lang_var))
            ttk.Button(
                item,
                text="選択",
                width=6,
                command=lambda i=idx: self._select_field_from_list(i),
            ).pack(anchor="e", pady=(2, 0))

    def _select_field_from_list(self, index: int) -> None:
        self.region_editor.select_region(index)
        self._refresh_field_list_panel()

    def _on_delete_selected_field(self) -> None:
        self.region_editor.delete_selected()
        self._field_rows = self.region_editor.get_regions()
        self._refresh_field_list_panel()

    def _on_save_fields(self) -> None:
        if not self._require_active_test():
            return
        self._field_rows = self.region_editor.get_regions()
        if not self._field_rows:
            messagebox.showerror("保存エラー", "記述欄がありません。Canvas 上で矩形を指定してください。")
            return
        if not self.region_editor.has_image():
            messagebox.showerror("保存エラー", "模範解答画像が読み込まれていません。")
            return
        try:
            warped = self.region_editor.get_image_bgr()
            if warped is not None:
                save_model_answer_image(self.active_test_id, warped)
            save_answer_fields(self.active_test_id, self._field_rows)
            messagebox.showinfo("保存完了", "模範解答と記述欄を保存しました。")
            self.step1_status_var.set("記述欄を保存しました")
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    # --- Step 2 ---
    def _build_step2(self) -> None:
        f = self.frames[2]
        ttk.Label(f, text="② 配点決定", style="Title.TLabel").pack(anchor="w")
        self.points_tree = ttk.Treeview(f, columns=("fieldId", "displayName", "points"), show="headings", height=12)
        for c, label in [("fieldId", "記述欄ID"), ("displayName", "表示名"), ("points", "配点")]:
            self.points_tree.heading(c, text=label)
            self.points_tree.column(c, width=160)
        self.points_tree.pack(fill="both", expand=True, pady=8)

        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="配点").pack(side="left")
        self.points_entry = ttk.Entry(row, width=8)
        self.points_entry.pack(side="left", padx=4)
        ttk.Button(row, text="選択行に適用", command=self._on_apply_points).pack(side="left", padx=4)
        ttk.Button(row, text="保存", command=self._on_save_points).pack(side="left", padx=4)
        ttk.Button(row, text="再読込", command=self._reload_points).pack(side="left", padx=4)

    def _reload_points(self) -> None:
        if not self._require_active_test():
            return
        info = get_test_info(self.active_test_id)
        fields = info["fields"]
        points = info["points"]
        self.points_tree.delete(*self.points_tree.get_children())
        self._points_map = {}
        for f in fields:
            pts = points.get(f["id"], 0)
            self._points_map[f["id"]] = pts
            self.points_tree.insert("", tk.END, iid=f["id"], values=(f["id"], f["displayName"], pts))

    def _on_apply_points(self) -> None:
        sel = self.points_tree.selection()
        if not sel:
            return
        try:
            pts = int(self.points_entry.get() or 0)
        except ValueError:
            messagebox.showerror("入力エラー", "配点は整数で入力してください。")
            return
        fid = sel[0]
        self._points_map[fid] = pts
        vals = list(self.points_tree.item(fid, "values"))
        vals[2] = pts
        self.points_tree.item(fid, values=vals)

    def _on_save_points(self) -> None:
        if not self._require_active_test():
            return
        try:
            save_points(self.active_test_id, self._points_map)
            messagebox.showinfo("保存完了", "配点を保存しました。")
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    # --- Step 3 ---
    def _build_step3(self) -> None:
        f = self.frames[3]
        ttk.Label(f, text="③ テキスト化（OCRバッチ）", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            f,
            text="生徒解答フォルダ内の画像を自動補正→OCR→SQLite に一括保存します。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 8))

        folder_row = ttk.Frame(f)
        folder_row.pack(fill="x", pady=4)
        ttk.Label(folder_row, text="解答フォルダ").pack(side="left")
        self.inbox_path_var = tk.StringVar()
        ttk.Entry(folder_row, textvariable=self.inbox_path_var, width=70).pack(side="left", padx=4)
        ttk.Button(folder_row, text="参照…", command=self._pick_inbox).pack(side="left")

        self.queue_stats_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.queue_stats_var, style="Muted.TLabel").pack(anchor="w", pady=4)

        prog = ttk.Frame(f)
        prog.pack(fill="x", pady=4)
        self.ocr_progress = ttk.Progressbar(prog, mode="determinate", length=400)
        self.ocr_progress.pack(side="left", fill="x", expand=True)
        self.ocr_progress_label = ttk.Label(prog, text="")
        self.ocr_progress_label.pack(side="left", padx=8)

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=4)
        self.ocr_run_btn = ttk.Button(btns, text="未処理のみ OCR", style="Primary.TButton", command=self._on_run_ocr)
        self.ocr_run_btn.pack(side="left", padx=2)
        ttk.Button(btns, text="キュー更新", command=self._reload_ocr_panel).pack(side="left", padx=2)
        ttk.Button(btns, text="Excel エクスポート", command=self._on_export_excel).pack(side="left", padx=2)

        self.ocr_log = tk.Text(f, height=16, wrap="word")
        style_text(self.ocr_log)
        self.ocr_log.pack(fill="both", expand=True, pady=8)

    def _pick_inbox(self) -> None:
        path = filedialog.askdirectory(title="生徒解答フォルダを選択")
        if path and self._require_active_test():
            self.inbox_path_var.set(path)
            save_student_folder(self.active_test_id, path)

    def _reload_ocr_panel(self) -> None:
        if not self._require_active_test():
            return
        info = get_test_info(self.active_test_id)
        folder = info.get("folderPath") or ""
        self.inbox_path_var.set(folder)
        queue = build_ocr_work_queue(self.active_test_id, folder)
        st = queue["stats"]
        self.queue_stats_var.set(
            f"未処理: {st['pending']} 件 / OCRのみ: {st['ocrOnly']} / 補正+OCR: {st['warpAndOcr']} / "
            f"反映済: {st['inSheet']} / inbox内: {st['inInbox']}"
        )
        preview = get_result_preview(self.active_test_id)
        self.ocr_log.delete("1.0", tk.END)
        for row in preview[-30:]:
            texts = ", ".join(f"{k}={v}" for k, v in row["textMapping"].items())
            self.ocr_log.insert(tk.END, f"{row['fileName']}: {texts}\n")

    def _on_run_ocr(self) -> None:
        if not self._require_active_test():
            return
        folder = self.inbox_path_var.get().strip()
        if not folder:
            messagebox.showerror("エラー", "解答フォルダを指定してください。")
            return

        self.ocr_run_btn.config(state="disabled")
        self.ocr_progress["value"] = 0
        self.ocr_log.insert(tk.END, "OCR バッチを開始…\n")

        def worker() -> None:
            def on_progress(current: int, total: int, name: str) -> None:
                pct = int(current / total * 100) if total else 0
                self.after(0, lambda: self._update_progress(current, total, pct, name))

            try:
                result = run_batch_ocr(
                    self.active_test_id,
                    folder,
                    on_progress=on_progress,
                )
                self.after(0, lambda: self._on_ocr_done(result, None))
            except Exception as e:
                self.after(0, lambda: self._on_ocr_done(None, e))

        threading.Thread(target=worker, daemon=True).start()

    def _update_progress(self, current: int, total: int, pct: int, name: str) -> None:
        self.ocr_progress["value"] = pct
        self.ocr_progress_label.config(text=f"{current}/{total} {name}")

    def _on_ocr_done(self, result: dict[str, Any] | None, error: Exception | None) -> None:
        self.ocr_run_btn.config(state="normal")
        if error:
            messagebox.showerror("OCR エラー", str(error))
            self.ocr_log.insert(tk.END, f"エラー: {error}\n")
            return
        assert result is not None
        flush = result.get("flush", {})
        self.ocr_log.insert(
            tk.END,
            f"完了: 処理 {result.get('processed', 0)} 件 / "
            f"書込 {flush.get('written', 0)} / スキップ {flush.get('skipped', 0)} / "
            f"エラー {len(result.get('errors', []))}\n",
        )
        for err in result.get("errors", []):
            self.ocr_log.insert(tk.END, f"  × {err.get('fileName')}: {err.get('error')}\n")
        self._reload_ocr_panel()
        messagebox.showinfo("OCR 完了", f"書込 {flush.get('written', 0)} 件")

    def _on_export_excel(self) -> None:
        if not self._require_active_test():
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            title="採点結果を Excel にエクスポート",
        )
        if not path:
            return
        try:
            export_results_to_excel(self.active_test_id, path)
            messagebox.showinfo("エクスポート完了", f"保存しました:\n{path}")
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    # --- Step 4 ---
    def _build_step4(self) -> None:
        f = self.frames[4]
        outer = ttk.Frame(f)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        scroll = ttk.Frame(canvas)
        scroll.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=scroll, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event: tk.Event) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        ttk.Label(scroll, text="④ 採点基準の設定", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            scroll,
            text="OCR置換・みなし採点で解答を整えてから、判定・得点の基準を設定します。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 8))

        toolbar = ttk.Frame(scroll)
        toolbar.pack(fill="x", pady=4)
        ttk.Label(toolbar, text="記述欄").pack(side="left")
        self.criteria_field_var = tk.StringVar()
        self.criteria_field_combo = ttk.Combobox(
            toolbar, textvariable=self.criteria_field_var, width=28, state="readonly"
        )
        self.criteria_field_combo.pack(side="left", padx=4)
        self.criteria_field_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_criteria_field_changed())
        ttk.Button(toolbar, text="解答を集約", command=self._on_aggregate_criteria).pack(
            side="left", padx=2
        )
        ttk.Button(toolbar, text="AI原案", command=self._on_gemini_criteria).pack(side="left", padx=2)
        ttk.Button(toolbar, text="基準を保存", style="Primary.TButton", command=self._on_save_criteria).pack(
            side="left", padx=6
        )

        ocr_frame = ttk.LabelFrame(scroll, text="OCRテキスト置換", padding=8)
        ocr_frame.pack(fill="x", pady=6)
        ttk.Label(
            ocr_frame,
            text="置換ルール保存はルールのみ。「置換を適用して再集約」で採点結果のテキスト列を書き換えます。",
            style="Caption.TLabel",
            wraplength=900,
        ).pack(anchor="w")

        ocr_cols = ("search", "replace", "regex")
        self.ocr_replace_tree = ttk.Treeview(ocr_frame, columns=ocr_cols, show="headings", height=4)
        for c, label, w in [("search", "検索", 220), ("replace", "置換後", 220), ("regex", "正規表現", 70)]:
            self.ocr_replace_tree.heading(c, text=label)
            self.ocr_replace_tree.column(c, width=w)
        self.ocr_replace_tree.pack(fill="x", pady=4)

        ocr_edit = ttk.Frame(ocr_frame)
        ocr_edit.pack(fill="x")
        self.ocr_edit_search = ttk.Entry(ocr_edit, width=24)
        self.ocr_edit_search.pack(side="left", padx=2)
        self.ocr_edit_replace = ttk.Entry(ocr_edit, width=24)
        self.ocr_edit_replace.pack(side="left", padx=2)
        self.ocr_regex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ocr_edit, text="正規表現", variable=self.ocr_regex_var).pack(side="left", padx=4)
        ttk.Button(ocr_edit, text="行追加", command=self._on_ocr_row_add).pack(side="left", padx=2)
        ttk.Button(ocr_edit, text="行削除", command=self._on_ocr_row_delete).pack(side="left", padx=2)
        ttk.Button(ocr_edit, text="ルール保存", command=self._on_save_ocr_rules).pack(side="left", padx=2)
        ttk.Button(
            ocr_edit,
            text="置換を適用して再集約",
            command=self._on_apply_ocr_replacements,
        ).pack(side="left", padx=2)

        deemed_frame = ttk.LabelFrame(scroll, text="みなし採点", padding=8)
        deemed_frame.pack(fill="x", pady=6)
        ttk.Label(
            deemed_frame,
            text="正答例を指定し、表の「みなし」「不正解」列をダブルクリックで選択 → 適用で正答例に統一します。",
            style="Caption.TLabel",
            wraplength=900,
        ).pack(anchor="w")
        deemed_row = ttk.Frame(deemed_frame)
        deemed_row.pack(fill="x", pady=4)
        ttk.Label(deemed_row, text="正答例").pack(side="left")
        self.deemed_canonical_var = tk.StringVar()
        ttk.Entry(deemed_row, textvariable=self.deemed_canonical_var, width=48).pack(
            side="left", padx=4
        )
        ttk.Button(deemed_row, text="下書き保存", command=self._on_save_deemed_draft).pack(
            side="left", padx=2
        )
        ttk.Button(
            deemed_row,
            text="みなし採点を適用して再集約",
            command=self._on_apply_deemed_scoring,
        ).pack(side="left", padx=2)

        cols = ("deemed", "incorrect", "answer", "count", "judgment", "score", "reason")
        self.criteria_tree = ttk.Treeview(scroll, columns=cols, show="headings", height=8)
        for c, label, w in [
            ("deemed", "みなし", 44),
            ("incorrect", "不正解", 44),
            ("answer", "解答", 220),
            ("count", "人数", 44),
            ("judgment", "判定", 44),
            ("score", "得点", 44),
            ("reason", "備考", 220),
        ]:
            self.criteria_tree.heading(c, text=label)
            self.criteria_tree.column(c, width=w)
        self.criteria_tree.pack(fill="both", expand=True, pady=6)
        self.criteria_tree.bind("<<TreeviewSelect>>", self._on_criteria_select)
        self.criteria_tree.bind("<Double-1>", self._on_criteria_double_click)

        edit = ttk.LabelFrame(scroll, text="選択行の編集", padding=8)
        edit.pack(fill="x", pady=(0, 8))
        self.criteria_edit: dict[str, ttk.Entry] = {}
        for i, (key, label) in enumerate(
            [("judgment", "判定(○/△/×)"), ("score", "得点"), ("reason", "備考")]
        ):
            ttk.Label(edit, text=label).grid(row=0, column=i * 2, sticky="w", padx=2)
            ent = ttk.Entry(edit, width=18 if key != "reason" else 40)
            ent.grid(row=0, column=i * 2 + 1, padx=2)
            self.criteria_edit[key] = ent
        ttk.Button(edit, text="選択行に適用", command=self._on_apply_criteria_edit).grid(
            row=0, column=6, padx=8
        )

        self._build_step4_outlier_section(scroll)

    def _on_criteria_field_changed(self) -> None:
        if not self.active_test_id:
            return
        self._outlier_groups = []
        self._outlier_flat_rows = []
        self._crop_grid_results = []
        self._load_ocr_and_deemed_for_field()
        self._on_aggregate_criteria()
        if hasattr(self, "outlier_tree"):
            self._render_outlier_tree()
            self._render_crop_grid()

    def _load_ocr_and_deemed_for_field(self) -> None:
        fid = self._selected_criteria_field_id()
        if not self.active_test_id or not fid:
            return
        self._ocr_replace_rows = [
            {
                "search": r["search"],
                "replace": r["replace"],
                "useRegex": r["useRegex"],
            }
            for r in get_ocr_replacements(self.active_test_id, fid)
        ]
        self._render_ocr_replace_tree()
        draft = get_deemed_draft(self.active_test_id, fid)
        self.deemed_canonical_var.set(draft.get("canonical", ""))
        self._deemed_sources = set(draft.get("sources") or [])
        self._load_deemed_maps_from_draft()

    def _render_ocr_replace_tree(self) -> None:
        self.ocr_replace_tree.delete(*self.ocr_replace_tree.get_children())
        for i, row in enumerate(self._ocr_replace_rows):
            self.ocr_replace_tree.insert(
                "",
                tk.END,
                iid=str(i),
                values=(
                    row.get("search", ""),
                    row.get("replace", ""),
                    "はい" if row.get("useRegex") else "",
                ),
            )

    def _on_ocr_row_add(self) -> None:
        search = self.ocr_edit_search.get().strip()
        if not search:
            messagebox.showwarning("入力不足", "検索文字列を入力してください。")
            return
        self._ocr_replace_rows.append(
            {
                "search": search,
                "replace": self.ocr_edit_replace.get(),
                "useRegex": self.ocr_regex_var.get(),
            }
        )
        self._render_ocr_replace_tree()
        self.ocr_edit_search.delete(0, tk.END)
        self.ocr_edit_replace.delete(0, tk.END)
        self.ocr_regex_var.set(False)

    def _on_ocr_row_delete(self) -> None:
        sel = self.ocr_replace_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self._ocr_replace_rows):
            del self._ocr_replace_rows[idx]
        self._render_ocr_replace_tree()

    def _on_save_ocr_rules(self) -> None:
        if not self._require_active_test():
            return
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        try:
            save_ocr_replacements(self.active_test_id, fid, self._ocr_replace_rows)
            messagebox.showinfo("保存完了", "OCR置換ルールを保存しました。")
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    def _on_apply_ocr_replacements(self) -> None:
        if not self._require_active_test():
            return
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        try:
            res = apply_text_replacements_to_field(
                self.active_test_id, fid, self._ocr_replace_rows
            )
            save_ocr_replacements(self.active_test_id, fid, self._ocr_replace_rows)
            self._on_aggregate_criteria()
            if hasattr(self, "_on_fetch_outliers"):
                self._on_fetch_outliers()
            messagebox.showinfo(
                "適用完了",
                f"{res.get('replacedCount', 0)} 件のテキストを置換しました。",
            )
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    def _on_save_deemed_draft(self) -> None:
        if not self._require_active_test():
            return
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        sources = self._get_deemed_sources_from_maps()
        try:
            save_deemed_scoring_draft(
                self.active_test_id,
                fid,
                self.deemed_canonical_var.get(),
                sources,
            )
            messagebox.showinfo("保存完了", "みなし採点の下書きを保存しました。")
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    def _on_apply_deemed_scoring(self) -> None:
        if not self._require_active_test():
            return
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        sources = self._get_deemed_sources_from_maps()
        try:
            res = apply_deemed_scoring_to_field(
                self.active_test_id,
                fid,
                self.deemed_canonical_var.get(),
                sources,
            )
            self.deemed_canonical_var.set(res.get("canonical", ""))
            applied = sources[:]
            fid = self._selected_criteria_field_id()
            if fid:
                self._deemed_map(fid).clear()
            self._on_aggregate_criteria()
            self._purge_deemed_from_outlier_ui(applied)
            self._on_fetch_outliers()
            messagebox.showinfo(
                "適用完了",
                f"{res.get('updatedCount', 0)} 件を正答例に統一しました。",
            )
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    def _sync_deemed_from_draft(self) -> None:
        self._sync_deemed_incorrect_to_criteria_rows()

    def _on_criteria_double_click(self, event: tk.Event) -> None:
        region = self.criteria_tree.identify("region", event.x, event.y)
        column = self.criteria_tree.identify_column(event.x)
        if region != "cell" or column not in ("#1", "#2"):
            return
        row_id = self.criteria_tree.identify_row(event.y)
        if not row_id:
            return
        idx = int(row_id)
        if idx >= len(self._criteria_rows):
            return
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        ans = self._criteria_rows[idx]["answer_text"]
        if column == "#1":
            self._toggle_deemed_answer(fid, ans)
        else:
            self._toggle_incorrect_answer(fid, ans)
        self.criteria_tree.selection_set(str(idx))

    def _reload_criteria_panel(self) -> None:
        if not self._require_active_test():
            return
        fields = get_answer_fields(self.active_test_id)
        labels = [f"{f['displayName']} ({f['id']})" for f in fields]
        self.criteria_field_combo["values"] = labels
        if labels and not self.criteria_field_var.get():
            self.criteria_field_combo.current(0)
        self._deemed_sources: set[str] = set()
        if labels:
            self._load_ocr_and_deemed_for_field()
            self._on_aggregate_criteria()

    def _selected_criteria_field_id(self) -> str | None:
        fields = get_answer_fields(self.active_test_id)
        idx = self.criteria_field_combo.current()
        if idx < 0 or idx >= len(fields):
            return None
        return fields[idx]["id"]

    def _render_criteria_tree(self) -> None:
        self.criteria_tree.delete(*self.criteria_tree.get_children())
        fid = self._selected_criteria_field_id() or ""
        canonical = self._get_deemed_canonical()
        for i, row in enumerate(self._criteria_rows):
            ans = row.get("answer_text", "")
            deemed = (
                "—"
                if canonical and ans == canonical
                else ("☑" if self._is_deemed_checked(fid, ans) else "☐")
            )
            incorrect = "☑" if self._is_incorrect_checked(fid, ans) else "☐"
            tags = ()
            if row.get("deemed") or self._is_deemed_checked(fid, ans):
                tags = ("deemed",)
            elif row.get("incorrect") or self._is_incorrect_checked(fid, ans):
                tags = ("incorrect",)
            self.criteria_tree.insert(
                "",
                tk.END,
                iid=str(i),
                tags=tags,
                values=(
                    deemed,
                    incorrect,
                    ans,
                    row.get("count", 0),
                    row.get("judgment", ""),
                    row.get("score", ""),
                    row.get("reason", ""),
                ),
            )
        self.criteria_tree.tag_configure("deemed", background="#eff6ff")
        self.criteria_tree.tag_configure("incorrect", background="#fef2f2")

    def _on_aggregate_criteria(self) -> None:
        if not self._require_active_test():
            return
        fid = self._selected_criteria_field_id()
        if not fid:
            messagebox.showwarning("記述欄未選択", "記述欄を選択してください。")
            return
        self._criteria_rows = merge_unique_with_criteria(self.active_test_id, fid)
        self._sync_deemed_from_draft()
        self._render_criteria_tree()

    def _on_criteria_select(self, _event=None) -> None:
        sel = self.criteria_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self._criteria_rows):
            return
        row = self._criteria_rows[idx]
        for key in ("judgment", "score", "reason"):
            ent = self.criteria_edit[key]
            ent.delete(0, tk.END)
            ent.insert(0, str(row.get(key, "") or ""))

    def _on_apply_criteria_edit(self) -> None:
        sel = self.criteria_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self._criteria_rows):
            return
        self._criteria_rows[idx]["judgment"] = self.criteria_edit["judgment"].get().strip()
        try:
            score_val = self.criteria_edit["score"].get().strip()
            self._criteria_rows[idx]["score"] = int(score_val) if score_val else ""
        except ValueError:
            messagebox.showerror("入力エラー", "得点は整数で入力してください。")
            return
        self._criteria_rows[idx]["reason"] = self.criteria_edit["reason"].get().strip()
        self._render_criteria_tree()
        self.criteria_tree.selection_set(str(idx))

    def _on_save_criteria(self) -> None:
        if not self._require_active_test():
            return
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        rules = []
        for row in self._criteria_rows:
            judgment = str(row.get("judgment") or "").strip()
            if not judgment:
                continue
            try:
                score = int(row.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            rules.append(
                {
                    "answer_text": row["answer_text"],
                    "judgment": judgment,
                    "score": score,
                    "reason": row.get("reason") or "",
                }
            )
        if not rules:
            messagebox.showwarning("保存不可", "判定が入力された行がありません。")
            return
        try:
            save_grading_criteria(self.active_test_id, fid, rules)
            messagebox.showinfo("保存完了", f"採点基準を {len(rules)} 件保存しました。")
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    def _on_gemini_criteria(self) -> None:
        if not self._require_active_test():
            return
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        if not self._criteria_rows:
            self._on_aggregate_criteria()
        unique = [{"answer_text": r["answer_text"], "count": r["count"]} for r in self._criteria_rows]

        def worker() -> None:
            try:
                result = generate_rubric_with_gemini(self.active_test_id, fid, unique)
                self.after(0, lambda: self._apply_gemini_result(result, None))
            except Exception as e:
                self.after(0, lambda: self._apply_gemini_result(None, e))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_gemini_result(self, result: dict[str, Any] | None, error: Exception | None) -> None:
        if error:
            messagebox.showerror("AI原案エラー", str(error))
            return
        assert result is not None
        ai_map = {
            str(item["answer_text"]): item for item in result.get("scrutinized_list", [])
        }
        for row in self._criteria_rows:
            ai = ai_map.get(row["answer_text"])
            if not ai:
                continue
            row["judgment"] = ai.get("judgment", "")
            row["score"] = ai.get("recommended_score", "")
            row["reason"] = ai.get("reason", "")
        self._render_criteria_tree()
        messagebox.showinfo(
            "AI原案",
            "Gemini の原案を表に反映しました。内容を確認して「基準を保存」してください。",
        )

    # --- Step 5 ---
    def _build_step5(self) -> None:
        f = self.frames[5]
        ttk.Label(f, text="⑤ 採点の実施", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            f,
            text="保存済みの採点基準に従い、全受験者の判定・得点を一括反映し、考査総括を生成します。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 8))

        ttk.Button(f, text="一括採点を実行", style="Primary.TButton", command=self._on_run_grading).pack(
            anchor="w", pady=8
        )

        self.grading_status_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.grading_status_var, style="Muted.TLabel").pack(anchor="w")

        summary_frame = ttk.LabelFrame(f, text="考査総括", padding=8)
        summary_frame.pack(fill="both", expand=True, pady=8)
        self.summary_tree = ttk.Treeview(
            summary_frame,
            columns=("category", "item", "value", "note"),
            show="headings",
            height=18,
        )
        for c, label, w in [
            ("category", "区分", 80),
            ("item", "項目", 220),
            ("value", "値", 100),
            ("note", "備考", 280),
        ]:
            self.summary_tree.heading(c, text=label)
            self.summary_tree.column(c, width=w)
        self.summary_tree.pack(fill="both", expand=True)

    def _reload_summary_panel(self) -> None:
        if not self._require_active_test():
            return
        self._fill_summary_tree(get_summary_data(self.active_test_id))

    def _fill_summary_tree(self, rows: list[dict[str, Any]]) -> None:
        self.summary_tree.delete(*self.summary_tree.get_children())
        for row in rows:
            self.summary_tree.insert(
                "",
                tk.END,
                values=(row["category"], row["item"], row["value"], row.get("note", "")),
            )

    def _on_run_grading(self) -> None:
        if not self._require_active_test():
            return
        try:
            res = execute_grading(self.active_test_id)
            self.grading_status_var.set(
                f"採点完了: {res['gradedCount']} 件 / "
                f"未登録パターン照合: {res['unregisteredCount']} 件"
            )
            self._fill_summary_tree(get_summary_data(self.active_test_id))
            messagebox.showinfo(
                "採点完了",
                f"{res['gradedCount']} 件を採点しました。\n"
                f"採点基準に無い解答: {res['unregisteredCount']} 件（×・0点として処理）",
            )
        except Exception as e:
            messagebox.showerror("採点エラー", str(e))
