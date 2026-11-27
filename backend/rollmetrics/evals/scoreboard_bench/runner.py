"""End-to-end runner for the Scoreboard-Grounded Video Scoring Benchmark (rollmetrics-scoreboard-v1)."""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw

from rollmetrics.config import Settings, get_settings
from rollmetrics.evals.pricing import get_model_pricing
from rollmetrics.evals.scoreboard_bench.masking import (
    _find_binary,
    assert_frame_leakage_free,
    extract_masked_anchor_frame,
    mask_frame_scoreboard,
    render_masked_video,
)
from rollmetrics.evals.scoreboard_bench.metrics import (
    aggregate_split_metrics,
    score_match_prediction,
)
from rollmetrics.evals.scoreboard_bench.schema import (
    CompactScore,
    GroundTruthMatchLabel,
    MatchManifestEntry,
    MatchPredictionRecord,
    PredictedScoreEvent,
)
from rollmetrics.evals.scoreboard_bench.splits import (
    build_deterministic_splits,
    enforce_split_discipline,
    write_splits_manifest,
)
from rollmetrics.knowledge.indexer import sha256_file
from rollmetrics.providers.base import VIDEO_ANALYSIS_JSON_SCHEMA, coerce_raw_event, parse_json_lenient
from rollmetrics.rules.schema import (
    AgeDivision,
    BeltRank,
    MatchDivisionConfig,
    RawObservedEvent,
    UniformType,
)
from rollmetrics.rules.state_machine import IBJJFRefereeEngine

DEFAULT_SCOREBOARD_MODELS: tuple[str, ...] = (
    "gemma-4-26b-a4b-it",
    "gemini-4-hydrogen",
    "ollama/gemma4:e2b-it-q4_K_M",
)


