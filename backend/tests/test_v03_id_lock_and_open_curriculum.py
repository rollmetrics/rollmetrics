"""Tests for v0.3 Athlete Tap-to-Identify ID-Lock % eval and video-backed OSJJ open curriculum."""

from __future__ import annotations

import re

import numpy as np
from rollmetrics.evals.id_lock_eval import (
    AthleteBox,
    IdLockCase,
    TapAnchor,
    bind_tap_anchor_to_box,
    box_iou,
    default_id_lock_benchmark_cases,
    evaluate_athlete_id_lock,
    evaluate_id_lock_from_frames,
    match_predicted_identity,
)
from rollmetrics.knowledge.osjj_roadmap import (
    OSJJ_ACADEMY_ID,
    build_osjj_roadmap_corpus,
)
from rollmetrics.knowledge.retriever import KnowledgeRetriever

YOUTUBE_TIMESTAMP_RE = re.compile(r"^https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}&t=\d+s$")


def test_osjj_roadmap_corpus_video_backed_and_canonical_positions():
    docs, meta = build_osjj_roadmap_corpus()
    assert len(docs) == 24
    assert meta["academy_id"] == OSJJ_ACADEMY_ID
    assert meta["total_osjj_roadmap_docs"] == 24
    assert meta["video_backed_docs"] == 24

    expected_positions = {
        "all",
        "closed_guard",
        "half_guard",
        "side_control",
        "mount",
        "back_control",
        "turtle",
        "butterfly_guard",
        "open_guard",
        "standing_pass",
        "takedowns",
    }
    seen_positions = {d.position for d in docs}
    assert expected_positions.issubset(seen_positions)

    for doc in docs:
        assert doc.academy_id == OSJJ_ACADEMY_ID
        assert doc.timestamp_sec is not None and doc.timestamp_sec >= 0
        assert YOUTUBE_TIMESTAMP_RE.match(doc.source_url), f"Invalid URL: {doc.source_url}"
        assert doc.metadata["coaching_cue"]
        assert doc.metadata["video_id"]
        assert doc.metadata["source_kind"] == "public_academy_curriculum"


def test_osjj_roadmap_v010_index_retrieval_and_tenant_isolation():
    retriever = KnowledgeRetriever(version="v0.1.0")
    osjj_hits = retriever.recommend_cta_for_position(
        position="side_control",
        situation="escaping bottom side control with frames and underhook",
        belt="white",
        top_k=3,
        academy_id=OSJJ_ACADEMY_ID,
    )
    assert len(osjj_hits) >= 2
    assert all(hit["metadata"].get("academy_id") == OSJJ_ACADEMY_ID for hit in osjj_hits)
    assert all(YOUTUBE_TIMESTAMP_RE.match(hit["source_url"]) for hit in osjj_hits)

    cta_hits = retriever.recommend_cta_for_position(
        position="closed_guard",
        situation="breaking posture and double ankle sweep",
        belt="white",
        top_k=3,
        academy_id="cta",
    )
    assert cta_hits
    assert all(hit["metadata"].get("academy_id", "cta") == "cta" for hit in cta_hits)


def test_box_iou_and_tap_anchor_binding():
    box_a = AthleteBox(athlete="A", x0=0.1, y0=0.2, x1=0.4, y1=0.8)
    box_b = AthleteBox(athlete="B", x0=0.6, y0=0.2, x1=0.9, y1=0.8)
    zero_box = AthleteBox(athlete="A", x0=0.5, y0=0.5, x1=0.5, y1=0.5)

    assert box_iou(box_a, box_a) == 1.0
    assert box_iou(box_a, box_b) == 0.0
    assert box_iou(zero_box, zero_box) == 0.0

    # Empty boxes returns None
    anchor_a = TapAnchor(x_norm=0.25, y_norm=0.50, athlete="A")
    assert bind_tap_anchor_to_box(anchor_a, []) is None

    # Tap inside box A binds to A
    assert bind_tap_anchor_to_box(anchor_a, [box_a, box_b]) == "A"

    # Tap slightly outside both boxes falls back to nearest center (B)
    near_b = TapAnchor(x_norm=0.95, y_norm=0.50, athlete="B")
    assert bind_tap_anchor_to_box(near_b, [box_a, box_b]) == "B"


def test_match_predicted_identity_edge_cases():
    gt = [
        AthleteBox(athlete="A", x0=0.1, y0=0.2, x1=0.4, y1=0.8),
        AthleteBox(athlete="B", x0=0.6, y0=0.2, x1=0.9, y1=0.8),
    ]
    # Missing target or empty predictions
    assert match_predicted_identity([gt[1]], gt, "A") is False
    assert match_predicted_identity(gt, [], "A") is False

    # Exact match passes
    assert match_predicted_identity(gt, gt, "A") is True

    # Swapped label fails
    swapped = [
        AthleteBox(athlete="B", x0=0.1, y0=0.2, x1=0.4, y1=0.8),
        AthleteBox(athlete="A", x0=0.6, y0=0.2, x1=0.9, y1=0.8),
    ]
    assert match_predicted_identity(gt, swapped, "A") is False

    # Low IoU fails
    disjoint = [AthleteBox(athlete="A", x0=0.85, y0=0.85, x1=0.95, y1=0.95)]
    assert match_predicted_identity(gt, disjoint, "A", min_iou=0.25) is False


