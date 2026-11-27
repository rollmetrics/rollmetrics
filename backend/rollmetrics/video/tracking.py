"""Dynamic multi-person mat action bounding-box tracker for tight 1.5x-2x resolution crops."""

from __future__ import annotations

import io
import subprocess
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from rollmetrics.evals.scoreboard_bench.masking import _find_binary, mask_frame_scoreboard
from rollmetrics.evals.scoreboard_bench.schema import NormalizedBox


def _sample_masked_frames(
    video_path: Path,
    start_sec: float,
    end_sec: float,
    preset_name: str,
    num_samples: int = 8,
) -> list[np.ndarray]:
    """Extracts small RGB numpy frames across the bout with scoreboard masked out."""
    ffmpeg = _find_binary("ffmpeg")
    if not ffmpeg:
        return []

    dur = max(1.0, float(end_sec) - float(start_sec))
    count = max(2, min(12, int(num_samples)))
    step = dur / float(count + 1)
    arrays: list[np.ndarray] = []

    for idx in range(count):
        ts = max(0.0, float(start_sec) + step * (idx + 1))
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{ts:.2f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=320:-2",
                "-q:v",
                "5",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
            ],
            check=False,
            capture_output=True,
        )
        if proc.returncode != 0 or not proc.stdout:
            continue
        try:
            with Image.open(io.BytesIO(proc.stdout)) as raw_im:
                masked = mask_frame_scoreboard(raw_im, preset_name=preset_name)
                arrays.append(np.asarray(masked.convert("RGB"), dtype=np.float32))
        except (OSError, KeyError):
            continue
    return arrays


def _clamp_crop_box(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    min_width: float = 0.50,
    min_height: float = 0.55,
    max_width: float = 0.94,
    max_height: float = 0.94,
    pad: float = 0.08,
) -> NormalizedBox | None:
    """Expands a raw action region by safety padding and clamps to a clean 1.5x-2x mat zoom box."""
    cx = 0.5 * (x0 + x1)
    cy = 0.5 * (y0 + y1)
    w = max(min_width, min(max_width, (x1 - x0) + 2.0 * pad))
    h = max(min_height, min(max_height, (y1 - y0) + 2.0 * pad))

    # Skip crop if zoom gain is negligible (< 1.12x)
    if w * h > 0.88:
        return None

    nx0 = max(0.0, min(1.0 - w, cx - 0.5 * w))
    ny0 = max(0.0, min(1.0 - h, cy - 0.5 * h))
    nx1 = min(1.0, nx0 + w)
    ny1 = min(1.0, ny0 + h)
    return NormalizedBox(
        x0=round(nx0, 4),
        y0=round(ny0, 4),
        x1=round(nx1, 4),
        y1=round(ny1, 4),
    )


def compute_mat_crop_box_from_arrays(
    frames_rgb: list[np.ndarray],
    *,
    yolo_detector: Callable[[np.ndarray], list[tuple[float, float, float, float]]] | None = None,
    min_width: float = 0.50,
    min_height: float = 0.55,
) -> NormalizedBox | None:
    """Computes a tight normalized mat-action bounding box around the two athletes and center referee.

    Uses an optional YOLO-Seg person detector hook when supplied, falling back to fast zero-bloat
    inter-frame motion energy so it runs with zero heavy weights on any CPU.
    """
    if len(frames_rgb) < 2:
        return None

    # 1. Optional YOLO-Seg person detector hook (returns normalized [x0, y0, x1, y1] boxes)
    if yolo_detector is not None:
        all_boxes: list[tuple[float, float, float, float]] = []
        for arr in frames_rgb:
            for box in yolo_detector(arr) or []:
                if len(box) == 4 and box[2] > box[0] and box[3] > box[1]:
                    all_boxes.append(
                        (
                            max(0.0, min(1.0, float(box[0]))),
                            max(0.0, min(1.0, float(box[1]))),
                            max(0.0, min(1.0, float(box[2]))),
                            max(0.0, min(1.0, float(box[3]))),
                        )
                    )
        if all_boxes:
            xs0 = [b[0] for b in all_boxes]
            ys0 = [b[1] for b in all_boxes]
            xs1 = [b[2] for b in all_boxes]
            ys1 = [b[3] for b in all_boxes]
            return _clamp_crop_box(
                float(np.percentile(xs0, 10)),
                float(np.percentile(ys0, 10)),
                float(np.percentile(xs1, 90)),
                float(np.percentile(ys1, 90)),
                min_width=min_width,
                min_height=min_height,
            )

    # 2. Fast zero-bloat motion energy accumulator across sampled RGB frames
    h, w = frames_rgb[0].shape[:2]
    if h < 16 or w < 16:
        return None

    motion_acc = np.zeros((h, w), dtype=np.float32)
    valid_pairs = 0
    for idx in range(1, len(frames_rgb)):
        prev = frames_rgb[idx - 1]
        curr = frames_rgb[idx]
        if prev.shape != curr.shape:
            continue
        diff = np.mean(np.abs(curr - prev), axis=2)
        motion_acc += diff
        valid_pairs += 1

    if valid_pairs == 0:
        return None
    motion_acc /= float(valid_pairs)

    # Ignore zeroed-out blackout regions and low background noise
    peak = float(np.max(motion_acc))
    if peak < 4.0:
        return None

    thresh = max(4.0, peak * 0.22)
    ys, xs = np.where(motion_acc >= thresh)
    if ys.size < 20:
        return None

    # Use robust 5th..95th percentiles to ignore stray spectator movement at arena edges
    x0 = float(np.percentile(xs, 5)) / float(w)
    x1 = float(np.percentile(xs, 95)) / float(w)
    y0 = float(np.percentile(ys, 5)) / float(h)
    y1 = float(np.percentile(ys, 95)) / float(h)

    return _clamp_crop_box(
        x0,
        y0,
        x1,
        y1,
        min_width=min_width,
        min_height=min_height,
    )