def load_matches_jsonl(path: Path) -> list[MatchManifestEntry]:
    if not path.exists():
        return []
    return [
        MatchManifestEntry.model_validate_json(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def write_matches_jsonl(path: Path, entries: list[MatchManifestEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e.model_dump(mode="json"), sort_keys=True) for e in entries]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_labels_jsonl(path: Path) -> dict[str, GroundTruthMatchLabel]:
    if not path.exists():
        return {}
    out: dict[str, GroundTruthMatchLabel] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            lbl = GroundTruthMatchLabel.model_validate_json(ln)
            out[lbl.match_id] = lbl
    return out


def write_labels_jsonl(path: Path, labels: list[GroundTruthMatchLabel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(lbl.model_dump(mode="json"), sort_keys=True) for lbl in labels]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build_masked_storyboard_sheets(
    video_path: Path,
    bout_start_sec: float,
    bout_end_sec: float,
    preset_name: str = "flograppling_lower_bar",
    num_frames: int = 24,
) -> tuple[list[bytes], int]:
    """Extracts bout keyframes, masks every scoreboard/name pixel, verifies zero leakage, and tiles sheets."""
    ffmpeg = _find_binary("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg binary is required to build masked storyboard sheets.")

    import tempfile

    duration = max(4.0, bout_end_sec - bout_start_sec)
    step = duration / max(1, num_frames)
    fps_expr = f"1/{max(0.5, step):.3f}"

    verified_frames: list[tuple[float, Image.Image]] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{max(0.0, bout_start_sec):.2f}",
                "-t",
                f"{duration:.2f}",
                "-i",
                str(video_path),
                "-vf",
                f"fps={fps_expr},scale=480:-2",
                "-frames:v",
                str(num_frames),
                "-q:v",
                "3",
                str(tmp_path / "m_%04d.jpg"),
            ],
            check=True,
            capture_output=True,
        )
        for idx, fpath in enumerate(sorted(tmp_path.glob("m_*.jpg"))[:num_frames]):
            elapsed = round(idx * step, 1)
            with Image.open(fpath) as raw_im:
                masked = mask_frame_scoreboard(raw_im, preset_name=preset_name)
                # Enforce automated zero-scoreboard-pixel leakage gate BEFORE tiling!
                assert_frame_leakage_free(masked, preset_name=preset_name, allow_codec_luma_floor=0.0)
                tile = masked.resize((420, 236))
                draw_t = ImageDraw.Draw(tile)
                draw_t.rectangle((6, 6, 118, 28), fill=(9, 12, 16))
                draw_t.text((12, 10), f"t=+{elapsed:05.1f}s", fill=(248, 250, 252))
                verified_frames.append((elapsed, tile))

    # Pack into 3x4 (12-frame) storyboard sheets so models see full bout progression cleanly
    sheets: list[bytes] = []
    cols, rows = 3, 4
    per_sheet = cols * rows
    for start_idx in range(0, len(verified_frames), per_sheet):
        chunk = verified_frames[start_idx : start_idx + per_sheet]
        used_rows = max(1, (len(chunk) + cols - 1) // cols)
        sheet = Image.new("RGB", (cols * 420, used_rows * 236), (9, 12, 16))
        for k, (_ts, tile) in enumerate(chunk):
            r, c = divmod(k, cols)
            sheet.paste(tile, (c * 420, r * 236))
        buf = io.BytesIO()
        sheet.save(buf, format="JPEG", quality=82)
        sheets.append(buf.getvalue())

    return sheets, len(verified_frames)


def adjudicate_raw_events_to_prediction(
    match_id: str,
    model_id: str,
    division: MatchDivisionConfig,
    raw_events: list[RawObservedEvent],
    leakage_verified: bool,
    latency_ms: float,
    input_tokens: int,
    output_tokens: int,
    bout_duration_sec: float,
) -> MatchPredictionRecord:
    """Runs model-emitted RawObservedEvents through IBJJFRefereeEngine and computes USD cost."""
    div_with_dur = division.model_copy(update={"duration_sec": max(30, round(bout_duration_sec))})
    engine = IBJJFRefereeEngine(division=div_with_dur)
    pred_events: list[PredictedScoreEvent] = []

    for ev in sorted(raw_events, key=lambda x: x.timestamp_sec):
        adj = engine.ingest_event(ev)
        other = "B" if adj.actor.value == "A" else "A"
        if adj.awarded_points > 0 or adj.awarded_advantages > 0 or adj.awarded_penalties > 0:
            pred_events.append(
                PredictedScoreEvent(
                    match_elapsed_sec=round(adj.timestamp_sec, 1),
                    athlete=adj.actor.value,  # type: ignore[arg-type]
                    pts_delta=adj.awarded_points,
                    adv_delta=adj.awarded_advantages,
                    pen_delta=adj.awarded_penalties,
                    raw_kind=adj.raw_kind.value,
                )
            )
        if adj.opponent_bonus_points > 0 or adj.opponent_bonus_advantages > 0:
            pred_events.append(
                PredictedScoreEvent(
                    match_elapsed_sec=round(adj.timestamp_sec, 1),
                    athlete=other,  # type: ignore[arg-type]
                    pts_delta=adj.opponent_bonus_points,
                    adv_delta=adj.opponent_bonus_advantages,
                    pen_delta=0,
                    raw_kind="penalty_bonus",
                )
            )

    snap = engine.finalize_regulation()
    pricing = get_model_pricing(model_id)
    cost_usd = (
        (input_tokens / 1_000_000.0) * pricing.input_usd_per_1m_tokens
        + (output_tokens / 1_000_000.0) * pricing.output_usd_per_1m_tokens
    )

    winner_val: Literal["A", "B", "draw", "referee_decision"] = (
        snap.winner if snap.winner in ("A", "B", "draw", "referee_decision") else "referee_decision"  # type: ignore[assignment]
    )
    return MatchPredictionRecord(
        match_id=match_id,
        model_id=pricing.model_id,
        events=pred_events,
        final_score_a=CompactScore(
            points=snap.athlete_a.points,
            advantages=snap.athlete_a.advantages,
            penalties=snap.athlete_a.penalties,
        ),
        final_score_b=CompactScore(
            points=snap.athlete_b.points,
            advantages=snap.athlete_b.advantages,
            penalties=snap.athlete_b.penalties,
        ),
        winner=winner_val,
        method=snap.win_method,
        leakage_verified=leakage_verified,
        latency_ms=round(latency_ms, 1),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost_usd, 6),
    )


def _build_identity_anchor_prompt(div_cfg: MatchDivisionConfig) -> str:
    """Builds explicit A/B starting-side, appearance, and IBJJF referee wristband identity anchors."""
    return (
        "CRITICAL ATHLETE IDENTITY BINDING (names and scoreboard are masked):\n"
        f"- Athlete A: starts on the LEFT side of the mat at t=0s | Appearance: {div_cfg.athlete_a_appearance}.\n"
        f"- Athlete B: starts on the RIGHT side of the mat at t=0s | Appearance: {div_cfg.athlete_b_appearance}.\n"
        "- IBJJF Referee Wristband Mapping: When the center referee raises 2/3/4 fingers or awards an advantage, "
        "points raised with the referee's GREEN/YELLOW wristband (or RED side on Smoothcomp) belong to Athlete A; "
        "points raised with the referee's UNBANDED / OTHER hand (or BLUE side) belong to Athlete B.\n"
        "- Use the provided t=+2s ANCHOR image (with top A/B header badges) to lock both athletes visually before scoring."
    )


def _reconcile_observed_winner(
    rec: MatchPredictionRecord,
    obs_win: str,
    obs_method: str,
) -> MatchPredictionRecord:
    """Reconciles deterministic engine result with final referee hand-raise on ties or low-margin discrepancies."""
    clean_win = (obs_win or "").strip().upper()
    clean_method = (obs_method or "").strip().lower()
    valid_methods = ("points", "advantages", "penalties", "submission", "dq", "referee_decision")
    if clean_win not in ("A", "B"):
        return rec

    pt_margin = abs(rec.final_score_a.points - rec.final_score_b.points)
    if rec.winner == "referee_decision" or rec.winner != clean_win and (clean_method in ("submission", "dq") or pt_margin <= 2):
        rec.winner = clean_win  # type: ignore[assignment]
        if clean_method in valid_methods:
            rec.method = clean_method
    return rec


def score_masked_match_live(
    entry: MatchManifestEntry,
    gt: GroundTruthMatchLabel,
    video_path: Path,
    model_id: str,
    settings: Settings,
    genai_client: Any | None = None,
    cached_sheets: tuple[list[bytes], int] | None = None,
) -> MatchPredictionRecord:
    """Evaluates a single match on strictly scoreboard-masked frames without leaking names or scores."""
    bout_dur = max(10.0, gt.active_bout_end_sec - gt.active_bout_start_sec)
    div_cfg = MatchDivisionConfig(
        uniform=UniformType(entry.uniform),
        belt=BeltRank(entry.belt),
        age_division=AgeDivision(entry.age_division),
        duration_sec=round(bout_dur),
        athlete_a_name="Athlete A",
        athlete_a_appearance=gt.athlete_a_appearance or entry.athlete_a_appearance,
        athlete_b_name="Athlete B",
        athlete_b_appearance=gt.athlete_b_appearance or entry.athlete_b_appearance,
    )

    pricing = get_model_pricing(model_id)
    if pricing.provider == "ollama_local":
        import asyncio

        from rollmetrics.providers.ollama_local import OllamaLocalProvider

        clean_tag = model_id.removeprefix("ollama/")
        masked_frames_verified = 0

        def _strict_frame_mask(jpeg_bytes: bytes) -> bytes:
            nonlocal masked_frames_verified
            with Image.open(io.BytesIO(jpeg_bytes)) as raw_im:
                masked = mask_frame_scoreboard(raw_im, preset_name=entry.overlay_preset)
                assert_frame_leakage_free(masked, preset_name=entry.overlay_preset, allow_codec_luma_floor=0.0)
                buf = io.BytesIO()
                masked.save(buf, format="JPEG", quality=85)
                masked_frames_verified += 1
                return buf.getvalue()

        provider = OllamaLocalProvider(settings=settings, model_override=clean_tag)
        vision_res = asyncio.run(
            provider.analyze_video_agentic(
                video_path=video_path,
                division=div_cfg,
                coarse_frames=12,
                max_windows=4,
                zoom_fps=1.0,
                max_frames_per_window=8,
                frame_mask_fn=_strict_frame_mask,
                start_sec=gt.active_bout_start_sec,
                end_sec=gt.active_bout_end_sec,
            )
        )
        rec = adjudicate_raw_events_to_prediction(
            match_id=entry.match_id,
            model_id=model_id,
            division=div_cfg,
            raw_events=vision_res.events,
            leakage_verified=(masked_frames_verified > 0),
            latency_ms=vision_res.latency_ms,
            input_tokens=vision_res.input_tokens,
            output_tokens=vision_res.output_tokens,
            bout_duration_sec=bout_dur,
        )
        return _reconcile_observed_winner(
            rec,
            str(vision_res.telemetry.get("observed_winner", "") or ""),
            str(vision_res.telemetry.get("observed_win_method", "") or ""),
        )

    sheets, verified_count = cached_sheets or build_masked_storyboard_sheets(
        video_path=video_path,
        bout_start_sec=gt.active_bout_start_sec,
        bout_end_sec=gt.active_bout_end_sec,
        preset_name=entry.overlay_preset,
        num_frames=24,
    )
    anchor_jpeg = extract_masked_anchor_frame(
        video_path=video_path,
        anchor_sec=gt.active_bout_start_sec + min(2.0, bout_dur * 0.1),
        preset_name=entry.overlay_preset,
        athlete_a_appearance=div_cfg.athlete_a_appearance,
        athlete_b_appearance=div_cfg.athlete_b_appearance,
    )

    prompt = (
        "You are an official IBJJF Black Belt Referee scoring a BJJ match from scoreboard-masked video frames.\n"
        f"Division: {entry.age_division.upper()} {entry.belt.upper()} BELT ({entry.uniform.upper()}). "
        f"Active Bout Window: 0.0s to {bout_dur:.1f}s.\n"
        f"{_build_identity_anchor_prompt(div_cfg)}\n"
        "Chronologically detect every real IBJJF point-scoring position (takedown=2, sweep=2, knee_on_belly=2, "
        "guard_pass=3, mount=4, back_control=4; set hold_duration_sec >= 3.0 ONLY if stabilized 3 full seconds "
        "in-bounds, or 1.0..2.5s if incomplete for +1 advantage), explicit near-score advantages, minor_foul "
        "penalties (guard_pull_without_grip, stalling, fleeing_mat_bounds), serious_foul DQs, and tap submissions.\n"
        "Be conservative and accurate: emit ONLY genuine scoring exchanges, fouls, or submissions with exact "
        "`timestamp_sec` matching the `t=+...s` frame stamps.\n"
        "Return JSON matching schema `{summary: string, events: [...]}`."
    )

    if genai_client is None:
        from google import genai
        from google.genai import types

        genai_client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=90000),
        )
    items: list[dict[str, Any]] = []
    if anchor_jpeg is not None:
        items.append(
            {
                "type": "image",
                "data": base64.b64encode(anchor_jpeg).decode("ascii"),
                "mime_type": "image/jpeg",
            }
        )
    items.extend(
        {
            "type": "image",
            "data": base64.b64encode(s).decode("ascii"),
            "mime_type": "image/jpeg",
        }
        for s in sheets
    )
    items.append({"type": "text", "text": prompt})
    kwargs: dict[str, Any] = {
        "model": model_id,
        "input": items,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": VIDEO_ANALYSIS_JSON_SCHEMA,
        },
    }
    if "gemma" not in model_id.lower():
        kwargs["generation_config"] = {"thinking_level": "high" if "neon" in model_id.lower() else "low"}
    t0 = time.perf_counter()
    interaction = genai_client.interactions.create(**kwargs)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    txt = str(getattr(interaction, "output_text", "") or getattr(interaction, "text", "") or "")
    parsed = parse_json_lenient(txt)
    usage = getattr(interaction, "usage", None)
    in_tok = int(getattr(usage, "total_input_tokens", 0) or 0) if usage else 0
    out_tok = (
        int(getattr(usage, "total_output_tokens", 0) or 0)
        + int(getattr(usage, "total_thought_tokens", 0) or 0)
        if usage
        else 0
    )

    raw_evs: list[RawObservedEvent] = []
    for item in parsed.get("events") or []:
        ev = coerce_raw_event(item)
        if ev is not None:
            raw_evs.append(ev)

    return adjudicate_raw_events_to_prediction(
        match_id=entry.match_id,
        model_id=model_id,
        division=div_cfg,
        raw_events=raw_evs,
        leakage_verified=(verified_count > 0),
        latency_ms=latency_ms,
        input_tokens=in_tok,
        output_tokens=out_tok,
        bout_duration_sec=bout_dur,
    )


