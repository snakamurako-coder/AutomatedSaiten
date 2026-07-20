"""薄い手書きの事前検出（Weber Contrast による記述欄クロップ評価）。"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from config import faint_thresholds_from_config
from services.image_warp import crop_region

_EDGE_TRIM_PX = 8
_MIN_INK_RATIO = 0.005
_MU_BG_EPS = 1.0


def _to_gray(crop_bgr: np.ndarray) -> np.ndarray:
    if crop_bgr.ndim == 2:
        return crop_bgr.astype(np.float32)
    return cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)


def _trim_edges(gray: np.ndarray, trim: int = _EDGE_TRIM_PX) -> np.ndarray:
    h, w = gray.shape[:2]
    if h <= trim * 2 + 4 or w <= trim * 2 + 4:
        return gray
    return gray[trim : h - trim, trim : w - trim]


def _ink_mask(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """字候補マスク M（True=字）と ink_ratio を返す。"""
    flat = gray.reshape(-1)
    p10 = float(np.percentile(flat, 10))
    bg = float(np.percentile(flat, 90))
    bg_delta = max(0.0, bg - p10)
    thr = bg - max(8.0, bg_delta * 0.35)
    ink = gray < thr
    ink_ratio = float(np.mean(ink)) if flat.size else 0.0
    return ink, ink_ratio


def analyze_field_crop(crop_bgr: np.ndarray) -> dict[str, float]:
    """単一記述欄クロップの Weber Contrast 指標を返す。"""
    gray = _trim_edges(_to_gray(crop_bgr))
    if gray.size == 0:
        return {"weber_c": 1.0, "mu_bg": 0.0, "mu_text": 0.0, "ink_ratio": 0.0}

    ink_mask, ink_ratio = _ink_mask(gray)
    bg_mask = ~ink_mask
    bg_count = int(np.count_nonzero(bg_mask))
    ink_count = int(np.count_nonzero(ink_mask))

    if bg_count == 0 or ink_count == 0 or ink_ratio < _MIN_INK_RATIO:
        return {
            "weber_c": 1.0,
            "mu_bg": round(float(np.mean(gray[bg_mask])) if bg_count else 0.0, 2),
            "mu_text": round(float(np.mean(gray[ink_mask])) if ink_count else 0.0, 2),
            "ink_ratio": round(ink_ratio, 4),
        }

    mu_bg = float(np.mean(gray[bg_mask]))
    mu_text = float(np.mean(gray[ink_mask]))
    mu_bg_safe = max(mu_bg, _MU_BG_EPS)
    weber_c = max(0.0, min(1.0, (mu_bg - mu_text) / mu_bg_safe))
    return {
        "weber_c": round(weber_c, 4),
        "mu_bg": round(mu_bg, 2),
        "mu_text": round(mu_text, 2),
        "ink_ratio": round(ink_ratio, 4),
    }


def failed_criteria(
    metrics: dict[str, float],
    thresholds: dict[str, float | bool] | None = None,
) -> list[str]:
    """基準未満の項目キー一覧（weber_c）。"""
    th = thresholds if thresholds is not None else faint_thresholds_from_config()
    failed: list[str] = []
    ink_ratio = float(metrics.get("ink_ratio") or 0)
    if ink_ratio < _MIN_INK_RATIO:
        return failed
    if float(metrics.get("weber_c") or 0) < float(th["min_weber_contrast"]):
        failed.append("weber_c")
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
        "weber_c": ("C", "min_weber_contrast"),
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
    worst_c = 2.0

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
        c = float(metrics.get("weber_c") or 1.0)
        if c < worst_c:
            worst_c = c
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
            "min_weber_contrast": float(th["min_weber_contrast"]),
        },
    }


def apply_gamma_bgr(image_bgr: np.ndarray, gamma: float) -> np.ndarray:
    """LAB の L チャンネルに I_out = 255 * (I/255)^γ を適用する。"""
    g = float(gamma)
    if g <= 1.01:
        return image_bgr
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    lf = l.astype(np.float32) / 255.0
    l2 = np.clip(255.0 * np.power(lf, g), 0.0, 255.0).astype(np.uint8)
    return cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)


def enhance_bgr(
    image_bgr: np.ndarray,
    *,
    contrast: float = 1.35,
    brightness: float = 0.0,
    clahe_clip: float = 2.5,
    bg_whiten: float = 0.0,
    gamma: float = 1.0,
) -> np.ndarray:
    """薄い字向けの強調（地色除去 → ガンマ → コントラスト → CLAHE）。

    bg_whiten: 0〜1。背景輝度（概ね P92）を白に寄せる強度。
    gamma: >1 で薄い字を暗く強調（Weber 向け前処理）。
    """
    out = image_bgr
    if float(bg_whiten) > 0.01:
        out = _whiten_background(out, float(bg_whiten))
    if float(gamma) > 1.01:
        out = apply_gamma_bgr(out, float(gamma))
    if abs(contrast - 1.0) > 1e-3 or abs(brightness) > 1e-3:
        out = cv2.convertScaleAbs(out, alpha=float(contrast), beta=float(brightness))
    if clahe_clip > 0.05:
        lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=float(clahe_clip), tileGridSize=(8, 8))
        l2 = clahe.apply(l)
        out = cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)
    elif clahe_clip < -0.05:
        strength = min(1.0, abs(float(clahe_clip)) / 5.0)
        lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l_blur = cv2.GaussianBlur(l, (0, 0), sigmaX=21, sigmaY=21)
        l2 = cv2.addWeighted(l, 1.0 - strength, l_blur, strength, 0)
        out = cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)
    return out


def _whiten_background(image_bgr: np.ndarray, strength: float) -> np.ndarray:
    """背景推定輝度を白へ正規化し、strength で原画像とブレンドする。"""
    strength = max(0.0, min(1.0, float(strength)))
    if strength <= 0.01:
        return image_bgr
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    lf = l.astype(np.float32)
    bg = float(np.percentile(lf, 92))
    bg = max(bg, 8.0)
    scale = 255.0 / bg
    whitened = np.clip(lf * scale, 0.0, 255.0)
    blended = lf * (1.0 - strength) + whitened * strength
    l2 = np.clip(blended, 0.0, 255.0).astype(np.uint8)
    return cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)
