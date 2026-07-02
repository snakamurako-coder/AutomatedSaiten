"""模範解答画像上の記述欄矩形エディタ（GAS RegionEditor の Tkinter 版）。"""

from __future__ import annotations

import copy
from typing import Any, Callable

import cv2
import numpy as np
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from services.image_loader import load_image_bgr
from ui.theme import COLORS, FONTS


class AnswerRegionEditor(tk.Frame):
    HANDLE = 8
    MIN_SIZE = 15
    MAX_DISPLAY_W = 760
    MAX_DISPLAY_H = 520

    def __init__(
        self,
        parent: tk.Misc,
        on_change: Callable[[], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(parent, **kwargs)
        self.configure(bg=COLORS["bg"])
        self._on_change = on_change
        self._on_status = on_status
        self.regions: list[dict[str, Any]] = []
        self.selected_idx = -1
        self._drag_state: dict[str, Any] | None = None
        self._image_bgr: np.ndarray | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._scale = 1.0
        self._image_w = 0
        self._image_h = 0

        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(
            outer,
            bg=COLORS["canvas_bg"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            cursor="crosshair",
            bd=0,
        )
        scroll_y = ttk.Scrollbar(outer, orient="vertical", command=self._canvas.yview)
        scroll_x = ttk.Scrollbar(outer, orient="horizontal", command=self._canvas.xview)
        self._canvas.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        self._canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self._canvas.bind("<B1-Motion>", self._on_mouse_move)
        self._canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self._canvas.bind("<Motion>", self._on_hover)
        self._canvas.bind("<Enter>", lambda _e: self._canvas.focus_set())
        self._canvas.configure(takefocus=True, width=self.MAX_DISPLAY_W, height=420)
        self.after_idle(self._draw_placeholder)

    def has_image(self) -> bool:
        return self._image_bgr is not None

    def get_image_bgr(self) -> np.ndarray | None:
        return self._image_bgr

    def set_image(self, image_bgr: np.ndarray) -> None:
        self._image_bgr = image_bgr.copy()
        self._image_h, self._image_w = image_bgr.shape[:2]
        scale_w = self.MAX_DISPLAY_W / max(1, self._image_w)
        scale_h = self.MAX_DISPLAY_H / max(1, self._image_h)
        self._scale = min(1.0, scale_w, scale_h)
        self._refresh_photo()
        self.redraw()
        self._canvas.focus_set()

    def _draw_placeholder(self) -> None:
        if self._image_bgr is not None:
            return
        self._canvas.delete("all")
        w = max(int(self._canvas.winfo_width()), 320)
        h = max(int(self._canvas.winfo_height()), 240)
        self._canvas.configure(scrollregion=(0, 0, w, h))
        self._canvas.create_text(
            w / 2,
            h / 2,
            text="PDF / JPG / PNG をドロップ\nまたは「画像を開く」",
            fill=COLORS["text_muted"],
            font=FONTS.get("body", ("Segoe UI", 10)),
            justify="center",
            tags="placeholder",
        )

    def _status(self, message: str) -> None:
        if self._on_status:
            self._on_status(message)

    def load_image_from_path(self, path: str) -> None:
        self.set_image(load_image_bgr(path))

    def set_regions(self, regions: list[dict[str, Any]]) -> None:
        self.regions = []
        for i, r in enumerate(regions or []):
            self.regions.append(
                {
                    "id": r.get("id") or f"記述欄{i + 1}",
                    "displayName": r.get("displayName") or r.get("id") or f"記述欄{i + 1}",
                    "x": float(r.get("x") or 0),
                    "y": float(r.get("y") or 0),
                    "w": float(r.get("width") or r.get("w") or 0),
                    "h": float(r.get("height") or r.get("h") or 0),
                    "order": int(r.get("order") or i + 1),
                    "ocrLang": "ja" if str(r.get("ocrLang") or "").lower() == "ja" else "en",
                }
            )
        self.selected_idx = -1
        self.redraw()

    def get_regions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, r in enumerate(self.regions):
            out.append(
                {
                    "id": r["id"],
                    "displayName": r.get("displayName") or r["id"],
                    "x": int(round(r["x"])),
                    "y": int(round(r["y"])),
                    "width": int(round(r["w"])),
                    "height": int(round(r["h"])),
                    "order": int(r.get("order") or i + 1),
                    "ocrLang": "ja" if str(r.get("ocrLang") or "").lower() == "ja" else "en",
                }
            )
        return out

    def delete_selected(self) -> None:
        if self.selected_idx < 0:
            return
        self.regions.pop(self.selected_idx)
        self.selected_idx = -1
        self._renumber_orders()
        self.redraw()
        self._notify_change()

    def select_region(self, index: int) -> None:
        if 0 <= index < len(self.regions):
            self.selected_idx = index
            self.redraw()

    def set_region_ocr_lang(self, index: int, lang: str) -> None:
        if 0 <= index < len(self.regions):
            self.regions[index]["ocrLang"] = "ja" if lang == "ja" else "en"
            self._notify_change()

    def _renumber_orders(self) -> None:
        for i, r in enumerate(self.regions):
            r["order"] = i + 1

    def _notify_change(self) -> None:
        if self._on_change:
            self._on_change()

    def _refresh_photo(self) -> None:
        if self._image_bgr is None:
            return
        rgb = cv2.cvtColor(self._image_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        disp_w = max(1, int(self._image_w * self._scale))
        disp_h = max(1, int(self._image_h * self._scale))
        if disp_w != pil.width or disp_h != pil.height:
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            pil = pil.resize((disp_w, disp_h), resample)
        self._photo = ImageTk.PhotoImage(pil)
        self._canvas.configure(scrollregion=(0, 0, disp_w, disp_h))

    def _to_image_coords(self, event: tk.Event) -> tuple[float, float]:
        cx = self._canvas.canvasx(event.x)
        cy = self._canvas.canvasy(event.y)
        return cx / self._scale, cy / self._scale

    def _hit_test_handle(self, r: dict[str, Any], px: float, py: float) -> str | None:
        x, y, w, h = r["x"], r["y"], r["w"], r["h"]
        tol = self.HANDLE / self._scale
        handles = [
            ("nw", x, y),
            ("n", x + w / 2, y),
            ("ne", x + w, y),
            ("e", x + w, y + h / 2),
            ("se", x + w, y + h),
            ("s", x + w / 2, y + h),
            ("sw", x, y + h),
            ("w", x, y + h / 2),
        ]
        for key, hx, hy in handles:
            if abs(hx - px) <= tol and abs(hy - py) <= tol:
                return key
        return None

    def _hit_test_region(self, px: float, py: float) -> int:
        for i in range(len(self.regions) - 1, -1, -1):
            r = self.regions[i]
            if r["x"] <= px <= r["x"] + r["w"] and r["y"] <= py <= r["y"] + r["h"]:
                return i
        return -1

    def _is_center_area(self, r: dict[str, Any], px: float, py: float) -> bool:
        margin_x = r["w"] * 0.25
        margin_y = r["h"] * 0.25
        return (
            r["x"] + margin_x <= px <= r["x"] + r["w"] - margin_x
            and r["y"] + margin_y <= py <= r["y"] + r["h"] - margin_y
        )

    def _on_mouse_down(self, event: tk.Event) -> None:
        if self._image_bgr is None:
            self._status("先に PDF / JPG / PNG の模範解答を読み込んでください")
            return
        px, py = self._to_image_coords(event)
        if self.selected_idx >= 0:
            handle = self._hit_test_handle(self.regions[self.selected_idx], px, py)
            if handle:
                self._drag_state = {
                    "type": "resize",
                    "handle": handle,
                    "start_x": px,
                    "start_y": py,
                    "orig": copy.deepcopy(self.regions[self.selected_idx]),
                }
                return
        idx = self._hit_test_region(px, py)
        if idx >= 0:
            self.selected_idx = idx
            r = self.regions[idx]
            if self._is_center_area(r, px, py):
                self._drag_state = {
                    "type": "move",
                    "start_x": px,
                    "start_y": py,
                    "orig": copy.deepcopy(r),
                }
            self.redraw()
            self._notify_change()
            return
        self.selected_idx = -1
        self._drag_state = {"type": "create", "start_x": px, "start_y": py}
        self.redraw()

    def _on_mouse_move(self, event: tk.Event) -> None:
        if not self._drag_state:
            return
        px, py = self._to_image_coords(event)
        if self._drag_state["type"] == "create":
            self.redraw()
            x0 = self._drag_state["start_x"] * self._scale
            y0 = self._drag_state["start_y"] * self._scale
            x1 = px * self._scale
            y1 = py * self._scale
            self._canvas.create_rectangle(
                x0,
                y0,
                x1,
                y1,
                outline="#9333ea",
                width=2,
                dash=(6, 4),
                tags="preview",
            )
            return
        r = self.regions[self.selected_idx]
        if self._drag_state["type"] == "move":
            orig = self._drag_state["orig"]
            r["x"] = orig["x"] + (px - self._drag_state["start_x"])
            r["y"] = orig["y"] + (py - self._drag_state["start_y"])
        elif self._drag_state["type"] == "resize":
            self._apply_resize(r, self._drag_state["orig"], self._drag_state["handle"], px, py)
        self.redraw()

    def _apply_resize(
        self,
        r: dict[str, Any],
        orig: dict[str, Any],
        handle: str,
        px: float,
        py: float,
    ) -> None:
        dx = px - self._drag_state["start_x"]
        dy = py - self._drag_state["start_y"]
        x, y, w, h = orig["x"], orig["y"], orig["w"], orig["h"]
        min_s = self.MIN_SIZE
        if "e" in handle:
            w = max(min_s, w + dx)
        if "s" in handle:
            h = max(min_s, h + dy)
        if "w" in handle:
            x = x + dx
            w = max(min_s, w - dx)
        if "n" in handle:
            y = y + dy
            h = max(min_s, h - dy)
        r["x"], r["y"], r["w"], r["h"] = x, y, w, h

    def _on_mouse_up(self, event: tk.Event) -> None:
        if not self._drag_state:
            return
        if self._drag_state["type"] == "create":
            px, py = self._to_image_coords(event)
            w = abs(px - self._drag_state["start_x"])
            h = abs(py - self._drag_state["start_y"])
            if w > self.MIN_SIZE and h > self.MIN_SIZE:
                self._add_region(
                    min(self._drag_state["start_x"], px),
                    min(self._drag_state["start_y"], py),
                    w,
                    h,
                )
            else:
                self.redraw()
        else:
            self._notify_change()
        self._drag_state = None

    def _on_hover(self, event: tk.Event) -> None:
        if self._drag_state or self._image_bgr is None:
            return
        px, py = self._to_image_coords(event)
        if self.selected_idx >= 0:
            handle = self._hit_test_handle(self.regions[self.selected_idx], px, py)
            if handle:
                cursor = {
                    "nw": "top_left_corner",
                    "ne": "top_right_corner",
                    "se": "bottom_right_corner",
                    "sw": "bottom_left_corner",
                    "n": "top_side",
                    "s": "bottom_side",
                    "e": "right_side",
                    "w": "left_side",
                }.get(handle, "crosshair")
                self._canvas.configure(cursor=cursor)
                return
        idx = self._hit_test_region(px, py)
        self._canvas.configure(cursor="fleur" if idx >= 0 else "crosshair")

    def _add_region(self, x: float, y: float, w: float, h: float) -> None:
        num = len(self.regions) + 1
        field_id = f"記述欄{num}"
        self.regions.append(
            {
                "id": field_id,
                "displayName": field_id,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "order": num,
                "ocrLang": "en",
            }
        )
        self.selected_idx = len(self.regions) - 1
        self.redraw()
        self._notify_change()

    def redraw(self) -> None:
        self._canvas.delete("all")
        if self._photo:
            self._canvas.create_image(0, 0, anchor="nw", image=self._photo, tags="bg")
        for idx, r in enumerate(self.regions):
            x = r["x"] * self._scale
            y = r["y"] * self._scale
            w = r["w"] * self._scale
            h = r["h"] * self._scale
            selected = idx == self.selected_idx
            stroke = "#2563eb" if selected else "#16a34a"
            fill = "#2563eb22" if selected else "#16a34a22"
            self._canvas.create_rectangle(x, y, x + w, y + h, outline=stroke, width=2, fill=fill)
            label = r.get("displayName") or r["id"]
            self._canvas.create_text(
                x + 4,
                y + 4,
                anchor="nw",
                text=label,
                fill=COLORS["accent"],
                font=(FONTS["body"][0], 10, "bold"),
            )
            if selected:
                self._draw_handles(x, y, w, h)

    def _draw_handles(self, x: float, y: float, w: float, h: float) -> None:
        points = [
            (x, y),
            (x + w / 2, y),
            (x + w, y),
            (x + w, y + h / 2),
            (x + w, y + h),
            (x + w / 2, y + h),
            (x, y + h),
            (x, y + h / 2),
        ]
        hs = self.HANDLE
        for px, py in points:
            self._canvas.create_rectangle(
                px - hs / 2,
                py - hs / 2,
                px + hs / 2,
                py + hs / 2,
                fill="#2563eb",
                outline="",
            )