def compute_mat_crop_box(
    video_path: Path,
    start_sec: float,
    end_sec: float,
    preset_name: str = "flograppling_lower_bar",
    num_samples: int = 8,
    yolo_detector: Callable[[np.ndarray], list[tuple[float, float, float, float]]] | None = None,
) -> NormalizedBox | None:
    """Samples scoreboard-masked bout frames and returns a tight 1.5x-2x mat action crop box."""
    frames = _sample_masked_frames(
        video_path=video_path,
        start_sec=start_sec,
        end_sec=end_sec,
        preset_name=preset_name,
        num_samples=num_samples,
    )
    return compute_mat_crop_box_from_arrays(frames, yolo_detector=yolo_detector)


def extract_hsv_signature(rgb_crop: np.ndarray, bins: tuple[int, int, int] = (8, 4, 4)) -> np.ndarray:
    """Computes an L2-normalized HSV color histogram signature for an athlete bounding-box crop."""
    if rgb_crop.size == 0 or rgb_crop.ndim != 3 or rgb_crop.shape[2] < 3:
        vec = np.zeros(bins[0] * bins[1] * bins[2], dtype=np.float32)
        vec[0] = 1.0
        return vec

    rgb_u8 = np.clip(rgb_crop[:, :, :3], 0, 255).astype(np.uint8)
    hsv_arr = np.asarray(Image.fromarray(rgb_u8, mode="RGB").convert("HSV"), dtype=np.float32)
    h_idx = np.clip((hsv_arr[:, :, 0] / 256.0 * bins[0]).astype(np.int32), 0, bins[0] - 1)
    s_idx = np.clip((hsv_arr[:, :, 1] / 256.0 * bins[1]).astype(np.int32), 0, bins[1] - 1)
    v_idx = np.clip((hsv_arr[:, :, 2] / 256.0 * bins[2]).astype(np.int32), 0, bins[2] - 1)
    flat_idx = (h_idx * bins[1] + s_idx) * bins[2] + v_idx
    hist = np.bincount(flat_idx.ravel(), minlength=bins[0] * bins[1] * bins[2]).astype(np.float32)
    norm = float(np.linalg.norm(hist))
    if norm > 1e-8:
        hist /= norm
    else:
        hist[0] = 1.0
    return hist


