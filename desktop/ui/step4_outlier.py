"""④ 外れ値検出・画像タイル・みなし/不正解チェック連動。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from models.criteria_repo import get_answer_rows_for_pattern, get_outlier_answer_groups
from models.test_repo import get_answer_fields
from services.crop_preview import load_crops_for_rows


class Step4OutlierMixin:
    """AutomatedSaitenApp 用 Mixin。"""

    def _init_step4_state(self) -> None:
        self._deemed_checked_by_field: dict[str, dict[str, bool]] = {}
        self._incorrect_checked_by_field: dict[str, dict[str, bool]] = {}
        self._outlier_groups: list[dict[str, Any]] = []
        self._outlier_flat_rows: list[dict[str, Any]] = []
        self._crop_grid_results: list[dict[str, Any]] = []
        self._crop_photo_refs: list[Any] = []

    def _deemed_map(self, field_id: str) -> dict[str, bool]:
        if field_id not in self._deemed_checked_by_field:
            self._deemed_checked_by_field[field_id] = {}
        return self._deemed_checked_by_field[field_id]

    def _incorrect_map(self, field_id: str) -> dict[str, bool]:
        if field_id not in self._incorrect_checked_by_field:
            self._incorrect_checked_by_field[field_id] = {}
        return self._incorrect_checked_by_field[field_id]

    def _get_deemed_canonical(self) -> str:
        return self.deemed_canonical_var.get().strip()

    def _is_deemed_checked(self, field_id: str, answer_text: str) -> bool:
        if self._get_deemed_canonical() and answer_text == self._get_deemed_canonical():
            return False
        return bool(self._deemed_map(field_id).get(answer_text))

    def _is_incorrect_checked(self, field_id: str, answer_text: str) -> bool:
        return bool(self._incorrect_map(field_id).get(answer_text))

    def _toggle_deemed_answer(
        self, field_id: str, answer_text: str, force: bool | None = None
    ) -> None:
        canonical = self._get_deemed_canonical()
        if canonical and answer_text == canonical:
            return
        m = self._deemed_map(field_id)
        next_val = (not m.get(answer_text)) if force is None else force
        if next_val:
            m[answer_text] = True
        else:
            m.pop(answer_text, None)
        self._sync_deemed_incorrect_to_criteria_rows()
        self._refresh_deemed_incorrect_views()

    def _toggle_incorrect_answer(
        self, field_id: str, answer_text: str, force: bool | None = None
    ) -> None:
        m = self._incorrect_map(field_id)
        next_val = (not m.get(answer_text)) if force is None else force
        if next_val:
            m[answer_text] = True
        else:
            m.pop(answer_text, None)
        self._sync_deemed_incorrect_to_criteria_rows()
        self._refresh_deemed_incorrect_views()
        self._purge_incorrect_from_crop_grid()

    def _sync_deemed_incorrect_to_criteria_rows(self) -> None:
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        for row in self._criteria_rows:
            ans = row["answer_text"]
            row["deemed"] = self._is_deemed_checked(fid, ans)
            row["incorrect"] = self._is_incorrect_checked(fid, ans)

    def _get_deemed_sources_from_maps(self) -> list[str]:
        fid = self._selected_criteria_field_id()
        if not fid:
            return []
        canonical = self._get_deemed_canonical()
        return [
            k
            for k, v in self._deemed_map(fid).items()
            if v and k != canonical
        ]

    def _load_deemed_maps_from_draft(self) -> None:
        fid = self._selected_criteria_field_id()
        if not fid or not self.active_test_id:
            return
        from models.text_processing import get_deemed_draft

        draft = get_deemed_draft(self.active_test_id, fid)
        self._deemed_map(fid).clear()
        for src in draft.get("sources") or []:
            self._deemed_map(fid)[src] = True

    def _should_skip_crop_for_answer(self, answer_text: str) -> bool:
        if not self.hide_incorrect_crops_var.get():
            return False
        fid = self._selected_criteria_field_id()
        return bool(fid and self._is_incorrect_checked(fid, answer_text))

    def _refresh_deemed_incorrect_views(self) -> None:
        self._render_criteria_tree()
        self._render_outlier_tree()
        self._render_crop_grid()

    def _build_step4_outlier_section(self, parent: ttk.Frame) -> None:
        outlier_frame = ttk.LabelFrame(
            parent, text="外れ値・少数派解答の確認（回答欄画像）", padding=8
        )
        outlier_frame.pack(fill="x", pady=6)
        ttk.Label(
            outlier_frame,
            text="「みなし」「不正解」列はダブルクリックで切替。画像タイルクリックでもみなしを切替えられます。",
            font=("", 8),
            wraplength=900,
        ).pack(anchor="w")

        ctrl = ttk.Frame(outlier_frame)
        ctrl.pack(fill="x", pady=4)
        ttk.Label(ctrl, text="人数上限 ≤").pack(side="left")
        self.outlier_max_count_var = tk.IntVar(value=2)
        ttk.Spinbox(ctrl, from_=1, to=99, width=4, textvariable=self.outlier_max_count_var).pack(
            side="left", padx=2
        )
        ttk.Button(ctrl, text="外れ値を検出", command=self._on_fetch_outliers).pack(
            side="left", padx=4
        )
        self.hide_incorrect_crops_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            ctrl,
            text="不正解対象の解答の画像は表示しない",
            variable=self.hide_incorrect_crops_var,
            command=self._purge_incorrect_from_crop_grid,
        ).pack(side="left", padx=4)
        ttk.Button(
            ctrl, text="なし（未回答）を確認", command=self._on_show_none_crops
        ).pack(side="left", padx=2)
        ttk.Button(ctrl, text="表示を全選択", command=lambda: self._select_all_outlier(True)).pack(
            side="left", padx=2
        )
        ttk.Button(
            ctrl, text="表示を解除", command=lambda: self._select_all_outlier(False)
        ).pack(side="left", padx=2)
        ttk.Button(
            ctrl, text="選択を画像表示", command=self._on_show_selected_outlier_crops
        ).pack(side="left", padx=2)

        zoom_row = ttk.Frame(outlier_frame)
        zoom_row.pack(fill="x", pady=2)
        ttk.Label(zoom_row, text="表示倍率").pack(side="left")
        self.crop_zoom_var = tk.IntVar(value=100)
        ttk.Scale(
            zoom_row,
            from_=30,
            to=400,
            orient="horizontal",
            variable=self.crop_zoom_var,
            command=lambda _v: self._render_crop_grid(),
        ).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Label(zoom_row, textvariable=self.crop_zoom_var, width=4).pack(side="left")

        ocols = ("deemed", "incorrect", "answer", "count", "show", "student", "file", "action")
        self.outlier_tree = ttk.Treeview(outlier_frame, columns=ocols, show="headings", height=6)
        for c, label, w in [
            ("deemed", "みなし", 44),
            ("incorrect", "不正解", 44),
            ("answer", "解答", 180),
            ("count", "人数", 40),
            ("show", "表示", 40),
            ("student", "生徒ID", 70),
            ("file", "ファイル名", 160),
            ("action", "操作", 50),
        ]:
            self.outlier_tree.heading(c, text=label)
            self.outlier_tree.column(c, width=w)
        self.outlier_tree.pack(fill="x", pady=4)
        self.outlier_tree.bind("<Double-1>", self._on_outlier_double_click)

        crop_outer = ttk.Frame(outlier_frame)
        crop_outer.pack(fill="both", expand=True, pady=4)
        self.crop_canvas = tk.Canvas(crop_outer, height=280, bg="#f3f4f6", highlightthickness=1)
        crop_scroll_y = ttk.Scrollbar(crop_outer, orient="vertical", command=self.crop_canvas.yview)
        crop_scroll_x = ttk.Scrollbar(crop_outer, orient="horizontal", command=self.crop_canvas.xview)
        self.crop_inner = ttk.Frame(self.crop_canvas)
        self.crop_inner.bind(
            "<Configure>",
            lambda e: self.crop_canvas.configure(scrollregion=self.crop_canvas.bbox("all")),
        )
        self.crop_canvas.create_window((0, 0), window=self.crop_inner, anchor="nw")
        self.crop_canvas.configure(yscrollcommand=crop_scroll_y.set, xscrollcommand=crop_scroll_x.set)
        self.crop_canvas.pack(side="left", fill="both", expand=True)
        crop_scroll_y.pack(side="right", fill="y")
        crop_scroll_x.pack(side="bottom", fill="x")

    def _on_fetch_outliers(self) -> None:
        if not self._require_active_test():
            return
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        max_count = self.outlier_max_count_var.get()
        self._outlier_groups = get_outlier_answer_groups(
            self.active_test_id, fid, max_count
        )
        self._build_outlier_flat_rows()
        self._render_outlier_tree()
        messagebox.showinfo(
            "検出完了",
            f"{len(self._outlier_groups)} 種類の外れ値解答（人数 ≤ {max_count}）",
        )

    def _build_outlier_flat_rows(self) -> None:
        self._outlier_flat_rows = []
        for gi, group in enumerate(self._outlier_groups):
            for ri, row in enumerate(group.get("rows") or []):
                skip = self._should_skip_crop_for_answer(group["answer_text"])
                self._outlier_flat_rows.append(
                    {
                        "key": f"{gi}:{ri}",
                        "group_index": gi,
                        "row_index": ri,
                        "answer_text": group["answer_text"],
                        "group_count": group["count"],
                        "show": not skip,
                        "skip_img": skip,
                        **row,
                    }
                )

    def _render_outlier_tree(self) -> None:
        if not hasattr(self, "outlier_tree"):
            return
        self.outlier_tree.delete(*self.outlier_tree.get_children())
        fid = self._selected_criteria_field_id() or ""
        for i, row in enumerate(self._outlier_flat_rows):
            ans = row["answer_text"]
            deemed = "☑" if self._is_deemed_checked(fid, ans) else "☐"
            incorrect = "☑" if self._is_incorrect_checked(fid, ans) else "☐"
            show_mark = "☑" if row.get("show") and not row.get("skip_img") else "☐"
            self.outlier_tree.insert(
                "",
                tk.END,
                iid=str(i),
                values=(
                    deemed,
                    incorrect,
                    ans,
                    row["group_count"],
                    show_mark,
                    row.get("studentId") or "-",
                    row.get("fileName") or "",
                    "—" if row.get("skip_img") else "1枚",
                ),
            )

    def _on_outlier_double_click(self, event: tk.Event) -> None:
        column = self.outlier_tree.identify_column(event.x)
        row_id = self.outlier_tree.identify_row(event.y)
        if not row_id:
            return
        idx = int(row_id)
        if idx >= len(self._outlier_flat_rows):
            return
        flat = self._outlier_flat_rows[idx]
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        ans = flat["answer_text"]
        if column == "#1":
            self._toggle_deemed_answer(fid, ans)
        elif column == "#2":
            self._toggle_incorrect_answer(fid, ans)
        elif column == "#5":
            if flat.get("skip_img"):
                return
            flat["show"] = not flat.get("show")
            self._render_outlier_tree()
        elif column == "#8" and not flat.get("skip_img"):
            self._load_crops_async([flat], allow_incorrect=True)

    def _select_all_outlier(self, checked: bool) -> None:
        for row in self._outlier_flat_rows:
            if row.get("skip_img"):
                continue
            row["show"] = checked
        self._render_outlier_tree()

    def _get_selected_outlier_rows(self) -> list[dict[str, Any]]:
        rows = []
        for row in self._outlier_flat_rows:
            if row.get("show") and not row.get("skip_img"):
                rows.append(row)
        return rows

    def _on_show_selected_outlier_crops(self) -> None:
        rows = self._get_selected_outlier_rows()
        if not rows:
            messagebox.showwarning("未選択", "表示する回答を選択してください。")
            return
        self._load_crops_async(rows, allow_incorrect=False)

    def _on_show_none_crops(self) -> None:
        if not self._require_active_test():
            return
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        rows = get_answer_rows_for_pattern(self.active_test_id, fid, "なし")
        if not rows:
            messagebox.showinfo("なし", "「なし」の回答は見つかりませんでした。")
            return
        self._load_crops_async(rows, allow_incorrect=True)

    def _load_crops_async(
        self, rows: list[dict[str, Any]], allow_incorrect: bool = False
    ) -> None:
        fid = self._selected_criteria_field_id()
        if not fid or not self.active_test_id:
            return
        fields = get_answer_fields(self.active_test_id)
        field = next((f for f in fields if f["id"] == fid), None)
        if not field:
            messagebox.showerror("エラー", "記述欄が見つかりません。")
            return
        if not allow_incorrect and self.hide_incorrect_crops_var.get():
            rows = [r for r in rows if not self._should_skip_crop_for_answer(r.get("answer_text", ""))]
        if not rows:
            messagebox.showinfo("除外", "表示対象がありません（不正解対象は除外されます）。")
            return

        for widget in self.crop_inner.winfo_children():
            widget.destroy()
        ttk.Label(self.crop_inner, text=f"画像を読み込み中…（{len(rows)}枚）").pack(pady=8)

        def worker() -> None:
            results = load_crops_for_rows(rows, field)
            self.after(0, lambda: self._on_crops_loaded(results))

        threading.Thread(target=worker, daemon=True).start()

    def _on_crops_loaded(self, results: list[dict[str, Any]]) -> None:
        self._crop_grid_results = results
        self._render_crop_grid()

    def _render_crop_grid(self) -> None:
        if not hasattr(self, "crop_inner"):
            return
        for widget in self.crop_inner.winfo_children():
            widget.destroy()
        self._crop_photo_refs.clear()

        if not self._crop_grid_results:
            ttk.Label(
                self.crop_inner,
                text="「選択を画像表示」または外れ値一覧の「1枚」で回答欄画像を表示します",
                font=("", 9),
            ).grid(row=0, column=0, padx=8, pady=8)
            return

        from PIL import Image, ImageTk

        fid = self._selected_criteria_field_id() or ""
        zoom = max(30, min(400, int(self.crop_zoom_var.get() or 100))) / 100.0
        cols = 4
        base_w = 180
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

        for idx, item in enumerate(self._crop_grid_results):
            r, c = divmod(idx, cols)
            if not item.get("ok"):
                err_frame = tk.Frame(self.crop_inner, bd=1, relief="solid", bg="#fef2f2")
                tk.Label(
                    err_frame,
                    text=f"{item['row'].get('fileName', '—')}\n{item.get('error', '読込失敗')}",
                    bg="#fef2f2",
                    font=("", 8),
                    wraplength=int(base_w * zoom),
                ).pack(padx=4, pady=4)
                err_frame.grid(row=r, column=c, padx=4, pady=4, sticky="nw")
                continue

            row = item["row"]
            ans = row.get("answer_text") or ""
            pil: Any = item["pil"]
            w = max(40, int(pil.width * zoom))
            h = max(20, int(pil.height * zoom))
            disp = pil.resize((w, h), resample)

            photo = ImageTk.PhotoImage(disp)
            self._crop_photo_refs.append(photo)

            deemed = self._is_deemed_checked(fid, ans)
            bg = "#eff6ff" if deemed else "#ffffff"
            border = "#2563eb" if deemed else "#d1d5db"
            tile = tk.Frame(
                self.crop_inner,
                bd=2,
                relief="solid",
                bg=bg,
                highlightbackground=border,
                highlightthickness=2 if deemed else 0,
                cursor="hand2",
            )
            img_label = tk.Label(tile, image=photo, bg=bg)
            img_label.pack()
            img_label.bind(
                "<Button-1>",
                lambda _e, a=ans: self._on_crop_tile_click(a),
            )
            tk.Label(
                tile,
                text=f"ID: {row.get('studentId') or '-'}",
                font=("", 8, "bold"),
                bg=bg,
            ).pack(anchor="w", padx=2)
            tk.Label(
                tile,
                text=str(row.get("fileName") or "")[:28],
                font=("", 7),
                bg=bg,
                wraplength=w,
            ).pack(anchor="w", padx=2)
            tk.Label(
                tile,
                text=ans[:40],
                font=("Consolas", 7),
                fg="#6b21a8",
                bg=bg,
                wraplength=w,
            ).pack(anchor="w", padx=2, pady=(0, 2))
            tile.bind(
                "<Button-1>",
                lambda _e, a=ans: self._on_crop_tile_click(a),
            )
            tile.grid(row=r, column=c, padx=4, pady=4, sticky="nw")

    def _on_crop_tile_click(self, answer_text: str) -> None:
        fid = self._selected_criteria_field_id()
        if not fid:
            return
        self._toggle_deemed_answer(fid, answer_text)

    def _purge_incorrect_from_crop_grid(self) -> None:
        fid = self._selected_criteria_field_id()
        if not fid or not self.hide_incorrect_crops_var.get():
            return
        self._crop_grid_results = [
            r
            for r in self._crop_grid_results
            if not self._is_incorrect_checked(fid, (r.get("row") or {}).get("answer_text", ""))
        ]
        for row in self._outlier_flat_rows:
            if self._should_skip_crop_for_answer(row.get("answer_text", "")):
                row["show"] = False
                row["skip_img"] = True
        self._render_outlier_tree()
        self._render_crop_grid()

    def _purge_deemed_from_outlier_ui(self, applied_sources: list[str]) -> None:
        source_set = set(applied_sources or [])
        self._outlier_groups = [
            g for g in self._outlier_groups if g.get("answer_text") not in source_set
        ]
        self._crop_grid_results = [
            r
            for r in self._crop_grid_results
            if (r.get("row") or {}).get("answer_text") not in source_set
        ]
        self._build_outlier_flat_rows()
        self._render_outlier_tree()
        self._render_crop_grid()
