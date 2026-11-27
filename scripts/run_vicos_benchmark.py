#!/usr/bin/env python3
"""Reproducible ViCoS 2022 (Hudovernik & Skocaj) Head-to-Head Benchmark Runner.

Supports two strict, zero-leakage evaluation modes:
  1. --mode table5 : Blind single-camera IBJJF match scoring on the official ViCoS Table 5
                     evaluation video (https://youtu.be/92RnyAlqJtQ, 325s) with BOTH the
                     top-right score overlay and bottom-left ViCoS HUD blacked out via ffmpeg.
  2. --mode table4 : Stratified 18-class BJJ position classification on a user-downloaded local
                     copy of the ViCoS Brazilian Jiu-Jitsu Positions Dataset (--vicos-dir).
                     Zero ViCoS images are ever committed to Git (CC BY-NC-SA 4.0 compliance).
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from rollmetrics.config import get_settings
from rollmetrics.evals.scoreboard_bench import (
    CompactScore,
    GroundTruthMatchLabel,
    MatchManifestEntry,
    NormalizedBox,
    score_masked_video_agentic,
)

# Hudovernik & Skocaj (2022) Table 5 official published baselines (Red vs Blue)
VICOS_TABLE5_YOUTUBE_URL = "https://youtu.be/92RnyAlqJtQ"
VICOS_TABLE5_JUDGES_GT = {
    "part_1": {"red": 9, "blue": 6},
    "part_2": {"red": 6, "blue": 13},
    "part_3": {"red": 9, "blue": 0},
    "part_4": {"red": 2, "blue": 4},
    "total": {"red": 26, "blue": 23, "winner": "A"},
}
VICOS_TABLE5_3CAM_PUBLISHED = {
    "method": "ViCoS 2022 (3-Camera Pose + NN)",
    "red": 26,
    "blue": 25,
    "winner": "A",
    "mae_points": 1.0,
}

# Hudovernik & Skocaj (2022) Table 4 official published baselines (18-class accuracy)
VICOS_TABLE4_PUBLISHED = {
    "1_view_raw": 78.7,
    "1_view_6frame_smoothed": 82.6,
    "1_view_aggregated_guard": 84.8,
    "3_view_multicam": 91.2,
}

# Exact normalized overlay blackout boxes for 92RnyAlqJtQ (top-right scoreboard + bottom-left HUD)
VICOS_TABLE5_MASK_BOXES: tuple[NormalizedBox, ...] = (
    NormalizedBox(x0=0.77, y0=0.00, x1=1.00, y1=0.35),  # PREDICTED + ACTUAL scoreboard table
    NormalizedBox(x0=0.00, y0=0.56, x1=0.44, y1=1.00),  # ViCoS position classifier HUD & bars
)

VICOS_18_CLASSES: tuple[str, ...] = (
    "standing",
    "takedown1",
    "takedown2",
    "open_guard1",
    "open_guard2",
    "closed_guard1",
    "closed_guard2",
    "half_guard1",
    "half_guard2",
    "side_control1",
    "side_control2",
    "mount1",
    "mount2",
    "back1",
    "back2",
    "turtle1",
    "turtle2",
    "other",
)


def _find_ffmpeg() -> str:
    for cand in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if cand and Path(cand).is_file():
            return cand
    raise FileNotFoundError("ffmpeg not found in PATH or /opt/homebrew/bin/ffmpeg")


def render_vicos_masked_video(src_video: Path, dst_video: Path, target_height: int = 480) -> dict[str, Any]:
    """Black out both the ViCoS top-right score table and bottom-left position HUD, then verify."""
    ffmpeg = _find_ffmpeg()
    dst_video.parent.mkdir(parents=True, exist_ok=True)
    filters = [
        f"drawbox=x=iw*{b.x0:.4f}:y=ih*{b.y0:.4f}:w=iw*{b.x1 - b.x0:.4f}:h=ih*{b.y1 - b.y0:.4f}:color=black:t=fill"
        for b in VICOS_TABLE5_MASK_BOXES
    ]
    filters.append(f"scale=-2:{target_height}")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src_video),
        "-vf",
        ",".join(filters),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        str(dst_video),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    import numpy as np
    from PIL import Image

    checked = 0
    max_seen_luma = 0.0
    for ts in (15, 75, 145, 225, 300):
        frame_jpg = dst_video.parent / f"verify_{ts}.jpg"
        subprocess.run(
            [ffmpeg, "-y", "-ss", str(ts), "-i", str(dst_video), "-frames:v", "1", "-q:v", "2", str(frame_jpg)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with Image.open(frame_jpg) as img:
            arr = np.asarray(img.convert("RGB"), dtype=np.float32)
            h, w = arr.shape[:2]
            for box in VICOS_TABLE5_MASK_BOXES:
                x0, y0, x1, y1 = box.to_pixel_rect(w, h)
                inset = 8
                crop = arr[y0 + inset : max(y0 + inset + 1, y1 - inset), x0 + inset : max(x0 + inset + 1, x1 - inset)]
                luma = float(np.max(0.299 * crop[:, :, 0] + 0.587 * crop[:, :, 1] + 0.114 * crop[:, :, 2]))
                max_seen_luma = max(max_seen_luma, luma)
                if luma > 20.0:
                    raise RuntimeError(f"ViCoS mask leakage at t={ts}s: max_luma={luma:.1f}")
        checked += 1
    return {
        "preset": "vicos_table5_hud_and_scoreboard",
        "frames_verified": checked,
        "max_interior_luma": round(max_seen_luma, 2),
        "passed": True,
    }


def run_table5_eval(
    video_path: Path,
    models: list[str],
    output_json: Path | None = None,
) -> dict[str, Any]:
    """Evaluate single-camera RollMetrics models blind against ViCoS 2022 Table 5 Judges GT."""
    cfg = get_settings()
    if not cfg.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required for live Table 5 evaluation")

    entry = MatchManifestEntry(
        match_id="vicos-2022-table5-92RnyAlqJtQ",
        video_id="92RnyAlqJtQ",
        url=VICOS_TABLE5_YOUTUBE_URL,
        source="ViCoS 2022 (Hudovernik & Skocaj)",
        event_name="ViCoS 2022 Table 5 Match",
        event_group="vicos_2022_table5",
        division="Adult No-Gi Sparring",
        belt="black",
        age_division="adult",
        uniform="nogi",
        athlete_a="Red Athlete",
        athlete_b="Blue Athlete",
        athlete_a_appearance="Red rashguard and white pants",
        athlete_b_appearance="Black rashguard and black shorts",
        overlay_preset="flograppling_lower_bar",
        start_sec=0.0,
        end_sec=325.0,
    )
    gt = GroundTruthMatchLabel(
        match_id=entry.match_id,
        video_id=entry.video_id,
        active_bout_start_sec=0.0,
        active_bout_end_sec=325.0,
        final_score_a=CompactScore(points=26, advantages=0, penalties=0),
        final_score_b=CompactScore(points=23, advantages=0, penalties=0),
        winner="A",
        method="points",
        athlete_a_appearance=entry.athlete_a_appearance,
        athlete_b_appearance=entry.athlete_b_appearance,
        human_spot_checked=True,
    )

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        masked_path = Path(tmp_dir) / "vicos_table5_masked_480p.mp4"
        leak_check = render_vicos_masked_video(video_path, masked_path, target_height=480)
        client = genai.Client(
            api_key=cfg.gemini_api_key,
            http_options=types.HttpOptions(timeout=480000),
        )
        for model_id in models:
            rec = score_masked_video_agentic(
                entry=entry,
                gt=gt,
                video_path=video_path,
                model_id=model_id,
                settings=cfg,
                genai_client=client,
                masked_video_path=masked_path,
                enable_mat_crop=False,
            )
            mae_pts = 0.5 * (
                abs(rec.final_score_a.points - 26) + abs(rec.final_score_b.points - 23)
            )
            results.append(
                {
                    "model_id": model_id,
                    "predicted_red": rec.final_score_a.points,
                    "predicted_blue": rec.final_score_b.points,
                    "predicted_winner": rec.winner,
                    "winner_correct": rec.winner == "A",
                    "mae_points_vs_judges": round(mae_pts, 2),
                    "event_count": len(rec.events),
                    "latency_ms": round(rec.latency_ms, 1),
                    "cost_usd": round(rec.cost_usd, 5),
                    "leakage_verified": True,
                }
            )

    payload = {
        "benchmark": "vicos_2022_table5",
        "video_url": VICOS_TABLE5_YOUTUBE_URL,
        "judges_gt": VICOS_TABLE5_JUDGES_GT,
        "vicos_3cam_baseline": VICOS_TABLE5_3CAM_PUBLISHED,
        "leakage_verification": leak_check,
        "models": results,
    }
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def run_table4_eval(
    vicos_dir: Path,
    model_id: str,
    sample_size: int = 180,
    seed: int = 42,
    output_json: Path | None = None,
) -> dict[str, Any]:
    """Stratified 18-class single-view evaluation on a local ViCoS dataset directory."""
    ann_path = vicos_dir / "annotations.json"
    if not ann_path.is_file():
        raise FileNotFoundError(
            f"Missing {ann_path}. Download annotations.json and images.zip from "
            "https://vicos.si/resources/jiujitsu/ (CC BY-NC-SA 4.0) into --vicos-dir."
        )
    cfg = get_settings()
    if not cfg.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required for live Table 4 evaluation")

    raw = json.loads(ann_path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("annotations", raw.get("images", []))
    by_label: dict[str, list[dict[str, Any]]] = {}
    for row in items:
        lbl = str(row.get("label") or row.get("position") or row.get("class") or "").strip().lower()
        img_rel = row.get("image") or row.get("file_name") or row.get("path")
        if lbl and img_rel:
            by_label.setdefault(lbl, []).append({"label": lbl, "path": vicos_dir / str(img_rel)})

    rng = random.Random(seed)
    per_class = max(1, sample_size // max(1, len(by_label)))
    sampled: list[dict[str, Any]] = []
    for lbl in sorted(by_label):
        pool = [x for x in by_label[lbl] if x["path"].is_file()]
        rng.shuffle(pool)
        sampled.extend(pool[:per_class])

    client = genai.Client(api_key=cfg.gemini_api_key)
    prompt = (
        "Classify this single-camera Brazilian Jiu-Jitsu sparring frame into EXACTLY ONE of these "
        f"18 ViCoS labels: {', '.join(VICOS_18_CLASSES)}. "
        "Return strict JSON: {\"label\": \"<one_of_18_labels>\"}."
    )
    correct = 0
    evaluated = 0
    for item in sampled:
        img_bytes = item["path"].read_bytes()
        resp = client.models.generate_content(
            model=model_id,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                prompt,
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0),
        )
        pred = json.loads(resp.text or "{}").get("label", "").strip().lower()
        evaluated += 1
        if pred == item["label"]:
            correct += 1

    acc = round(100.0 * correct / max(1, evaluated), 2)
    payload = {
        "benchmark": "vicos_2022_table4",
        "model_id": model_id,
        "evaluated_frames": evaluated,
        "accuracy_pct": acc,
        "vicos_published_baselines_pct": VICOS_TABLE4_PUBLISHED,
    }
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run reproducible ViCoS 2022 Table 4 / Table 5 head-to-head benchmarks.")
    p.add_argument("--mode", choices=["table5", "table4"], default="table5")
    p.add_argument("--video", type=Path, default=Path("/tmp/vicos_eval/92RnyAlqJtQ.f605.mp4"))
    p.add_argument("--vicos-dir", type=Path, default=Path("/tmp/vicos_eval/dataset"))
    p.add_argument(
        "--models",
        nargs="+",
        default=["gemini-4-hydrogen-agentic", "gemini-4-neon-agentic"],
        help="Models to evaluate in table5 mode (or first model in table4 mode).",
    )
    p.add_argument("--sample-size", type=int, default=180)
    p.add_argument("--output-json", type=Path, default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "table5":
        res = run_table5_eval(args.video, models=args.models, output_json=args.output_json)
    else:
        res = run_table4_eval(
            args.vicos_dir,
            model_id=args.models[0],
            sample_size=args.sample_size,
            output_json=args.output_json,
        )
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
