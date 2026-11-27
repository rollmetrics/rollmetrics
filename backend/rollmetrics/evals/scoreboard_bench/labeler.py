"""Independent Scoreboard-Crop Ground-Truth Labeler (never observes mat or athlete pixels)."""

from __future__ import annotations

import base64
import io
import json
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from rollmetrics.evals.scoreboard_bench.masking import _find_binary, crop_scoreboard_only
from rollmetrics.evals.scoreboard_bench.metrics import aggregate_split_metrics, score_match_prediction
from rollmetrics.evals.scoreboard_bench.schema import (
    CompactScore,
    GroundTruthMatchLabel,
    MatchManifestEntry,
    MatchPredictionRecord,
    PredictedScoreEvent,
    ScoreDeltaEvent,
)
from rollmetrics.providers.base import parse_json_lenient

_SWIFT_VISION_OCR_SRC = """
import Foundation
import Vision
import AppKit

let args = Array(CommandLine.arguments.dropFirst())
var results: [[String: Any]] = []

for path in args {
    guard let nsImg = NSImage(contentsOfFile: path),
          let cg = nsImg.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        results.append(["path": path, "tokens": []])
        continue
    }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false
    req.minimumTextHeight = 0.03
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    var toks: [[String: Any]] = []
    for obs in req.results ?? [] {
        if let top = obs.topCandidates(1).first {
            let b = obs.boundingBox
            toks.append([
                "text": top.string,
                "conf": top.confidence,
                "x": b.origin.x,
                "y": b.origin.y,
                "w": b.width,
                "h": b.height
            ])
        }
    }
    results.append(["path": path, "tokens": toks])
}

if let data = try? JSONSerialization.data(withJSONObject: results, options: []),
   let str = String(data: data, encoding: .utf8) {
    print(str)
}
"""

_CACHED_VISION_BIN: Path | None = None


def _get_macos_vision_ocr_bin() -> Path | None:
    """Compiles (once) and caches a native macOS Apple Vision OCR helper binary."""
    global _CACHED_VISION_BIN
    if _CACHED_VISION_BIN and _CACHED_VISION_BIN.exists():
        return _CACHED_VISION_BIN
    if platform.system() != "Darwin":
        return None
    swiftc = _find_binary("swiftc")
    if not swiftc:
        return None
    cache_dir = Path(tempfile.gettempdir()) / "rollmetrics_vision_ocr"
    cache_dir.mkdir(parents=True, exist_ok=True)
    bin_path = cache_dir / "vision_ocr_v1"
    if not bin_path.exists():
        src_path = cache_dir / "vision_ocr_v1.swift"
        src_path.write_text(_SWIFT_VISION_OCR_SRC, encoding="utf-8")
        subprocess.run([swiftc, "-O", str(src_path), "-o", str(bin_path)], check=True, capture_output=True)
    _CACHED_VISION_BIN = bin_path
    return bin_path


SCOREBOARD_OCR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scoreboard_first_seen_sec": {"type": "number"},
        "scoreboard_last_seen_sec": {"type": "number"},
        "left_athlete_bar_color": {"type": "string"},
        "right_athlete_bar_color": {"type": "string"},
        "final_clock_str": {"type": "string"},
        "winner_side": {"type": "string", "enum": ["A", "B", "tie"]},
        "ended_before_time_expired": {"type": "boolean"},
        "readings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rel_sec": {"type": "number"},
                    "clock_str": {"type": "string"},
                    "pts_a": {"type": "integer"},
                    "adv_a": {"type": "integer"},
                    "pen_a": {"type": "integer"},
                    "pts_b": {"type": "integer"},
                    "adv_b": {"type": "integer"},
                    "pen_b": {"type": "integer"},
                },
                "required": [
                    "rel_sec",
                    "clock_str",
                    "pts_a",
                    "adv_a",
                    "pen_a",
                    "pts_b",
                    "adv_b",
                    "pen_b",
                ],
            },
        },
    },
    "required": [
        "scoreboard_first_seen_sec",
        "scoreboard_last_seen_sec",
        "final_clock_str",
        "winner_side",
        "ended_before_time_expired",
        "readings",
    ],
}


