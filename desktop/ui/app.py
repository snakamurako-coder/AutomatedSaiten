"""メインウィンドウとステップ画面。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from constants import DESKTOP_READY_STEPS, STEPS
from models.database import init_db
from models.test_repo import (
    create_test,
    export_results_to_excel,
    get_answer_fields,
    get_result_preview,
    get_test_info,
    list_tests,
    save_answer_fields,
    save_points,
    save_student_folder,
    set_active_test,
)
from services.batch_processor import run_batch_ocr
from services.ocr import check_ocr_config
from services.work_queue import build_ocr_work_queue


class AutomatedSaitenApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("模範解答ベース自動採点システム（PC版）")
        self.geometry("1100x720")
        self.minsize(900, 600)

        init_db()
        self.active_test_id: str | None = None
        self.current_step = 0
        self._field_rows: list[dict[str, Any]] = []

        self._build_layout()
        self._refresh_ocr_status()
        self._load_step(0)
        self._refresh_test_list()

    def _build_layout(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        nav = ttk.Frame(self, padding=8, width=220)
        nav.grid(row=0, column=0, sticky="ns")
        nav.grid_propagate(False)

        ttk.Label(nav, text="自動採点（PC版）", font=("", 11, "bold")).pack(anchor="w", pady=(0, 8))
        self.step_buttons: dict[int, ttk.Button] = {}
        for step in STEPS:
            sid = step["id"]
            enabled = sid in DESKTOP_READY_STEPS
            label = step["label"] + ("" if enabled else " …準備中")
            btn = ttk.Button(
                nav,
                text=label,
                command=lambda s=sid: self._load_step(s) if s in DESKTOP_READY_STEPS else None,
                state="normal" if enabled else "disabled",
            )
            btn.pack(fill="x", pady=2)
            self.step_buttons[sid] = btn

        ttk.Separator(nav, orient="horizontal").pack(fill="x", pady=8)
        self.ocr_status_var = tk.StringVar(value="")
        ttk.Label(nav, textvariable=self.ocr_status_var, wraplength=200, font=("", 8)).pack(anchor="w")

        self.content = ttk.Frame(self, padding=12)
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
                    font=("", 12),
                ).pack(anchor="w")

        self._build_step0()
        self._build_step1()
        self._build_step2()
        self._build_step3()

    def _show_frame(self, step_id: int) -> None:
        self.frames[step_id].tkraise()
        for sid, btn in self.step_buttons.items():
            btn.state(["!pressed"])
        self.current_step = step_id

    def _require_active_test(self) -> bool:
        if not self.active_test_id:
            messagebox.showwarning("テスト未選択", "先にテストを作成または選択してください。")
            return False
        return True

    def _refresh_ocr_status(self) -> None:
        info = check_ocr_config()
        self.ocr_status_var.set(info.get("message", ""))

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

    # --- Step 0 ---
    def _build_step0(self) -> None:
        f = self.frames[0]
        ttk.Label(f, text="⓪ テスト作成", font=("", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")

        form = ttk.LabelFrame(f, text="新規テスト", padding=8)
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

        ttk.Button(form, text="テストを作成", command=self._on_create_test).grid(
            row=3, column=0, columnspan=2, pady=8
        )

        list_frame = ttk.LabelFrame(f, text="テスト一覧", padding=8)
        list_frame.grid(row=1, column=1, sticky="nsew", padx=(12, 0), pady=8)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(1, weight=1)

        self.test_listbox = tk.Listbox(list_frame, height=14)
        self.test_listbox.pack(fill="both", expand=True)
        btns = ttk.Frame(list_frame)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="選択", command=self._on_select_test).pack(side="left", padx=2)
        ttk.Button(btns, text="更新", command=self._refresh_test_list).pack(side="left", padx=2)

        self.active_test_label = ttk.Label(f, text="選択中: （なし）")
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
        ttk.Label(f, text="① 回答欄設定", font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="記述欄の矩形（x, y, width, height）と OCR 言語を設定します。",
            font=("", 9),
        ).pack(anchor="w", pady=4)

        cols = ("id", "displayName", "x", "y", "width", "height", "order", "ocrLang")
        self.fields_tree = ttk.Treeview(f, columns=cols, show="headings", height=12)
        headers = {
            "id": "記述欄ID",
            "displayName": "表示名",
            "x": "x",
            "y": "y",
            "width": "width",
            "height": "height",
            "order": "順",
            "ocrLang": "OCR言語",
        }
        for c in cols:
            self.fields_tree.heading(c, text=headers[c])
            self.fields_tree.column(c, width=90 if c != "displayName" else 120)
        self.fields_tree.pack(fill="both", expand=True, pady=8)

        edit = ttk.LabelFrame(f, text="行の追加 / 更新", padding=8)
        edit.pack(fill="x")
        self.field_entries: dict[str, ttk.Entry] = {}
        grid_specs = [
            ("id", "記述欄ID"),
            ("displayName", "表示名"),
            ("x", "x"),
            ("y", "y"),
            ("width", "width"),
            ("height", "height"),
            ("order", "順"),
            ("ocrLang", "OCR言語(ja/en)"),
        ]
        for i, (key, label) in enumerate(grid_specs):
            ttk.Label(edit, text=label).grid(row=i // 4, column=(i % 4) * 2, sticky="w", padx=2)
            ent = ttk.Entry(edit, width=12)
            ent.grid(row=i // 4, column=(i % 4) * 2 + 1, padx=2, pady=2)
            self.field_entries[key] = ent

        btns = ttk.Frame(edit)
        btns.grid(row=2, column=0, columnspan=8, pady=6)
        ttk.Button(btns, text="追加/更新", command=self._on_field_upsert).pack(side="left", padx=4)
        ttk.Button(btns, text="削除", command=self._on_field_delete).pack(side="left", padx=4)
        ttk.Button(btns, text="保存", command=self._on_save_fields).pack(side="left", padx=4)
        ttk.Button(btns, text="再読込", command=self._reload_fields).pack(side="left", padx=4)

        self.fields_tree.bind("<<TreeviewSelect>>", self._on_field_select)

    def _reload_fields(self) -> None:
        if not self._require_active_test():
            return
        self._field_rows = get_answer_fields(self.active_test_id)
        self.fields_tree.delete(*self.fields_tree.get_children())
        for row in self._field_rows:
            self.fields_tree.insert(
                "",
                tk.END,
                values=(
                    row["id"],
                    row["displayName"],
                    row["x"],
                    row["y"],
                    row["width"],
                    row["height"],
                    row["order"],
                    row["ocrLang"],
                ),
            )

    def _on_field_select(self, _event=None) -> None:
        sel = self.fields_tree.selection()
        if not sel:
            return
        vals = self.fields_tree.item(sel[0], "values")
        keys = ["id", "displayName", "x", "y", "width", "height", "order", "ocrLang"]
        for key, val in zip(keys, vals):
            ent = self.field_entries[key]
            ent.delete(0, tk.END)
            ent.insert(0, val)

    def _on_field_upsert(self) -> None:
        data = {k: e.get().strip() for k, e in self.field_entries.items()}
        if not data["id"]:
            messagebox.showerror("入力エラー", "記述欄IDが必要です。")
            return
        try:
            row = {
                "id": data["id"],
                "displayName": data["displayName"] or data["id"],
                "x": int(data["x"] or 0),
                "y": int(data["y"] or 0),
                "width": int(data["width"] or 0),
                "height": int(data["height"] or 0),
                "order": int(data["order"] or len(self._field_rows) + 1),
                "ocrLang": data["ocrLang"] or "en",
            }
        except ValueError:
            messagebox.showerror("入力エラー", "数値項目（x, y, width, height, 順）を確認してください。")
            return

        replaced = False
        for i, existing in enumerate(self._field_rows):
            if existing["id"] == row["id"]:
                self._field_rows[i] = row
                replaced = True
                break
        if not replaced:
            self._field_rows.append(row)
        self._field_rows.sort(key=lambda r: r["order"])
        self._reload_fields_from_memory()

    def _reload_fields_from_memory(self) -> None:
        self.fields_tree.delete(*self.fields_tree.get_children())
        for row in self._field_rows:
            self.fields_tree.insert(
                "",
                tk.END,
                values=(
                    row["id"],
                    row["displayName"],
                    row["x"],
                    row["y"],
                    row["width"],
                    row["height"],
                    row["order"],
                    row["ocrLang"],
                ),
            )

    def _on_field_delete(self) -> None:
        sel = self.fields_tree.selection()
        if not sel:
            return
        fid = self.fields_tree.item(sel[0], "values")[0]
        self._field_rows = [r for r in self._field_rows if r["id"] != fid]
        self._reload_fields_from_memory()

    def _on_save_fields(self) -> None:
        if not self._require_active_test():
            return
        if not self._field_rows:
            messagebox.showerror("保存エラー", "記述欄がありません。")
            return
        try:
            save_answer_fields(self.active_test_id, self._field_rows)
            messagebox.showinfo("保存完了", "記述欄を保存しました。")
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    # --- Step 2 ---
    def _build_step2(self) -> None:
        f = self.frames[2]
        ttk.Label(f, text="② 配点決定", font=("", 14, "bold")).pack(anchor="w")
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
        ttk.Label(f, text="③ テキスト化（OCRバッチ）", font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(
            f,
            text="生徒解答フォルダ内の画像を自動補正→OCR→SQLite に一括保存します。",
            font=("", 9),
        ).pack(anchor="w", pady=4)

        folder_row = ttk.Frame(f)
        folder_row.pack(fill="x", pady=4)
        ttk.Label(folder_row, text="解答フォルダ").pack(side="left")
        self.inbox_path_var = tk.StringVar()
        ttk.Entry(folder_row, textvariable=self.inbox_path_var, width=70).pack(side="left", padx=4)
        ttk.Button(folder_row, text="参照…", command=self._pick_inbox).pack(side="left")

        self.queue_stats_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.queue_stats_var, font=("", 9)).pack(anchor="w", pady=4)

        prog = ttk.Frame(f)
        prog.pack(fill="x", pady=4)
        self.ocr_progress = ttk.Progressbar(prog, mode="determinate", length=400)
        self.ocr_progress.pack(side="left", fill="x", expand=True)
        self.ocr_progress_label = ttk.Label(prog, text="")
        self.ocr_progress_label.pack(side="left", padx=8)

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=4)
        self.ocr_run_btn = ttk.Button(btns, text="未処理のみ OCR", command=self._on_run_ocr)
        self.ocr_run_btn.pack(side="left", padx=2)
        ttk.Button(btns, text="キュー更新", command=self._reload_ocr_panel).pack(side="left", padx=2)
        ttk.Button(btns, text="Excel エクスポート", command=self._on_export_excel).pack(side="left", padx=2)

        self.ocr_log = tk.Text(f, height=16, wrap="word")
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