def _crop_norm_box(frame_rgb: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    h, w = frame_rgb.shape[:2]
    x0 = max(0, min(w - 1, round(box[0] * w)))
    y0 = max(0, min(h - 1, round(box[1] * h)))
    x1 = max(x0 + 1, min(w, round(box[2] * w)))
    y1 = max(y0 + 1, min(h, round(box[3] * h)))
    return frame_rgb[y0:y1, x0:x1]


def _box_centroid(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return (0.5 * (box[0] + box[2]), 0.5 * (box[1] + box[3]))


def lock_athlete_identities(
    frames_rgb: list[np.ndarray],
    detections_per_frame: list[list[tuple[float, float, float, float]]],
    *,
    appearance_weight: float = 0.65,
    spatial_weight: float = 0.35,
    ema_alpha: float = 0.25,
    swap_risk_margin: float = 0.08,
) -> dict[str, object]:
    """Locks Athlete A (starts LEFT) and Athlete B (starts RIGHT) across frames via HSV + centroid continuity.

    Prevents A/B identity swaps during tight scrambles and reports per-frame swap-risk diagnostics.
    """
    if not frames_rgb or not detections_per_frame:
        return {"tracks": [], "swap_risk_frames": [], "swap_risk_rate": 0.0, "frames_tracked": 0}

    count = min(len(frames_rgb), len(detections_per_frame))
    sig_a: np.ndarray | None = None
    sig_b: np.ndarray | None = None
    pos_a: tuple[float, float] = (0.25, 0.5)
    pos_b: tuple[float, float] = (0.75, 0.5)

    tracks: list[dict[str, object]] = []
    swap_risk_frames: list[int] = []

    for f_idx in range(count):
        frame = frames_rgb[f_idx]
        raw_boxes = [
            b
            for b in (detections_per_frame[f_idx] or [])
            if len(b) == 4 and b[2] > b[0] and b[3] > b[1]
        ]
        if len(raw_boxes) < 2:
            tracks.append({"frame_index": f_idx, "A": None, "B": None, "swap_risk": True})
            swap_risk_frames.append(f_idx)
            continue

        # Sort by area descending and keep top-2 athlete detections
        top2 = sorted(raw_boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)[:2]
        b0, b1 = top2[0], top2[1]
        s0 = extract_hsv_signature(_crop_norm_box(frame, b0))
        s1 = extract_hsv_signature(_crop_norm_box(frame, b1))
        c0 = _box_centroid(b0)
        c1 = _box_centroid(b1)

        if sig_a is None or sig_b is None:
            # Frame 0 anchor: Athlete A starts LEFT (smaller centroid x), Athlete B starts RIGHT
            if c0[0] <= c1[0]:
                box_a, sig_a, pos_a = b0, s0, c0
                box_b, sig_b, pos_b = b1, s1, c1
            else:
                box_a, sig_a, pos_a = b1, s1, c1
                box_b, sig_b, pos_b = b0, s0, c0
            tracks.append(
                {
                    "frame_index": f_idx,
                    "A": {"box": box_a, "centroid": pos_a, "similarity": 1.0},
                    "B": {"box": box_b, "centroid": pos_b, "similarity": 1.0},
                    "margin": 1.0,
                    "swap_risk": False,
                }
            )
            continue

        def _pair_score(
            cand_s_a: np.ndarray,
            cand_c_a: tuple[float, float],
            cand_s_b: np.ndarray,
            cand_c_b: tuple[float, float],
            ref_s_a: np.ndarray = sig_a,
            ref_p_a: tuple[float, float] = pos_a,
            ref_s_b: np.ndarray = sig_b,
            ref_p_b: tuple[float, float] = pos_b,
        ) -> tuple[float, float, float]:
            sim_a = float(np.dot(ref_s_a, cand_s_a))
            sim_b = float(np.dot(ref_s_b, cand_s_b))
            dist_a = float(np.hypot(cand_c_a[0] - ref_p_a[0], cand_c_a[1] - ref_p_a[1]))
            dist_b = float(np.hypot(cand_c_b[0] - ref_p_b[0], cand_c_b[1] - ref_p_b[1]))
            prox = max(0.0, 1.0 - 0.5 * (dist_a + dist_b))
            total = appearance_weight * (0.5 * (sim_a + sim_b)) + spatial_weight * prox
            return total, sim_a, sim_b

        score_straight, sim_a0, sim_b1 = _pair_score(s0, c0, s1, c1)
        score_swapped, sim_a1, sim_b0 = _pair_score(s1, c1, s0, c0)
        margin = abs(score_straight - score_swapped)
        is_risky = margin < swap_risk_margin

        if score_straight >= score_swapped:
            box_a, cand_sa, pos_a, sim_a_val = b0, s0, c0, sim_a0
            box_b, cand_sb, pos_b, sim_b_val = b1, s1, c1, sim_b1
        else:
            box_a, cand_sa, pos_a, sim_a_val = b1, s1, c1, sim_a1
            box_b, cand_sb, pos_b, sim_b_val = b0, s0, c0, sim_b0

        # Update appearance templates gently only when assignment is unambiguous
        if not is_risky:
            sig_a = (1.0 - ema_alpha) * sig_a + ema_alpha * cand_sa
            sig_a /= max(1e-8, float(np.linalg.norm(sig_a)))
            sig_b = (1.0 - ema_alpha) * sig_b + ema_alpha * cand_sb
            sig_b /= max(1e-8, float(np.linalg.norm(sig_b)))
        else:
            swap_risk_frames.append(f_idx)

        tracks.append(
            {
                "frame_index": f_idx,
                "A": {"box": box_a, "centroid": pos_a, "similarity": round(sim_a_val, 4)},
                "B": {"box": box_b, "centroid": pos_b, "similarity": round(sim_b_val, 4)},
                "margin": round(margin, 4),
                "swap_risk": is_risky,
            }
        )

    return {
        "tracks": tracks,
        "swap_risk_frames": swap_risk_frames,
        "swap_risk_rate": round(len(swap_risk_frames) / float(count), 4) if count else 0.0,
        "frames_tracked": count,
    }