def _clock_to_seconds(clock_str: str) -> int | None:
    clean = (clock_str or "").strip().replace(".", ":")
    if ":" not in clean:
        return None
    parts = clean.split(":")
    if len(parts) != 2:
        return None
    try:
        mm, ss = int(parts[0]), int(parts[1])
        if 0 <= mm <= 20 and 0 <= ss < 60:
            return mm * 60 + ss
        return None
    except ValueError:
        return None


def _detect_flograppling_cap_colors(sb: Image.Image, uniform: str) -> tuple[str, str]:
    """Reads the left/right FloGrappling scoreboard end-cap color pills inside scoreboard crop."""
    rgb = sb.convert("RGB")
    w, h = rgb.size
    y0, y1 = max(0, round(0.55 * h)), min(h, round(0.83 * h))

    def _classify(x0_norm: float, x1_norm: float) -> str:
        x0, x1 = max(0, round(x0_norm * w)), min(w, max(1, round(x1_norm * w)))
        n = max(1, (x1 - x0) * max(1, y1 - y0))
        b_minus_r = sum(rgb.getpixel((x, y))[2] - rgb.getpixel((x, y))[0] for y in range(y0, y1) for x in range(x0, x1)) / n
        luma = sum(sum(rgb.getpixel((x, y))) / 3.0 for y in range(y0, y1) for x in range(x0, x1)) / n
        if uniform == "gi":
            if b_minus_r > 24.0:
                return "Blue Gi"
            if luma > 125.0:
                return "White Gi"
            return "Black Gi"
        if b_minus_r > 24.0:
            return "Blue/Dark Rashguard (Blue Side)"
        return "White/Dark Rashguard (White Side)"

    return _classify(0.049, 0.065), _classify(0.935, 0.951)


def _crop_digits_strip(im: Image.Image, preset_name: str) -> Image.Image:
    """Crops ONLY the numeric digits sub-strip inside the scoreboard crop and upscales 4x."""
    rgb = im.convert("RGB")
    w, h = rgb.size
    if preset_name == "smoothcomp_lower_bar":
        sub = rgb.crop((round(0.340 * w), round(0.865 * h), round(0.665 * w), round(0.925 * h)))
    else:
        sub = rgb.crop((round(0.375 * w), round(0.892 * h), round(0.625 * w), round(0.952 * h)))
    up = sub.resize((900, 160), Image.Resampling.BICUBIC)
    return ImageOps.expand(up, border=24, fill=(12, 16, 22))


def _parse_ocr_tokens_to_reading(
    tokens: list[dict[str, Any]],
    preset_name: str,
) -> dict[str, Any] | None:
    """Maps spatial OCR tokens on a scoreboard digit strip to (clock_str, pts/adv/pen A & B)."""
    clock_str = ""
    for tok in tokens:
        txt = str(tok.get("text", "")).strip()
        x = float(tok.get("x", 0.0))
        if preset_name == "smoothcomp_lower_bar":
            if 0.44 <= x <= 0.74 and _clock_to_seconds(txt) is not None:
                clock_str = txt
                break
        elif 0.35 <= x <= 0.60 and _clock_to_seconds(txt) is not None:
            clock_str = txt
            break

    if not clock_str:
        return None

    vals = {"pts_a": 0, "adv_a": 0, "pen_a": 0, "pts_b": 0, "adv_b": 0, "pen_b": 0}
    for tok in tokens:
        txt = str(tok.get("text", "")).strip()
        if not txt.isdigit():
            continue
        num = int(txt)
        if num > 36:
            continue
        x = float(tok.get("x", 0.0))
        y = float(tok.get("y", 0.0))
        th = float(tok.get("h", 0.25))

        if preset_name == "smoothcomp_lower_bar":
            if x < 0.40:
                if th >= 0.27:
                    vals["pts_a"] = num
                elif y >= 0.40:
                    vals["adv_a"] = num
                else:
                    vals["pen_a"] = min(num, 4)
            elif x >= 0.68:
                if th >= 0.27:
                    vals["pts_b"] = num
                elif y >= 0.40:
                    vals["adv_b"] = num
                else:
                    vals["pen_b"] = min(num, 4)
        else:
            if 0.04 <= x < 0.13 and y < 0.58:
                vals["adv_a"] = num
            elif 0.13 <= x < 0.22 and y < 0.58:
                vals["pen_a"] = min(num, 4)
            elif 0.22 <= x < 0.36:
                vals["pts_a"] = num
            elif 0.60 <= x < 0.74:
                vals["pts_b"] = num
            elif 0.74 <= x < 0.84 and y < 0.58:
                vals["adv_b"] = num
            elif 0.84 <= x <= 0.96 and y < 0.58:
                vals["pen_b"] = min(num, 4)

    return {"clock_str": clock_str, **vals}


