"""Comprehensive full-stack tests for IBJJF rules, provider telemetry, video ingest, live HUD, and media."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image
from rollmetrics.config import Settings
from rollmetrics.providers import (
    FrameObservation,
    GeminiCloudProvider,
    OllamaLocalProvider,
    VideoVisionResult,
    check_providers_health,
    extract_interaction_usage,
    get_vision_provider,
    select_best_ollama_vision_model,
)
from rollmetrics.rules import (
    FOUL_CATALOG,
    AdjudicatedEvent,
    AgeDivision,
    AthleteId,
    AthleteScore,
    BeltRank,
    EventKind,
    IBJJFRefereeEngine,
    MatchDivisionConfig,
    RawObservedEvent,
    ScoreboardSnapshot,
    UniformType,
    check_technique_legality,
)
from rollmetrics.video import (
    LiveRefereeSession,
    MatchAnalyzer,
    MediaStudio,
    download_video_url,
    extract_frame_burst,
    ingest_video_source,
    is_video_url,
    normalize_video_720p,
    resolve_binary,
    validate_video_file,
)


@pytest.fixture(scope="module")
def synthetic_video_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generates a real 6-second 640x360 H.264 synthetic mat video via ffmpeg."""
    out_dir = tmp_path_factory.mktemp("synth_video")
    vid_path = out_dir / "synthetic_match.mp4"
    ffmpeg_bin = resolve_binary("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=640x360:rate=24:duration=6",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(vid_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return vid_path


@pytest.fixture(scope="module")
def highres_mov_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generates a 2-second 1600x900 QuickTime .mov video to test 720p normalization."""
    out_dir = tmp_path_factory.mktemp("highres_mov")
    mov_path = out_dir / "camera_raw_1080.mov"
    ffmpeg_bin = resolve_binary("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=1600x900:rate=24:duration=2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(mov_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return mov_path


# ---------------------------------------------------------------------------
# (c) Scoring & Legality across ALL 5 Belts x Gi/No-Gi x Juvenile/Adult/Master
# ---------------------------------------------------------------------------


def test_rules_exports_and_division_durations() -> None:
    assert "heel_hook" in FOUL_CATALOG
    assert "slam" in FOUL_CATALOG
    assert AthleteScore().points == 0
    assert AdjudicatedEvent is not None
    assert ScoreboardSnapshot is not None

    for belt in BeltRank:
        assert MatchDivisionConfig(age_division=AgeDivision.MASTER, belt=belt).resolved_duration_sec() == 300
        assert MatchDivisionConfig(age_division=AgeDivision.JUVENILE, belt=belt).resolved_duration_sec() == 300

    expected_adult = {
        BeltRank.WHITE: 300,
        BeltRank.BLUE: 360,
        BeltRank.PURPLE: 420,
        BeltRank.BROWN: 480,
        BeltRank.BLACK: 600,
    }
    for belt, dur in expected_adult.items():
        cfg = MatchDivisionConfig(age_division=AgeDivision.ADULT, belt=belt)
        assert cfg.resolved_duration_sec() == dur


def _expected_cartesian_severity(
    slug: str,
    belt: BeltRank,
    uniform: UniformType,
    age: AgeDivision,
) -> str:
    if slug in ("slam", "kani_basami"):
        return "serious_foul_dq"
    if slug == "jumping_closed_guard":
        return "serious_foul_dq" if belt == BeltRank.WHITE else "legal"
    if slug == "wristlock":
        return "serious_foul_dq" if (belt == BeltRank.WHITE or age == AgeDivision.JUVENILE) else "legal"
    if slug == "kneebar":
        if age != AgeDivision.JUVENILE and belt in (BeltRank.BROWN, BeltRank.BLACK):
            return "legal"
        return "serious_foul_dq"
    if slug in ("heel_hook", "knee_reap"):
        if (
            uniform == UniformType.NOGI
            and age == AgeDivision.ADULT
            and belt in (BeltRank.BROWN, BeltRank.BLACK)
        ):
            return "legal"
        return "serious_foul_dq"
    if slug == "grabbing_rashguard_or_shorts_nogi":
        return "minor_foul" if uniform == UniformType.NOGI else "legal"
    if slug == "stalling":
        return "minor_foul"
    return "legal"


def test_full_cartesian_5_belts_gi_nogi_juvenile_adult_master_matrix() -> None:
    """Sweeps all 30 IBJJF divisions (5 belts x 2 uniforms x 3 ages) x 9 canonical techniques (270 cases)."""
    techniques = [
        "slam",
        "kani_basami",
        "jumping_closed_guard",
        "wristlock",
        "kneebar",
        "heel_hook",
        "knee_reap",
        "grabbing_rashguard_or_shorts_nogi",
        "stalling",
        "cross_collar_choke",
    ]
    checked = 0
    for belt in BeltRank:
        for uniform in UniformType:
            for age in AgeDivision:
                for slug in techniques:
                    verdict = check_technique_legality(
                        slug,
                        belt=belt,
                        uniform=uniform,
                        age_division=age,
                    )
                    expected = _expected_cartesian_severity(slug, belt, uniform, age)
                    assert verdict.severity == expected, (
                        f"Mismatch on {slug} in {age.value} {belt.value} {uniform.value}: "
                        f"got {verdict.severity}, expected {expected}"
                    )
                    checked += 1
    assert checked == 300


def test_ibjjf_scoring_3s_stabilization_locked_sub_and_bounds() -> None:
    div = MatchDivisionConfig(belt=BeltRank.BLUE, age_division=AgeDivision.ADULT, uniform=UniformType.GI)
    engine = IBJJFRefereeEngine(div)

    scoring_seq = [
        (10.0, EventKind.TAKEDOWN, 3.2),
        (30.0, EventKind.SWEEP, 3.0),
        (50.0, EventKind.KNEE_ON_BELLY, 3.5),
        (75.0, EventKind.GUARD_PASS, 4.0),
        (100.0, EventKind.MOUNT, 3.1),
        (130.0, EventKind.BACK_CONTROL, 5.0),
    ]
    for ts, kind, hold in scoring_seq:
        adj = engine.ingest_event(
            RawObservedEvent(timestamp_sec=ts, actor=AthleteId.ATHLETE_A, kind=kind, hold_duration_sec=hold)
        )
        assert adj.awarded_points > 0
        assert adj.awarded_advantages == 0

    assert engine.scores[AthleteId.ATHLETE_A].points == 17

    # Incomplete 2.1s stabilization -> +1 Advantage
    adj_short = engine.ingest_event(
        RawObservedEvent(
            timestamp_sec=150.0,
            actor=AthleteId.ATHLETE_B,
            kind=EventKind.GUARD_PASS,
            hold_duration_sec=2.1,
            in_bounds=True,
        )
    )
    assert (adj_short.awarded_points, adj_short.awarded_advantages) == (0, 1)

    # Out-of-bounds pass despite 3.5s hold -> +1 Advantage
    adj_oob = engine.ingest_event(
        RawObservedEvent(
            timestamp_sec=170.0,
            actor=AthleteId.ATHLETE_B,
            kind=EventKind.TAKEDOWN,
            hold_duration_sec=3.5,
            in_bounds=False,
        )
    )
    assert (adj_oob.awarded_points, adj_oob.awarded_advantages) == (0, 1)

    # Caught inside locked submission despite 4.0s top hold -> +1 Advantage, 0 pts
    adj_sub_lock = engine.ingest_event(
        RawObservedEvent(
            timestamp_sec=178.0,
            actor=AthleteId.ATHLETE_B,
            kind=EventKind.GUARD_PASS,
            hold_duration_sec=4.0,
            in_bounds=True,
            opponent_locked_submission=True,
        )
    )
    assert (adj_sub_lock.awarded_points, adj_sub_lock.awarded_advantages) == (0, 1)

    # Brief 0.4s scramble -> 0 pts / 0 adv
    adj_scramble = engine.ingest_event(
        RawObservedEvent(
            timestamp_sec=185.0,
            actor=AthleteId.ATHLETE_B,
            kind=EventKind.SWEEP,
            hold_duration_sec=0.4,
            in_bounds=True,
        )
    )
    assert (adj_scramble.awarded_points, adj_scramble.awarded_advantages) == (0, 0)
    assert engine.scores[AthleteId.ATHLETE_B].points == 0
    assert engine.scores[AthleteId.ATHLETE_B].advantages == 3


def test_all_tiebreakers_penalty_cascade_submission_and_serious_dq() -> None:
    div = MatchDivisionConfig(belt=BeltRank.PURPLE, age_division=AgeDivision.ADULT, uniform=UniformType.GI)

    # 1. Advantages tie-breaker when points tied 2-2
    eng_adv = IBJJFRefereeEngine(div)
    eng_adv.ingest_event(RawObservedEvent(timestamp_sec=20, actor=AthleteId.ATHLETE_A, kind=EventKind.SWEEP, hold_duration_sec=3.5))
    eng_adv.ingest_event(RawObservedEvent(timestamp_sec=60, actor=AthleteId.ATHLETE_B, kind=EventKind.SWEEP, hold_duration_sec=3.5))
    eng_adv.ingest_event(RawObservedEvent(timestamp_sec=90, actor=AthleteId.ATHLETE_B, kind=EventKind.ADVANTAGE))
    snap_adv = eng_adv.finalize_regulation()
    assert snap_adv.winner == "B"
    assert "Advantages" in snap_adv.win_method

    # 2. Fewer penalties tie-breaker when points (2-2) and advantages (0-0) are tied
    eng_pen = IBJJFRefereeEngine(div)
    eng_pen.ingest_event(RawObservedEvent(timestamp_sec=20, actor=AthleteId.ATHLETE_A, kind=EventKind.TAKEDOWN, hold_duration_sec=3.2))
    eng_pen.ingest_event(RawObservedEvent(timestamp_sec=50, actor=AthleteId.ATHLETE_B, kind=EventKind.SWEEP, hold_duration_sec=3.2))
    eng_pen.ingest_event(RawObservedEvent(timestamp_sec=80, actor=AthleteId.ATHLETE_B, kind=EventKind.MINOR_FOUL, technique_slug="stalling"))
    snap_pen = eng_pen.finalize_regulation()
    assert snap_pen.winner == "A"
    assert "Penalties" in snap_pen.win_method

    # 3. Referee decision when points, advantages, and penalties are all tied (double stalling x2)
    eng_ref = IBJJFRefereeEngine(div)
    eng_ref.ingest_event(RawObservedEvent(timestamp_sec=120, actor=AthleteId.ATHLETE_A, kind=EventKind.DOUBLE_STALLING))
    eng_ref.ingest_event(RawObservedEvent(timestamp_sec=240, actor=AthleteId.ATHLETE_A, kind=EventKind.DOUBLE_STALLING))
    snap_ref = eng_ref.finalize_regulation()
    assert snap_ref.winner == "referee_decision"

    # 4. Submission victory overrides trailing points
    eng_sub = IBJJFRefereeEngine(div)
    eng_sub.ingest_event(RawObservedEvent(timestamp_sec=30, actor=AthleteId.ATHLETE_B, kind=EventKind.GUARD_PASS, hold_duration_sec=4.0))
    eng_sub.ingest_event(RawObservedEvent(timestamp_sec=95, actor=AthleteId.ATHLETE_A, kind=EventKind.SUBMISSION))
    assert eng_sub.winner == "A"
    assert eng_sub.win_method == "Submission"

    # 5. Immediate serious foul DQ overrides points lead (knee reap in Gi)
    eng_dq = IBJJFRefereeEngine(div)
    eng_dq.ingest_event(RawObservedEvent(timestamp_sec=25, actor=AthleteId.ATHLETE_A, kind=EventKind.MOUNT, hold_duration_sec=4.0))
    dq_adj = eng_dq.ingest_event(
        RawObservedEvent(timestamp_sec=70, actor=AthleteId.ATHLETE_A, kind=EventKind.POSITION_CHANGE, technique_slug="knee_reap")
    )
    assert dq_adj.caused_dq is True
    assert eng_dq.winner == "B"

    # 6. 4-step minor foul penalty cascade ending in DQ
    eng_cascade = IBJJFRefereeEngine(div)
    for idx in range(4):
        p_adj = eng_cascade.ingest_event(
            RawObservedEvent(timestamp_sec=30.0 * (idx + 1), actor=AthleteId.ATHLETE_B, kind=EventKind.MINOR_FOUL, technique_slug="stalling")
        )
    assert p_adj.caused_dq is True
    assert eng_cascade.scores[AthleteId.ATHLETE_A].points == 2
    assert eng_cascade.scores[AthleteId.ATHLETE_A].advantages == 1
    assert eng_cascade.winner == "A"


# ---------------------------------------------------------------------------
# Provider Payloads & Real Telemetry (`latency_ms`, `input_tokens`, `output_tokens`, `model_id`)
# ---------------------------------------------------------------------------


def test_extract_interaction_usage_accounting() -> None:
    inter_with_thoughts = SimpleNamespace(
        usage=SimpleNamespace(
            total_input_tokens=200,
            total_output_tokens=45,
            total_thought_tokens=80,
            total_tokens=325,
        )
    )
    assert extract_interaction_usage(inter_with_thoughts) == (200, 125, 80)
    assert extract_interaction_usage(None) == (0, 0, 0)


@pytest.mark.asyncio
async def test_gemini_cloud_interactions_api_payloads_and_telemetry(synthetic_video_mp4: Path) -> None:
    recorded_interactions: list[dict[str, Any]] = []
    file_states = [
        SimpleNamespace(name="files/vid123", uri="https://generativelanguage.googleapis.com/v1beta/files/vid123", state="PROCESSING"),
        SimpleNamespace(name="files/vid123", uri="https://generativelanguage.googleapis.com/v1beta/files/vid123", state="ACTIVE"),
    ]
    poll_count = 0

    def fake_upload(*, file: str, **_kwargs: Any) -> Any:
        assert Path(file).exists()
        return file_states[0]

    def fake_get(*, name: str) -> Any:
        nonlocal poll_count
        assert name == "files/vid123"
        poll_count += 1
        return file_states[min(poll_count, len(file_states) - 1)]

    def fake_create(**kwargs: Any) -> Any:
        recorded_interactions.append(kwargs)
        model = kwargs["model"]
        gen_cfg = kwargs.get("generation_config") or {}
        thinking = gen_cfg.get("thinking_level")
        if "gemma" in model.lower():
            assert "generation_config" not in kwargs
            payload = {
                "response": "Clamp shoulder and cut 45-degree hip angle.",
                "current_position": "Closed Guard",
                "top_or_dominant_actor": "A",
                "stabilizing_scoring_kind": "sweep",
                "active_combativeness_a": True,
                "active_combativeness_b": True,
                "in_bounds": True,
                "occluded": False,
                "confidence": 0.92,
                "corner_cue": "Cut 45-degree angle and underhook far leg for pendulum.",
                "detected_events": [],
            }
            usage = SimpleNamespace(total_input_tokens=110, total_output_tokens=24, total_thought_tokens=0, total_tokens=134)
        elif thinking == "low":
            payload = {
                "current_position": "Closed Guard",
                "top_or_dominant_actor": "A",
                "stabilizing_scoring_kind": "sweep",
                "active_combativeness_a": True,
                "active_combativeness_b": True,
                "in_bounds": True,
                "occluded": False,
                "confidence": 0.91,
                "corner_cue": "Cut 45-degree angle and underhook far leg for pendulum.",
                "detected_events": [],
            }
            usage = SimpleNamespace(total_input_tokens=420, total_output_tokens=65, total_thought_tokens=15, total_tokens=500)
        elif thinking == "medium":
            payload = {
                "summary": "Competitive closed guard battle with boundary pass exchange.",
                "events": [
                    {
                        "timestamp_sec": 42.0,
                        "actor": "A",
                        "kind": "guard_pass",
                        "hold_duration_sec": 2.8,
                        "in_bounds": True,
                        "confidence": 0.68,
                        "summary": "Borderline knee-cut stabilization near 3s mark.",
                        "position_after": "Side Control",
                    }
                ],
                "coaching_notes": [],
            }
            usage = SimpleNamespace(total_input_tokens=1800, total_output_tokens=140, total_thought_tokens=60, total_tokens=2000)
        else:
            assert model == "gemini-4-neon"
            payload = {
                "rulings": [
                    {
                        "event_index": 0,
                        "confirmed_kind": "guard_pass",
                        "hold_duration_sec": 3.3,
                        "in_bounds": True,
                        "technique_slug": "",
                        "confidence": 0.94,
                        "ruling_note": "Confirmed full 3.3s chest-to-chest side control stabilization.",
                    }
                ],
                "cta_synthesis": [
                    {
                        "position": "Side Control",
                        "headline": "CTA Crossface & Underhook Wedge",
                        "cta_detail": "Drive shoulder pressure through jaw line while blocking near hip.",
                    }
                ],
            }
            usage = SimpleNamespace(total_input_tokens=500, total_output_tokens=90, total_thought_tokens=110, total_tokens=700)
        return SimpleNamespace(
            outputs=[SimpleNamespace(type="text", text=json.dumps(payload))],
            usage=usage,
        )

    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=fake_upload, get=fake_get),
        interactions=SimpleNamespace(create=fake_create),
    )
    settings = Settings(gemini_api_key="test-gemini-key")
    provider = GeminiCloudProvider(settings=settings, client=fake_client, poll_interval_sec=0.01)
    div = MatchDivisionConfig()

    # 1. Smart tiered routing: default live frame -> gemma-4-26b-a4b-it (omits thinking_level)
    obs = await provider.analyze_live_frame(b"\xff\xd8\xff\xd9", division=div, elapsed_sec=12.0)
    assert obs.current_position == "Closed Guard"
    assert obs.model_id == "gemma-4-26b-a4b-it"
    assert "generation_config" not in recorded_interactions[0]
    assert obs.latency_ms > 0.0
    assert obs.input_tokens == 110
    assert obs.output_tokens == 24
    assert obs.telemetry["model_id"] == "gemma-4-26b-a4b-it"

    # 2. Full video call -> gemini-4-hydrogen (medium, agentic) + gemini-4-neon (high) adjudicator
    vid_res = await provider.analyze_video(synthetic_video_mp4, division=div)
    assert poll_count == 1
    assert recorded_interactions[1]["model"] == "gemini-4-hydrogen"
    assert recorded_interactions[1]["generation_config"] == {"thinking_level": "medium"}
    assert recorded_interactions[1]["input"][0] == {
        "type": "video",
        "uri": "https://generativelanguage.googleapis.com/v1beta/files/vid123",
        "mime_type": "video/mp4",
        "processing": "agentic",
    }
    assert recorded_interactions[2]["model"] == "gemini-4-neon"
    assert recorded_interactions[2]["generation_config"] == {"thinking_level": "high"}
    assert vid_res.model_id == "gemini-4-hydrogen"
    assert vid_res.adjudicator_model_used == "gemini-4-neon"
    assert vid_res.input_tokens == 2300  # 1800 + 500
    assert vid_res.output_tokens == 400  # (140+60) + (90+110)
    assert vid_res.latency_ms > 0.0
    assert vid_res.events[0].hold_duration_sec == 3.3

    # 3. Direct Gemma 4 assistant query
    gemma_out = await provider.query_gemma_api("Summarize closed guard clamp.")
    assert gemma_out["model_id"] == "gemma-4-26b-a4b-it"
    assert gemma_out["input_tokens"] == 110
    assert gemma_out["output_tokens"] == 24
    assert gemma_out["latency_ms"] > 0.0
    assert "generation_config" not in recorded_interactions[3]

    # 4. Explicit model_override on live frame -> routes to gemini-4-hydrogen with thinking_level="low"
    hydrogen_override = GeminiCloudProvider(
        settings=settings,
        client=fake_client,
        model_override="gemini-4-hydrogen",
    )
    obs_override = await hydrogen_override.analyze_live_frame(
        b"\xff\xd8\xff\xd9",
        division=div,
        elapsed_sec=15.0,
    )
    assert obs_override.model_id == "gemini-4-hydrogen"
    assert recorded_interactions[4]["generation_config"] == {"thinking_level": "low"}
    assert obs_override.input_tokens == 420
    assert obs_override.output_tokens == 80


@pytest.mark.asyncio
async def test_ollama_local_provider_telemetry_and_factory() -> None:
    assert (
        select_best_ollama_vision_model(
            ["nomic-embed-text:latest", "gemma3:1b", "gemma4:e2b-it-q4_K_M"],
            "gemma3:4b",
        )
        == "gemma4:e2b-it-q4_K_M"
    )

    captured_chat_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "nomic-embed-text:latest"}, {"name": "gemma4:e2b-it-q4_K_M"}]},
            )
        if request.url.path == "/api/chat":
            body = json.loads(request.content.decode("utf-8"))
            captured_chat_bodies.append(body)
            reply = {
                "current_position": "Half Guard",
                "top_or_dominant_actor": "B",
                "stabilizing_scoring_kind": "guard_pass",
                "active_combativeness_a": True,
                "active_combativeness_b": True,
                "in_bounds": True,
                "occluded": False,
                "confidence": 0.88,
                "corner_cue": "Build knee shield and fight for far-side underhook.",
                "detected_events": [],
                "events": [
                    {
                        "timestamp_sec": 12.0,
                        "actor": "A",
                        "kind": "sweep",
                        "hold_duration_sec": 3.4,
                        "in_bounds": True,
                        "confidence": 0.9,
                    }
                ],
                "rulings": [],
                "cta_synthesis": [],
            }
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "prompt_eval_count": 312,
                    "eval_count": 78,
                    "message": {"role": "assistant", "content": json.dumps(reply)},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    settings = Settings(ollama_base_url="http://127.0.0.1:11434", ollama_vision_model="gemma3:4b")
    provider = OllamaLocalProvider(settings=settings, transport=transport)

    obs = await provider.analyze_live_frame(b"fake-jpeg-bytes", division=MatchDivisionConfig(), elapsed_sec=18.5)
    assert obs.model_id == "gemma4:e2b-it-q4_K_M"
    assert obs.input_tokens == 312
    assert obs.output_tokens == 78
    assert obs.latency_ms > 0.0
    assert obs.telemetry["model_id"] == "gemma4:e2b-it-q4_K_M"
    assert captured_chat_bodies[0]["keep_alive"] == "10m"

    # Multi-batch video telemetry aggregation on Ollama
    vid_res = await provider.analyze_video(
        Path("unused.mp4"),
        division=MatchDivisionConfig(),
        burst_frames=[(float(i), b"jpg") for i in range(5)],  # 2 batches (4 + 1)
    )
    assert vid_res.input_tokens == 624
    assert vid_res.output_tokens == 156
    assert len(vid_res.events) == 2

    # Default out-of-the-box Settings profile is 'local' (Ollama Gemma 4 without API keys)
    default_cfg = Settings(_env_file=None)
    assert default_cfg.profile == "local"
    assert isinstance(get_vision_provider(settings=default_cfg), OllamaLocalProvider)

    # Factory model_override routing ("ollama/...")
    routed = get_vision_provider("cloud", settings=settings, model_override="ollama/gemma4:e2b-it-q4_K_M")
    assert isinstance(routed, OllamaLocalProvider)
    assert routed.model_override == "gemma4:e2b-it-q4_K_M"

    health = await check_providers_health(settings=Settings(ollama_base_url="http://127.0.0.1:59999", gemini_api_key="k"))
    assert set(health.keys()) == {
        "active_profile",
        "cloud_gemini_configured",
        "ollama_reachable",
        "ollama_models",
        "recommended_local_model",
    }


# ---------------------------------------------------------------------------
# (a) Pre-Recorded Video Ingestion, Error Handling, yt-dlp Plumbing & Analyzer
# ---------------------------------------------------------------------------


def test_video_ingest_validation_errors_normalization_and_ytdlp(
    synthetic_video_mp4: Path,
    highres_mov_video: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Missing file -> FileNotFoundError
    with pytest.raises(FileNotFoundError):
        validate_video_file(tmp_path / "does_not_exist.mp4")

    # 2. Empty 0-byte file -> ValueError
    empty_mp4 = tmp_path / "empty.mp4"
    empty_mp4.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        validate_video_file(empty_mp4)

    # 3. Unsupported extension -> ValueError
    bad_ext = tmp_path / "clip.avi"
    bad_ext.write_bytes(b"not-an-avi")
    with pytest.raises(ValueError, match="Unsupported video format"):
        validate_video_file(bad_ext)

    # 4. Corrupt binary bytes inside .mp4 -> ValueError from ffprobe
    corrupt_mp4 = tmp_path / "corrupt.mp4"
    corrupt_mp4.write_bytes(b"\x00\x01\x02corrupt-garbage-header" * 20)
    with pytest.raises(ValueError, match="ffprobe failed"):
        validate_video_file(corrupt_mp4)

    # 5. Audio-only .mp4 with no video stream -> ValueError
    audio_only = tmp_path / "audio_only.mp4"
    ffmpeg_bin = resolve_binary("ffmpeg")
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "1",
            "-c:a",
            "aac",
            str(audio_only),
        ],
        check=True,
        capture_output=True,
    )
    with pytest.raises(ValueError, match="No video stream"):
        validate_video_file(audio_only)

    # 6. High-res 1600x900 .mov normalized to <=1280x720 H.264 .mp4
    raw_meta = validate_video_file(highres_mov_video)
    assert raw_meta.width == 1600 and raw_meta.height == 900
    norm_path = normalize_video_720p(highres_mov_video, output_path=tmp_path / "normalized_720p.mp4")
    norm_meta = validate_video_file(norm_path)
    assert norm_meta.width == 1280 and norm_meta.height == 720
    assert norm_meta.codec == "h264"

    # 7. Frame burst extraction with start_sec offset & JPEG magic header check
    frames = extract_frame_burst(
        synthetic_video_mp4,
        output_dir=tmp_path / "burst",
        sample_fps=2.0,
        start_sec=1.0,
        max_frames=3,
    )
    assert [f.timestamp_sec for f in frames] == [1.0, 1.5, 2.0]
    assert all(f.jpeg_bytes[:2] == b"\xff\xd8" for f in frames)

    # 8. URL validation & yt-dlp download + ingest_video_source plumbing
    assert is_video_url("https://www.youtube.com/watch?v=sH8gkS1eti4") is True
    assert is_video_url("file:///etc/passwd") is False
    assert is_video_url(synthetic_video_mp4) is False
    with pytest.raises(ValueError, match="http:// and https://"):
        download_video_url("file:///etc/passwd", output_dir=tmp_path)

    real_run = subprocess.run

    def fake_ytdlp_run(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        if cmd and str(cmd[0]).endswith("yt-dlp"):
            out_idx = cmd.index("-o") + 1
            out_tmpl = cmd[out_idx]
            target_file = Path(out_tmpl.replace("%(ext)s", "mov"))
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(highres_mov_video.read_bytes())
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("rollmetrics.video.ingest.subprocess.run", fake_ytdlp_run)
    ingested = ingest_video_source(
        "https://www.youtube.com/watch?v=demo_bjj_match",
        work_dir=tmp_path / "url_work",
        normalize=True,
    )
    assert ingested.source_kind == "url"
    assert ingested.width == 1280 and ingested.height == 720
    assert ingested.path.suffix == ".mp4"


def test_match_analyzer_event_stream_and_full_video_telemetry(
    synthetic_video_mp4: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = MatchAnalyzer(settings=Settings())
    div = MatchDivisionConfig(
        athlete_a_name="Kory Mathewson",
        athlete_b_name="Michael Dube",
        locked_athlete=AthleteId.ATHLETE_A,
    )
    events = [
        RawObservedEvent(
            timestamp_sec=37.0,
            actor=AthleteId.ATHLETE_A,
            kind=EventKind.POSITION_CHANGE,
            position_after="Closed Guard",
        ),
        RawObservedEvent(
            timestamp_sec=53.0,
            actor=AthleteId.ATHLETE_B,
            kind=EventKind.MINOR_FOUL,
            technique_slug="fingers_inside_sleeve_or_pant_cuff",
            position_after="Closed Guard",
        ),
        RawObservedEvent(
            timestamp_sec=120.0,
            actor=AthleteId.ATHLETE_A,
            kind=EventKind.SWEEP,
            hold_duration_sec=1.9,
            position_after="Mount",
        ),
        RawObservedEvent(
            timestamp_sec=171.0,
            actor=AthleteId.ATHLETE_A,
            kind=EventKind.SUBMISSION,
            position_after="Closed Guard",
        ),
    ]
    res = analyzer.analyze_event_stream(events=events, division=div)
    assert res["winner"] == "A"
    assert res["win_method"] == "Submission"
    assert res["athlete_a"]["advantages"] == 1
    assert res["athlete_b"]["penalties"] == 1
    assert len(res["timeline"]) == 4
    assert len(res["rule_citations"]["ev-002"]) >= 1
    assert any(c["category"] == "3-Second Stabilization Conversion" for c in res["coaching_summary"])

    class _StubVideoProvider(OllamaLocalProvider):
        async def analyze_video(
            self,
            video_path: Path,
            division: MatchDivisionConfig,
            sample_fps: float = 0.5,
            burst_frames: Any = None,
        ) -> VideoVisionResult:
            del video_path, division, sample_fps, burst_frames
            return VideoVisionResult(
                events=events,
                model_used="gemini-4-hydrogen",
                latency_ms=412.5,
                input_tokens=1450,
                output_tokens=190,
                summary="Full match analysis",
            )

    monkeypatch.setattr("rollmetrics.video.analyzer.get_vision_provider", lambda **_kw: _StubVideoProvider())
    vid_out = asyncio.run(analyzer.analyze_video(synthetic_video_mp4, division=div, profile="cloud"))
    assert vid_out["winner"] == "A"
    assert vid_out["source_meta"]["model_id"] == "gemini-4-hydrogen"
    assert vid_out["source_meta"]["telemetry"]["latency_ms"] == 412.5
    assert vid_out["source_meta"]["telemetry"]["input_tokens"] == 1450


# ---------------------------------------------------------------------------
# (b) Live Ingestion: Rapid Bursts, 3s Hold & Reset, 20s Stalling, Occlusion, Lock-On
# ---------------------------------------------------------------------------


class _ScriptedLiveProvider(OllamaLocalProvider):
    def __init__(self, observations: list[FrameObservation | Exception]) -> None:
        super().__init__(settings=Settings())
        self._observations = list(observations)
        self.calls = 0

    async def analyze_live_frame(
        self,
        jpeg_bytes: bytes,
        division: MatchDivisionConfig,
        elapsed_sec: float = 0.0,
        current_position: str = "Standing",
        audio_transcript: str = "",
    ) -> FrameObservation:
        del jpeg_bytes, division, current_position, audio_transcript
        item = self._observations[min(self.calls, len(self._observations) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item.model_copy(
            update={
                "elapsed_sec": elapsed_sec,
                "model_used": "gemma4:e2b-it-q4_K_M",
                "latency_ms": 185.0,
                "input_tokens": 260,
                "output_tokens": 42,
            }
        )


@pytest.mark.asyncio
async def test_live_session_rapid_bursts_hold_reset_stalling_occlusion_and_lockon() -> None:
    scripted = _ScriptedLiveProvider(
        [
            # Call 1 (t=0.0): Athlete A starts knee-on-belly hold
            FrameObservation(
                current_position="Knee on Belly",
                top_or_dominant_actor="A",
                stabilizing_scoring_kind=EventKind.KNEE_ON_BELLY,
                corner_cue="Pull far lapel and post foot wide.",
            ),
            # Call 2 (t=1.8): Athlete B shrimps back to Closed Guard BEFORE 3.0s -> +1 Adv to A!
            FrameObservation(
                current_position="Closed Guard",
                top_or_dominant_actor="none",
                stabilizing_scoring_kind=None,
                corner_cue="Break posture immediately.",
            ),
            # Call 3 (t=3.5): Athlete A initiates guard pass (occluded & low confidence 0.58)
            FrameObservation(
                current_position="Side Control",
                top_or_dominant_actor="A",
                stabilizing_scoring_kind=EventKind.GUARD_PASS,
                confidence=0.58,
                occluded=True,
            ),
            # Call 4 (t=5.2): Transient runtime error -> non-crashing fallback still ticks hold!
            RuntimeError("Simulated camera buffer hiccup"),
            # Call 5 (t=7.0): Still holding Side Control (3.5s held since t=3.5 -> +3 pts + needs_review!)
            FrameObservation(
                current_position="Side Control",
                top_or_dominant_actor="A",
                stabilizing_scoring_kind=EventKind.GUARD_PASS,
                confidence=0.60,
                occluded=True,
            ),
            # Call 6 (t=9.0): Holding same Side Control -> must NOT double-award +3 pts! Both stall.
            FrameObservation(
                current_position="Side Control",
                top_or_dominant_actor="A",
                stabilizing_scoring_kind=EventKind.GUARD_PASS,
                active_combativeness_a=False,
                active_combativeness_b=False,
            ),
            # Call 7 (t=29.5): 20.5s simultaneous lack of combativeness -> double_stalling!
            FrameObservation(
                current_position="Side Control",
                top_or_dominant_actor="none",
                stabilizing_scoring_kind=None,
                active_combativeness_a=False,
                active_combativeness_b=False,
            ),
        ]
    )

    div = MatchDivisionConfig(
        athlete_a_name="Kory Math",
        athlete_b_name="Rival",
        locked_athlete=AthleteId.ATHLETE_A,
    )
    session = LiveRefereeSession(
        division=div,
        profile="local",
        provider=scripted,
        min_vision_interval_sec=1.5,
    )

    # Rapid 5-frame webcam burst (t=0.0, 0.3, 0.6, 0.9, 1.2): only t=0.0 hits vision!
    s0 = await session.process_frame(b"jpg", elapsed_sec=0.0)
    assert s0["vision_throttled"] is False
    for burst_t in (0.3, 0.6, 0.9, 1.2):
        sb = await session.process_frame(b"jpg", elapsed_sec=burst_t)
        assert sb["vision_throttled"] is True
    assert scripted.calls == 1
    assert sb["active_stabilization"]["held_sec"] == pytest.approx(1.2, abs=0.05)

    # t=1.8s: knee-on-belly broken at 1.8s (<3.0s) -> downgraded to +1 Advantage for A
    s_reset = await session.process_frame(b"jpg", elapsed_sec=1.8)
    assert s_reset["athlete_a"]["points"] == 0
    assert s_reset["athlete_a"]["advantages"] == 1
    assert s_reset["active_stabilization"] is None

    # t=3.5s -> t=5.2s (provider error recovered!) -> t=6.8s crosses 3.0s hold -> +3 pts + review badge!
    await session.process_frame(b"jpg", elapsed_sec=3.5)
    s_err = await session.process_frame(b"jpg", elapsed_sec=5.2)
    assert any("Vision fallback" in alert for alert in s_err["audio_alerts"])
    s_scored = await session.process_frame(b"jpg", elapsed_sec=7.0, audio_transcript="Parou")
    assert s_scored["athlete_a"]["points"] == 3
    assert len(s_scored["review_flags"]) >= 1
    assert s_scored["telemetry"]["model_id"] == "gemma4:e2b-it-q4_K_M"

    # Continued hold at t=9.0s does NOT double-score the same guard_pass
    s_cont = await session.process_frame(b"jpg", elapsed_sec=9.0)
    assert s_cont["athlete_a"]["points"] == 3

    # t=29.5s: 20.5s double non-combativeness triggers double_stalling (1-1 penalties)
    s_stall = await session.process_frame(b"jpg", elapsed_sec=29.5)
    assert s_stall["athlete_a"]["penalties"] == 1
    assert s_stall["athlete_b"]["penalties"] == 1

    # Switch Athlete Lock-On from A to B and verify corner cues re-target Rival
    s_lock_b = session.lock_athlete("B")
    assert s_lock_b["locked_athlete"] == "B"
    assert all(c["athlete"] == "Rival" for c in s_lock_b["corner_cues"])


# ---------------------------------------------------------------------------
# (d) Display / Presentation Media (`MediaStudio` ffprobe & Pillow Verification)
# ---------------------------------------------------------------------------


def test_media_studio_presentation_contrast_codec_and_study_reel(
    synthetic_video_mp4: Path,
    tmp_path: Path,
) -> None:
    studio = MediaStudio()

    # 1. 3-panel pointer board: verify exact 1860x614 dimensions, dark mat bg, emerald accent, contrast, & boundary clamp
    board_png = studio.generate_three_panel_board(
        video_path=synthetic_video_mp4,
        center_timestamp_sec=0.3,  # exercises t-1.5s clamping cleanly at 0.0s
        output_path=tmp_path / "study_board.png",
        title="Semifinal Inside-Triangle Armbar Sequence",
        cues=[
            "Drag right sleeve across hip line to collapse tripod base.",
            "Shoot high diamond lock isolating right shoulder and head.",
            "Pinch knees, curl heels down, and extend hips with thumb up.",
        ],
        actor_label="KORY",
    )
    with Image.open(board_png) as img:
        assert img.size == (1860, 614)
        assert img.mode == "RGB"
        arr = np.asarray(img)
        # Verify emerald left header accent strip (x=3, y=20) == (16, 185, 129)
        assert tuple(int(v) for v in arr[20, 3]) == (16, 185, 129)
        # Verify bottom-left outer margin is dark mat (#0b0f14 = 11, 15, 20)
        assert tuple(int(v) for v in arr[610, 4]) == (11, 15, 20)
        # Verify non-trivial visual contrast across rendered panels + badges + cues
        assert float(arr.std()) > 35.0

    board_jpg = studio.generate_three_panel_board(
        video_path=synthetic_video_mp4,
        center_timestamp_sec=5.8,  # exercises t+1.5s EOF clamping
        output_path=tmp_path / "study_board.jpg",
        title="EOF Boundary Clamp Check",
        cues=["Single cue fallback check"],
    )
    assert board_jpg.is_file() and board_jpg.stat().st_size > 8_000

    # 2. 0.4x slow-mo clip (with and without caption) + ffprobe codec/pix_fmt/duration verification
    slowmo_captioned = studio.generate_slowmo_clip(
        video_path=synthetic_video_mp4,
        start_sec=1.0,
        duration_sec=2.0,
        output_path=tmp_path / "study_slowmo.mp4",
        speed=0.4,
        caption="0.4x Replay: High-Diamond Lock to Armbar Finish",
    )
    slow_meta = validate_video_file(slowmo_captioned)
    assert slow_meta.codec == "h264"
    assert slow_meta.duration_sec == pytest.approx(5.0, abs=0.5)

    ffprobe_bin = resolve_binary("ffprobe")
    pix_probe = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=pix_fmt",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(slowmo_captioned),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert pix_probe.stdout.strip() == "yuv420p"

    slowmo_plain = studio.generate_slowmo_clip(
        video_path=synthetic_video_mp4,
        start_sec=0.5,
        duration_sec=1.0,
        output_path=tmp_path / "study_slowmo_plain.mp4",
        speed=0.5,
        caption="",
    )
    assert validate_video_file(slowmo_plain).duration_sec == pytest.approx(2.0, abs=0.4)

    # 3. Uncut coaching study reel + empty guard
    with pytest.raises(ValueError, match="at least one"):
        studio.generate_uncut_study_reel([], output_path=tmp_path / "empty.mp4")

    reel_path = studio.generate_uncut_study_reel(
        segments=[
            {
                "video_path": synthetic_video_mp4,
                "start_sec": 0.5,
                "duration_sec": 1.5,
                "title": "Opening Guard Pull Geometry",
                "cue": "Same-side collar + elbow control with left foot on hip.",
                "title_duration_sec": 1.0,
            },
            {
                "video_path": synthetic_video_mp4,
                "start_sec": 2.5,
                "end_sec": 4.0,
                "title": "High-Diamond Armbar Finish",
                "cue": "Keep knees pinched and drive heels downward.",
                "title_duration_sec": 1.0,
            },
        ],
        output_path=tmp_path / "study_reel.mp4",
    )
    reel_meta = validate_video_file(reel_path)
    assert (reel_meta.width, reel_meta.height, reel_meta.codec) == (1280, 720, "h264")
    assert reel_meta.duration_sec == pytest.approx(5.0, abs=0.6)


# ---------------------------------------------------------------------------
# (e) Local Agentic Video Understanding (`OllamaLocalProvider.analyze_video_agentic`)
# ---------------------------------------------------------------------------


def test_ollama_local_agentic_video_understanding_loop_and_masking(
    synthetic_video_mp4: Path,
) -> None:
    """Verifies the 3-stage coarse -> zoom-window -> 1-fps adjudication loop with real ffmpeg + mask hook."""
    chat_calls: list[dict[str, Any]] = []
    mask_call_count = 0

    def fake_mask(jpeg_bytes: bytes) -> bytes:
        nonlocal mask_call_count
        mask_call_count += 1
        return jpeg_bytes

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "gemma4:e2b-it-q4_K_M"}]},
            )
        if request.url.path == "/api/chat":
            payload = json.loads(request.content.decode("utf-8"))
            chat_calls.append(payload)
            user_prompt = payload["messages"][-1]["content"]
            if "STAGE 1 COARSE SCAN" in user_prompt:
                reply = {
                    "summary": "Coarse scan detected a guard sweep and late mount finish.",
                    "inspect_windows": [
                        {
                            "start_sec": 1.0,
                            "end_sec": 4.5,
                            "reason": "verify 3s sweep stabilization and referee hand signal",
                        },
                        {
                            "start_sec": 3.5,
                            "end_sec": 6.0,
                            "reason": "verify final finish and hand-raise",
                        },
                    ],
                    "preliminary_events": [
                        {
                            "timestamp_sec": 2.0,
                            "actor": "A",
                            "kind": "sweep",
                            "hold_duration_sec": 2.5,
                            "in_bounds": True,
                            "confidence": 0.65,
                            "summary": "Coarse sweep sighting",
                        }
                    ],
                }
            else:
                reply = {
                    "window_summary": "Verified 3-second sweep stabilization across 3 consecutive 1-fps frames.",
                    "events": [
                        {
                            "timestamp_sec": 2.2,
                            "actor": "A",
                            "kind": "sweep",
                            "hold_duration_sec": 3.4,
                            "in_bounds": True,
                            "confidence": 0.94,
                            "summary": "Verified 3.4s sweep stabilization in zoomed 1-fps window",
                            "position_after": "Top Half Guard",
                        }
                    ],
                    "coaching_notes": [
                        {
                            "timestamp_sec": 2.2,
                            "position": "Half Guard",
                            "headline": "Pin far hip immediately after sweep",
                            "cta_detail": "Keep knee-elbow connection tight for full 3-second count.",
                        }
                    ],
                }
            return httpx.Response(
                200,
                json={
                    "model": "gemma4:e2b-it-q4_K_M",
                    "message": {"role": "assistant", "content": json.dumps(reply)},
                    "prompt_eval_count": 320,
                    "eval_count": 85,
                },
            )
        return httpx.Response(404)

    settings = Settings(profile="local", ollama_vision_model="gemma4:e2b-it-q4_K_M")
    provider = OllamaLocalProvider(settings=settings, transport=httpx.MockTransport(handler))
    div = MatchDivisionConfig(
        uniform=UniformType.GI,
        belt=BeltRank.WHITE,
        age_division=AgeDivision.ADULT,
        athlete_a_name="Sebastian Bussieres",
        athlete_b_name="Ludovic Thauvette",
    )

    res = asyncio.run(
        provider.analyze_video(
            video_path=synthetic_video_mp4,
            division=div,
            use_agentic_loop=True,
            frame_mask_fn=fake_mask,
        )
    )

    assert isinstance(res, VideoVisionResult)
    assert res.model_id == "gemma4:e2b-it-q4_K_M"
    # Stage 1 coarse call(s) + Stage 3 zoomed window adjudication call(s)
    assert len(chat_calls) >= 2
    assert any("STAGE 1 COARSE SCAN" in c["messages"][-1]["content"] for c in chat_calls)
    assert any("STAGE 2 ZOOMED WINDOW" in c["messages"][-1]["content"] for c in chat_calls)
    # Every extracted coarse + zoomed frame passed through frame_mask_fn
    assert mask_call_count >= res.telemetry["coarse_frames_scanned"] + 2
    assert res.telemetry["agentic_mode"] is True
    assert res.telemetry["agentic_windows_count"] >= 1
    assert len(res.telemetry["agentic_windows_inspected"]) == res.telemetry["agentic_windows_count"]
    assert res.input_tokens == 320 * len(chat_calls)
    assert res.output_tokens == 85 * len(chat_calls)
    assert res.latency_ms > 0.0
    # Zoomed verified event (hold 3.4s, conf 0.94) superseded coarse sighting
    assert len(res.events) == 1
    assert res.events[0].kind == EventKind.SWEEP
    assert res.events[0].hold_duration_sec == pytest.approx(3.4)
    assert len(res.coaching_notes) >= 1


@pytest.mark.asyncio
async def test_gemini_live_frame_fallback_to_tracker_and_reraise_on_override() -> None:
    calls: list[str] = []

    class _RateLimitError(Exception):
        pass

    class _FallbackClient:
        class _Interactions:
            def create(self, **kwargs: Any) -> dict[str, Any]:
                model = str(kwargs.get("model", ""))
                calls.append(model)
                if "gemma" in model:
                    raise _RateLimitError("429 RESOURCE_EXHAUSTED on gemma")
                return {
                    "outputs": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "current_position": "Side Control",
                                    "top_or_dominant_actor": "B",
                                    "stabilizing_scoring_kind": "guard_pass",
                                    "active_combativeness_a": True,
                                    "active_combativeness_b": True,
                                    "in_bounds": True,
                                    "confidence": 0.91,
                                    "corner_cue": "Frame under the chin and shrimp.",
                                    "detected_events": [],
                                }
                            ),
                        }
                    ],
                    "usage": {"total_input_tokens": 120, "total_output_tokens": 45},
                }

        def __init__(self) -> None:
            self.interactions = self._Interactions()

    settings = Settings(
        profile="cloud",
        gemini_api_key="test-key",
        gemma_api_model="gemma-4-26b-a4b-it",
        tracker_model="gemini-4-hydrogen",
    )
    provider = GeminiCloudProvider(settings=settings, client=_FallbackClient())
    obs = await provider.analyze_live_frame(b"\xff\xd8\xff\xd9", division=MatchDivisionConfig(), elapsed_sec=14.0)
    assert calls == ["gemma-4-26b-a4b-it", "gemini-4-hydrogen"]
    assert obs.model_used == "gemini-4-hydrogen"
    assert obs.stabilizing_scoring_kind == EventKind.GUARD_PASS

    override_provider = GeminiCloudProvider(
        settings=settings,
        client=_FallbackClient(),
        model_override="gemma-4-26b-a4b-it",
    )
    with pytest.raises(Exception, match="429 RESOURCE_EXHAUSTED"):
        await override_provider.analyze_live_frame(b"\xff\xd8\xff\xd9", division=MatchDivisionConfig(), elapsed_sec=16.0)


@pytest.mark.asyncio
async def test_live_session_infers_scoring_position_emits_position_change_and_survives_api_error() -> None:
    from rollmetrics.video.live_session import _infer_scoring_kind_from_position

    assert _infer_scoring_kind_from_position("") is None
    assert _infer_scoring_kind_from_position("Open Guard") is None
    assert _infer_scoring_kind_from_position("Side Control") == EventKind.GUARD_PASS
    assert _infer_scoring_kind_from_position("Back Mount") == EventKind.BACK_CONTROL
    assert _infer_scoring_kind_from_position("Full Mount") == EventKind.MOUNT

    class _CustomApiError(Exception):
        pass

    scripted = _ScriptedLiveProvider(
        [
            # Frame 1 (t=0.0): Open Guard -> emits POSITION_CHANGE moment into timeline!
            FrameObservation(
                current_position="Open Guard",
                top_or_dominant_actor="B",
                stabilizing_scoring_kind=None,
                corner_cue="Control sleeves and off-balance.",
            ),
            # Frame 2 (t=2.0): Side Control with stabilizing_scoring_kind=None -> infers GUARD_PASS!
            FrameObservation(
                current_position="side_control",
                top_or_dominant_actor="B",
                stabilizing_scoring_kind=None,
                corner_cue="Frame across the neck.",
            ),
            # Frame 3 (t=4.0): Generic API Exception -> caught cleanly without crashing, ticks hold!
            _CustomApiError("503 UNAVAILABLE"),
            # Frame 4 (t=5.5): Still Side Control -> 3.5s held -> awards +3 PTS to B!
            FrameObservation(
                current_position="side_control",
                top_or_dominant_actor="B",
                stabilizing_scoring_kind=None,
                corner_cue="Keep frames tight.",
            ),
        ]
    )
    session = LiveRefereeSession(
        division=MatchDivisionConfig(),
        profile="cloud",
        provider=scripted,
        min_vision_interval_sec=1.0,
    )
    s0 = await session.process_frame(b"jpg", elapsed_sec=0.0)
    assert len(s0["scoreboard"]["timeline"]) == 1
    assert s0["scoreboard"]["timeline"][0]["raw_kind"] == "position_change"

    s1 = await session.process_frame(b"jpg", elapsed_sec=2.0)
    assert s1["active_stabilization"] is not None
    assert s1["active_stabilization"]["kind"] == "guard_pass"

    s2 = await session.process_frame(b"jpg", elapsed_sec=4.0)
    assert "error" in s2["telemetry"]
    assert "_CustomApiError" in s2["telemetry"]["error"]

    s3 = await session.process_frame(b"jpg", elapsed_sec=5.5)
    assert s3["athlete_b"]["points"] == 3
    assert any(ev["awarded_points"] == 3 for ev in s3["scoreboard"]["timeline"])