AGENTIC_VIDEO_SCORING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **VIDEO_ANALYSIS_JSON_SCHEMA["properties"],
        "observed_winner": {"type": "string", "enum": ["A", "B", "referee_decision"]},
        "observed_win_method": {
            "type": "string",
            "enum": ["points", "advantages", "penalties", "submission", "dq", "referee_decision"],
        },
    },
    "required": ["summary", "events", "observed_winner", "observed_win_method"],
}


def score_masked_video_agentic(
    entry: MatchManifestEntry,
    gt: GroundTruthMatchLabel,
    video_path: Path,
    model_id: str,
    settings: Settings,
    genai_client: Any | None = None,
    masked_video_path: Path | None = None,
    thinking_level: str | None = None,
    enable_mat_crop: bool = True,
) -> MatchPredictionRecord:
    """Scores a full scoreboard-masked MP4 clip using Gemini 4 Agentic Video Understanding."""
    import contextlib
    import tempfile

    from rollmetrics.video.tracking import compute_mat_crop_box

    base_model = model_id.removesuffix("-agentic")
    record_model_id = model_id if model_id.endswith("-agentic") else f"{model_id}-agentic"
    chosen_thinking = thinking_level or ("high" if "neon" in base_model.lower() else "medium")

    if genai_client is None:
        from google import genai
        from google.genai import types

        genai_client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=480000),
        )

    bout_dur = max(10.0, gt.active_bout_end_sec - gt.active_bout_start_sec)
    div_cfg = MatchDivisionConfig(
        uniform=UniformType(entry.uniform),
        belt=BeltRank(entry.belt),
        age_division=AgeDivision(entry.age_division),
        duration_sec=round(bout_dur),
        athlete_a_name="Athlete A",
        athlete_a_appearance=gt.athlete_a_appearance or entry.athlete_a_appearance,
        athlete_b_name="Athlete B",
        athlete_b_appearance=gt.athlete_b_appearance or entry.athlete_b_appearance,
    )

    anchor_jpeg = extract_masked_anchor_frame(
        video_path=video_path,
        anchor_sec=gt.active_bout_start_sec + min(2.0, bout_dur * 0.1),
        preset_name=entry.overlay_preset,
        athlete_a_appearance=div_cfg.athlete_a_appearance,
        athlete_b_appearance=div_cfg.athlete_b_appearance,
    )

    prompt = (
        "You are an official IBJJF Black Belt Head Referee watching a full continuous BJJ match video "
        "whose on-screen scoreboard and athlete names have been completely blacked out.\n"
        f"Division: {entry.age_division.upper()} {entry.belt.upper()} BELT ({entry.uniform.upper()}). "
        f"Clip duration: 0.0s to {bout_dur:.1f}s (all timestamps are seconds from the start of this clip).\n"
        f"{_build_identity_anchor_prompt(div_cfg)}\n"
        "Watch the entire continuous match carefully, tracking both athletes by Gi/rashguard appearance AND "
        "observing the center referee's official hand signals throughout the bout:\n"
        "- Referee holds 2 fingers up: +2 pts (takedown, sweep, or knee_on_belly held >= 3.0s)\n"
        "- Referee holds 3 fingers up: +3 pts (guard_pass held >= 3.0s)\n"
        "- Referee holds 4 fingers up: +4 pts (mount or back_control held >= 3.0s)\n"
        "- STRICT 3.0s STABILIZATION RULE: A near-pass or incomplete transition held < 3.0s (1.0..2.9s) is "
        "+1 advantage (NOT 2/3/4 points); only emit hold_duration_sec >= 3.0 when fully stabilized 3 full seconds.\n"
        "- Referee extends arm horizontally / thumbs-up: +1 advantage (advantage, or incomplete hold 1.0..2.5s)\n"
        "- Referee points / signals stalling or technical foul: minor_foul penalty (or double_stalling)\n"
        "- EARLY SUBMISSION CHECK: Even if one athlete is ahead on points, check the end of the bout carefully "
        "for a tap, verbal tap, or referee stoppage; emit `submission` for the attacker whenever a tap occurs!\n"
        "- Referee crosses arms or signals severe foul: serious_foul (DQ)\n"
        "- At the very end of the clip, observe whose hand the referee raises (observed_winner: A or B) "
        "and the finish method (observed_win_method).\n"
        "Be accurate and disciplined: emit ONLY confirmed IBJJF scoring events, advantages, penalties, "
        "or submissions with accurate `timestamp_sec` (seconds from clip start) and `hold_duration_sec`.\n"
        "Return strict JSON matching `{summary, events, observed_winner, observed_win_method}`."
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        if masked_video_path is not None and masked_video_path.exists():
            clip_path = masked_video_path
            leakage_ok = True
        else:
            clip_path = Path(tmp_dir) / f"{entry.match_id}_masked.mp4"
            crop_box = (
                compute_mat_crop_box(
                    video_path=video_path,
                    start_sec=gt.active_bout_start_sec,
                    end_sec=gt.active_bout_end_sec,
                    preset_name=entry.overlay_preset,
                )
                if enable_mat_crop
                else None
            )
            mask_meta = render_masked_video(
                src_video=video_path,
                start_sec=gt.active_bout_start_sec,
                end_sec=gt.active_bout_end_sec,
                dst_video=clip_path,
                preset_name=entry.overlay_preset,
                target_height=480,
                crop_box=crop_box,
            )
            leakage_ok = bool(mask_meta.get("leakage_free", False))

        uploaded = genai_client.files.upload(file=str(clip_path))
        try:
            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline:
                state_str = str(getattr(uploaded, "state", "") or "").upper()
                if state_str.endswith("ACTIVE") or state_str == "ACTIVE":
                    break
                if state_str.endswith("FAILED"):
                    raise RuntimeError(f"Gemini Files API processing failed for {entry.match_id}")
                time.sleep(2.0)
                uploaded = genai_client.files.get(name=uploaded.name)

            def _run_interaction_call(call_prompt: str, call_thinking: str) -> tuple[dict[str, Any], float, int, int]:
                items_list: list[dict[str, Any]] = []
                if anchor_jpeg is not None:
                    items_list.append(
                        {
                            "type": "image",
                            "data": base64.b64encode(anchor_jpeg).decode("ascii"),
                            "mime_type": "image/jpeg",
                        }
                    )
                items_list.append(
                    {
                        "type": "video",
                        "uri": uploaded.uri,
                        "mime_type": "video/mp4",
                        "processing": "agentic",
                    }
                )
                items_list.append({"type": "text", "text": call_prompt})
                t0 = time.perf_counter()
                resp = genai_client.interactions.create(
                    model=base_model,
                    input=items_list,
                    generation_config={"thinking_level": call_thinking},
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": AGENTIC_VIDEO_SCORING_SCHEMA,
                    },
                )
                lat = (time.perf_counter() - t0) * 1000.0
                out_txt = str(getattr(resp, "output_text", "") or getattr(resp, "text", "") or "")
                u = getattr(resp, "usage", None)
                i_tok = int(getattr(u, "total_input_tokens", 0) or 0) if u else 0
                o_tok = (
                    int(getattr(u, "total_output_tokens", 0) or 0)
                    + int(getattr(u, "total_thought_tokens", 0) or 0)
                    if u
                    else 0
                )
                return parse_json_lenient(out_txt), lat, i_tok, o_tok

            parsed, latency_ms, in_tok, out_tok = _run_interaction_call(prompt, chosen_thinking)
            raw_evs = [
                ev
                for item in (parsed.get("events") or [])
                if (ev := coerce_raw_event(item)) is not None
            ]

            # Zero-Event Retry / Smart Escalation on >60s bouts without a verified tap/DQ
            obs_win_first = str(parsed.get("observed_winner", "") or "").strip().upper()
            obs_meth_first = str(parsed.get("observed_win_method", "") or "").strip().lower()
            if (
                len(raw_evs) == 0
                and bout_dur > 60.0
                and obs_meth_first not in ("submission", "dq")
            ):
                retry_prompt = (
                    f"{prompt}\n\n"
                    "VERIFICATION RETRY: Your first pass emitted 0 scoring events on a >60s bout. "
                    "Re-inspect every exchange carefully for takedowns, sweeps, guard passes (distinguish "
                    "near-pass <3.0s advantage vs stabilized >=3.0s 3 pts), mounts, back takes, stalling "
                    "penalties, early taps/submissions, and the final referee hand-raise (observed_winner)."
                )
                retry_thinking = "high" if chosen_thinking != "high" else chosen_thinking
                r_parsed, r_lat, r_in, r_out = _run_interaction_call(retry_prompt, retry_thinking)
                latency_ms += r_lat
                in_tok += r_in
                out_tok += r_out
                retry_evs = [
                    ev
                    for item in (r_parsed.get("events") or [])
                    if (ev := coerce_raw_event(item)) is not None
                ]
                if retry_evs or str(r_parsed.get("observed_winner", "") or "").strip().upper() in ("A", "B"):
                    parsed = r_parsed
                    raw_evs = retry_evs
                elif obs_win_first in ("A", "B"):
                    parsed["observed_winner"] = obs_win_first
        finally:
            with contextlib.suppress(Exception):
                genai_client.files.delete(name=uploaded.name)

    rec = adjudicate_raw_events_to_prediction(
        match_id=entry.match_id,
        model_id=record_model_id,
        division=div_cfg,
        raw_events=raw_evs,
        leakage_verified=leakage_ok,
        latency_ms=latency_ms,
        input_tokens=in_tok,
        output_tokens=out_tok,
        bout_duration_sec=bout_dur,
    )
    return _reconcile_observed_winner(
        rec,
        str(parsed.get("observed_winner", "") or ""),
        str(parsed.get("observed_win_method", "") or ""),
    )


