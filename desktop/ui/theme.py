"""アプリ全体のモダン UI テーマ（フォント・色・ttk スタイル）。"""

from __future__ import annotations

import platform
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

try:
    import sv_ttk

    _HAS_SV_TTK = True
except ImportError:
    _HAS_SV_TTK = False


# --- カラーパレット（ライト） ---
COLORS = {
    "bg": "#f8fafc",
    "surface": "#ffffff",
    "sidebar": "#f1f5f9",
    "sidebar_border": "#e2e8f0",
    "text": "#0f172a",
    "text_secondary": "#64748b",
    "text_muted": "#94a3b8",
    "border": "#e2e8f0",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_soft": "#eff6ff",
    "accent_text": "#ffffff",
    "success": "#16a34a",
    "danger": "#dc2626",
    "danger_soft": "#fef2f2",
    "canvas_bg": "#ffffff",
    "input_bg": "#ffffff",
    "select_bg": "#dbeafe",
}

FONTS: dict[str, tuple] = {}


def _pick_family() -> str:
    available = set(tkfont.families())
    for name in (
        "Yu Gothic UI",
        "Meiryo UI",
        "Segoe UI Variable",
        "Segoe UI",
        "Helvetica Neue",
        "Arial",
    ):
        if name in available:
            return name
    return "TkDefaultFont"


def _build_fonts(root: tk.Misc) -> None:
    family = _pick_family()
    FONTS.clear()
    FONTS.update(
        {
            "title": (family, 16, "bold"),
            "heading": (family, 13, "bold"),
            "subheading": (family, 11, "bold"),
            "body": (family, 10),
            "small": (family, 9),
            "caption": (family, 8),
            "mono": ("Consolas", 9) if "Consolas" in set(tkfont.families()) else (family, 9),
        }
    )
    root.option_add("*Font", FONTS["body"])
    root.option_add("*TButton*Font", FONTS["body"])
    root.option_add("*TLabel*Font", FONTS["body"])
    root.option_add("*TEntry*Font", FONTS["body"])


def apply_theme(root: tk.Misc, *, dark: bool = False) -> ttk.Style:
    """ルートウィンドウにモダンテーマを適用する。"""
    if _HAS_SV_TTK:
        sv_ttk.set_theme("dark" if dark else "light")

    if isinstance(root, (tk.Tk, tk.Toplevel)):
        try:
            root.configure(bg=COLORS["bg"])
        except tk.TclError:
            pass

    _build_fonts(root)
    style = ttk.Style(root)

    # ベース
    style.configure(".", font=FONTS["body"])
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("TLabelframe", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("TLabelframe.Label", font=FONTS["subheading"], foreground=COLORS["text"])
    style.configure("TButton", padding=(14, 8), font=FONTS["body"])
    style.configure("TEntry", padding=6)
    style.configure("TCombobox", padding=4)
    style.configure("Treeview", rowheight=28, font=FONTS["body"])
    style.configure("Treeview.Heading", font=FONTS["subheading"])
    style.configure("Horizontal.TProgressbar", thickness=8)
    style.configure("Vertical.TScrollbar", gripcount=0)

    # タイポグラフィ
    style.configure("Title.TLabel", font=FONTS["title"], foreground=COLORS["text"], background=COLORS["bg"])
    style.configure("Heading.TLabel", font=FONTS["heading"], foreground=COLORS["text"], background=COLORS["bg"])
    style.configure("Subheading.TLabel", font=FONTS["subheading"], foreground=COLORS["text"], background=COLORS["bg"])
    style.configure("Muted.TLabel", font=FONTS["small"], foreground=COLORS["text_secondary"], background=COLORS["bg"])
    style.configure("Body.TLabel", font=FONTS["body"], foreground=COLORS["text"], background=COLORS["bg"])
    style.configure("Caption.TLabel", font=FONTS["caption"], foreground=COLORS["text_muted"], background=COLORS["bg"])

    # サイドバー
    style.configure("Sidebar.TFrame", background=COLORS["sidebar"])
    style.configure(
        "SidebarTitle.TLabel",
        font=FONTS["subheading"],
        foreground=COLORS["text"],
        background=COLORS["sidebar"],
    )
    style.configure(
        "SidebarMuted.TLabel",
        font=FONTS["caption"],
        foreground=COLORS["text_secondary"],
        background=COLORS["sidebar"],
        wraplength=200,
    )
    style.configure(
        "Nav.TButton",
        padding=(12, 10),
        font=FONTS["body"],
        anchor="w",
    )
    style.configure(
        "NavActive.TButton",
        padding=(12, 10),
        font=FONTS["body"],
        anchor="w",
    )
    style.map(
        "NavActive.TButton",
        background=[("!disabled", COLORS["accent"]), ("active", COLORS["accent_hover"])],
        foreground=[("!disabled", COLORS["accent_text"])],
    )
    style.configure("NavDisabled.TButton", padding=(12, 10), font=FONTS["body"], anchor="w")

    # アクセントボタン
    style.configure("Primary.TButton", font=FONTS["body"], padding=(16, 9))
    style.configure("Accent.TButton", font=FONTS["body"], padding=(14, 8))
    style.map(
        "Primary.TButton",
        background=[("!disabled", COLORS["accent"]), ("active", COLORS["accent_hover"])],
        foreground=[("!disabled", COLORS["accent_text"])],
    )

    # コンテンツエリア
    style.configure("Content.TFrame", background=COLORS["bg"])

    return style


def style_listbox(widget: tk.Listbox) -> None:
    widget.configure(
        font=FONTS.get("body", ("Segoe UI", 10)),
        bg=COLORS["surface"],
        fg=COLORS["text"],
        selectbackground=COLORS["accent"],
        selectforeground=COLORS["accent_text"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["accent"],
        bd=0,
        activestyle="none",
    )


def style_text(widget: tk.Text) -> None:
    widget.configure(
        font=FONTS.get("mono", ("Consolas", 9)),
        bg=COLORS["surface"],
        fg=COLORS["text"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["accent"],
        bd=0,
        padx=8,
        pady=8,
        insertbackground=COLORS["accent"],
    )


def style_canvas(widget: tk.Canvas, *, bg: str | None = None) -> None:
    widget.configure(
        bg=bg or COLORS["canvas_bg"],
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["accent"],
        bd=0,
    )


def is_windows() -> bool:
    return platform.system() == "Windows"