def label_match_from_scoreboard_crops_local_ocr(
    entry: MatchManifestEntry,
    video_path: Path,
    sample_every_sec: float = 2.0,
    winner_hint_if_early_stoppage: str = "",
) -> GroundTruthMatchLabel:
    """Labels a match deterministically using native Vision OCR restricted strictly to scoreboard crops."""
    ffmpeg = _find_binary("ffmpeg")
    ocr_bin = _get_macos_vision_ocr_bin()
    if not ffmpeg or not ocr_bin:
        raise RuntimeError("Local Vision OCR requires macOS swiftc Vision binary and ffmpeg.")

    duration = max(4.0, entry.end_sec - entry.start_sec)
    fps_expr = f"1/{max(0.5, sample_every_sec):.2f}"

    raw_rows: list[dict[str, Any]] = []
    winner_banner_side = ""
    detected_app_a = entry.athlete_a_appearance
    detected_app_b = entry.athlete_b_appearance

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{max(0.0, entry.start_sec):.2f}",
                "-t",
                f"{duration:.2f}",
                "-i",
                str(video_path),
                "-vf",
                f"fps={fps_expr}",
                "-q:v",
                "3",
                str(tmp_path / "raw_%04d.jpg"),
            ],
            check=True,
            capture_output=True,
        )
        raw_files = sorted(tmp_path.glob("raw_*.jpg"))
        strip_paths: list[Path] = []
        rel_secs: list[float] = []
        full_sb_paths: list[Path] = []
        mid_idx = len(raw_files) // 2

        for idx, rf in enumerate(raw_files):
            rel_s = round(idx * sample_every_sec, 1)
            with Image.open(rf) as im:
                d_strip = _crop_digits_strip(im, entry.overlay_preset)
                sp = tmp_path / f"d_{idx:04d}.png"
                d_strip.save(sp)
                strip_paths.append(sp)
                rel_secs.append(rel_s)

                if idx == mid_idx and entry.overlay_preset == "flograppling_lower_bar":
                    mid_sb = crop_scoreboard_only(im, preset_name=entry.overlay_preset)
                    detected_app_a, detected_app_b = _detect_flograppling_cap_colors(mid_sb, entry.uniform)

                # Save full scoreboard crop on the final 15 frames to detect post-bout WINNER banner
                if idx >= max(0, len(raw_files) - 18):
                    full_sb = crop_scoreboard_only(im, preset_name=entry.overlay_preset)
                    fp = tmp_path / f"sb_{idx:04d}.png"
                    full_sb.save(fp)
                    full_sb_paths.append(fp)

        # Batch invoke Vision OCR in chunks of 64 images
        all_ocr: list[dict[str, Any]] = []
        batch_sz = 64
        for start_i in range(0, len(strip_paths), batch_sz):
            chunk = [str(p) for p in strip_paths[start_i : start_i + batch_sz]]
            out_str = subprocess.check_output([str(ocr_bin), *chunk], text=True)
            all_ocr.extend(json.loads(out_str))

        for rel_s, ocr_item in zip(rel_secs, all_ocr, strict=True):
            parsed_row = _parse_ocr_tokens_to_reading(ocr_item.get("tokens", []), entry.overlay_preset)
            if parsed_row is not None:
                raw_rows.append({"rel_sec": rel_s, **parsed_row})

        # Check post-bout scoreboard crops for WINNER badge or highlight text
        if full_sb_paths:
            sb_out = json.loads(
                subprocess.check_output([str(ocr_bin), *[str(p) for p in full_sb_paths]], text=True)
            )
            for item in sb_out:
                for tok in item.get("tokens", []):
                    t_up = str(tok.get("text", "")).upper()
                    if "WIN" in t_up or "VENC" in t_up:
                        winner_banner_side = "A" if float(tok.get("x", 0.5)) < 0.5 else "B"

    if not raw_rows:
        return GroundTruthMatchLabel(
            match_id=entry.match_id,
            video_id=entry.video_id,
            active_bout_start_sec=entry.start_sec,
            active_bout_end_sec=entry.end_sec,
            athlete_a_appearance=entry.athlete_a_appearance,
            athlete_b_appearance=entry.athlete_b_appearance,
            winner=(winner_hint_if_early_stoppage if winner_hint_if_early_stoppage in ("A", "B") else "referee_decision"),  # type: ignore[arg-type]
            method="submission" if winner_hint_if_early_stoppage in ("A", "B") else "referee_decision",
            labeler_id="scoreboard_crop_vision_ocr_v1",
        )

    # Require 2-frame persistence before accepting an upward counter jump to reject 1-frame blur
    filtered_readings: list[dict[str, Any]] = [raw_rows[0]]
    cur = {k: int(raw_rows[0][k]) for k in ("pts_a", "adv_a", "pen_a", "pts_b", "adv_b", "pen_b")}

    for i in range(1, len(raw_rows)):
        row = raw_rows[i]
        nxt_row = raw_rows[i + 1] if i + 1 < len(raw_rows) else row
        confirmed = dict(cur)
        changed = False
        for k in ("pts_a", "adv_a", "pen_a", "pts_b", "adv_b", "pen_b"):
            v_now = int(row[k])
            v_nxt = int(nxt_row[k])
            # Accept valid IBJJF single-step delta (+1..+6) confirmed across two consecutive readings
            if v_now > cur[k] and v_nxt >= v_now and (v_now - cur[k]) <= 6:
                confirmed[k] = v_now
                changed = True
        if changed:
            cur = confirmed
            filtered_readings.append({"rel_sec": row["rel_sec"], "clock_str": row["clock_str"], **cur})

    last_clk = str(raw_rows[-1]["clock_str"])
    last_clk_sec = _clock_to_seconds(last_clk)
    ended_early = last_clk_sec is not None and last_clk_sec >= 12

    win_side = winner_banner_side or winner_hint_if_early_stoppage or "tie"
    payload = {
        "scoreboard_first_seen_sec": raw_rows[0]["rel_sec"],
        "scoreboard_last_seen_sec": raw_rows[-1]["rel_sec"],
        "left_athlete_bar_color": entry.athlete_a_appearance or detected_app_a,
        "right_athlete_bar_color": entry.athlete_b_appearance or detected_app_b,
        "final_clock_str": last_clk,
        "winner_side": win_side if win_side in ("A", "B") else "tie",
        "ended_before_time_expired": ended_early,
        "readings": filtered_readings,
    }
    lbl = derive_events_from_readings(entry, payload)
    lbl.labeler_id = "scoreboard_crop_vision_ocr_v1"
    return lbl


