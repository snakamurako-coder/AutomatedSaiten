"""⑩ 個票レンダラー（GAS FeedbackRenderer の PIL 移植）。

合成レイヤー:
  1. 補正済み解答画像（フル解像度）
  2. 各記述欄の判定マーク ○/△/× + 小問得点
  3. 合計欄（出力欄設定の矩形）のテキスト
  4. 手書きストローク（スタイラス層・最前面）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

from config import test_feedback
from models.domain_repo import DOMAIN_KINDS, _domain_groups, get_domain_settings
from models.output_repo import get_feedback_style, get_output_slots
from models.test_repo import get_all_results, get_answer_fields
from services.compositor import hex_to_rgba, render_supersampled_rgba
from services.image_loader import imread_bgr

_FONT_CANDIDATES_BOLD = ["meiryob.ttc", "YuGothB.ttc", "msgothic.ttc", "arialbd.ttf"]
_FONT_CANDIDATES = ["meiryo.ttc", "YuGothM.ttc", "msgothic.ttc", "arial.ttf"]


def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for name in (_FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ==================== 判定の正規化・推論 ====================

def infer_judgment_from_score(score: float, max_points: float) -> str:
    if score <= 0:
        return "×"
    if max_points and score >= max_points:
        return "○"
    return "△"


def normalize_judgment(judgment: str, score: Any) -> str | None:
    """描画種別 'maru' / 'sankaku' / 'batsu' / None を返す（GAS normalizeJudgment 互換）。"""
    j = str(judgment or "").strip()
    if j in ("○", "〇"):
        return "maru"
    if j == "△":
        return "sankaku"
    if j in ("×", "x", "X"):
        return "batsu"
    if j:
        return "batsu"
    if score is None or str(score) == "":
        return None
    try:
        return "sankaku" if float(score) > 0 else "batsu"
    except (TypeError, ValueError):
        return None


# ==================== 描画プリミティブ ====================

def _inset_rect(
    x: float, y: float, w: float, h: float, inset_ratio: float
) -> tuple[float, float, float, float]:
    ratio = max(0.0, min(0.45, inset_ratio))
    dx = w * ratio
    dy = h * ratio
    return (x + dx, y + dy, w - dx * 2, h - dy * 2)


def _text_size(font: ImageFont.FreeTypeFont, text: str) -> tuple[float, float]:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    text: str,
    color: tuple[int, int, int, int],
    font_size: int,
    max_width: float,
    min_size: int = 8,
) -> None:
    size = max(min_size, int(font_size))
    font = _load_font(size)
    tw, _th = _text_size(font, text)
    while tw > max_width and size > min_size:
        size = max(min_size, int(size * 0.9))
        font = _load_font(size)
        tw, _th = _text_size(font, text)
    draw.text((cx, cy), text, font=font, fill=color, anchor="mm")


def draw_mark(
    layer: Image.Image,
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
    draw = ImageDraw.Draw(layer)
    mark_style = style["mark"]
    ix, iy, iw, ih = _inset_rect(x, y, w, h, float(mark_style.get("insetRatio", 0.05)))
    min_dim = min(iw, ih)

    if kind == "maru":
        st = mark_style["maru"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.06)))
        fill = hex_to_rgba(st["strokeColor"], float(st.get("fillOpacity", 0.12)))
        outline = hex_to_rgba(st["strokeColor"], float(st.get("strokeOpacity", 1.0)))
        box = [ix, iy, ix + iw, iy + ih]
        draw.ellipse(box, fill=fill)
        draw.ellipse(box, outline=outline, width=max(1, round(line_w)))
    elif kind == "sankaku":
        st = mark_style["sankaku"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.06)))
        color = hex_to_rgba(st["strokeColor"], float(st.get("strokeOpacity", 1.0)))
        points = [(ix + iw / 2, iy), (ix + iw, iy + ih), (ix, iy + ih)]
        draw.polygon(points, outline=color, width=max(1, round(line_w)))
    else:  # batsu
        st = mark_style["batsu"]
        line_w = max(2.0, min_dim * float(st.get("lineWidthRatio", 0.08)))
        color = hex_to_rgba(st["strokeColor"], float(st.get("strokeOpacity", 1.0)))
        lw = max(1, round(line_w))
        draw.line([ix, iy, ix + iw, iy + ih], fill=color, width=lw, joint="curve")
        draw.line([ix + iw, iy, ix, iy + ih], fill=color, width=lw, joint="curve")

    # 小問得点（× かつ 0 点は非表示 — GAS 互換）
    score_text = "" if score is None else str(score).strip()
    if not score_text:
        return
    try:
        if kind == "batsu" and float(score_text) == 0:
            return
    except ValueError:
        pass
    sc = mark_style["score"]
    _draw_centered_text(
        ImageDraw.Draw(layer),
        ix + iw / 2,
        iy + ih / 2,
        score_text,
        hex_to_rgba(sc["color"], float(sc.get("opacity", 1.0))),
        int(min_dim * float(sc.get("sizeRatio", 0.35))),
        iw * 0.9,
    )


def format_total_text(slot: dict[str, Any], value: Any) -> str:
    text = "" if value is None else str(value)
    if slot.get("printMode") == "label":
        return f"{slot['slotKey']} {text}"
    return text


def draw_total(layer: Image.Image, slot: dict[str, Any], value: Any, style: dict[str, Any]) -> None:
    if value is None or str(value) == "":
        return
    st = style["total"]
    x, y = float(slot["x"]), float(slot["y"])
    w, h = float(slot["width"]), float(slot["height"])
    font_size = max(
        int(st.get("minFontSize", 10)), int(min(w, h) * float(st.get("sizeRatio", 0.5)))
    )
    _draw_centered_text(
        ImageDraw.Draw(layer),
        x + w / 2,
        y + h / 2,
        format_total_text(slot, value),
        hex_to_rgba(st["color"], float(st.get("opacity", 1.0))),
        font_size,
        w * 0.92,
    )


def _scale_slot(slot: dict[str, Any], sf: float) -> dict[str, Any]:
    if sf == 1.0:
        return slot
    return {
        **slot,
        "x": float(slot["x"]) * sf,
        "y": float(slot["y"]) * sf,
        "width": float(slot["width"]) * sf,
        "height": float(slot["height"]) * sf,
    }


def composite_mark_on_image(
    image: Image.Image,
    judgment: str,
    score: Any,
    style: dict[str, Any] | None = None,
    *,
    supersample: int = 4,
) -> Image.Image:
    """クロップ画像へ判定マーク・得点を滑らかに重ねる（手動採点プレビュー用）。"""
    style = style or get_feedback_style()
    base = image.convert("RGBA")
    w, h = base.size

    def paint(layer: Image.Image, sf: float) -> None:
        draw_mark(layer, 0, 0, w * sf, h * sf, judgment, score, style)

    overlay = render_supersampled_rgba(base.size, paint, supersample)
    return Image.alpha_composite(base, overlay).convert("RGB")


def render_feedback_overlay_layer(
    size: tuple[int, int],
    fields: list[dict[str, Any]],
    output_slots: list[dict[str, Any]],
    field_marks: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    style: dict[str, Any],
    *,
    supersample: int | None = None,
) -> Image.Image:
    """判定マーク・合計欄をスーパーサンプリングで描いた RGBA レイヤー。"""

    def paint(layer: Image.Image, sf: float) -> None:
        for f in fields:
            marks = field_marks.get(f["id"]) or field_marks.get(f.get("displayName") or "") or {}
            draw_mark(
                layer,
                float(f["x"]) * sf,
                float(f["y"]) * sf,
                float(f["width"]) * sf,
                float(f["height"]) * sf,
                str(marks.get("judgment") or ""),
                marks.get("score"),
                style,
            )
        for slot in output_slots:
            draw_total(layer, _scale_slot(slot, sf), totals.get(slot["slotKey"]), style)

    return render_supersampled_rgba(size, paint, supersample)


# ==================== 合成本体 ====================

def render_feedback_image(
    warped_path: str,
    fields: list[dict[str, Any]],
    output_slots: list[dict[str, Any]],
    field_marks: dict[str, dict[str, Any]],
    totals: dict[str, Any],
    style: dict[str, Any] | None = None,
    ink_strokes: list[dict[str, Any]] | None = None,
    text_annotations: list[dict[str, Any]] | None = None,
) -> Image.Image:
    style = style or get_feedback_style()
    bgr = imread_bgr(warped_path)
    if bgr is None:
        raise ValueError(f"補正画像を読み込めません: {warped_path}")
    from services.compositor import bgr_to_rgba_image, render_ink_layer, render_text_annotation_layer

    base = bgr_to_rgba_image(bgr)
    # 個票出力はフルページ 2〜4x を避け、ネイティブ解像度で合成する
    layer = render_feedback_overlay_layer(
        base.size,
        fields,
        output_slots,
        field_marks,
        totals,
        style,
        supersample=1,
    )

    composite = Image.alpha_composite(base, layer)
    if text_annotations:
        text_layer = render_text_annotation_layer(
            composite.size, text_annotations, scale=1.0, supersample=1
        )
        composite = Image.alpha_composite(composite, text_layer)
    if ink_strokes:
        ink_layer = render_ink_layer(
            composite.size, ink_strokes, scale=1.0, supersample=1
        )
        composite = Image.alpha_composite(composite, ink_layer)
    return composite.convert("RGB")


# ==================== ペイロード構築 ====================

def _compute_totals(
    test_id: str,
    row: dict[str, Any],
    slot_keys: list[str],
    *,
    domain_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """slotKey → 値。domain_scores_json 優先、なければ ⑥のグループ定義から合算。"""
    domain_scores = row.get("domainScores") or {}
    scores = row.get("scores") or {}
    settings = domain_settings if domain_settings is not None else get_domain_settings(test_id)
    groups = _domain_groups(settings)
    prefix_map = {prefix: groups.get(prefix, {}) for _attr, prefix in DOMAIN_KINDS}

    totals: dict[str, Any] = {}
    for key in slot_keys:
        if key == "総計点":
            totals[key] = _fmt_num(row.get("totalScore"))
            continue
        if key == "外部連携得点":
            totals[key] = _fmt_num(row.get("externalScore"))
            continue
        col = f"{key}_得点"
        if col in domain_scores:
            totals[key] = _fmt_num(domain_scores[col])
            continue
        # フォールバック: グループ合算
        for prefix, by_label in prefix_map.items():
            if key.startswith(prefix):
                label = key[len(prefix):]
                fids = by_label.get(label)
                if fids is not None:
                    totals[key] = sum(int(scores.get(fid, 0) or 0) for fid in fids)
                    break
    return totals


def _fmt_num(value: Any) -> Any:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return int(f) if f == int(f) else f


def build_feedback_shared_context(test_id: str) -> dict[str, Any]:
    """一括生成向け: テスト共通データを1回だけ読む。"""
    from models.test_repo import get_test_info

    info = get_test_info(test_id)
    return {
        "points": {k: int(v) for k, v in (info.get("points") or {}).items()},
        "fields": get_answer_fields(test_id),
        "output_slots": get_output_slots(test_id),
        "style": get_feedback_style(),
        "domain_settings": get_domain_settings(test_id),
    }


def build_feedback_payload(
    test_id: str,
    row: dict[str, Any],
    points: dict[str, int],
    *,
    fields: list[dict[str, Any]] | None = None,
    output_slots: list[dict[str, Any]] | None = None,
    domain_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """1 生徒分の描画データ（判定推論込み）を作る。"""
    field_list = fields if fields is not None else get_answer_fields(test_id)
    slots = output_slots if output_slots is not None else get_output_slots(test_id)
    field_marks: dict[str, dict[str, Any]] = {}
    for f in field_list:
        fid = f["id"]
        judgment = str((row.get("judgments") or {}).get(fid, "") or "").strip()
        score = (row.get("scores") or {}).get(fid)
        if not judgment and score not in (None, ""):
            try:
                judgment = infer_judgment_from_score(float(score), float(points.get(fid, 0)))
            except (TypeError, ValueError):
                judgment = ""
        field_marks[fid] = {"judgment": judgment, "score": score}
    totals = _compute_totals(
        test_id,
        row,
        [s["slotKey"] for s in slots],
        domain_settings=domain_settings,
    )
    return {
        "fields": field_list,
        "outputSlots": slots,
        "fieldMarks": field_marks,
        "totals": totals,
    }


def _load_rows_with_extras(test_id: str) -> list[dict[str, Any]]:
    """get_all_results に領域・総計カラムを追加した行リスト。"""
    from models.database import connect

    rows = get_all_results(test_id)
    with connect() as conn:
        extras = {
            r["id"]: r
            for r in conn.execute(
                "SELECT id, domain_scores_json, external_score, total_score "
                "FROM results WHERE test_id = ?",
                (test_id,),
            ).fetchall()
        }
    for row in rows:
        ex = extras.get(row["id"])
        if ex:
            row["domainScores"] = json.loads(ex["domain_scores_json"] or "{}")
            row["externalScore"] = ex["external_score"] or 0
            row["totalScore"] = ex["total_score"] or 0
    return rows


def render_feedback_for_row(test_id: str, row: dict[str, Any]) -> Image.Image:
    from services.feedback_exporter import gather_row_render_data

    data = gather_row_render_data(test_id, row)
    payload = data["payload"]
    return render_feedback_image(
        data["warped_path"],
        payload["fields"],
        payload["outputSlots"],
        payload["fieldMarks"],
        payload["totals"],
        style=data["style"],
        ink_strokes=data["ink_strokes"],
        text_annotations=data["text_annotations"],
    )


def _safe_name(value: str) -> str:
    return "".join(c for c in str(value or "") if c not in '\\/:*?"<>|').strip() or "無名"


def batch_generate_feedback(
    test_id: str,
    on_progress: Callable[[int, int, str], None] | None = None,
    *,
    export_format: str | None = None,
) -> dict[str, Any]:
    """全結果行の個票を生成して 個票/ フォルダに保存する。"""
    from services.feedback_exporter import (
        COMBINED_PDF_FILENAME,
        export_combined_feedback_pdf,
        export_feedback_row,
        feedback_filename,
        is_combined_pdf_export,
        normalize_export_format,
        per_file_export_format,
    )

    slots = get_output_slots(test_id)
    if not slots:
        raise ValueError("合計欄が未設定です。先に出力欄を配置・保存してください。")
    rows = _load_rows_with_extras(test_id)
    if not rows:
        raise ValueError("採点結果がありません。")

    fmt = normalize_export_format(export_format)
    out_dir = test_feedback(test_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    total = len(rows)
    shared = build_feedback_shared_context(test_id)

    if is_combined_pdf_export(fmt):
        combined_path = out_dir / COMBINED_PDF_FILENAME
        page_count, skipped, errors = export_combined_feedback_pdf(
            test_id,
            rows,
            combined_path,
            on_progress=on_progress,
            shared=shared,
        )
        from models.test_repo import touch_progress

        touch_progress(test_id, 10, "個票出力済み")
        return {
            "saved": page_count,
            "skipped": skipped,
            "errors": errors,
            "outputDir": str(out_dir),
            "exportFormat": fmt,
            "combined": True,
            "combinedFile": str(combined_path),
            "pageCount": page_count,
        }

    file_fmt = per_file_export_format(fmt)
    saved = 0

    for i, row in enumerate(rows):
        name = str(row.get("fileName") or "")
        if on_progress:
            on_progress(i + 1, total, name)
        warped = str(row.get("warpedPath") or "").strip()
        if not warped or not Path(warped).exists():
            skipped.append(name)
            continue
        try:
            sid = _safe_name(row.get("studentId") or "不明")
            sname = _safe_name(row.get("name") or row.get("fileName") or "")
            out_path = out_dir / feedback_filename(sid, sname, file_fmt)
            export_feedback_row(test_id, row, out_path, file_fmt, shared=shared)
            saved += 1
        except Exception as e:
            errors.append({"fileName": name, "error": str(e)})

    from models.test_repo import touch_progress

    touch_progress(test_id, 10, "個票出力済み")
    return {
        "saved": saved,
        "skipped": skipped,
        "errors": errors,
        "outputDir": str(out_dir),
        "exportFormat": fmt,
        "combined": False,
    }
