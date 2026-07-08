"""リッチテキスト注釈のラスター／PDF 描画。"""

from __future__ import annotations

import io
import re
from html import unescape
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from models.text_annotation_repo import resolve_text_style
from ui_qt.floating_palette.text_rich import (
    box_plain_text,
    box_text_html,
    html_for_pdf_box,
    normalize_text_align,
)


def _load_font(size: int, *, bold: bool = False, italic: bool = False):
    names: list[str] = []
    if bold:
        names.extend(["meiryob.ttc", "YuGothB.ttc", "msgothic.ttc"])
    names.extend(["meiryo.ttc", "YuGothM.ttc", "msgothic.ttc", "arial.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, max(8, int(size)))
        except OSError:
            continue
    return ImageFont.load_default()


def _hex_rgba(hex_color: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    h = str(hex_color or "#111827").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return (17, 24, 39, max(0, min(255, int(alpha * 255))))
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return (r, g, b, max(0, min(255, int(alpha * 255))))


def _plain_lines_from_box(box: dict[str, Any]) -> list[str]:
    plain = box_plain_text(box)
    if plain.strip():
        return plain.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html = str(box.get("textHtml") or "")
    if not html.strip():
        return []
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _wrap_line(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> list[str]:
    if not text:
        return [""]
    if draw.textlength(text, font=font) <= max_w:
        return [text]
    lines: list[str] = []
    buf = ""
    for ch in text:
        trial = buf + ch
        if draw.textlength(trial, font=font) <= max_w or not buf:
            buf = trial
        else:
            lines.append(buf)
            buf = ch
    if buf:
        lines.append(buf)
    return lines or [""]


def render_box_pil_patch(
    box: dict[str, Any],
    *,
    scale: float = 1.0,
) -> Image.Image | None:
    """スレッド安全な PIL レンダー（個票プレビュー／PDF 埋め込み用）。"""
    st = resolve_text_style(box.get("style") or {})
    lines_src = _plain_lines_from_box(box)
    has_text = any(line.strip() for line in lines_src)
    fill_alpha = float(st.get("fillAlpha") or 0)
    if not has_text and fill_alpha <= 0:
        return None

    work_scale = max(0.01, float(scale))
    w = max(20.0, float(box.get("width") or 32) * work_scale)
    h = max(12.0, float(box.get("height") or 18) * work_scale)
    iw = max(1, int(round(w)))
    ih = max(1, int(round(h)))
    patch = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    draw = ImageDraw.Draw(patch)

    if fill_alpha > 0:
        fill = _hex_rgba(str(st.get("fillColor") or "#ffffff"), fill_alpha)
        draw.rectangle([0, 0, iw - 1, ih - 1], fill=fill)

    if not has_text:
        return patch

    fs = max(8, int(round(float(st.get("fontSize") or 14) * work_scale)))
    line_pt = float(st.get("lineSpacing") or st.get("fontSize") or 14) * work_scale
    line_h = max(float(fs), float(line_pt))
    bold = str(st.get("fontWeight") or "").lower() == "bold"
    italic = str(st.get("fontStyle") or "").lower() == "italic"
    underline = str(st.get("textDecoration") or "").lower() == "underline"
    font = _load_font(fs, bold=bold, italic=italic)
    color = _hex_rgba(str(st.get("textColor") or "#111827"), 1.0)
    h_align, v_align = normalize_text_align(st)

    wrapped: list[str] = []
    max_text_w = max(1.0, iw - 2.0)
    for src in lines_src:
        wrapped.extend(_wrap_line(draw, src, font, max_text_w))

    content_h = line_h * max(1, len(wrapped))
    if v_align == "center":
        y = max(0.0, (ih - content_h) / 2.0)
    elif v_align == "bottom":
        y = max(0.0, ih - content_h)
    else:
        y = 0.0

    for line in wrapped:
        tw = float(draw.textlength(line, font=font))
        if h_align == "center":
            x = max(0.0, (iw - tw) / 2.0)
        elif h_align == "right":
            x = max(0.0, iw - tw)
        else:
            x = 0.0
        draw.text((x, y), line, font=font, fill=color)
        if underline and line:
            uy = y + fs + max(1.0, work_scale)
            draw.line([(x, uy), (x + tw, uy)], fill=color, width=max(1, int(round(work_scale))))
        y += line_h
        if y >= ih:
            break
    return patch


def _qimage_to_pil(qimg) -> Image.Image:
    """QImage → RGBA Pillow。ImageQt(QImage) は逆方向用途のため使わない。"""
    from PySide6.QtGui import QImage

    converted = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
    w = converted.width()
    h = converted.height()
    bpl = converted.bytesPerLine()
    raw = bytes(converted.constBits())
    if bpl == w * 4:
        return Image.frombuffer("RGBA", (w, h), raw, "raw", "RGBA", 0, 1).copy()
    rows = []
    row_bytes = w * 4
    for y in range(h):
        start = y * bpl
        rows.append(raw[start : start + row_bytes])
    return Image.frombytes("RGBA", (w, h), b"".join(rows))


def _render_box_qt_patch(
    box: dict[str, Any],
    *,
    scale: float = 1.0,
) -> Image.Image | None:
    """メインスレッド向け Qt リッチ描画。失敗時は None。"""
    try:
        from PySide6.QtCore import QCoreApplication, Qt
        from PySide6.QtGui import QImage, QPainter, QTextDocument
    except Exception:
        return None
    if QCoreApplication.instance() is None:
        return None

    st = resolve_text_style(box.get("style") or {})
    html = box_text_html(box, st)
    if not str(html or "").strip() and not str(box.get("text") or "").strip():
        return None

    work_scale = max(0.01, float(scale))
    w = max(20.0, float(box.get("width") or 32) * work_scale)
    h = max(12.0, float(box.get("height") or 18) * work_scale)
    iw = max(1, int(round(w)))
    ih = max(1, int(round(h)))

    doc = QTextDocument()
    doc.setDocumentMargin(0)
    doc.setDefaultStyleSheet("body { margin: 0; padding: 0; }")
    doc.setHtml(html if str(html or "").strip() else str(box.get("text") or ""))
    doc.setTextWidth(float(iw))

    doc_h = float(doc.size().height())
    _, v_align = normalize_text_align(st)
    y_off = 0.0
    if v_align == "center":
        y_off = max(0.0, (ih - doc_h) / 2)
    elif v_align == "bottom":
        y_off = max(0.0, ih - doc_h)

    qimg = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
    qimg.fill(Qt.GlobalColor.transparent)
    painter = QPainter(qimg)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.translate(0, y_off)
        doc.drawContents(painter)
    finally:
        painter.end()
    return _qimage_to_pil(qimg)


def render_box_html_patch(
    box: dict[str, Any],
    *,
    scale: float = 1.0,
) -> Image.Image | None:
    """テキストボックスを透過 RGBA 画像にレンダー（プレビュー／PDF 共用）。"""
    # ワーカースレッドでは Qt を使わず、常に PIL 経路を優先する。
    patch = render_box_pil_patch(box, scale=scale)
    if patch is not None:
        return patch
    try:
        return _render_box_qt_patch(box, scale=scale)
    except Exception:
        return None


def render_annotation_html_to_pil(
    size: tuple[int, int],
    box: dict[str, Any],
    *,
    scale: float = 1.0,
) -> Image.Image | None:
    """1 件のテキストボックス HTML を透過 RGBA 画像にレンダー。"""
    patch = render_box_html_patch(box, scale=scale)
    if patch is None:
        return None

    work_scale = max(0.01, float(scale))
    x = int(round(float(box.get("x") or 0) * work_scale))
    y = int(round(float(box.get("y") or 0) * work_scale))
    canvas_w = max(1, int(size[0]))
    canvas_h = max(1, int(size[1]))
    layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    layer.paste(patch, (x, y), patch)
    return layer


def _draw_annotation_pdf_plain(page, box: dict[str, Any], st: dict[str, Any]) -> bool:
    """プレーンテキストのフォールバック描画。成功したら True。"""
    import fitz

    from services.feedback_pdf import _hex_to_rgb01, _resolve_font_file

    text = str(box.get("text") or "").strip()
    if not text:
        return False
    x = float(box.get("x") or 0)
    y = float(box.get("y") or 0)
    w = max(20.0, float(box.get("width") or 32))
    h = max(12.0, float(box.get("height") or 18))
    rect = fitz.Rect(x, y, x + w, y + h)
    font_path = _resolve_font_file(preferred=str(st.get("fontFamily") or "") or None)
    fs = max(8.0, float(st.get("fontSize") or 14))
    h_align, _ = normalize_text_align(st)
    align_map = {
        "left": fitz.TEXT_ALIGN_LEFT,
        "center": fitz.TEXT_ALIGN_CENTER,
        "right": fitz.TEXT_ALIGN_RIGHT,
    }
    page.insert_textbox(
        rect,
        text,
        fontfile=font_path,
        fontsize=fs,
        color=_hex_to_rgb01(st.get("textColor") or "#111827"),
        align=align_map.get(h_align, fitz.TEXT_ALIGN_LEFT),
    )
    return True


def draw_annotation_pdf(page, box: dict[str, Any]) -> None:
    """fitz.Page にテキストボックス 1 件を描画。

    日本語・リッチテキストが insert_htmlbox で空描画になりやすいため、
    PIL で描いた画像を埋め込む経路を優先する（ワーカースレッドでも安全）。
    """
    import fitz

    from services.feedback_pdf import _hex_to_rgb01

    st = resolve_text_style(box.get("style") or {})
    x = float(box.get("x") or 0)
    y = float(box.get("y") or 0)
    w = max(20.0, float(box.get("width") or 32))
    h = max(12.0, float(box.get("height") or 18))
    rect = fitz.Rect(x, y, x + w, y + h)

    fill_alpha = float(st.get("fillAlpha") or 0)
    if fill_alpha > 0:
        fc = st.get("fillColor") or "#ffffff"
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(
            color=_hex_to_rgb01(fc),
            fill=_hex_to_rgb01(fc),
            fill_opacity=fill_alpha,
            width=0,
            closePath=True,
        )
        shape.commit()

    try:
        # 埋め込みは高解像、背景塗りは shape 側で描くので patch は文字だけでもよい
        box_for_render = dict(box)
        style = dict(st)
        style["fillAlpha"] = 0.0
        box_for_render["style"] = style
        patch = render_box_html_patch(box_for_render, scale=2.0)
        if patch is not None and patch.getbbox() is not None:
            buf = io.BytesIO()
            patch.save(buf, format="PNG")
            page.insert_image(rect, stream=buf.getvalue(), keep_proportion=False)
            return
    except Exception:
        pass

    html = html_for_pdf_box(box_text_html(box, st), st, box_height=h)
    if html.strip():
        try:
            page.insert_htmlbox(rect, html)
            return
        except Exception:
            pass

    _draw_annotation_pdf_plain(page, box, st)