def extract_scoreboard_strip_sheets(
    video_path: Path,
    start_sec: float,
    end_sec: float,
    sample_every_sec: float = 4.0,
    preset_name: str = "flograppling_lower_bar",
    strips_per_sheet: int = 26,
    work_dir: Path | None = None,
) -> list[bytes]:
    """Extracts ONLY cropped scoreboard strips and stacks them into timestamped JPEG sheets."""
    ffmpeg = _find_binary("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg binary is required for scoreboard strip extraction.")

    duration = max(2.0, end_sec - start_sec)
    fps_expr = f"1/{max(1.0, sample_every_sec):.2f}"

    with tempfile.TemporaryDirectory(dir=str(work_dir) if work_dir else None) as tmp_dir:
        tmp_path = Path(tmp_dir)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{max(0.0, start_sec):.2f}",
                "-t",
                f"{duration:.2f}",
                "-i",
                str(video_path),
                "-vf",
                f"fps={fps_expr}",
                "-q:v",
                "3",
                str(tmp_path / "f_%04d.jpg"),
            ],
            check=True,
            capture_output=True,
        )
        frame_files = sorted(tmp_path.glob("f_*.jpg"))
        strips: list[tuple[float, Image.Image]] = []
        for idx, fpath in enumerate(frame_files):
            rel_sec = round(idx * sample_every_sec, 1)
            with Image.open(fpath) as im:
                crop = crop_scoreboard_only(im, preset_name=preset_name)
                resized = crop.resize((620, 56))
                strips.append((rel_sec, resized))

    sheets_jpeg: list[bytes] = []
    gutter_w = 88
    row_h = 58
    for chunk_start in range(0, len(strips), strips_per_sheet):
        chunk = strips[chunk_start : chunk_start + strips_per_sheet]
        canvas = Image.new("RGB", (gutter_w + 620, row_h * len(chunk)), (8, 11, 16))
        draw = ImageDraw.Draw(canvas)
        for r_idx, (rel_sec, strip_im) in enumerate(chunk):
            y0 = r_idx * row_h
            draw.text((8, y0 + 20), f"t={rel_sec:05.1f}s", fill=(16, 185, 129))
            canvas.paste(strip_im, (gutter_w, y0))
        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=85)
        sheets_jpeg.append(buf.getvalue())
    return sheets_jpeg


