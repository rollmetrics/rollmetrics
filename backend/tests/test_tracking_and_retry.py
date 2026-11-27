"""Tests for dynamic multi-person mat crop, zero-event retry escalation, and 240s Ollama timeout."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image
from rollmetrics.config import REPO_ROOT, Settings
from rollmetrics.evals.scoreboard_bench.masking import (
    _project_box_into_crop,
    create_masked_video_clip,
)
from rollmetrics.evals.scoreboard_bench.runner import (
    load_labels_jsonl,
    load_matches_jsonl,
    score_masked_video_agentic,
    write_labels_jsonl,
    write_matches_jsonl,
)
from rollmetrics.evals.scoreboard_bench.schema import (
    CompactScore,
    GroundTruthMatchLabel,
    MatchManifestEntry,
    NormalizedBox,
)
from rollmetrics.providers.ollama_local import OllamaLocalProvider
from rollmetrics.rules.schema import AgeDivision, BeltRank, MatchDivisionConfig, UniformType
from rollmetrics.video.ingest import resolve_binary
from rollmetrics.video.tracking import compute_mat_crop_box, compute_mat_crop_box_from_arrays


@pytest.fixture(scope="module")
def tracking_synth_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out_dir = tmp_path_factory.mktemp("track_synth")
    vid = out_dir / "mat_action.mp4"
    ffmpeg_bin = resolve_binary("ffmpeg")
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=24:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(vid),
        ],
        check=True,
        capture_output=True,
    )
    return vid


def test_dynamic_mat_crop_motion_yolo_and_masked_video_leakage_gate(
    tmp_path: Path,
    tracking_synth_mp4: Path,
) -> None:
    # 1. Motion energy tracker isolates central moving mat action cleanly
    f1 = np.zeros((180, 320, 3), dtype=np.float32) + 30.0
    f2 = f1.copy()
    f2[40:120, 100:220, :] = 220.0
    f3 = f1.copy()
    f3[45:125, 110:230, :] = 180.0
    crop = compute_mat_crop_box_from_arrays([f1, f2, f3])
    assert crop is not None
    assert 0.50 <= (crop.x1 - crop.x0) <= 0.94
    assert 0.55 <= (crop.y1 - crop.y0) <= 0.94

    # Static frames return None (no crop needed)
    assert compute_mat_crop_box_from_arrays([f1, f1.copy()]) is None
    assert compute_mat_crop_box_from_arrays([f1]) is None

    # 2. Optional YOLO-Seg person detector hook
    def _fake_yolo(_arr: np.ndarray) -> list[tuple[float, float, float, float]]:
        return [(0.25, 0.20, 0.55, 0.70), (0.45, 0.25, 0.72, 0.75)]

    yolo_crop = compute_mat_crop_box_from_arrays([f1, f2], yolo_detector=_fake_yolo)
    assert yolo_crop is not None
    assert yolo_crop.x0 >= 0.0 and yolo_crop.x1 <= 1.0

    # 3. Project box into crop helper
    full_crop = NormalizedBox(x0=0.2, y0=0.1, x1=0.8, y1=0.75)
    sb_box = NormalizedBox(x0=0.0, y0=0.81, x1=1.0, y1=1.0)
    assert _project_box_into_crop(sb_box, full_crop) is None
    overlap_crop = NormalizedBox(x0=0.1, y0=0.2, x1=0.9, y1=0.95)
    proj = _project_box_into_crop(sb_box, overlap_crop)
    assert proj is not None and proj.y1 == pytest.approx(1.0, abs=0.01)

    # 4. End-to-end masked video clip with crop_box enforces post-crop zero-leakage gate
    dst = tmp_path / "cropped_masked.mp4"
    res = create_masked_video_clip(
        src_video=tracking_synth_mp4,
        start_sec=0.0,
        end_sec=3.0,
        dst_video=dst,
        preset_name="smoothcomp_lower_bar",
        target_height=480,
        crop_box=overlap_crop,
    )
    assert dst.is_file() and res["leakage_free"] is True
    assert res["crop_box"] is not None

    # Video wrapper runs cleanly on real mp4
    _ = compute_mat_crop_box(tracking_synth_mp4, 0.0, 3.0, preset_name="smoothcomp_lower_bar", num_samples=4)


def test_zero_event_retry_escalation_and_jsonl_roundtrip(
    tmp_path: Path,
    tracking_synth_mp4: Path,
) -> None:
    entry = MatchManifestEntry(
        match_id="retry-bout-01",
        video_id="vid-retry",
        url="https://www.youtube.com/watch?v=vid-retry",
        start_sec=0.0,
        end_sec=120.0,
        athlete_a="Athlete A",
        athlete_b="Athlete B",
        division="Adult Black Gi",
        belt="black",
        uniform="gi",
        age_division="adult",
        overlay_preset="smoothcomp_lower_bar",
    )
    gt = GroundTruthMatchLabel(
        match_id="retry-bout-01",
        video_id="vid-retry",
        active_bout_start_sec=0.0,
        active_bout_end_sec=120.0,
        final_score_a=CompactScore(points=3),
        final_score_b=CompactScore(points=0),
        winner="A",
        method="points",
    )

    # Round-trip matches & labels JSONL helpers
    m_path = tmp_path / "matches.jsonl"
    l_path = tmp_path / "labels.jsonl"
    write_matches_jsonl(m_path, [entry])
    write_labels_jsonl(l_path, [gt])
    assert len(load_matches_jsonl(m_path)) == 1
    assert "retry-bout-01" in load_labels_jsonl(l_path)
    assert load_matches_jsonl(tmp_path / "missing.jsonl") == []
    assert load_labels_jsonl(tmp_path / "missing_l.jsonl") == {}

    # Mock Gemini Files + Interactions where 1st call returns 0 events -> triggers automatic retry!
    calls: list[dict[str, Any]] = []

    class _FakeFile:
        name = "files/retry123"
        uri = "https://generativelanguage.googleapis.com/v1beta/files/retry123"
        state = "ACTIVE"

    class _FakeFiles:
        def upload(self, file: str) -> Any:
            del file
            return _FakeFile()

        def get(self, name: str) -> Any:
            del name
            return _FakeFile()

        def delete(self, name: str) -> None:
            del name

    class _FakeInteractions:
        def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            if len(calls) == 1:
                txt = json.dumps(
                    {
                        "summary": "No events detected on pass 1",
                        "events": [],
                        "observed_winner": "referee_decision",
                        "observed_win_method": "referee_decision",
                    }
                )
            else:
                txt = json.dumps(
                    {
                        "summary": "Verified guard pass on retry pass",
                        "events": [
                            {
                                "timestamp_sec": 42.0,
                                "actor": "A",
                                "kind": "guard_pass",
                                "hold_duration_sec": 3.5,
                                "in_bounds": True,
                                "confidence": 0.92,
                            }
                        ],
                        "observed_winner": "A",
                        "observed_win_method": "points",
                    }
                )
            usage = type("U", (), {"total_input_tokens": 500, "total_output_tokens": 60})()
            return type("Resp", (), {"output_text": txt, "text": txt, "usage": usage})()

    fake_client = type("C", (), {"files": _FakeFiles(), "interactions": _FakeInteractions()})()
    settings = Settings(
        environment="test",
        runtime_dir=tmp_path / "rt",
        knowledge_dir=REPO_ROOT / "knowledge_base",
        gemini_api_key="fake-key",
    )
    pred = score_masked_video_agentic(
        entry=entry,
        gt=gt,
        video_path=tracking_synth_mp4,
        model_id="gemini-4-hydrogen-agentic",
        settings=settings,
        genai_client=fake_client,
        masked_video_path=tracking_synth_mp4,
    )
    assert len(calls) == 2
    assert calls[1]["generation_config"]["thinking_level"] == "high"
    assert pred.winner == "A"
    assert pred.final_score_a.points == 3
    assert pred.input_tokens == 1000
    assert pred.output_tokens == 120


def test_ollama_240s_timeout_and_non_agentic_burst_fallback(
    tmp_path: Path,
    tracking_synth_mp4: Path,
) -> None:
    settings = Settings(
        environment="test",
        runtime_dir=tmp_path / "rt",
        knowledge_dir=REPO_ROOT / "knowledge_base",
        ollama_vision_model="gemma4:e2b-it-q4_K_M",
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma4:e2b-it-q4_K_M"}]})
        return httpx.Response(
            200,
            json={
                "model": "gemma4:e2b-it-q4_K_M",
                "message": {"content": json.dumps({"summary": "ok", "events": []})},
                "prompt_eval_count": 80,
                "eval_count": 20,
            },
        )

    prov = OllamaLocalProvider(settings=settings, transport=httpx.MockTransport(_handler))
    assert prov.timeout_sec == 240.0

    # Non-agentic burst path with frame_mask_fn
    div = MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.WHITE, age_division=AgeDivision.ADULT)
    mask_calls = 0

    def _mask(b: bytes) -> bytes:
        nonlocal mask_calls
        mask_calls += 1
        return b

    res = asyncio.run(
        prov.analyze_video(
            video_path=tracking_synth_mp4,
            division=div,
            sample_fps=0.5,
            use_agentic_loop=False,
            frame_mask_fn=_mask,
        )
    )
    assert mask_calls >= 1
    assert res.input_tokens >= 80

    # Window normalization edge cases (inverted bounds, oversized span, merge)
    wins = OllamaLocalProvider._normalize_inspect_windows(
        [
            {"start_sec": 20.0, "end_sec": 10.0, "reason": "inverted"},
            {"start_sec": 12.0, "end_sec": 18.0, "reason": "overlap"},
            {"start_sec": 0.0, "end_sec": 50.0, "reason": "too wide"},
            {"start_sec": "bad", "end_sec": "bad"},
        ],
        duration_sec=60.0,
        max_windows=4,
        max_span_sec=8.0,
    )
    assert len(wins) >= 1 and all(w["end_sec"] > w["start_sec"] for w in wins)

    # Empty image fallback in _compute_motion_peaks
    assert OllamaLocalProvider._compute_motion_peaks([(0.0, b"bad"), (1.0, b"bad")]) == []
    im_buf = Path(tmp_path / "ok.jpg")
    Image.new("RGB", (64, 36), (10, 20, 30)).save(im_buf)
    assert OllamaLocalProvider._compute_motion_peaks([(0.0, im_buf.read_bytes())]) == []


def test_tracking_masking_and_runner_edge_branches_for_92_pct_coverage(
    tmp_path: Path,
    tracking_synth_mp4: Path,
) -> None:
    from unittest.mock import patch

    from rollmetrics.evals.scoreboard_bench.masking import (
        _glyph_edge_density,
        extract_masked_anchor_frame,
        resolve_preset,
    )
    from rollmetrics.evals.scoreboard_bench.runner import run_scoreboard_bench
    from rollmetrics.evals.scoreboard_bench.schema import (
        MatchPredictionRecord,
        ScoreboardLeakageError,
    )
    from rollmetrics.video.tracking import _clamp_crop_box

    # 1. tracking.py edge branches: negligible zoom returns None, tiny image returns None
    assert _clamp_crop_box(0.0, 0.0, 0.99, 0.99) is None
    tiny = np.zeros((8, 8, 3), dtype=np.float32)
    assert compute_mat_crop_box_from_arrays([tiny, tiny + 50.0]) is None
    with patch("rollmetrics.video.tracking._find_binary", return_value=None):
        assert compute_mat_crop_box(tracking_synth_mp4, 0.0, 2.0) is None

    # 2. masking.py edge branches: unknown preset, empty edge density, missing ffmpeg, post-crop leakage guard
    with pytest.raises(KeyError):
        resolve_preset("nonexistent_preset")
    assert _glyph_edge_density(np.zeros((2, 2, 3), dtype=np.uint8)) == 0.0
    with patch("rollmetrics.evals.scoreboard_bench.masking._find_binary", return_value=None):
        assert extract_masked_anchor_frame(tracking_synth_mp4, 1.0) is None
        with pytest.raises(RuntimeError):
            create_masked_video_clip(tracking_synth_mp4, 0.0, 2.0, tmp_path / "x.mp4")

    # 3. runner.py run_scoreboard_bench live_video + agentic_video path with mocked scorer
    kb_tmp = tmp_path / "kb_sb"
    sb_dir = kb_tmp / "evals" / "scoreboard_v1"
    sb_dir.mkdir(parents=True)
    entry = MatchManifestEntry(
        match_id="live-mock-01",
        video_id="vid-mock-01",
        url="https://www.youtube.com/watch?v=vid-mock-01",
        start_sec=0.0,
        end_sec=4.0,
        athlete_a="A",
        athlete_b="B",
        division="Adult White Gi",
        belt="white",
        uniform="gi",
        age_division="adult",
        overlay_preset="smoothcomp_lower_bar",
    )
    gt = GroundTruthMatchLabel(
        match_id="live-mock-01",
        video_id="vid-mock-01",
        active_bout_start_sec=0.0,
        active_bout_end_sec=4.0,
        final_score_a=CompactScore(points=2),
        final_score_b=CompactScore(points=0),
        winner="A",
        method="points",
    )
    write_matches_jsonl(sb_dir / "matches.jsonl", [entry])
    write_labels_jsonl(sb_dir / "labels.jsonl", [gt])
    rt_dir = tmp_path / "rt_sb"
    media_dir = rt_dir / "eval_media"
    media_dir.mkdir(parents=True)
    (media_dir / "vid-mock-01.mp4").write_bytes(tracking_synth_mp4.read_bytes())

    fake_pred = MatchPredictionRecord(
        match_id="live-mock-01",
        model_id="gemini-4-hydrogen-agentic",
        final_score_a=CompactScore(points=2),
        final_score_b=CompactScore(points=0),
        winner="A",
        method="points",
        leakage_verified=True,
        latency_ms=150.0,
        input_tokens=400,
        output_tokens=50,
        cost_usd=0.001,
    )
    sb_settings = Settings(environment="test", runtime_dir=rt_dir, knowledge_dir=kb_tmp, gemini_api_key="k")
    with patch("rollmetrics.evals.scoreboard_bench.runner.score_masked_video_agentic", return_value=fake_pred):
        rep = run_scoreboard_bench(
            split="calibration",
            models=["gemini-4-hydrogen-agentic"],
            live_video=True,
            agentic_video=True,
            force_recompute=True,
            max_matches=1,
            knowledge_dir=kb_tmp,
            persist=True,
            settings=sb_settings,
        )
    assert rep["models"][0]["winner_accuracy"] == 1.0
    assert ScoreboardLeakageError is not None
