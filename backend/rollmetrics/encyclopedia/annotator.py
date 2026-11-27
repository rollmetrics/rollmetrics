"""Tier 1B 4-layer Gemini Vision annotator for the IBJJF Match Encyclopedia."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from google import genai
from PIL import Image, ImageDraw

from rollmetrics.config import Settings, get_settings
from rollmetrics.encyclopedia.gcs_vault import GCSVault
from rollmetrics.encyclopedia.schema import (
    MatchAnnotationBundle,
    MatchRecord,
    RefereeSignalEvent,
    RuleEdgeCaseTag,
    ScoringRuleEvent,
    TechniqueEvent,
    VisionCalibrationMetadata,
)
from rollmetrics.encyclopedia.store import EncyclopediaStore
from rollmetrics.providers.base import parse_json_lenient
from rollmetrics.providers.gemini_cloud import extract_interaction_text

logger = logging.getLogger(__name__)

ANNOTATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "scoring_events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "video_sec": {"type": "number"},
                    "athlete": {"type": "string", "enum": ["A", "B", "both"]},
                    "event_type": {
                        "type": "string",
                        "enum": [
                            "takedown",
                            "sweep",
                            "knee_on_belly",
                            "guard_pass",
                            "mount",
                            "back_control",
                            "back_mount",
                            "advantage",
                            "penalty",
                            "stalling_clock",
                            "stabilization_window",
                            "submission",
                            "dq",
                            "referee_decision",
                        ],
                    },
                    "points": {"type": "integer"},
                    "advantages": {"type": "integer"},
                    "penalties": {"type": "integer"},
                    "submission_type": {"type": "string"},
                    "stabilization_sec": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["video_sec", "athlete", "event_type"],
            },
        },
        "technique_events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "video_sec": {"type": "number"},
                    "actor": {"type": "string", "enum": ["A", "B", "both"]},
                    "category": {
                        "type": "string",
                        "enum": [
                            "guard_pull",
                            "guard_state",
                            "takedown",
                            "sweep",
                            "pass",
                            "mount",
                            "back_take",
                            "escape",
                            "submission_attempt",
                            "submission_finish",
                            "reset",
                        ],
                    },
                    "technique_name": {"type": "string"},
                    "guard_family": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["video_sec", "actor", "category", "technique_name"],
            },
        },
        "vision_metadata": {
            "type": "object",
            "properties": {
                "athlete_a_appearance": {"type": "string"},
                "athlete_b_appearance": {"type": "string"},
                "camera_angle": {
                    "type": "string",
                    "enum": ["broadcast_elevated", "matside_static", "matside_handheld", "overhead", "unknown"],
                },
                "referee_signals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "video_sec": {"type": "number"},
                            "signal_type": {
                                "type": "string",
                                "enum": [
                                    "start_combate",
                                    "stop_parou",
                                    "points_2",
                                    "points_3",
                                    "points_4",
                                    "advantage",
                                    "penalty",
                                    "stalling_20s",
                                    "dq",
                                    "winner_raise",
                                ],
                            },
                            "athlete": {"type": "string", "enum": ["A", "B", "both", "unknown"]},
                        },
                        "required": ["video_sec", "signal_type"],
                    },
                },
            },
        },
        "rule_edge_cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "video_sec": {"type": "number"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "knee_reaping",
                            "illegal_lock_for_belt",
                            "double_guard_pull",
                            "out_of_bounds_reset",
                            "stalling_20s",
                            "stabilization_borderline",
                            "advantage_vs_pass_borderline",
                            "fleeing_mat",
                            "slam",
                            "other",
                        ],
                    },
                    "ibjjf_rule_ref": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["video_sec", "category", "description"],
            },
        },
    },
    "required": ["summary", "scoring_events", "technique_events", "vision_metadata", "rule_edge_cases"],
}


def _ffmpeg_bin() -> str:
    env_path = f"/opt/homebrew/bin:/usr/local/bin:{os.environ.get('PATH', '')}"
    binary = shutil.which("ffmpeg", path=env_path)
    if not binary:
        raise FileNotFoundError("ffmpeg not found in PATH")
    return binary


def build_storyboard_sheets(
    video_path: Path,
    *,
    duration_sec: float,
    max_sheets: int = 4,
    cols: int = 3,
    rows: int = 3,
    tile_w: int = 384,
    tile_h: int = 216,
) -> list[bytes]:
    """Samples evenly spaced video frames into timestamped JPEG storyboard grids."""
    ffmpeg = _ffmpeg_bin()
    tiles_per_sheet = cols * rows
    total_frames = max(tiles_per_sheet, min(max_sheets * tiles_per_sheet, 36))
    dur = max(10.0, duration_sec)
    step = dur / total_frames

    tmp_dir = video_path.parent / ".frames"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    frames: list[tuple[float, Image.Image]] = []
    try:
        fps = total_frames / dur
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(video_path),
                "-vf",
                f"fps={fps:.5f},scale={tile_w}:{tile_h}",
                "-frames:v",
                str(total_frames),
                str(tmp_dir / "f_%03d.jpg"),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        jpgs = sorted(tmp_dir.glob("f_*.jpg"))
        for idx, jpg in enumerate(jpgs):
            ts = round(idx * step, 1)
            with Image.open(jpg) as im:
                frames.append((ts, im.convert("RGB")))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    sheets: list[bytes] = []
    for start in range(0, len(frames), tiles_per_sheet):
        chunk = frames[start : start + tiles_per_sheet]
        sheet = Image.new("RGB", (cols * tile_w, rows * tile_h), (12, 14, 18))
        draw = ImageDraw.Draw(sheet)
        for i, (ts, tile) in enumerate(chunk):
            r, c = divmod(i, cols)
            x, y = c * tile_w, r * tile_h
            sheet.paste(tile, (x, y))
            draw.rectangle([x + 4, y + 4, x + 78, y + 22], fill=(0, 0, 0))
            draw.text((x + 8, y + 7), f"t={ts:.1f}s", fill=(255, 255, 255))
        buf = io.BytesIO()
        sheet.save(buf, format="JPEG", quality=82)
        sheets.append(buf.getvalue())
    return sheets[:max_sheets]


def compute_ocr_agreement(bundle: MatchAnnotationBundle, ocr_label_json: str | None) -> float | None:
    """Computes simple directional agreement [0.0, 1.0] against local OCR scoreboard ground truth."""
    if not ocr_label_json:
        return None
    try:
        ocr = json.loads(ocr_label_json)
    except json.JSONDecodeError:
        return None

    pts_a_pred = sum(e.points for e in bundle.scoring_events if e.athlete == "A")
    pts_b_pred = sum(e.points for e in bundle.scoring_events if e.athlete == "B")
    ocr_a = int((ocr.get("final_score_a") or {}).get("points", 0))
    ocr_b = int((ocr.get("final_score_b") or {}).get("points", 0))

    denom = max(1, ocr_a + ocr_b, pts_a_pred + pts_b_pred)
    err = abs(pts_a_pred - ocr_a) + abs(pts_b_pred - ocr_b)
    return round(max(0.0, 1.0 - (err / denom)), 3)


def coerce_annotation_bundle(
    raw: dict[str, Any],
    *,
    match: MatchRecord,
    model_id: str,
    ocr_label_json: str | None = None,
) -> MatchAnnotationBundle:
    """Safely coerces LLM JSON output into a validated 4-layer MatchAnnotationBundle."""
    scoring: list[ScoringRuleEvent] = []
    for item in raw.get("scoring_events") or []:
        if isinstance(item, dict):
            try:
                scoring.append(ScoringRuleEvent.model_validate(item))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping malformed scoring event: %s", exc)

    techniques: list[TechniqueEvent] = []
    for item in raw.get("technique_events") or []:
        if isinstance(item, dict):
            try:
                techniques.append(TechniqueEvent.model_validate(item))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping malformed technique event: %s", exc)

    vm_raw = raw.get("vision_metadata") if isinstance(raw.get("vision_metadata"), dict) else {}
    ref_signals: list[RefereeSignalEvent] = []
    for item in vm_raw.get("referee_signals") or []:
        if isinstance(item, dict):
            try:
                ref_signals.append(RefereeSignalEvent.model_validate(item))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping malformed referee signal: %s", exc)

    vision = VisionCalibrationMetadata(
        athlete_a_appearance=str(vm_raw.get("athlete_a_appearance") or ""),
        athlete_b_appearance=str(vm_raw.get("athlete_b_appearance") or ""),
        camera_angle=vm_raw.get("camera_angle") if vm_raw.get("camera_angle") in {
            "broadcast_elevated", "matside_static", "matside_handheld", "overhead", "unknown"
        } else "unknown",
        overlay_preset=match.overlay_preset,
        overlay_verified=bool(match.gcs_masked_uri),
        leakage_verified=bool(match.gcs_masked_uri),
        referee_signals=ref_signals,
    )

    edges: list[RuleEdgeCaseTag] = []
    for item in raw.get("rule_edge_cases") or []:
        if isinstance(item, dict):
            try:
                edges.append(RuleEdgeCaseTag.model_validate(item))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping malformed rule edge case: %s", exc)

    bundle = MatchAnnotationBundle(
        match_id=match.match_id,
        annotator_id=f"gemini_4layer:{model_id}",
        tier=1,
        scoring_events=scoring,
        technique_events=techniques,
        vision_metadata=vision,
        rule_edge_cases=edges,
        summary=str(raw.get("summary") or "").strip(),
    )
    bundle.ocr_agreement_score = compute_ocr_agreement(bundle, ocr_label_json)
    return bundle


def annotate_match_from_gcs(
    match: MatchRecord,
    *,
    store: EncyclopediaStore,
    vault: GCSVault,
    scratch_root: Path,
    settings: Settings | None = None,
    model_id: str | None = None,
) -> MatchAnnotationBundle:
    """Downloads masked (or raw matside) clip from GCS, runs 4-layer Gemini Vision tagging, and stores to SQLite + GCS."""
    cfg = settings or get_settings()
    if not cfg.gemini_api_key.strip():
        raise RuntimeError("GEMINI_API_KEY is required for Tier 1B 4-layer annotation.")
    chosen_model = model_id or cfg.tracker_model

    rel_video = (
        f"clips/{match.match_id}/masked.mp4"
        if match.gcs_masked_uri
        else f"clips/{match.match_id}/raw.mp4"
    )
    work_dir = scratch_root / f"ann_{match.match_id}"
    local_mp4 = work_dir / "input.mp4"
    ann_json = work_dir / "annotation.json"

    try:
        vault.download_file(rel_video, local_mp4)
        sheets = build_storyboard_sheets(local_mp4, duration_sec=match.duration_sec or 300.0)
        input_items: list[dict[str, Any]] = [
            {
                "type": "image",
                "data": base64.b64encode(sheet).decode("ascii"),
                "mime_type": "image/jpeg",
            }
            for sheet in sheets
        ]
        prompt = (
            "You are an expert IBJJF black-belt referee and film-study analyst.\n"
            f"Match: {match.athlete_a} (Athlete A) vs {match.athlete_b} (Athlete B).\n"
            f"Event: {match.event_name} ({match.event_year or 'unknown'}) | Belt: {match.belt} | Uniform: {match.uniform}.\n"
            "Inspect these chronological timestamped storyboard frames (t=...s) and emit all 4 structured layers:\n"
            "1. scoring_events: IBJJF points (2/3/4), advantages, penalties, stalling, submissions, or DQ.\n"
            "2. technique_events: positional states and transitions (guard_pull, guard_state, takedown, sweep, pass, mount, back_take, escape, submission_attempt/finish) with exact guard_family (e.g., de_la_riva, lasso, closed_guard, half_guard, x_guard, butterfly).\n"
            "3. vision_metadata: Athlete A and B gi/rashguard appearance, camera_angle, and visible referee hand signals.\n"
            "4. rule_edge_cases: any double_guard_pull, out_of_bounds_reset, stalling_20s, knee_reaping, stabilization_borderline, or advantage_vs_pass_borderline.\n"
            "Be precise and only report events visibly supported by the frames."
        )
        input_items.append({"type": "text", "text": prompt})

        client = genai.Client(api_key=cfg.gemini_api_key.strip())
        interaction = client.interactions.create(
            model=chosen_model,
            input=input_items,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": ANNOTATION_JSON_SCHEMA,
            },
            generation_config={"thinking_level": "low"},
        )
        raw_text = extract_interaction_text(interaction)
        parsed = parse_json_lenient(raw_text)
        ocr_json = store.get_ocr_label_json(match.match_id)
        bundle = coerce_annotation_bundle(parsed, match=match, model_id=chosen_model, ocr_label_json=ocr_json)

        ann_json.write_text(bundle.model_dump_json(indent=2) + "\n", encoding="utf-8")
        uri, size_b, sha = vault.upload_file(ann_json, f"annotations/{match.match_id}.json")
        store.record_gcs_asset(match.match_id, "annotation_json", uri, size_b, sha)
        store.upsert_annotation(bundle, gcs_uri=uri)
        return bundle
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