def derive_events_from_readings(
    entry: MatchManifestEntry,
    parsed: dict[str, Any],
) -> GroundTruthMatchLabel:
    """Converts raw OCR scoreboard readings into monotonic IBJJF ScoreDeltaEvents + match outcome."""
    raw_readings = sorted(
        parsed.get("readings") or [],
        key=lambda item: float(item.get("rel_sec", 0.0)),
    )
    first_rel = max(0.0, float(parsed.get("scoreboard_first_seen_sec", 0.0) or 0.0))
    last_rel = max(
        first_rel + 5.0,
        float(parsed.get("scoreboard_last_seen_sec", entry.end_sec - entry.start_sec) or 0.0),
    )
    active_start = round(entry.start_sec + first_rel, 1)
    active_end = round(min(entry.end_sec, entry.start_sec + last_rel), 1)
    if active_end <= active_start + 5.0:
        active_start = entry.start_sec
        active_end = entry.end_sec

    cur = {"pts_a": 0, "adv_a": 0, "pen_a": 0, "pts_b": 0, "adv_b": 0, "pen_b": 0}
    events: list[ScoreDeltaEvent] = []

    for r in raw_readings:
        rel_s = max(0.0, float(r.get("rel_sec", 0.0)))
        nxt = {
            "pts_a": max(cur["pts_a"], int(r.get("pts_a", 0) or 0)),
            "adv_a": max(cur["adv_a"], int(r.get("adv_a", 0) or 0)),
            "pen_a": max(cur["pen_a"], int(r.get("pen_a", 0) or 0)),
            "pts_b": max(cur["pts_b"], int(r.get("pts_b", 0) or 0)),
            "adv_b": max(cur["adv_b"], int(r.get("adv_b", 0) or 0)),
            "pen_b": max(cur["pen_b"], int(r.get("pen_b", 0) or 0)),
        }
        clk = str(r.get("clock_str", "") or "")
        vid_sec = round(entry.start_sec + rel_s, 1)
        elapsed_in_bout = round(max(0.0, vid_sec - active_start), 1)

        da_pts = nxt["pts_a"] - cur["pts_a"]
        da_adv = nxt["adv_a"] - cur["adv_a"]
        da_pen = nxt["pen_a"] - cur["pen_a"]
        if da_pts > 0 or da_adv > 0 or da_pen > 0:
            cur["pts_a"], cur["adv_a"], cur["pen_a"] = nxt["pts_a"], nxt["adv_a"], nxt["pen_a"]
            events.append(
                ScoreDeltaEvent(
                    match_elapsed_sec=elapsed_in_bout,
                    video_sec=vid_sec,
                    match_clock_str=clk,
                    athlete="A",
                    pts_delta=da_pts,
                    adv_delta=da_adv,
                    pen_delta=da_pen,
                    score_after=dict(cur),
                )
            )

        db_pts = nxt["pts_b"] - cur["pts_b"]
        db_adv = nxt["adv_b"] - cur["adv_b"]
        db_pen = nxt["pen_b"] - cur["pen_b"]
        if db_pts > 0 or db_adv > 0 or db_pen > 0:
            cur["pts_b"], cur["adv_b"], cur["pen_b"] = nxt["pts_b"], nxt["adv_b"], nxt["pen_b"]
            events.append(
                ScoreDeltaEvent(
                    match_elapsed_sec=elapsed_in_bout,
                    video_sec=vid_sec,
                    match_clock_str=clk,
                    athlete="B",
                    pts_delta=db_pts,
                    adv_delta=db_adv,
                    pen_delta=db_pen,
                    score_after=dict(cur),
                )
            )

    final_a = CompactScore(points=cur["pts_a"], advantages=cur["adv_a"], penalties=cur["pen_a"])
    final_b = CompactScore(points=cur["pts_b"], advantages=cur["adv_b"], penalties=cur["pen_b"])

    final_clk_sec = _clock_to_seconds(str(parsed.get("final_clock_str", "")))
    ended_early = bool(parsed.get("ended_before_time_expired", False)) or (
        final_clk_sec is not None and final_clk_sec >= 12
    )
    raw_winner_side = str(parsed.get("winner_side", "tie") or "tie").strip().upper()

    if final_a.penalties >= 4 or final_b.penalties >= 4:
        winner = "B" if final_a.penalties >= 4 else "A"
        method = "dq"
    elif ended_early and raw_winner_side in ("A", "B"):
        winner = raw_winner_side  # type: ignore[assignment]
        method = "submission"
    elif final_a.points != final_b.points:
        winner = "A" if final_a.points > final_b.points else "B"
        method = "points"
    elif final_a.advantages != final_b.advantages:
        winner = "A" if final_a.advantages > final_b.advantages else "B"
        method = "advantages"
    elif final_a.penalties != final_b.penalties:
        winner = "A" if final_a.penalties < final_b.penalties else "B"
        method = "penalties"
    elif raw_winner_side in ("A", "B"):
        winner = raw_winner_side  # type: ignore[assignment]
        method = "submission" if ended_early else "referee_decision"
    else:
        winner = "referee_decision"
        method = "referee_decision"

    left_color = str(parsed.get("left_athlete_bar_color", "") or "").strip()
    right_color = str(parsed.get("right_athlete_bar_color", "") or "").strip()
    app_a = (
        f"{left_color} Gi"
        if left_color and "gi" not in left_color.lower() and entry.uniform == "gi"
        else (left_color or entry.athlete_a_appearance)
    )
    app_b = (
        f"{right_color} Gi"
        if right_color and "gi" not in right_color.lower() and entry.uniform == "gi"
        else (right_color or entry.athlete_b_appearance)
    )

    return GroundTruthMatchLabel(
        match_id=entry.match_id,
        video_id=entry.video_id,
        active_bout_start_sec=active_start,
        active_bout_end_sec=active_end,
        athlete_a_appearance=app_a,
        athlete_b_appearance=app_b,
        events=events,
        final_score_a=final_a,
        final_score_b=final_b,
        winner=winner,  # type: ignore[arg-type]
        method=method,  # type: ignore[arg-type]
        labeler_id="scoreboard_crop_ocr_v1",
        human_spot_checked=False,
    )


