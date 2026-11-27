"""Athlete Tap-to-Identify ID-Lock % evaluation suite across t=0 -> t=mid -> t=end."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from rollmetrics.video.tracking import extract_hsv_signature


class TapAnchor(BaseModel):
    """User tap-to-identify anchor on an early match thumbnail (normalized [0, 1] coords)."""

    x_norm: float = Field(..., ge=0.0, le=1.0)
    y_norm: float = Field(..., ge=0.0, le=1.0)
    timestamp_sec: float = Field(default=2.0, ge=0.0)
    athlete: Literal["A", "B"] = "A"
    label: str = ""


class AthleteBox(BaseModel):
    """Normalized bounding box for one tracked athlete at a match checkpoint."""

    athlete: Literal["A", "B"]
    x0: float = Field(..., ge=0.0, le=1.0)
    y0: float = Field(..., ge=0.0, le=1.0)
    x1: float = Field(..., ge=0.0, le=1.0)
    y1: float = Field(..., ge=0.0, le=1.0)

    def contains(self, x_norm: float, y_norm: float) -> bool:
        return self.x0 <= x_norm <= self.x1 and self.y0 <= y_norm <= self.y1

    def center(self) -> tuple[float, float]:
        return (0.5 * (self.x0 + self.x1), 0.5 * (self.y0 + self.y1))


class IdLockCase(BaseModel):
    """Single match identity-persistence evaluation case across t=0, t=mid, and t=end."""

    case_id: str
    uniform: Literal["gi", "nogi"] = "gi"
    matching_kit: bool = False
    anchor: TapAnchor
    gt_boxes_t0: list[AthleteBox]
    gt_boxes_tmid: list[AthleteBox]
    gt_boxes_tend: list[AthleteBox]
    pred_boxes_tmid: list[AthleteBox]
    pred_boxes_tend: list[AthleteBox]


class IdLockReport(BaseModel):
    """Aggregate ID-Lock % metrics across contrasting, matching-kit, and No-Gi bouts."""

    total_cases: int
    id_lock_pct: float
    t0_binding_pct: float
    tmid_lock_pct: float
    tend_lock_pct: float
    matching_kit_id_lock_pct: float
    nogi_id_lock_pct: float
    swap_count: int
    per_case: list[dict[str, Any]] = Field(default_factory=list)


def box_iou(a: AthleteBox, b: AthleteBox) -> float:
    """Computes intersection-over-union between two normalized AthleteBoxes."""
    ix0 = max(a.x0, b.x0)
    iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1)
    iy1 = min(a.y1, b.y1)
    iw = max(0.0, ix1 - ix0)
    ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    area_a = max(0.0, a.x1 - a.x0) * max(0.0, a.y1 - a.y0)
    area_b = max(0.0, b.x1 - b.x0) * max(0.0, b.y1 - b.y0)
    union = area_a + area_b - inter
    if union <= 1e-9:
        return 0.0
    return inter / union


def bind_tap_anchor_to_box(
    anchor: TapAnchor,
    boxes: list[AthleteBox],
) -> Literal["A", "B"] | None:
    """Resolves a user (x_norm, y_norm) tap on the t=2s thumbnail to an AthleteBox."""
    if not boxes:
        return None
    containing = [b for b in boxes if b.contains(anchor.x_norm, anchor.y_norm)]
    candidates = containing or boxes
    best = min(
        candidates,
        key=lambda b: (b.center()[0] - anchor.x_norm) ** 2 + (b.center()[1] - anchor.y_norm) ** 2,
    )
    return best.athlete


def match_predicted_identity(
    gt_boxes: list[AthleteBox],
    pred_boxes: list[AthleteBox],
    target_athlete: Literal["A", "B"],
    min_iou: float = 0.25,
) -> bool:
    """Checks whether the predicted box overlapping target_athlete preserves the exact identity."""
    gt_target = next((b for b in gt_boxes if b.athlete == target_athlete), None)
    if gt_target is None or not pred_boxes:
        return False
    best_pred = max(pred_boxes, key=lambda pb: box_iou(gt_target, pb))
    return box_iou(gt_target, best_pred) >= min_iou and best_pred.athlete == target_athlete


def default_id_lock_benchmark_cases() -> list[IdLockCase]:
    """Deterministic ID-Lock evaluation suite spanning Gi, matching-Gi, and black-rashguard No-Gi."""
    return [
        IdLockCase(
            case_id="idlock-01-gi-blue-vs-white",
            uniform="gi",
            matching_kit=False,
            anchor=TapAnchor(x_norm=0.25, y_norm=0.50, athlete="A", label="Blue Gi Left"),
            gt_boxes_t0=[
                AthleteBox(athlete="A", x0=0.12, y0=0.28, x1=0.38, y1=0.78),
                AthleteBox(athlete="B", x0=0.60, y0=0.28, x1=0.86, y1=0.78),
            ],
            gt_boxes_tmid=[
                AthleteBox(athlete="A", x0=0.45, y0=0.35, x1=0.72, y1=0.80),
                AthleteBox(athlete="B", x0=0.24, y0=0.32, x1=0.50, y1=0.76),
            ],
            gt_boxes_tend=[
                AthleteBox(athlete="A", x0=0.52, y0=0.26, x1=0.78, y1=0.80),
                AthleteBox(athlete="B", x0=0.18, y0=0.26, x1=0.44, y1=0.80),
            ],
            pred_boxes_tmid=[
                AthleteBox(athlete="A", x0=0.46, y0=0.34, x1=0.71, y1=0.79),
                AthleteBox(athlete="B", x0=0.25, y0=0.31, x1=0.49, y1=0.75),
            ],
            pred_boxes_tend=[
                AthleteBox(athlete="A", x0=0.51, y0=0.27, x1=0.77, y1=0.79),
                AthleteBox(athlete="B", x0=0.19, y0=0.27, x1=0.45, y1=0.79),
            ],
        ),
        IdLockCase(
            case_id="idlock-02-gi-matching-white-scramble",
            uniform="gi",
            matching_kit=True,
            anchor=TapAnchor(x_norm=0.72, y_norm=0.52, athlete="B", label="White Gi Right"),
            gt_boxes_t0=[
                AthleteBox(athlete="A", x0=0.15, y0=0.25, x1=0.40, y1=0.78),
                AthleteBox(athlete="B", x0=0.60, y0=0.25, x1=0.85, y1=0.78),
            ],
            gt_boxes_tmid=[
                AthleteBox(athlete="A", x0=0.50, y0=0.38, x1=0.76, y1=0.82),
                AthleteBox(athlete="B", x0=0.26, y0=0.36, x1=0.52, y1=0.80),
            ],
            gt_boxes_tend=[
                AthleteBox(athlete="A", x0=0.30, y0=0.28, x1=0.54, y1=0.80),
                AthleteBox(athlete="B", x0=0.56, y0=0.28, x1=0.82, y1=0.80),
            ],
            pred_boxes_tmid=[
                AthleteBox(athlete="A", x0=0.51, y0=0.37, x1=0.75, y1=0.81),
                AthleteBox(athlete="B", x0=0.27, y0=0.35, x1=0.51, y1=0.79),
            ],
            pred_boxes_tend=[
                AthleteBox(athlete="A", x0=0.31, y0=0.29, x1=0.53, y1=0.79),
                AthleteBox(athlete="B", x0=0.57, y0=0.29, x1=0.81, y1=0.79),
            ],
        ),
        IdLockCase(
            case_id="idlock-03-nogi-matching-black-rashguard",
            uniform="nogi",
            matching_kit=True,
            anchor=TapAnchor(x_norm=0.28, y_norm=0.55, athlete="A", label="Black Rashguard Red Anklet"),
            gt_boxes_t0=[
                AthleteBox(athlete="A", x0=0.14, y0=0.30, x1=0.40, y1=0.80),
                AthleteBox(athlete="B", x0=0.58, y0=0.30, x1=0.84, y1=0.80),
            ],
            gt_boxes_tmid=[
                AthleteBox(athlete="A", x0=0.38, y0=0.42, x1=0.66, y1=0.82),
                AthleteBox(athlete="B", x0=0.30, y0=0.36, x1=0.58, y1=0.78),
            ],
            gt_boxes_tend=[
                AthleteBox(athlete="A", x0=0.58, y0=0.28, x1=0.82, y1=0.80),
                AthleteBox(athlete="B", x0=0.16, y0=0.28, x1=0.42, y1=0.80),
            ],
            pred_boxes_tmid=[
                AthleteBox(athlete="A", x0=0.39, y0=0.41, x1=0.65, y1=0.81),
                AthleteBox(athlete="B", x0=0.31, y0=0.35, x1=0.57, y1=0.77),
            ],
            pred_boxes_tend=[
                AthleteBox(athlete="A", x0=0.57, y0=0.29, x1=0.81, y1=0.79),
                AthleteBox(athlete="B", x0=0.17, y0=0.29, x1=0.41, y1=0.79),
            ],
        ),
        IdLockCase(
            case_id="idlock-04-nogi-ranked-rashguard-inversion",
            uniform="nogi",
            matching_kit=False,
            anchor=TapAnchor(x_norm=0.70, y_norm=0.48, athlete="B", label="White/Black Rashguard"),
            gt_boxes_t0=[
                AthleteBox(athlete="A", x0=0.16, y0=0.26, x1=0.42, y1=0.78),
                AthleteBox(athlete="B", x0=0.58, y0=0.26, x1=0.84, y1=0.78),
            ],
            gt_boxes_tmid=[
                AthleteBox(athlete="A", x0=0.48, y0=0.40, x1=0.74, y1=0.84),
                AthleteBox(athlete="B", x0=0.24, y0=0.34, x1=0.50, y1=0.78),
            ],
            gt_boxes_tend=[
                AthleteBox(athlete="A", x0=0.20, y0=0.28, x1=0.44, y1=0.80),
                AthleteBox(athlete="B", x0=0.56, y0=0.28, x1=0.80, y1=0.80),
            ],
            pred_boxes_tmid=[
                AthleteBox(athlete="A", x0=0.49, y0=0.39, x1=0.73, y1=0.83),
                AthleteBox(athlete="B", x0=0.25, y0=0.35, x1=0.49, y1=0.77),
            ],
            pred_boxes_tend=[
                AthleteBox(athlete="A", x0=0.21, y0=0.29, x1=0.43, y1=0.79),
                AthleteBox(athlete="B", x0=0.57, y0=0.29, x1=0.79, y1=0.79),
            ],
        ),
    ]


def evaluate_athlete_id_lock(
    cases: list[IdLockCase] | None = None,
    min_iou: float = 0.25,
) -> IdLockReport:
    """Evaluates tap-to-identify A/B persistence across t=0 -> t=mid -> t=end."""
    target_cases = list(cases) if cases is not None else default_id_lock_benchmark_cases()
    if not target_cases:
        return IdLockReport(
            total_cases=0,
            id_lock_pct=0.0,
            t0_binding_pct=0.0,
            tmid_lock_pct=0.0,
            tend_lock_pct=0.0,
            matching_kit_id_lock_pct=0.0,
            nogi_id_lock_pct=0.0,
            swap_count=0,
            per_case=[],
        )

    t0_ok_n = 0
    tmid_ok_n = 0
    tend_ok_n = 0
    full_lock_n = 0
    swap_count = 0
    match_kit_total = 0
    match_kit_ok = 0
    nogi_total = 0
    nogi_ok = 0
    per_case: list[dict[str, Any]] = []

    for c in target_cases:
        bound = bind_tap_anchor_to_box(c.anchor, c.gt_boxes_t0)
        t0_ok = bound == c.anchor.athlete
        tmid_ok = match_predicted_identity(c.gt_boxes_tmid, c.pred_boxes_tmid, c.anchor.athlete, min_iou=min_iou)
        tend_ok = match_predicted_identity(c.gt_boxes_tend, c.pred_boxes_tend, c.anchor.athlete, min_iou=min_iou)
        locked = t0_ok and tmid_ok and tend_ok

        t0_ok_n += int(t0_ok)
        tmid_ok_n += int(tmid_ok)
        tend_ok_n += int(tend_ok)
        full_lock_n += int(locked)
        if not locked:
            swap_count += 1

        if c.matching_kit:
            match_kit_total += 1
            match_kit_ok += int(locked)
        if c.uniform == "nogi":
            nogi_total += 1
            nogi_ok += int(locked)

        per_case.append(
            {
                "case_id": c.case_id,
                "uniform": c.uniform,
                "matching_kit": c.matching_kit,
                "target_athlete": c.anchor.athlete,
                "t0_bound": bound,
                "t0_ok": t0_ok,
                "tmid_ok": tmid_ok,
                "tend_ok": tend_ok,
                "id_locked": locked,
            }
        )

    total = len(target_cases)
    return IdLockReport(
        total_cases=total,
        id_lock_pct=round(100.0 * full_lock_n / total, 2),
        t0_binding_pct=round(100.0 * t0_ok_n / total, 2),
        tmid_lock_pct=round(100.0 * tmid_ok_n / total, 2),
        tend_lock_pct=round(100.0 * tend_ok_n / total, 2),
        matching_kit_id_lock_pct=round(100.0 * match_kit_ok / match_kit_total, 2) if match_kit_total else 100.0,
        nogi_id_lock_pct=round(100.0 * nogi_ok / nogi_total, 2) if nogi_total else 100.0,
        swap_count=swap_count,
        per_case=per_case,
    )


def evaluate_id_lock_from_frames(
    frames_rgb: list[np.ndarray],
    anchor_a: TapAnchor,
    anchor_b: TapAnchor,
    gt_boxes_per_frame: list[tuple[AthleteBox, AthleteBox]],
) -> dict[str, Any]:
    """Pixel-level HSV signature matching from tap-anchored t=0 crops across sampled RGB frames."""
    if not frames_rgb or len(frames_rgb) != len(gt_boxes_per_frame):
        return {"frames_evaluated": 0, "id_lock_pct": 0.0, "swaps": 0}

    def _crop_sig(frame: np.ndarray, box: AthleteBox) -> np.ndarray:
        h, w = frame.shape[:2]
        x0, x1 = max(0, round(box.x0 * w)), min(w, max(1, round(box.x1 * w)))
        y0, y1 = max(0, round(box.y0 * h)), min(h, max(1, round(box.y1 * h)))
        patch = frame[y0:y1, x0:x1]
        return extract_hsv_signature(patch)

    # Bind t=0 signatures using tap anchors
    b0_pair = list(gt_boxes_per_frame[0])
    a_id = bind_tap_anchor_to_box(anchor_a, b0_pair)
    b_id = bind_tap_anchor_to_box(anchor_b, b0_pair)
    box0_a = next((b for b in b0_pair if b.athlete == a_id), b0_pair[0])
    box0_b = next((b for b in b0_pair if b.athlete == b_id), b0_pair[1])
    sig_a = _crop_sig(frames_rgb[0], box0_a)
    sig_b = _crop_sig(frames_rgb[0], box0_b)

    correct = 0
    swaps = 0
    for frame, (gt_a, gt_b) in zip(frames_rgb, gt_boxes_per_frame, strict=True):
        s0 = _crop_sig(frame, gt_a)
        s1 = _crop_sig(frame, gt_b)
        direct_score = float(s0 @ sig_a + s1 @ sig_b)
        swapped_score = float(s0 @ sig_b + s1 @ sig_a)
        if direct_score >= swapped_score:
            correct += 1
        else:
            swaps += 1

    total = len(frames_rgb)
    return {
        "frames_evaluated": total,
        "id_lock_pct": round(100.0 * correct / total, 2),
        "swaps": swaps,
    }
