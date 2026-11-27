"""Unit and CI integration tests for the Scoreboard-Grounded Video Benchmark (scoreboard-v1)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from rollmetrics.config import REPO_ROOT
from rollmetrics.evals.scoreboard_bench import (
    CompactScore,
    HoldoutContaminationError,
    MatchManifestEntry,
    MatchPredictionRecord,
    PredictedScoreEvent,
    ScoreboardLeakageError,
    aggregate_split_metrics,
    assert_frame_leakage_free,
    build_deterministic_splits,
    compute_spot_check_report,
    create_masked_video_clip,
    crop_scoreboard_only,
    derive_events_from_readings,
    enforce_split_discipline,
    load_and_verify_splits,
    load_matches_jsonl,
    mask_frame_scoreboard,
    run_scoreboard_bench,
    score_match_prediction,
    write_splits_manifest,
)


def _make_synthetic_broadcast_frame(width: int = 854, height: int = 480) -> Image.Image:
    """Creates a synthetic mat frame with high-contrast scoreboard text in the lower bar."""
    img = Image.new("RGB", (width, height), (36, 84, 150))
    draw = ImageDraw.Draw(img)
    # Athletes on mat (center)
    draw.ellipse((320, 160, 420, 280), fill=(245, 245, 250))
    draw.ellipse((410, 180, 520, 300), fill=(30, 64, 175))
    # Bright scoreboard bar inside y=[0.81..0.97]
    bx0, by0 = round(0.10 * width), round(0.83 * height)
    bx1, by1 = round(0.90 * width), round(0.95 * height)
    draw.rectangle((bx0, by0, bx1, by1), fill=(248, 250, 252), outline=(220, 38, 38), width=3)
    draw.text((bx0 + 20, by0 + 18), "MICA GALVAO  2  |  04:44  |  2  LEONARDO SOUZA", fill=(11, 15, 20))
    # Top-right broadcaster bug
    draw.rectangle((round(0.89 * width), round(0.03 * height), round(0.96 * width), round(0.09 * height)), fill=(255, 255, 255))
    return img


def test_leakage_detector_catches_unmasked_scoreboard_and_passes_masked() -> None:
    raw_frame = _make_synthetic_broadcast_frame()

    # Labeler crop sees the bright scoreboard bar
    sb_crop = crop_scoreboard_only(raw_frame, preset_name="flograppling_lower_bar")
    assert sb_crop.size[0] > 400 and sb_crop.size[1] > 40

    # Passing an unmasked frame to the leakage gate MUST fail loudly
    with pytest.raises(ScoreboardLeakageError):
        assert_frame_leakage_free(raw_frame, preset_name="flograppling_lower_bar")

    # Masking zeroes out all scoreboard, walkout, and corner-bug pixels while keeping mat athletes intact
    masked = mask_frame_scoreboard(raw_frame, preset_name="flograppling_lower_bar")
    audit = assert_frame_leakage_free(
        masked,
        preset_name="flograppling_lower_bar",
        allow_codec_luma_floor=0.0,
    )
    assert audit["leakage_free"] is True
    assert audit["scoreboard_max_pixel"] == 0.0
    assert audit["glyph_edge_density"] == 0.0
    assert audit["ocr_text"] == ""
    # Center mat pixel remains untouched
    assert masked.getpixel((370, 220)) == (245, 245, 250)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None and not Path("/opt/homebrew/bin/ffmpeg").exists(),
    reason="ffmpeg not installed",
)
def test_masked_video_clip_pipeline_verifies_encoded_frames(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    frame_png = tmp_path / "src_frame.png"
    _make_synthetic_broadcast_frame().save(frame_png)

    raw_mp4 = tmp_path / "raw_unmasked.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            str(frame_png),
            "-t",
            "3.0",
            "-r",
            "12",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(raw_mp4),
        ],
        check=True,
        capture_output=True,
    )

    masked_mp4 = tmp_path / "masked_clean.mp4"
    res = create_masked_video_clip(
        src_video=raw_mp4,
        start_sec=0.0,
        end_sec=2.5,
        dst_video=masked_mp4,
        preset_name="flograppling_lower_bar",
    )
    assert res["leakage_free"] is True
    assert res["audit_frames_verified"] == 3
    assert masked_mp4.exists() and masked_mp4.stat().st_size > 1024


def test_deterministic_source_grouped_splits_and_holdout_contamination_guard(tmp_path: Path) -> None:
    entries = [
        MatchManifestEntry(
            match_id=f"ev{ev_idx}-m{m_idx}",
            video_id=f"vid_{ev_idx}",
            url=f"https://www.youtube.com/watch?v=vid_{ev_idx:07d}",
            start_sec=float(m_idx * 300),
            end_sec=float((m_idx + 1) * 300),
            athlete_a="Athlete Left",
            athlete_b="Athlete Right",
            division="Adult Middleweight",
            belt="black",
            uniform="gi",
            source="FloGrappling",
            event_name=f"Event #{ev_idx}",
            event_group=f"event_group_{ev_idx}",
        )
        for ev_idx in range(1, 8)
        for m_idx in range(3)
    ]

    manifest = build_deterministic_splits(entries, matches_sha256="abc123", calibration_ratio=0.30)
    assert manifest.total_matches == 21
    assert manifest.calibration_count + manifest.heldout_count == 21
    # Zero shared video_ids between calibration and heldout
    assert set(manifest.calibration_video_ids).isdisjoint(set(manifest.heldout_video_ids))

    splits_path = tmp_path / "splits.json"
    write_splits_manifest(splits_path, manifest)
    reloaded = load_and_verify_splits(splits_path)
    assert reloaded.splits_content_sha256 == manifest.splits_content_sha256

    # Calibration tuning is permitted
    enforce_split_discipline(
        splits_path,
        requested_split="calibration",
        tuning_mode=True,
        run_id="prompt-tune-01",
    )

    # Touching heldout with tuning_mode=True MUST raise HoldoutContaminationError and taint run_id
    with pytest.raises(HoldoutContaminationError):
        enforce_split_discipline(
            splits_path,
            requested_split="heldout",
            tuning_mode=True,
            run_id="leaky-tune-run",
        )

    # Even if tuning_mode=False later, reporting heldout for a tainted run_id MUST be refused!
    with pytest.raises(HoldoutContaminationError):
        enforce_split_discipline(
            splits_path,
            requested_split="heldout",
            tuning_mode=False,
            run_id="leaky-tune-run",
        )

    # Clean untainted evaluation run on heldout succeeds
    clean_ok = enforce_split_discipline(
        splits_path,
        requested_split="heldout",
        tuning_mode=False,
        run_id="clean-final-eval",
    )
    assert clean_ok.heldout_count == manifest.heldout_count


def test_labeler_derivation_spot_check_and_metrics_math() -> None:
    entry = MatchManifestEntry(
        match_id="test-m1",
        video_id="3mf7jDcht_8",
        url="https://www.youtube.com/watch?v=3mf7jDcht_8",
        start_sec=10.0,
        end_sec=320.0,
        athlete_a="Mica Galvao",
        athlete_b="Opponent B",
        division="Adult Middle",
    )
    ocr_payload = {
        "scoreboard_first_seen_sec": 20.0,
        "scoreboard_last_seen_sec": 260.0,
        "left_athlete_bar_color": "Blue Gi",
        "right_athlete_bar_color": "White Gi",
        "final_clock_str": "01:45",
        "winner_side": "A",
        "ended_before_time_expired": True,
        "readings": [
            {"rel_sec": 20.0, "clock_str": "10:00", "pts_a": 0, "adv_a": 0, "pen_a": 0, "pts_b": 0, "adv_b": 0, "pen_b": 0},
            {"rel_sec": 65.0, "clock_str": "09:15", "pts_a": 2, "adv_a": 0, "pen_a": 0, "pts_b": 0, "adv_b": 0, "pen_b": 0},
            {"rel_sec": 130.0, "clock_str": "08:10", "pts_a": 5, "adv_a": 1, "pen_a": 0, "pts_b": 2, "adv_b": 0, "pen_b": 0},
        ],
    }
    gt = derive_events_from_readings(entry, ocr_payload)
    assert gt.winner == "A"
    assert gt.method == "submission"
    assert gt.final_score_a == CompactScore(points=5, advantages=1, penalties=0)
    assert gt.final_score_b == CompactScore(points=2, advantages=0, penalties=0)
    assert len(gt.events) == 3

    # Spot check self-consistency = 1.0
    spot = compute_spot_check_report([gt], [gt])
    assert spot["labeler_winner_accuracy"] == 1.0
    assert spot["labeler_score_event_f1"] == 1.0

    # Prediction within +/-5s on 2 events, 1 missed event (>5s away), 1 wrong-athlete FP
    pred = MatchPredictionRecord(
        match_id="test-m1",
        model_id="gemini-4-hydrogen",
        events=[
            PredictedScoreEvent(match_elapsed_sec=48.5, athlete="A", pts_delta=2),  # TP vs 45.0s (dt=3.5s <= 5s)
            PredictedScoreEvent(match_elapsed_sec=112.0, athlete="B", pts_delta=2),  # TP vs 110.0s (dt=2.0s <= 5s)
            PredictedScoreEvent(match_elapsed_sec=125.0, athlete="A", pts_delta=3),  # FP/FN: dt=15.0s > 5s!
        ],
        final_score_a=CompactScore(points=5, advantages=0, penalties=0),
        final_score_b=CompactScore(points=2, advantages=0, penalties=0),
        winner="A",
        latency_ms=1450.0,
        input_tokens=3200,
        output_tokens=210,
        cost_usd=0.0011,
    )
    m_eval = score_match_prediction(gt, pred, event_tolerance_sec=5.0)
    assert m_eval["winner_ok"] is True
    assert m_eval["final_score_exact"] is False  # adv_a 0 vs 1
    assert m_eval["event_tp"] == 2
    assert m_eval["event_fp"] == 1
    assert m_eval["event_fn"] == 1

    agg = aggregate_split_metrics("gemini-4-hydrogen", "calibration", [m_eval], event_tolerance_sec=5.0)
    assert agg.winner_accuracy == 1.0
    assert agg.mae_points == 0.0
    assert agg.mae_advantages == 0.5
    assert agg.score_event_f1 == round(2 * (2 / 3) * (2 / 3) / (4 / 3), 4)


def test_committed_scoreboard_v1_artifacts_and_cached_replay() -> None:
    bench_dir = REPO_ROOT / "knowledge_base" / "evals" / "scoreboard_v1"
    matches_path = bench_dir / "matches.jsonl"
    splits_path = bench_dir / "splits.json"
    labels_path = bench_dir / "labels.jsonl"
    if not matches_path.exists():
        pytest.skip("scoreboard_v1 artifacts still building")

    entries = load_matches_jsonl(matches_path)
    assert len(entries) >= 100
    for check_p in (matches_path, splits_path, labels_path):
        assert check_p.exists()
        assert "\u2014" not in check_p.read_text(encoding="utf-8")

    sp = load_and_verify_splits(splits_path)
    assert sp.total_matches == len(entries)
    assert set(sp.calibration_video_ids).isdisjoint(set(sp.heldout_video_ids))

    # Replay calibration and heldout from committed predictions cache with zero network/media deps
    cal_rep = run_scoreboard_bench(split="calibration", live_video=False, persist=False)
    held_rep = run_scoreboard_bench(split="heldout", live_video=False, persist=False)
    assert cal_rep["split"] == "calibration"
    assert held_rep["split"] == "heldout"
    assert len(cal_rep["models"]) >= 1
    assert len(held_rep["models"]) >= 1


def test_cli_eval_scoreboard_and_readonly_api_endpoints(tmp_path: Path) -> None:
    import rollmetrics.cli as cli_mod
    from fastapi.testclient import TestClient
    from rollmetrics.config import Settings
    from rollmetrics.main import create_app

    bench_dir = REPO_ROOT / "knowledge_base" / "evals" / "scoreboard_v1"
    if not (bench_dir / "matches.jsonl").exists():
        pytest.skip("scoreboard_v1 artifacts still building")

    out_json = tmp_path / "cli_sb_cal.json"
    rc = cli_mod.main(
        [
            "eval",
            "scoreboard",
            "--split",
            "calibration",
            "--output",
            str(out_json),
        ]
    )
    assert rc == 0
    assert out_json.is_file()

    # Read-only /api/evals/scoreboard endpoint + 409 holdout contamination guard
    client = TestClient(
        create_app(
            Settings(
                environment="test",
                allow_local_dev_bypass=True,
                runtime_dir=tmp_path / "runtime",
            )
        )
    )
    r_get = client.get("/api/evals/scoreboard?split=heldout")
    assert r_get.status_code == 200
    assert r_get.json()["benchmark_id"] == "rollmetrics-scoreboard-v1"
    assert r_get.json()["split"] == "heldout"

    # Note: isolate a copy of knowledge_base in tmp_path so taint test never touches repo knowledge_base
    tmp_kb = tmp_path / "kb"
    shutil.copytree(REPO_ROOT / "knowledge_base" / "evals" / "scoreboard_v1", tmp_kb / "evals" / "scoreboard_v1")
    isolated_client = TestClient(
        create_app(
            Settings(
                environment="test",
                allow_local_dev_bypass=True,
                knowledge_dir=tmp_kb,
                runtime_dir=tmp_path / "runtime_iso",
            )
        )
    )
    r_contaminate = isolated_client.post(
        "/api/evals/scoreboard/run",
        json={"split": "heldout", "tuning_mode": True, "run_id": "bad-api-tune"},
    )
    assert r_contaminate.status_code == 409


def test_osjj_roadmap_second_public_academy_tenant_isolation(tmp_path: Path) -> None:
    """Proves osjj-roadmap queries return global IBJJF + OSJJ/Roadmap and NEVER CTA private notes."""
    from rollmetrics.config import Settings
    from rollmetrics.knowledge.academies import add_academy_reference
    from rollmetrics.knowledge.osjj_roadmap import OSJJ_ACADEMY_ID, build_osjj_roadmap_corpus
    from rollmetrics.knowledge.retriever import KnowledgeRetriever

    osjj_docs, meta = build_osjj_roadmap_corpus()
    assert len(osjj_docs) >= 20
    assert meta["academy_id"] == OSJJ_ACADEMY_ID
    assert all(d.academy_id == OSJJ_ACADEMY_ID for d in osjj_docs)

    settings = Settings(
        profile="local",
        gemini_api_key="",
        knowledge_dir=REPO_ROOT / "knowledge_base",
        runtime_dir=tmp_path / "runtime",
    )
    # Seed a private CTA note and ensure osjj-roadmap queries never see it
    add_academy_reference(
        settings,
        academy_id="cta",
        title="CTA Private Secret Camp Drill",
        text="Secret CTA-only shoulder clamp detail never visible to other academies.",
        position="closed_guard",
    )

    retriever = KnowledgeRetriever(
        knowledge_dir=REPO_ROOT / "knowledge_base",
        version="v0.1.0",
        settings=settings,
    )

    osjj_hits = retriever.search(
        query="closed guard scissor sweep and posture break",
        corpus="all",
        top_k=10,
        academy_id=OSJJ_ACADEMY_ID,
    )
    assert len(osjj_hits) >= 3
    assert any(h.academy_id == OSJJ_ACADEMY_ID for h in osjj_hits)
    assert all(h.academy_id in ("global", OSJJ_ACADEMY_ID) for h in osjj_hits)
    assert not any(h.academy_id == "cta" for h in osjj_hits)

    # Confirm private CTA secret note is strictly invisible to osjj-roadmap
    secret_probe = retriever.search(
        query="CTA Private Secret Camp Drill shoulder clamp",
        corpus="all",
        top_k=10,
        academy_id=OSJJ_ACADEMY_ID,
    )
    assert all(h.academy_id in ("global", OSJJ_ACADEMY_ID) for h in secret_probe)
    assert not any("Secret CTA-only" in h.text for h in secret_probe)

    # Confirm global IBJJF rules remain accessible to osjj-roadmap users
    rule_hits = retriever.search(
        query="IBJJF knee reaping Art. 6.2.3",
        corpus="ibjjf",
        top_k=3,
        academy_id=OSJJ_ACADEMY_ID,
    )
    assert len(rule_hits) >= 1
    assert all(h.academy_id == "global" for h in rule_hits)
