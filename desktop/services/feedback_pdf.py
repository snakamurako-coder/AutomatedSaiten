"""⑩ 個票のベクトル PDF 出力（手書き・テキスト・判定マークをベクトル描画）。"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np
from PIL import Image

from services.compositor import hex_to_rgba
from services.feedback_renderer import (
    TOTAL_FRAME_FILL,
    TOTAL_FRAME_HEADING,
    TOTAL_FRAME_STROKE,
    _inset_rect,
    format_total_text,
    frame_print_metrics,
    normalize_judgment,
    slot_heading,
    slot_prints_frame,
)
from services.image_loader import imread_bgr

_FONT_CANDIDATES = ["meiryo.ttc", "meiryb.ttc", "YuGothM.ttc", "msgothic.ttc", "arial.ttf"]
_FONT_CANDIDATES_BOLD = ["meiryb.ttc", "meiryo.ttc", "YuGothB.ttc", "msgothic.ttc", "arialbd.ttf"]


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    r, g, b, _a = hex_to_rgba(hex_color, 1.0)
    return r / 255.0, g / 255.0, b / 255.0


def _resolve_font_file(*, bold: bool = False, preferred: str | None = None) -> str:
    names: list[str] = []
    if preferred:
        names.append(str(preferred))
    names.extend(_FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES)
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        direct = Path(name)
        if direct.is_file():
            return str(direct)
        cand = fonts_dir / name
        if cand.is_file():
            return str(cand)
    raise FileNotFoundError("日本語フォントが見つかりません（meiryo.ttc 等）")


def _fit_font_size(
    text: str,
    font_path: str,
    max_size: float,
    max_width: float,
    *,
    min_size: float = 8.0,
) -> float:
    font = fitz.Font(fontfile=font_path)
    size = max(min_size, float(max_size))
    while size > min_size:
        if font.text_length(text, fontsize=size) <= max_width:
            return size
        size = max(min_size, size * 0.9)
    return size


def _write_font_text(
    page: fitz.Page,
    x: float,
    y: float,
    text: str,
    fontsize: float,
    color: tuple[float, float, float],
    fontfile: str,
    morph: tuple[fitz.Point, fitz.Matrix] | None = None,
) -> None:
    """ベースライン左端 (x, y) に文字を置く。日本語は TextWriter でないと欠ける。"""
    font = fitz.Font(fontfile=fontfile)
    writer = fitz.TextWriter(page.rect)
    writer.append(fitz.Point(x, y), text, font=font, fontsize=fontsize)
    writer.write_text(page, color=color, morph=morph)


def _draw_centered_textbox(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    color: tuple[float, float, float],
    font_size: float,
    *,
    bold: bool = False,
    font_path: str | None = None,
) -> None:
    if not text:
        return
    path = font_path or _resolve_font_file(bold=bold)
    fs = _fit_font_size(text, path, font_size, rect.width * 0.92, min_size=8.0)
    font = fitz.Font(fontfile=path)
    text_w = float(font.text_length(text, fontsize=fs))
    asc = float(font.ascender)
    desc = float(font.descender)
    left = rect.x0 + (rect.width - text_w) / 2.0
    baseline = rect.y0 + rect.height / 2.0 + (asc + desc) * fs / 2.0
    _write_font_text(page, left, baseline, text, fs, color, path)


def _draw_mark_pdf(
    page: fitz.Page,
    x: float,
    y: float,
    w: float,
    h: float,
    judgment: str,
    score: Any,
    style: dict[str, Any],
) -> None:
    kind = normalize_judgment(judgment, score)
    if kind is None:
        return
    mark_style = style["mark"]
    ix, iy, iw, ih = _inset_rect(x, y, w, h, float(mark_style.get("insetRatio", 0.05)))
    min_dim = min(iw, ih)
    rect = fitz.Rect(ix, iy, ix + iw, iy + ih)
    shape = page.new_shape()

    if kind == "maru":
        st = mark_style["maru"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.06)))
        fill = _hex_to_rgb01(st["strokeColor"])
        stroke = _hex_to_rgb01(st["strokeColor"])
        shape.draw_oval(rect)
        shape.finish(
            width=line_w,
            color=stroke,
            fill=fill,
            fill_opacity=float(st.get("fillOpacity", 0.12)),
            stroke_opacity=float(st.get("strokeOpacity", 1.0)),
            closePath=False,
        )
        shape.commit()
    elif kind == "sankaku":
        st = mark_style["sankaku"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.06)))
        color = _hex_to_rgb01(st["strokeColor"])
        points = [
            fitz.Point(ix + iw / 2, iy),
            fitz.Point(ix + iw, iy + ih),
            fitz.Point(ix, iy + ih),
            fitz.Point(ix + iw / 2, iy),
        ]
        shape.draw_polyline(points)
        shape.finish(
            width=line_w,
            color=color,
            stroke_opacity=float(st.get("strokeOpacity", 1.0)),
            closePath=False,
        )
        shape.commit()
    else:
        st = mark_style["batsu"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.08)))
        color = _hex_to_rgb01(st["strokeColor"])
        for p1, p2 in (
            (fitz.Point(ix, iy), fitz.Point(ix + iw, iy + ih)),
            (fitz.Point(ix + iw, iy), fitz.Point(ix, iy + ih)),
        ):
            seg = page.new_shape()
            seg.draw_line(p1, p2)
            seg.finish(
                width=line_w,
                color=color,
                stroke_opacity=float(st.get("strokeOpacity", 1.0)),
                closePath=False,
            )
            seg.commit()

    score_text = "" if score is None else str(score).strip()
    if not score_text:
        return
    try:
        if kind == "batsu" and float(score_text) == 0:
            return
    except ValueError:
        pass
    sc = mark_style["score"]
    _draw_centered_textbox(
        page,
        rect,
        score_text,
        _hex_to_rgb01(sc["color"]),
        min_dim * float(sc.get("sizeRatio", 0.35)),
        bold=True,
    )


def _draw_topleft_text_mapped(
    page: fitz.Page,
    matrix: np.ndarray,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    color: tuple[float, float, float],
    font_size: float,
    *,
    bold: bool = False,
) -> None:
    """(x, y) を左上として、用紙の向きに沿った文字を置く。"""
    if not text or w <= 0 or h <= 0:
        return
    path = _resolve_font_file(bold=bold)
    target = min(font_size, h * 0.9)
    fs = _fit_font_size(text, path, target, w, min_size=max(8.0, target * 0.72))
    font = fitz.Font(fontfile=path)
    baseline = y + float(font.ascender) * fs
    px, py = _map_xy(matrix, x, baseline)
    right, down, _scale = _axes_at(matrix, x, baseline)
    _write_font_text(
        page,
        px,
        py,
        text,
        fs,
        color,
        path,
        morph=(fitz.Point(px, py), _text_morph(right, down)),
    )


def _identity_page_matrix() -> np.ndarray:
    """補正画像の PDF はページ座標＝画素座標なので、写像は恒等。"""
    return np.eye(3, dtype=np.float32)


def _draw_total_frame_pdf(
    page: fitz.Page,
    matrix: np.ndarray,
    x: float,
    y: float,
    w: float,
    h: float,
    slot: dict[str, Any],
    value: Any,
    style: dict[str, Any],
) -> None:
    if w <= 1 or h <= 1:
        return
    _right, _down, scale = _axes_at(matrix, x + w / 2.0, y + h / 2.0)
    scale = max(float(scale), 0.2)
    line_w, head_page, band_page, score_page = frame_print_metrics(
        float(page.rect.width), w * scale, h * scale, style
    )
    head_size = head_page / scale
    band = band_page / scale
    score_size = score_page / scale
    quad = _map_pts(
        matrix,
        [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)],
    )
    fill = page.new_shape()
    fill.draw_polyline(quad)
    fill.finish(
        color=_hex_to_rgb01(TOTAL_FRAME_FILL),
        fill=_hex_to_rgb01(TOTAL_FRAME_FILL),
        width=0,
        closePath=True,
    )
    fill.commit()
    stroke = page.new_shape()
    stroke.draw_polyline(quad)
    stroke.finish(
        color=_hex_to_rgb01(TOTAL_FRAME_STROKE),
        width=line_w,
        stroke_opacity=1,
        closePath=True,
        lineJoin=1,
        lineCap=1,
    )
    stroke.commit()
    pad = max(line_w / scale, min(w, h) * 0.06)
    _draw_topleft_text_mapped(
        page,
        matrix,
        x + pad,
        y + pad,
        max(8.0, w - pad * 2),
        max(8.0, band - pad),
        slot_heading(slot),
        _hex_to_rgb01(TOTAL_FRAME_HEADING),
        head_size,
        bold=True,
    )
    if value is None or str(value) == "":
        return
    st = style["total"]
    _draw_centered_text_mapped(
        page,
        matrix,
        x + pad,
        y + band,
        max(8.0, w - pad * 2),
        max(8.0, h - band - pad),
        str(value),
        _hex_to_rgb01(st["color"]),
        score_size,
        bold=True,
    )


def _draw_total_pdf(
    page: fitz.Page,
    slot: dict[str, Any],
    value: Any,
    style: dict[str, Any],
) -> None:
    has_value = value is not None and str(value) != ""
    if slot_prints_frame(slot):
        _draw_total_frame_pdf(
            page,
            _identity_page_matrix(),
            float(slot["x"]),
            float(slot["y"]),
            float(slot["width"]),
            float(slot["height"]),
            slot,
            value if has_value else None,
            style,
        )
        return
    if not has_value:
        return
    st = style["total"]
    x, y = float(slot["x"]), float(slot["y"])
    w, h = float(slot["width"]), float(slot["height"])
    font_size = max(
        float(st.get("minFontSize", 10)), min(w, h) * float(st.get("sizeRatio", 0.5))
    )
    _draw_centered_textbox(
        page,
        fitz.Rect(x, y, x + w, y + h),
        format_total_text(slot, value),
        _hex_to_rgb01(st["color"]),
        font_size,
        bold=True,
    )


def _draw_text_annotations_pdf(
    page: fitz.Page,
    annotations: list[dict[str, Any]],
) -> None:
    from services.text_rich_render import draw_annotation_pdf

    for box in annotations or []:
        draw_annotation_pdf(page, box)


def _stroke_mean_width(base_width: float, points: list[dict[str, Any]]) -> float:
    """筆圧の平均からストローク一定幅を算出（compositor と同式）。"""
    if not points:
        return max(1.0, float(base_width))
    pressures = [float(p.get("p", 1.0)) for p in points]
    mean_p = sum(pressures) / len(pressures)
    mean_p = max(0.0, min(1.0, mean_p))
    return max(1.0, float(base_width) * (0.5 + 0.5 * mean_p))


def _draw_ink_strokes_pdf(page: fitz.Page, strokes: list[dict[str, Any]]) -> None:
    """手書きをストローク単位のベクトル polyline（round cap/join）で描く。"""
    for stroke in strokes or []:
        points = stroke.get("points") or []
        if not points:
            continue
        color = _hex_to_rgb01(stroke.get("color") or "#111827")
        alpha = float(stroke.get("alpha", 1.0))
        base_width = float(stroke.get("baseWidth") or 2.5)
        width = _stroke_mean_width(base_width, points)
        shape = page.new_shape()
        if len(points) == 1:
            p = points[0]
            r = width / 2
            cx, cy = float(p["x"]), float(p["y"])
            shape.draw_oval(fitz.Rect(cx - r, cy - r, cx + r, cy + r))
            shape.finish(
                color=color,
                fill=color,
                fill_opacity=alpha,
                width=0,
                closePath=True,
            )
        else:
            pts = [fitz.Point(float(p["x"]), float(p["y"])) for p in points]
            shape.draw_polyline(pts)
            shape.finish(
                width=width,
                color=color,
                stroke_opacity=alpha,
                closePath=False,
                lineCap=1,
                lineJoin=1,
            )
        shape.commit()


def _warp_to_page_matrix(warp_w: int, warp_h: int, corners: Any) -> np.ndarray:
    """補正画像の画素座標を、元画像上の切り出し四隅へ写す射影行列。"""
    src = np.float32([[0, 0], [warp_w, 0], [warp_w, warp_h], [0, warp_h]])
    dst = np.float32(
        [
            [float(corners.tl[0]), float(corners.tl[1])],
            [float(corners.tr[0]), float(corners.tr[1])],
            [float(corners.br[0]), float(corners.br[1])],
            [float(corners.bl[0]), float(corners.bl[1])],
        ]
    )
    return cv2.getPerspectiveTransform(src, dst)


def _map_xy(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
    pt = cv2.perspectiveTransform(np.float32([[[x, y]]]), matrix)
    return float(pt[0, 0, 0]), float(pt[0, 0, 1])


def _map_pts(matrix: np.ndarray, pts: list[tuple[float, float]]) -> list[fitz.Point]:
    if not pts:
        return []
    arr = np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2)
    mapped = cv2.perspectiveTransform(arr, matrix).reshape(-1, 2)
    return [fitz.Point(float(p[0]), float(p[1])) for p in mapped]


def _axes_at(
    matrix: np.ndarray, x: float, y: float
) -> tuple[tuple[float, float], tuple[float, float], float]:
    """補正画像の +x / +y が、元画像上でどの向き・倍率になるか。"""
    ox, oy = _map_xy(matrix, x, y)
    rx, ry = _map_xy(matrix, x + 1.0, y)
    dx, dy = _map_xy(matrix, x, y + 1.0)
    right = (rx - ox, ry - oy)
    down = (dx - ox, dy - oy)
    scale = max(0.2, (math.hypot(*right) + math.hypot(*down)) / 2.0)
    return right, down, scale


def _text_morph(right: tuple[float, float], down: tuple[float, float]) -> fitz.Matrix:
    """用紙の向きに文字を沿わせる。morph は PDF の上向き Y で掛かる。"""
    rx, ry = right
    dx, dy = down
    return fitz.Matrix(rx, -ry, -dx, dy, 0, 0)


def _ellipse_pts(x: float, y: float, w: float, h: float, n: int = 0) -> list[tuple[float, float]]:
    cx, cy = x + w / 2.0, y + h / 2.0
    rx, ry = w / 2.0, h / 2.0
    if n <= 0:
        # 補正画像上で約1px間隔。射影後も折れ線に見えない密度にする。
        n = max(72, int(math.pi * max(w, h, 1.0)))
    pts = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        pts.append((cx + rx * math.cos(t), cy + ry * math.sin(t)))
    pts.append(pts[0])
    return pts


def _draw_centered_text_mapped(
    page: fitz.Page,
    matrix: np.ndarray,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    color: tuple[float, float, float],
    font_size: float,
    *,
    bold: bool = False,
) -> None:
    if not text or w <= 0 or h <= 0:
        return
    path = _resolve_font_file(bold=bold)
    fs = _fit_font_size(text, path, font_size, w * 0.92, min_size=8.0)
    font = fitz.Font(fontfile=path)
    text_w = float(font.text_length(text, fontsize=fs))
    asc = float(font.ascender)
    desc = float(font.descender)
    left = x + (w - text_w) / 2.0
    baseline = y + h / 2.0 + (asc + desc) * fs / 2.0
    px, py = _map_xy(matrix, left, baseline)
    right, down, _scale = _axes_at(matrix, left, baseline)
    _write_font_text(
        page,
        px,
        py,
        text,
        fs,
        color,
        path,
        morph=(fitz.Point(px, py), _text_morph(right, down)),
    )


def _finish_mapped_stroke(
    shape: fitz.Shape,
    *,
    width: float,
    color: tuple[float, float, float],
    fill: tuple[float, float, float] | None = None,
    fill_opacity: float = 1.0,
    stroke_opacity: float = 1.0,
    close_path: bool = False,
) -> None:
    shape.finish(
        width=width,
        color=color,
        fill=fill,
        fill_opacity=fill_opacity,
        stroke_opacity=stroke_opacity,
        closePath=close_path,
        lineCap=1,
        lineJoin=1,
    )
    shape.commit()


def _draw_mark_pdf_mapped(
    page: fitz.Page,
    matrix: np.ndarray,
    x: float,
    y: float,
    w: float,
    h: float,
    judgment: str,
    score: Any,
    style: dict[str, Any],
) -> None:
    kind = normalize_judgment(judgment, score)
    if kind is None:
        return
    mark_style = style["mark"]
    ix, iy, iw, ih = _inset_rect(x, y, w, h, float(mark_style.get("insetRatio", 0.05)))
    min_dim = min(iw, ih)
    _right, _down, scale = _axes_at(matrix, ix + iw / 2.0, iy + ih / 2.0)

    if kind == "maru":
        st = mark_style["maru"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.06))) * scale
        color = _hex_to_rgb01(st["strokeColor"])
        shape = page.new_shape()
        shape.draw_polyline(_map_pts(matrix, _ellipse_pts(ix, iy, iw, ih)))
        _finish_mapped_stroke(
            shape,
            width=line_w,
            color=color,
            fill=color,
            fill_opacity=float(st.get("fillOpacity", 0.12)),
            stroke_opacity=float(st.get("strokeOpacity", 1.0)),
            close_path=True,
        )
    elif kind == "sankaku":
        st = mark_style["sankaku"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.06))) * scale
        color = _hex_to_rgb01(st["strokeColor"])
        points = [
            (ix + iw / 2, iy),
            (ix + iw, iy + ih),
            (ix, iy + ih),
            (ix + iw / 2, iy),
        ]
        shape = page.new_shape()
        shape.draw_polyline(_map_pts(matrix, points))
        _finish_mapped_stroke(
            shape,
            width=line_w,
            color=color,
            stroke_opacity=float(st.get("strokeOpacity", 1.0)),
            close_path=True,
        )
    else:
        st = mark_style["batsu"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.08))) * scale
        color = _hex_to_rgb01(st["strokeColor"])
        for p1, p2 in (
            ((ix, iy), (ix + iw, iy + ih)),
            ((ix + iw, iy), (ix, iy + ih)),
        ):
            shape = page.new_shape()
            shape.draw_line(_map_pts(matrix, [p1])[0], _map_pts(matrix, [p2])[0])
            _finish_mapped_stroke(
                shape,
                width=line_w,
                color=color,
                stroke_opacity=float(st.get("strokeOpacity", 1.0)),
            )

    score_text = "" if score is None else str(score).strip()
    if not score_text:
        return
    try:
        if kind == "batsu" and float(score_text) == 0:
            return
    except ValueError:
        pass
    sc = mark_style["score"]
    _draw_centered_text_mapped(
        page,
        matrix,
        ix,
        iy,
        iw,
        ih,
        score_text,
        _hex_to_rgb01(sc["color"]),
        min_dim * float(sc.get("sizeRatio", 0.35)),
        bold=True,
    )


def _draw_total_pdf_mapped(
    page: fitz.Page,
    matrix: np.ndarray,
    slot: dict[str, Any],
    value: Any,
    style: dict[str, Any],
) -> None:
    has_value = value is not None and str(value) != ""
    x, y = float(slot["x"]), float(slot["y"])
    w, h = float(slot["width"]), float(slot["height"])
    if slot_prints_frame(slot):
        _draw_total_frame_pdf(
            page,
            matrix,
            x,
            y,
            w,
            h,
            slot,
            value if has_value else None,
            style,
        )
        return
    if not has_value:
        return
    st = style["total"]
    font_size = max(float(st.get("minFontSize", 10)), min(w, h) * float(st.get("sizeRatio", 0.5)))
    _draw_centered_text_mapped(
        page,
        matrix,
        x,
        y,
        w,
        h,
        format_total_text(slot, value),
        _hex_to_rgb01(st["color"]),
        font_size,
        bold=True,
    )


def _draw_ink_strokes_mapped(
    page: fitz.Page, matrix: np.ndarray, strokes: list[dict[str, Any]]
) -> None:
    for stroke in strokes or []:
        points = stroke.get("points") or []
        if not points:
            continue
        color = _hex_to_rgb01(stroke.get("color") or "#111827")
        alpha = float(stroke.get("alpha", 1.0))
        base_width = float(stroke.get("baseWidth") or 2.5)
        width = _stroke_mean_width(base_width, points)
        raw = [(float(p["x"]), float(p["y"])) for p in points]
        mapped = _map_pts(matrix, raw)
        mid = raw[len(raw) // 2]
        _right, _down, scale = _axes_at(matrix, mid[0], mid[1])
        page_width = max(1.0, width * scale)
        shape = page.new_shape()
        if len(mapped) == 1:
            r = page_width / 2.0
            cx, cy = mapped[0].x, mapped[0].y
            shape.draw_oval(fitz.Rect(cx - r, cy - r, cx + r, cy + r))
            shape.finish(
                color=color,
                fill=color,
                fill_opacity=alpha,
                width=0,
                closePath=True,
            )
        else:
            shape.draw_polyline(mapped)
            shape.finish(
                width=page_width,
                color=color,
                stroke_opacity=alpha,
                closePath=False,
                lineCap=1,
                lineJoin=1,
            )
        shape.commit()


def _insert_annotation_patch(
    page: fitz.Page,
    box: dict[str, Any],
    style: dict[str, Any],
    quad: list[fitz.Point],
    page_w: int,
    page_h: int,
) -> None:
    from services.text_rich_render import render_box_html_patch

    w = max(20.0, float(box.get("width") or 32))
    edge = math.hypot(quad[1].x - quad[0].x, quad[1].y - quad[0].y)
    scale = max(2.0, edge / max(w, 1.0))
    box_for_render = dict(box)
    render_style = dict(style)
    render_style["fillAlpha"] = 0.0
    box_for_render["style"] = render_style
    patch = render_box_html_patch(box_for_render, scale=scale)
    if patch is None or patch.getbbox() is None:
        return
    pw, ph = patch.size
    xs = [p.x for p in quad]
    ys = [p.y for p in quad]
    minx = max(0, int(math.floor(min(xs))))
    miny = max(0, int(math.floor(min(ys))))
    maxx = min(page_w, int(math.ceil(max(xs))))
    maxy = min(page_h, int(math.ceil(max(ys))))
    if maxx - minx < 2 or maxy - miny < 2:
        return
    src = np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]])
    dst = np.float32([[p.x - minx, p.y - miny] for p in quad])
    bgra = cv2.cvtColor(np.array(patch.convert("RGBA")), cv2.COLOR_RGBA2BGRA)
    placed = cv2.warpPerspective(
        bgra,
        cv2.getPerspectiveTransform(src, dst),
        (maxx - minx, maxy - miny),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    ok, buf = cv2.imencode(".png", placed)
    if not ok:
        return
    page.insert_image(
        fitz.Rect(minx, miny, maxx, maxy),
        stream=bytes(buf),
        keep_proportion=False,
    )


def _draw_text_annotations_mapped(
    page: fitz.Page,
    annotations: list[dict[str, Any]],
    matrix: np.ndarray,
    page_w: int,
    page_h: int,
) -> None:
    from models.text_annotation_repo import resolve_text_style

    for box in annotations or []:
        st = resolve_text_style(box.get("style") or {})
        x = float(box.get("x") or 0)
        y = float(box.get("y") or 0)
        w = max(20.0, float(box.get("width") or 32))
        h = max(12.0, float(box.get("height") or 18))
        quad = _map_pts(matrix, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
        fill_alpha = float(st.get("fillAlpha") or 0)
        if fill_alpha > 0:
            fc = _hex_to_rgb01(st.get("fillColor") or "#ffffff")
            shape = page.new_shape()
            shape.draw_polyline(quad + [quad[0]])
            shape.finish(
                color=fc,
                fill=fc,
                fill_opacity=fill_alpha,
                width=0,
                closePath=True,
            )
            shape.commit()
        try:
            _insert_annotation_patch(page, box, st, quad, page_w, page_h)
        except Exception:
            continue


def build_feedback_pdf_document(
    warped_path: str,
    fields: list[dict[str, Any]],
    output_slots: list[dict[str, Any]],
    field_marks: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    style: dict[str, Any],
    *,
    ink_strokes: list[dict[str, Any]] | None = None,
    text_annotations: list[dict[str, Any]] | None = None,
    jpeg_quality: int = 92,
) -> fitz.Document:
    """個票 PDF をメモリ上に構築する。呼び出し側で close() すること。"""
    bgr = imread_bgr(warped_path)
    if bgr is None:
        raise ValueError(f"補正画像を読み込めません: {warped_path}")

    h_px, w_px = bgr.shape[:2]
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    if not ok:
        raise ValueError("補正画像の JPEG エンコードに失敗しました")

    doc = fitz.open()
    page = doc.new_page(width=w_px, height=h_px)
    page.insert_image(fitz.Rect(0, 0, w_px, h_px), stream=bytes(buf))

    for f in fields:
        marks = field_marks.get(f["id"]) or field_marks.get(f.get("displayName") or "") or {}
        _draw_mark_pdf(
            page,
            float(f["x"]),
            float(f["y"]),
            float(f["width"]),
            float(f["height"]),
            str(marks.get("judgment") or ""),
            marks.get("score"),
            style,
        )
    for slot in output_slots:
        _draw_total_pdf(page, slot, totals.get(slot["slotKey"]), style)
    if text_annotations:
        _draw_text_annotations_pdf(page, text_annotations)
    if ink_strokes:
        _draw_ink_strokes_pdf(page, ink_strokes)
    finalize_feedback_pdf(doc)
    return doc


def build_original_feedback_pdf_document(
    test_id: str,
    row: dict[str, Any],
    fields: list[dict[str, Any]],
    output_slots: list[dict[str, Any]],
    field_marks: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    style: dict[str, Any],
    *,
    ink_strokes: list[dict[str, Any]] | None = None,
    text_annotations: list[dict[str, Any]] | None = None,
    jpeg_quality: int = 92,
) -> fitz.Document:
    """元画像を背景にし、判定・配点・手書きはベクトルのまま載せる。

    座標は補正画像上の位置を、模範解答の切り出し四隅へ戻してから
    生徒の元画像の同じ相対位置へ写す。氏名欄は描かない。
    """
    from services.crop_preview import resolve_source_path
    from services.image_loader import load_image_bgr
    from services.model_crop import corners_for_image, get_model_crop

    crop = get_model_crop(test_id)
    source_path = resolve_source_path(row, test_id=test_id)
    original = load_image_bgr(source_path)
    if original is None:
        raise ValueError(f"元画像を読み込めません: {source_path}")
    orig_h, orig_w = original.shape[:2]
    corners = corners_for_image(crop, orig_w, orig_h)
    matrix = _warp_to_page_matrix(crop.warp_width, crop.warp_height, corners)

    ok, buf = cv2.imencode(".jpg", original, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    if not ok:
        raise ValueError("元画像の JPEG エンコードに失敗しました")

    doc = fitz.open()
    page = doc.new_page(width=orig_w, height=orig_h)
    page.insert_image(fitz.Rect(0, 0, orig_w, orig_h), stream=bytes(buf))

    for f in fields:
        marks = field_marks.get(f["id"]) or field_marks.get(f.get("displayName") or "") or {}
        _draw_mark_pdf_mapped(
            page,
            matrix,
            float(f["x"]),
            float(f["y"]),
            float(f["width"]),
            float(f["height"]),
            str(marks.get("judgment") or ""),
            marks.get("score"),
            style,
        )
    for slot in output_slots:
        _draw_total_pdf_mapped(page, matrix, slot, totals.get(slot["slotKey"]), style)
    if text_annotations:
        _draw_text_annotations_mapped(page, text_annotations, matrix, orig_w, orig_h)
    if ink_strokes:
        _draw_ink_strokes_mapped(page, matrix, ink_strokes)
    finalize_feedback_pdf(doc)
    return doc


def rasterize_pdf_bytes(pdf_bytes: bytes, *, scale: float = 2.0) -> Image.Image:
    """ベクトル PDF をプレビュー用にラスター化する（拡大しても線・文字が滑らか）。"""
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[0]
        matrix = fitz.Matrix(float(scale), float(scale))
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def finalize_feedback_pdf(doc: fitz.Document) -> None:
    """埋め込んだ日本語フォントを、実際に使った文字だけにする。

    Meiryo などをそのまま入れると 1 枚あたり約 9MB になる。
    文字の見た目は変えず、ファイルサイズだけ落とす。
    """
    try:
        doc.subset_fonts()
    except Exception:
        return


def save_feedback_pdf(doc: fitz.Document, path: str | Path) -> None:
    finalize_feedback_pdf(doc)
    doc.save(str(path), deflate=True, garbage=4)


def pdf_document_to_bytes(doc: fitz.Document) -> bytes:
    finalize_feedback_pdf(doc)
    return doc.tobytes(deflate=True, garbage=4)


def build_pdf_document_from_image(
    image: Image.Image,
    *,
    jpeg_quality: int = 92,
) -> fitz.Document:
    """合成済み画像を1ページの PDF にする。"""
    import io

    rgb = image.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=int(jpeg_quality))
    width, height = rgb.size
    doc = fitz.open()
    page = doc.new_page(width=float(width), height=float(height))
    page.insert_image(fitz.Rect(0, 0, width, height), stream=buf.getvalue())
    return doc


def render_feedback_pdf(
    warped_path: str,
    fields: list[dict[str, Any]],
    output_slots: list[dict[str, Any]],
    field_marks: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    style: dict[str, Any],
    *,
    ink_strokes: list[dict[str, Any]] | None = None,
    text_annotations: list[dict[str, Any]] | None = None,
    out_path: str | Path,
    jpeg_quality: int = 92,
) -> Path:
    """補正画像を背景ラスター、上物をベクトルとして PDF に書き出す。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = build_feedback_pdf_document(
        warped_path,
        fields,
        output_slots,
        field_marks,
        totals,
        style,
        ink_strokes=ink_strokes,
        text_annotations=text_annotations,
        jpeg_quality=jpeg_quality,
    )
    try:
        save_feedback_pdf(doc, out_path)
    finally:
        doc.close()
    return out_path
