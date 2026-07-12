"""薄い手書きの事前検出（記述欄クロップのコントラスト指標）。"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from config import faint_thresholds_from_config
from services.image_warp import crop_region

_EDGE_TRIM_PX = 4


def _to_gray(crop_bgr: np.ndarray) -> np.ndarray:
    if crop_bgr.ndim == 2:
        return crop_bgr.astype(np.float32)
    return cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _trim_edges(gray: np.ndarray, trim: int = _EDGE_TRIM_PX) -> np.ndarray:
    h, w = gray.shape[:2]
    if h <= trim * 2 + 4 or w <= trim * 2 + 4:
        return gray
    return gray[trim : h - trim, trim : w - trim]


def analyze_field_crop(crop_bgr: np.ndarray) -> dict[str, float]:
    """単一記述欄クロップの薄さ指標を返す。"""
    gray = _trim_edges(_to_gray(crop_bgr))
    if gray.size == 0:
        return {"sigma": 0.0, "p95_p5": 0.0, "bg_delta": 0.0, "ink_ratio": 0.0}
    flat = gray.reshape(-1)
    sigma = float(np.std(flat))
    p5 = float(np.percentile(flat, 5))
    p10 = float(np.percentile(flat, 10))
    p90 = float(np.percentile(flat, 90))
    p95 = float(np.percentile(flat, 95))
    p95_p5 = p95 - p5
    bg = p90
    ink = p10
    bg_delta = max(0.0, bg - ink)
    # 背景より十分暗い画素を字候補とみなす
    thr = bg - max(8.0, bg_delta * 0.35)
    ink_ratio = float(np.mean(flat < thr)) if flat.size else 0.0
    return {
        "sigma": round(sigma, 2),
        "p95_p5": round(p95_p5, 2),
        "bg_delta": round(bg_delta, 2),
        "ink_ratio": round(ink_ratio, 4),
    }


def failed_criteria(
    metrics: dict[str, float],
    thresholds: dict[str, float | bool] | None = None,
) -> list[str]:
    """基準未満の項目キー一覧（sigma / p95_p5 / bg_delta）。"""
    th = thresholds if thresholds is not None else faint_thresholds_from_config()
    failed: list[str] = []
    if float(metrics.get("sigma") or 0) < float(th["min_sigma"]):
        failed.append("sigma")
    if float(metrics.get("p95_p5") or 0) < float(th["min_p95_p5"]):
        failed.append("p95_p5")
    if float(metrics.get("bg_delta") or 0) < float(th["min_bg_delta"]):
        failed.append("bg_delta")
    return failed


def is_faint(
    metrics: dict[str, float],
    thresholds: dict[str, float | bool] | None = None,
) -> bool:
    th = thresholds if thresholds is not None else faint_thresholds_from_config()
    if not bool(th.get("enabled", True)):
        return False
    return bool(failed_criteria(metrics, th))


def format_fail_reason(
    metrics: dict[str, float],
    failed: list[str],
    thresholds: dict[str, float | bool] | None = None,
) -> str:
    th = thresholds if thresholds is not None else faint_thresholds_from_config()
    parts: list[str] = []
    labels = {
        "sigma": ("σ", "min_sigma"),
        "p95_p5": ("P95−P5", "min_p95_p5"),
        "bg_delta": ("Δ", "min_bg_delta"),
    }
    for key in failed:
        lab, th_key = labels[key]
        parts.append(f"{lab}={metrics.get(key, 0)}<{th[th_key]}")
    return " / ".join(parts)


def analyze_warped_fields(
    warped_bgr: np.ndarray,
    fields: list[dict[str, Any]],
    thresholds: dict[str, float | bool] | None = None,
) -> dict[str, Any]:
    """補正画像全体について記述欄ごとの計測と、答案としての薄い判定を返す。"""
    th = thresholds if thresholds is not None else faint_thresholds_from_config()
    per_field: list[dict[str, Any]] = []
    worst: dict[str, Any] | None = None
    worst_score = 1e9

    for f in fields:
        fid = str(f.get("id") or "")
        crop = crop_region(
            warped_bgr,
            int(f.get("x") or 0),
            int(f.get("y") or 0),
            int(f.get("width") or 0),
            int(f.get("height") or 0),
        )
        metrics = analyze_field_crop(crop)
        failed = failed_criteria(metrics, th)
        entry = {
            "fieldId": fid,
            "displayName": str(f.get("displayName") or fid),
            "metrics": metrics,
            "failedCriteria": failed,
            "isFaint": bool(failed) and bool(th.get("enabled", True)),
        }
        per_field.append(entry)
        # 小さいほど悪い（3指標の正規化合計）
        score = (
            float(metrics["sigma"]) / max(1.0, float(th["min_sigma"]))
            + float(metrics["p95_p5"]) / max(1.0, float(th["min_p95_p5"]))
            + float(metrics["bg_delta"]) / max(1.0, float(th["min_bg_delta"]))
        )
        if score < worst_score:
            worst_score = score
            worst = entry

    any_faint = any(e["isFaint"] for e in per_field) if bool(th.get("enabled", True)) else False
    reason = ""
    if worst and worst.get("isFaint"):
        reason = format_fail_reason(worst["metrics"], worst["failedCriteria"], th)
        if worst.get("displayName"):
            reason = f"{worst['displayName']}: {reason}"

    return {
        "isFaint": any_faint,
        "fields": per_field,
        "worstField": worst,
        "reason": reason,
        "thresholds": {
            "min_sigma": float(th["min_sigma"]),
            "min_p95_p5": float(th["min_p95_p5"]),
            "min_bg_delta": float(th["min_bg_delta"]),
        },
    }


def enhance_bgr(
    image_bgr: np.ndarray,
    *,
    contrast: float = 1.35,
    brightness: float = 0.0,
    clahe_clip: float = 2.5,
) -> np.ndarray:
    """薄い字向けの簡易強調（コントラスト＋CLAHE）。"""
    out = image_bgr
    if abs(contrast - 1.0) > 1e-3 or abs(brightness) > 1e-3:
        out = cv2.convertScaleAbs(out, alpha=float(contrast), beta=float(brightness))
    if clahe_clip > 0.05:
        lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=float(clahe_clip), tileGridSize=(8, 8))
        l2 = clahe.apply(l)
        out = cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)
    return out
