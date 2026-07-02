"""共通画像合成エンジン（編集プレビュー・個票出力の共用描画層）。

方針:
- 入力 UI（Tkinter / 将来の Web・Qt）は座標やストロークの「データ」だけを扱う。
- 半透明の見た目はすべてここ（Pillow の alpha_composite）で作る。
  Tkinter に透明度を求めない。印刷・個票出力も同じ合成関数を通す。

手書きストロークの JSON スキーマ（スタイラス入力層と共有する契約）:
{
  "fieldId": "記述欄1",
  "color": "#111827",
  "alpha": 1.0,
  "baseWidth": 2.5,                       # 基準線幅（補正後画像ピクセル）
  "points": [{"x": 120.0, "y": 340.0, "p": 0.8}, ...]   # p = 筆圧 0..1
}
座標は必ず「補正後（warp 済み）画像のピクセル座標」で保存する。
表示スケールへの変換は合成時に scale 引数で行う。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

# GAS 版 RegionEditor の rgba(…, 0.12〜0.15) に合わせたスタイル
REGION_STROKE_NORMAL = "#16a34a"
REGION_STROKE_SELECTED = "#2563eb"
REGION_FILL_ALPHA = 0.12
REGION_FILL_ALPHA_SELECTED = 0.18

_RESAMPLE = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS


def hex_to_rgba(hex_color: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    h = str(hex_color or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    a = max(0, min(255, round(float(alpha) * 255)))
    return (r, g, b, a)


def bgr_to_rgba_image(image_bgr: np.ndarray) -> Image.Image:
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb).convert("RGBA")


def prepare_display_base(image_bgr: np.ndarray, display_size: tuple[int, int]) -> Image.Image:
    """表示用に縮小した RGBA 底画像を作る（呼び出し側でキャッシュする想定）。"""
    base = bgr_to_rgba_image(image_bgr)
    if display_size != base.size:
        base = base.resize(display_size, _RESAMPLE)
    return base


def render_region_fills(
    size: tuple[int, int],
    regions: list[dict[str, Any]],
    selected_idx: int = -1,
    scale: float = 1.0,
    exclude_idx: int | None = None,
) -> Image.Image:
    """記述欄の半透明塗りだけを RGBA レイヤーとして描く（輪郭は UI 側）。"""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for idx, r in enumerate(regions):
        if idx == exclude_idx:
            continue
        x = float(r["x"]) * scale
        y = float(r["y"]) * scale
        w = float(r.get("w") or r.get("width") or 0) * scale
        h = float(r.get("h") or r.get("height") or 0) * scale
        if w <= 0 or h <= 0:
            continue
        selected = idx == selected_idx
        color = REGION_STROKE_SELECTED if selected else REGION_STROKE_NORMAL
        alpha = REGION_FILL_ALPHA_SELECTED if selected else REGION_FILL_ALPHA
        draw.rectangle([x, y, x + w, y + h], fill=hex_to_rgba(color, alpha))
    return layer


def render_ink_layer(
    size: tuple[int, int],
    strokes: list[dict[str, Any]],
    scale: float = 1.0,
) -> Image.Image:
    """手書きストローク（筆圧付き）を RGBA レイヤーとして描く。"""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for stroke in strokes or []:
        points = stroke.get("points") or []
        if not points:
            continue
        color = hex_to_rgba(stroke.get("color") or "#111827", float(stroke.get("alpha", 1.0)))
        base_width = float(stroke.get("baseWidth") or 2.0) * scale

        def seg_width(pressure: float) -> float:
            # 筆圧 0..1 → 線幅 50%〜100%
            return max(1.0, base_width * (0.5 + 0.5 * max(0.0, min(1.0, pressure))))

        if len(points) == 1:
            p = points[0]
            r = seg_width(float(p.get("p", 1.0))) / 2
            cx, cy = float(p["x"]) * scale, float(p["y"]) * scale
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
            continue

        for a, b in zip(points, points[1:]):
            ax, ay = float(a["x"]) * scale, float(a["y"]) * scale
            bx, by = float(b["x"]) * scale, float(b["y"]) * scale
            w = seg_width(float(b.get("p", 1.0)))
            draw.line([ax, ay, bx, by], fill=color, width=round(w))
            # 継ぎ目を丸めて折れ線のギャップを消す
            r = w / 2
            draw.ellipse([bx - r, by - r, bx + r, by + r], fill=color)
        p0 = points[0]
        r0 = seg_width(float(p0.get("p", 1.0))) / 2
        x0, y0 = float(p0["x"]) * scale, float(p0["y"]) * scale
        draw.ellipse([x0 - r0, y0 - r0, x0 + r0, y0 + r0], fill=color)
    return layer


def composite_over_base(
    base_rgba: Image.Image,
    regions: list[dict[str, Any]] | None = None,
    selected_idx: int = -1,
    scale: float = 1.0,
    exclude_idx: int | None = None,
    strokes: list[dict[str, Any]] | None = None,
) -> Image.Image:
    """キャッシュ済み底画像の上に塗り・インクを合成して RGB を返す（プレビュー用）。"""
    out = base_rgba
    if regions:
        out = Image.alpha_composite(
            out, render_region_fills(out.size, regions, selected_idx, scale, exclude_idx)
        )
    if strokes:
        out = Image.alpha_composite(out, render_ink_layer(out.size, strokes, scale))
    return out.convert("RGB")


def composite_output(
    base_bgr: np.ndarray,
    strokes: list[dict[str, Any]] | None = None,
    regions: list[dict[str, Any]] | None = None,
    include_region_fills: bool = False,
) -> Image.Image:
    """フル解像度の出力合成（⑩個票・印刷用）。scale=1.0 固定。

    通常の個票では記述欄の枠は載せないため regions は省略可。
    """
    base = bgr_to_rgba_image(base_bgr)
    out = base
    if include_region_fills and regions:
        out = Image.alpha_composite(out, render_region_fills(out.size, regions, scale=1.0))
    if strokes:
        out = Image.alpha_composite(out, render_ink_layer(out.size, strokes, scale=1.0))
    return out.convert("RGB")


def save_jpeg(image: Image.Image, path: str | Path, quality: int = 90) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, "JPEG", quality=quality)