def run_scoreboard_bench(
    split: Literal["calibration", "heldout", "pilot"] = "calibration",
    models: list[str] | None = None,
    live_video: bool = False,
    agentic_video: bool = False,
    force_recompute: bool = False,
    tuning_mode: bool = False,
    run_id: str = "default",
    max_matches: int | None = None,
    knowledge_dir: Path | str | None = None,
    media_dir: Path | str | None = None,
    persist: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Runs or replays rollmetrics-scoreboard-v1 on calibration or heldout splits with leakage & split guards."""
    cfg = settings or get_settings()
    kb_root = Path(knowledge_dir).expanduser().resolve() if knowledge_dir else cfg.knowledge_dir
    bench_dir = kb_root / "evals" / "scoreboard_v1"
    bench_dir.mkdir(parents=True, exist_ok=True)

    matches_path = bench_dir / "matches.jsonl"
    labels_path = bench_dir / "labels.jsonl"
    splits_path = bench_dir / "splits.json"
    predictions_cache_path = bench_dir / "predictions_cache.jsonl"

    entries = load_matches_jsonl(matches_path)
    if not entries:
        raise FileNotFoundError(f"No matches found in {matches_path}.")

    if not splits_path.exists():
        m_sha = sha256_file(matches_path)
        sp_manifest = build_deterministic_splits(entries, matches_sha256=m_sha)
        write_splits_manifest(splits_path, sp_manifest)

    split_manifest = enforce_split_discipline(
        splits_path=splits_path,
        requested_split=split,
        tuning_mode=tuning_mode,
        run_id=run_id,
    )

    labels_by_id = load_labels_jsonl(labels_path)

    # Load cached predictions for deterministic CI / read-only API replay when live_video=False
    cached_preds: dict[tuple[str, str], MatchPredictionRecord] = {}
    cached_model_ids: list[str] = []
    if predictions_cache_path.exists():
        for ln in predictions_cache_path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                rec = MatchPredictionRecord.model_validate_json(ln)
                cached_preds[(rec.match_id, rec.model_id)] = rec
                if rec.model_id not in cached_model_ids:
                    cached_model_ids.append(rec.model_id)

    if models:
        target_models = list(models)
    elif not live_video and cached_model_ids:
        target_models = sorted(cached_model_ids)
    else:
        target_models = list(DEFAULT_SCOREBOARD_MODELS)

    if split == "calibration":
        allowed_ids = set(split_manifest.calibration_match_ids)
    elif split == "heldout":
        allowed_ids = set(split_manifest.heldout_match_ids)
    else:
        allowed_ids = {e.match_id for e in entries}

    selected_entries = [
        e for e in entries if e.match_id in allowed_ids and e.match_id in labels_by_id
    ]
    if max_matches is not None:
        selected_entries = selected_entries[:max_matches]

    eval_media_root = (
        Path(media_dir).expanduser().resolve()
        if media_dir
        else (cfg.runtime_dir / "eval_media")
    )

    genai_client = None
    if live_video and cfg.gemini_api_key:
        from google import genai
        from google.genai import types

        genai_client = genai.Client(
            api_key=cfg.gemini_api_key,
            http_options=types.HttpOptions(timeout=90000),
        )

    # Cache masked storyboard sheets per match across models during live runs
    sheet_cache: dict[str, tuple[list[bytes], int]] = {}
    new_preds_written = False

    model_summaries: list[dict[str, Any]] = []
    per_model_details: dict[str, list[dict[str, Any]]] = {}

    for m_id in target_models:
        canon_m = get_model_pricing(m_id).model_id
        match_evals: list[dict[str, Any]] = []

        for entry in selected_entries:
            gt = labels_by_id[entry.match_id]
            pred_rec = cached_preds.get((entry.match_id, canon_m))

            if live_video and (force_recompute or pred_rec is None):
                vid_candidates = [
                    eval_media_root / f"{entry.video_id}.mp4",
                    eval_media_root / f"{entry.video_id}.f397.mp4",
                    eval_media_root / f"{entry.match_id}.mp4",
                ]
                vid_file = next((p for p in vid_candidates if p.exists()), None)
                if vid_file is not None:
                    use_agentic = agentic_video or m_id.endswith("-agentic")
                    if use_agentic:
                        pred_rec = score_masked_video_agentic(
                            entry=entry,
                            gt=gt,
                            video_path=vid_file,
                            model_id=m_id,
                            settings=cfg,
                            genai_client=genai_client,
                        )
                    else:
                        if entry.match_id not in sheet_cache:
                            sheet_cache[entry.match_id] = build_masked_storyboard_sheets(
                                video_path=vid_file,
                                bout_start_sec=gt.active_bout_start_sec,
                                bout_end_sec=gt.active_bout_end_sec,
                                preset_name=entry.overlay_preset,
                                num_frames=24,
                            )
                        pred_rec = score_masked_match_live(
                            entry=entry,
                            gt=gt,
                            video_path=vid_file,
                            model_id=m_id,
                            settings=cfg,
                            genai_client=genai_client,
                            cached_sheets=sheet_cache[entry.match_id],
                        )
                    cached_preds[(entry.match_id, canon_m)] = pred_rec
                    new_preds_written = True

            if pred_rec is None:
                continue

            match_evals.append(score_match_prediction(gt, pred_rec, event_tolerance_sec=5.0))

        agg = aggregate_split_metrics(canon_m, split, match_evals, event_tolerance_sec=5.0)
        model_summaries.append(agg.model_dump(mode="json"))
        per_model_details[canon_m] = match_evals

    if persist and new_preds_written:
        ordered_recs = [
            cached_preds[k] for k in sorted(cached_preds.keys())
        ]
        predictions_cache_path.write_text(
            "\n".join(json.dumps(r.model_dump(mode="json"), sort_keys=True) for r in ordered_recs) + "\n",
            encoding="utf-8",
        )

    today_str = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    spot_check_path = bench_dir / "spot_check_audit.json"
    spot_check_summary = (
        json.loads(spot_check_path.read_text(encoding="utf-8"))
        if spot_check_path.exists()
        else None
    )

    report: dict[str, Any] = {
        "benchmark_id": "rollmetrics-scoreboard-v1",
        "evaluated_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        "split": split,
        "tuning_mode": tuning_mode,
        "corpus_total_matches": len(entries),
        "labeled_matches": len(labels_by_id),
        "evaluated_matches_in_split": len(selected_entries),
        "splits_content_sha256": split_manifest.splits_content_sha256,
        "leakage_control": "strict_blackout_drawbox_plus_pixel_edge_ocr_gate",
        "spot_check_audit": {
            k: v for k, v in (spot_check_summary or {}).items() if k != "per_match"
        },
        "models": model_summaries,
        "per_match_details": per_model_details,
    }

    if persist:
        dated_path = bench_dir / f"results_{today_str}.json"
        # Merge calibration and heldout results in the dated artifact if an earlier split ran today
        if dated_path.exists():
            prev = json.loads(dated_path.read_text(encoding="utf-8"))
            by_split = prev.get("splits_summary", {})
        else:
            by_split = {}
        by_split[split] = model_summaries
        report["splits_summary"] = by_split
        dated_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (bench_dir / "latest_results.json").write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )

    return report