def label_match_from_scoreboard_crops(
    entry: MatchManifestEntry,
    video_path: Path,
    genai_client: Any | None = None,
    ocr_model_id: str = "gemma-4-26b-a4b-it",
    sample_every_sec: float = 2.0,
    prefer_local_ocr: bool = True,
    winner_hint_if_early_stoppage: str = "",
) -> GroundTruthMatchLabel:
    """Labels a match using native scoreboard-crop Vision OCR (default) or Gemini crop OCR."""
    if prefer_local_ocr and _get_macos_vision_ocr_bin() is not None:
        return label_match_from_scoreboard_crops_local_ocr(
            entry=entry,
            video_path=video_path,
            sample_every_sec=sample_every_sec,
            winner_hint_if_early_stoppage=winner_hint_if_early_stoppage,
        )

    if genai_client is None:
        raise RuntimeError("genai_client is required when local Vision OCR is unavailable.")

    sheets = extract_scoreboard_strip_sheets(
        video_path=video_path,
        start_sec=entry.start_sec,
        end_sec=entry.end_sec,
        sample_every_sec=sample_every_sec,
        preset_name=entry.overlay_preset,
    )
    input_items: list[dict[str, Any]] = [
        {
            "type": "image",
            "data": base64.b64encode(sheet).decode("ascii"),
            "mime_type": "image/jpeg",
        }
        for sheet in sheets[:6]
    ]
    prompt = (
        "Read ONLY these stacked IBJJF broadcast scoreboard strips chronologically (left timestamp `t=...s` "
        f"is relative to clip start {entry.start_sec:.1f}s).\n"
        f"Left side of scoreboard = Athlete A ({entry.athlete_a}). Right side = Athlete B ({entry.athlete_b}).\n"
        "Extract scoreboard_first_seen_sec, scoreboard_last_seen_sec, left/right bar colors, every PTS/ADV/PEN "
        "change in readings, final_clock_str, ended_before_time_expired, and winner_side."
    )
    input_items.append({"type": "text", "text": prompt})

    kwargs: dict[str, Any] = {
        "model": ocr_model_id,
        "input": input_items,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": SCOREBOARD_OCR_SCHEMA,
        },
    }
    if "gemma" not in ocr_model_id.lower():
        kwargs["generation_config"] = {"thinking_level": "low"}

    interaction = genai_client.interactions.create(**kwargs)
    raw_text = str(getattr(interaction, "output_text", "") or getattr(interaction, "text", "") or "")
    parsed = parse_json_lenient(raw_text)
    label = derive_events_from_readings(entry, parsed)
    label.labeler_id = f"scoreboard_crop_ocr_v1:{ocr_model_id}"
    return label