def test_evaluate_athlete_id_lock_default_empty_and_swapped():
    # Empty suite
    empty_report = evaluate_athlete_id_lock(cases=[])
    assert empty_report.total_cases == 0
    assert empty_report.id_lock_pct == 0.0
    assert empty_report.swap_count == 0

    # Default benchmark suite passes 100%
    default_report = evaluate_athlete_id_lock()
    assert default_report.total_cases == 4
    assert default_report.id_lock_pct == 100.0
    assert default_report.t0_binding_pct == 100.0
    assert default_report.tmid_lock_pct == 100.0
    assert default_report.tend_lock_pct == 100.0
    assert default_report.matching_kit_id_lock_pct == 100.0
    assert default_report.nogi_id_lock_pct == 100.0
    assert default_report.swap_count == 0

    # Inject a mid-match A/B identity swap into case 0 (matching_kit=False, uniform="gi")
    cases = default_id_lock_benchmark_cases()
    c0 = cases[0]
    swapped_mid = [
        AthleteBox(athlete="B", x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
        if b.athlete == "A"
        else AthleteBox(athlete="A", x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
        for b in c0.pred_boxes_tmid
    ]
    cases[0] = IdLockCase(
        case_id=c0.case_id,
        uniform=c0.uniform,
        matching_kit=c0.matching_kit,
        anchor=c0.anchor,
        gt_boxes_t0=c0.gt_boxes_t0,
        gt_boxes_tmid=c0.gt_boxes_tmid,
        gt_boxes_tend=c0.gt_boxes_tend,
        pred_boxes_tmid=swapped_mid,
        pred_boxes_tend=c0.pred_boxes_tend,
    )
    report = evaluate_athlete_id_lock(cases=cases)
    assert report.swap_count == 1
    assert report.id_lock_pct == 75.0
    assert report.tmid_lock_pct == 75.0
    assert report.tend_lock_pct == 100.0

    # Subset with no matching_kit and no nogi exercises fallback 100.0 branches
    gi_only = evaluate_athlete_id_lock(cases=[default_id_lock_benchmark_cases()[0]])
    assert gi_only.matching_kit_id_lock_pct == 100.0
    assert gi_only.nogi_id_lock_pct == 100.0


def test_evaluate_id_lock_from_frames_direct_and_swapped():
    anchor_a = TapAnchor(x_norm=0.25, y_norm=0.50, athlete="A")
    anchor_b = TapAnchor(x_norm=0.75, y_norm=0.50, athlete="B")
    box_left = AthleteBox(athlete="A", x0=0.10, y0=0.20, x1=0.40, y1=0.80)
    box_right = AthleteBox(athlete="B", x0=0.60, y0=0.20, x1=0.90, y1=0.80)

    # Empty or mismatched lengths
    assert evaluate_id_lock_from_frames([], anchor_a, anchor_b, [])["frames_evaluated"] == 0
    dummy = np.zeros((60, 100, 3), dtype=np.uint8)
    assert evaluate_id_lock_from_frames([dummy], anchor_a, anchor_b, [])["frames_evaluated"] == 0

    # Frame 0: Left is bright blue (A), Right is bright red (B)
    f0 = np.zeros((60, 100, 3), dtype=np.uint8)
    f0[12:48, 10:40] = [20, 60, 230]   # Blue gi (A)
    f0[12:48, 60:90] = [230, 30, 30]   # Red rashguard (B)

    # Frame 1: Athletes cross sides, GT boxes track them accurately -> direct match
    f1 = np.zeros((60, 100, 3), dtype=np.uint8)
    f1[12:48, 60:90] = [20, 60, 230]   # Blue gi (A) moved right
    f1[12:48, 10:40] = [230, 30, 30]   # Red rashguard (B) moved left
    box_a_right = AthleteBox(athlete="A", x0=0.60, y0=0.20, x1=0.90, y1=0.80)
    box_b_left = AthleteBox(athlete="B", x0=0.10, y0=0.20, x1=0.40, y1=0.80)

    # Frame 2: Deliberately pass swapped GT boxes -> triggers swap branch
    res = evaluate_id_lock_from_frames(
        frames_rgb=[f0, f1, f1],
        anchor_a=anchor_a,
        anchor_b=anchor_b,
        gt_boxes_per_frame=[
            (box_left, box_right),
            (box_a_right, box_b_left),
            (box_left, box_right),  # wrong boxes for f1 -> swap
        ],
    )
    assert res["frames_evaluated"] == 3
    assert res["swaps"] == 1
    assert res["id_lock_pct"] == 66.67


def test_scoreboard_v1_labels_ibjjf_points_never_one():
    """Deterministic IBJJF OCR sanity gate: valid point totals are 0 or >= 2 (never 1)."""
    import json
    from pathlib import Path

    labels_path = (
        Path(__file__).resolve().parents[2]
        / "knowledge_base"
        / "evals"
        / "scoreboard_v1"
        / "labels.jsonl"
    )
    rows = [json.loads(ln) for ln in labels_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) == 122
    for row in rows:
        mid = row["match_id"]
        assert row["final_score_a"]["points"] != 1, f"{mid} has impossible A.points==1"
        assert row["final_score_b"]["points"] != 1, f"{mid} has impossible B.points==1"
        for ev in row.get("events", []):
            sa = ev.get("score_after", {})
            assert sa.get("pts_a", 0) != 1, f"{mid} has impossible event score_after.pts_a==1"
            assert sa.get("pts_b", 0) != 1, f"{mid} has impossible event score_after.pts_b==1"
