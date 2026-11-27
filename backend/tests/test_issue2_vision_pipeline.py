"""Tests for GitHub Issue #2 vision pipeline: temporal smoother, HSV A/B identity lock, and ViCoS adapter."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from rollmetrics.evals.vicos_adapter import (
    VICOS_CLASS_TO_ROLLMETRICS,
    VICOS_LICENSE_NOTICE,
    evaluate_vicos_predictions,
    load_vicos_directory,
    map_vicos_label,
)
from rollmetrics.rules.schema import AthleteId, EventKind, RawObservedEvent
from rollmetrics.rules.temporal_smoother import (
    FramePositionSample,
    TemporalPositionSmoother,
    smooth_scoring_events,
)
from rollmetrics.video.tracking import extract_hsv_signature, lock_athlete_identities


def test_temporal_position_smoother_hysteresis_and_scoring_event_smoothing() -> None:
    smoother = TemporalPositionSmoother(window_size=3, max_dropout_sec=1.0, min_hold_sec=3.0)
    assert smoother.smooth_samples([]) == []
    assert smoother.extract_segments([]) == []

    # 1. Single-frame scramble flicker inside a 4.0s Mount hold is bridged cleanly
    samples = [
        FramePositionSample(timestamp_sec=0.0, state="standing", top_athlete="neutral"),
        FramePositionSample(timestamp_sec=1.0, state="standing", top_athlete="neutral"),
        FramePositionSample(timestamp_sec=2.0, state="mount", top_athlete="A"),
        FramePositionSample(timestamp_sec=3.0, state="mount", top_athlete="A"),
        FramePositionSample(timestamp_sec=3.5, state="half_guard", top_athlete="A"),  # 0.5s flicker!
        FramePositionSample(timestamp_sec=4.0, state="mount", top_athlete="A"),
        FramePositionSample(timestamp_sec=5.0, state="mount", top_athlete="A"),
        FramePositionSample(timestamp_sec=6.0, state="mount", top_athlete="A"),
    ]
    segs = smoother.extract_segments(samples)
    mount_segs = [s for s in segs if s.state == "mount"]
    assert len(mount_segs) == 1
    assert mount_segs[0].duration_sec >= 3.0
    assert mount_segs[0].stabilized_3s is True

    # 2. Sub-1.0s dropout sandwiched by identical states bridges even with window_size=1
    bridge_smoother = TemporalPositionSmoother(window_size=1, max_dropout_sec=1.0, min_hold_sec=3.0)
    dropout_samples = [
        FramePositionSample(timestamp_sec=10.0, state="side_control", top_athlete="B"),
        FramePositionSample(timestamp_sec=10.4, state="open_guard", top_athlete="B"),
        FramePositionSample(timestamp_sec=10.8, state="side_control", top_athlete="B"),
        FramePositionSample(timestamp_sec=13.5, state="side_control", top_athlete="B"),
    ]
    b_segs = bridge_smoother.extract_segments(dropout_samples)
    assert len(b_segs) == 1 and b_segs[0].state == "side_control" and b_segs[0].stabilized_3s is True

    # 3. smooth_scoring_events merges flicker-split holds & clamps occluded short noise
    assert smooth_scoring_events([]) == []
    raw_evs = [
        RawObservedEvent(timestamp_sec=20.0, actor=AthleteId.ATHLETE_A, kind=EventKind.GUARD_PASS, hold_duration_sec=1.8, confidence=0.80),
        RawObservedEvent(timestamp_sec=21.5, actor=AthleteId.ATHLETE_A, kind=EventKind.GUARD_PASS, hold_duration_sec=2.0, confidence=0.92),
        RawObservedEvent(timestamp_sec=40.0, actor=AthleteId.ATHLETE_B, kind=EventKind.SWEEP, hold_duration_sec=1.5, occluded=True),
    ]
    smoothed = smooth_scoring_events(raw_evs, merge_window_sec=2.0, min_hold_sec=3.0)
    assert len(smoothed) == 2
    assert smoothed[0].hold_duration_sec >= 3.0
    assert smoothed[0].confidence == 0.92
    assert smoothed[1].hold_duration_sec <= 0.5


def test_athlete_hsv_identity_locking_prevents_scramble_swaps() -> None:
    # Empty guard
    assert lock_athlete_identities([], [])["frames_tracked"] == 0
    assert extract_hsv_signature(np.zeros((0, 0, 3), dtype=np.uint8))[0] == 1.0

    # Synthetic 3-frame bout: Athlete A wears bright BLUE (left), Athlete B wears bright WHITE/YELLOW (right),
    # then they cross sides in frame 2 (tight scramble) and swap screen positions cleanly!
    def _make_frame(box_blue: tuple[int, int, int, int], box_white: tuple[int, int, int, int]) -> np.ndarray:
        img = np.zeros((100, 200, 3), dtype=np.uint8) + 20
        img[box_blue[1] : box_blue[3], box_blue[0] : box_blue[2]] = (20, 60, 230)
        img[box_white[1] : box_white[3], box_white[0] : box_white[2]] = (240, 240, 230)
        return img

    f0 = _make_frame((20, 20, 70, 80), (130, 20, 180, 80))
    f1 = _make_frame((60, 20, 110, 80), (95, 20, 145, 80))
    f2 = _make_frame((130, 20, 180, 80), (20, 20, 70, 80))  # Blue moved to RIGHT, White moved to LEFT!

    dets = [
        [(0.10, 0.20, 0.35, 0.80), (0.65, 0.20, 0.90, 0.80)],
        [(0.30, 0.20, 0.55, 0.80), (0.475, 0.20, 0.725, 0.80)],
        [(0.10, 0.20, 0.35, 0.80), (0.65, 0.20, 0.90, 0.80)],  # Detection 0 is LEFT (White=B), 1 is RIGHT (Blue=A)!
    ]
    res = lock_athlete_identities([f0, f1, f2], dets)
    assert res["frames_tracked"] == 3
    tracks = res["tracks"]
    assert isinstance(tracks, list) and len(tracks) == 3
    # In frame 2, Athlete A (Blue) must stay locked to RIGHT box (centroid x ~ 0.775), NOT swap to LEFT!
    f2_a = tracks[2]["A"]
    f2_b = tracks[2]["B"]
    assert f2_a["centroid"][0] > 0.65
    assert f2_b["centroid"][0] < 0.35

    # Missing detection frame triggers swap_risk diagnostic cleanly
    risk_res = lock_athlete_identities([f0], [[(0.1, 0.2, 0.3, 0.8)]])
    assert risk_res["swap_risk_frames"] == [0]
    assert risk_res["swap_risk_rate"] == 1.0


def test_vicos_research_only_adapter_mapping_coco_and_jsonl(tmp_path: Path) -> None:
    assert "CC BY-NC-SA 4.0" in VICOS_LICENSE_NOTICE
    assert len(VICOS_CLASS_TO_ROLLMETRICS) == 18
    assert map_vicos_label("mount_top") == ("mount", "A")
    assert map_vicos_label("knee_on_belly_bottom") == ("side_control", "B")
    assert map_vicos_label("unknown_scramble") == ("standing", "neutral")

    with pytest.raises(FileNotFoundError):
        load_vicos_directory(tmp_path / "does_not_exist")

    # 1. JSONL format external directory
    d_jsonl = tmp_path / "vicos_jsonl"
    d_jsonl.mkdir()
    (d_jsonl / "annotations.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"sample_id": "s1", "image_path": "f1.jpg", "label": "mount_top"}),
                json.dumps({"sample_id": "s2", "image_path": "f2.jpg", "label": "closed_guard_bottom"}),
                json.dumps({"sample_id": "s3", "image_path": "f3.jpg", "label": "back_control_top"}),
            ]
        ),
        encoding="utf-8",
    )
    samples = load_vicos_directory(d_jsonl)
    assert len(samples) == 3
    eval_res = evaluate_vicos_predictions(
        samples,
        {"s1": "mount_top", "s2": "closed_guard_bottom", "s3": "side_control_top"},
    )
    assert eval_res["non_commercial_only"] is True
    assert eval_res["num_samples"] == 3
    assert eval_res["canonical_accuracy"] == pytest.approx(2 / 3, abs=0.01)
    assert eval_res["top_bottom_accuracy"] == 1.0
    assert evaluate_vicos_predictions([], {})["num_samples"] == 0

    # 2. COCO-style annotations.json format external directory
    d_coco = tmp_path / "vicos_coco"
    d_coco.mkdir()
    (d_coco / "annotations.json").write_text(
        json.dumps(
            {
                "categories": [{"id": 1, "name": "half_guard_top"}, {"id": 2, "name": "turtle_bottom"}],
                "images": [{"id": 10, "file_name": "img10.jpg"}, {"id": 11, "file_name": "img11.jpg"}],
                "annotations": [
                    {"id": 100, "image_id": 10, "category_id": 1},
                    {"id": 101, "image_id": 11, "category_id": 2},
                ],
            }
        ),
        encoding="utf-8",
    )
    coco_samples = load_vicos_directory(d_coco)
    assert len(coco_samples) == 2
    assert coco_samples[0].canonical_state == "half_guard"
    assert coco_samples[1].canonical_state == "turtle"