def compute_spot_check_report(
    auto_labels: list[GroundTruthMatchLabel],
    human_verified: list[GroundTruthMatchLabel],
) -> dict[str, Any]:
    """Compares automated scoreboard-crop OCR labels against hand-audited ground truth."""
    by_id = {lbl.match_id: lbl for lbl in auto_labels}
    evals = []
    for hv in human_verified:
        auto = by_id.get(hv.match_id)
        if auto is None:
            continue
        as_pred = MatchPredictionRecord(
            match_id=auto.match_id,
            model_id=auto.labeler_id,
            events=[
                PredictedScoreEvent(
                    match_elapsed_sec=e.match_elapsed_sec,
                    athlete=e.athlete,
                    pts_delta=e.pts_delta,
                    adv_delta=e.adv_delta,
                    pen_delta=e.pen_delta,
                )
                for e in auto.events
            ],
            final_score_a=auto.final_score_a,
            final_score_b=auto.final_score_b,
            winner=auto.winner,
            method=auto.method,
        )
        evals.append(score_match_prediction(hv, as_pred, event_tolerance_sec=5.0))

    agg = aggregate_split_metrics("scoreboard_crop_vision_ocr_v1", "pilot", evals, event_tolerance_sec=5.0)
    return {
        "spot_checked_matches": len(evals),
        "labeler_winner_accuracy": agg.winner_accuracy,
        "labeler_final_score_exact_match": agg.final_score_exact_match,
        "labeler_mae_points": agg.mae_points,
        "labeler_mae_advantages": agg.mae_advantages,
        "labeler_mae_penalties": agg.mae_penalties,
        "labeler_score_event_f1": agg.score_event_f1,
        "per_match": evals,
    }
