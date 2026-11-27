"""Comprehensive unit tests for scoreboard_bench/labeler.py (100% offline/mocked, >=93% line coverage)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from rollmetrics.evals.scoreboard_bench import labeler as lbl_mod
from rollmetrics.evals.scoreboard_bench.schema import (
    CompactScore,
    GroundTruthMatchLabel,
    MatchManifestEntry,
)


def _sample_entry(
    *,
    preset: str = "flograppling_lower_bar",
    uniform: str = "gi",
) -> MatchManifestEntry:
    return MatchManifestEntry(
        match_id="cov-test-m1",
        video_id="vid_cov_001",
        url="https://www.youtube.com/watch?v=vid_cov_001",
        start_sec=10.0,
        end_sec=220.0,
        athlete_a="Athlete Left",
        athlete_b="Athlete Right",
        athlete_a_appearance="Blue Gi" if uniform == "gi" else "Dark Rashguard",
        athlete_b_appearance="White Gi" if uniform == "gi" else "White Rashguard",
        division="Adult Middleweight",
        belt="black",
        uniform=uniform,  # type: ignore[arg-type]
        overlay_preset=preset,
    )


def test_clock_to_seconds_and_cap_color_and_digit_crop() -> None:
    assert lbl_mod._clock_to_seconds("10:00") == 600
    assert lbl_mod._clock_to_seconds("04.07") == 247
    assert lbl_mod._clock_to_seconds("0:45") == 45
    assert lbl_mod._clock_to_seconds("") is None
    assert lbl_mod._clock_to_seconds("no-colon") is None
    assert lbl_mod._clock_to_seconds("1:2:3") is None
    assert lbl_mod._clock_to_seconds("25:00") is None
    assert lbl_mod._clock_to_seconds("05:65") is None
    assert lbl_mod._clock_to_seconds("ab:cd") is None

    # Synthetic scoreboard crop: blue left cap, white right cap
    sb_blue_white = Image.new("RGB", (400, 100), (20, 20, 20))
    for y in range(55, 83):
        for x in range(19, 27):
            sb_blue_white.putpixel((x, y), (20, 50, 210))
        for x in range(373, 382):
            sb_blue_white.putpixel((x, y), (220, 220, 220))
    assert lbl_mod._detect_flograppling_cap_colors(sb_blue_white, "gi") == ("Blue Gi", "White Gi")
    assert lbl_mod._detect_flograppling_cap_colors(sb_blue_white, "nogi") == (
        "Blue/Dark Rashguard (Blue Side)",
        "White/Dark Rashguard (White Side)",
    )

    # Black Gi vs Black Gi (dark caps)
    sb_dark = Image.new("RGB", (400, 100), (25, 25, 25))
    assert lbl_mod._detect_flograppling_cap_colors(sb_dark, "gi") == ("Black Gi", "Black Gi")

    # Digit strip crop for both presets
    frame = Image.new("RGB", (854, 480), (40, 40, 40))
    sc_strip = lbl_mod._crop_digits_strip(frame, "smoothcomp_lower_bar")
    flo_strip = lbl_mod._crop_digits_strip(frame, "flograppling_lower_bar")
    assert sc_strip.size == (948, 208)
    assert flo_strip.size == (948, 208)


def test_parse_ocr_tokens_both_presets_and_edge_cases() -> None:
    # Missing clock returns None
    assert lbl_mod._parse_ocr_tokens_to_reading([{"text": "2", "x": 0.25, "y": 0.2}], "flograppling_lower_bar") is None

    # Smoothcomp tokens: tall pts (h>=0.27), upper adv (y>=0.40), lower pen (y<0.40), ignore >36 & non-digit
    sc_tokens = [
        {"text": "04:12", "x": 0.56, "y": 0.25, "h": 0.38},
        {"text": "18", "x": 0.12, "y": 0.25, "h": 0.36},
        {"text": "2", "x": 0.05, "y": 0.55, "h": 0.18},
        {"text": "1", "x": 0.05, "y": 0.20, "h": 0.18},
        {"text": "4", "x": 0.80, "y": 0.25, "h": 0.36},
        {"text": "3", "x": 0.91, "y": 0.55, "h": 0.18},
        {"text": "5", "x": 0.91, "y": 0.20, "h": 0.18},  # clamped to 4
        {"text": "99", "x": 0.15, "y": 0.25, "h": 0.35},  # >36 ignored
        {"text": "SMOOTHCOMP", "x": 0.30, "y": 0.40, "h": 0.20},
    ]
    parsed_sc = lbl_mod._parse_ocr_tokens_to_reading(sc_tokens, "smoothcomp_lower_bar")
    assert parsed_sc == {
        "clock_str": "04:12",
        "pts_a": 18,
        "adv_a": 2,
        "pen_a": 1,
        "pts_b": 4,
        "adv_b": 3,
        "pen_b": 4,
    }

    # FloGrappling tokens
    flo_tokens = [
        {"text": "8:30", "x": 0.47, "y": 0.22, "h": 0.35},
        {"text": "1", "x": 0.08, "y": 0.22, "h": 0.20},
        {"text": "2", "x": 0.16, "y": 0.22, "h": 0.20},
        {"text": "4", "x": 0.28, "y": 0.22, "h": 0.35},
        {"text": "2", "x": 0.65, "y": 0.22, "h": 0.35},
        {"text": "3", "x": 0.78, "y": 0.22, "h": 0.20},
        {"text": "1", "x": 0.89, "y": 0.22, "h": 0.20},
    ]
    parsed_flo = lbl_mod._parse_ocr_tokens_to_reading(flo_tokens, "flograppling_lower_bar")
    assert parsed_flo == {
        "clock_str": "8:30",
        "pts_a": 4,
        "adv_a": 1,
        "pen_a": 2,
        "pts_b": 2,
        "adv_b": 3,
        "pen_b": 1,
    }


def test_get_macos_vision_ocr_bin_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lbl_mod, "_CACHED_VISION_BIN", None)
    monkeypatch.setattr(lbl_mod.platform, "system", lambda: "Linux")
    assert lbl_mod._get_macos_vision_ocr_bin() is None

    monkeypatch.setattr(lbl_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(lbl_mod, "_find_binary", lambda _name: None)
    assert lbl_mod._get_macos_vision_ocr_bin() is None

    monkeypatch.setattr(lbl_mod, "_find_binary", lambda _name: "/usr/bin/swiftc")
    monkeypatch.setattr(lbl_mod.tempfile, "gettempdir", lambda: str(tmp_path))

    def _fake_compile(cmd: list[str], **_kw: Any) -> SimpleNamespace:
        out_bin = Path(cmd[cmd.index("-o") + 1])
        out_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lbl_mod.subprocess, "run", _fake_compile)
    first = lbl_mod._get_macos_vision_ocr_bin()
    assert first is not None and first.exists()
    # Second call hits cached _CACHED_VISION_BIN immediately
    assert lbl_mod._get_macos_vision_ocr_bin() == first
    monkeypatch.setattr(lbl_mod, "_CACHED_VISION_BIN", None)


def test_derive_events_from_readings_all_outcome_branches() -> None:
    entry = _sample_entry(uniform="gi")

    # 1. DQ via 4 penalties on A -> winner B
    dq_lbl = lbl_mod.derive_events_from_readings(
        entry,
        {
            "scoreboard_first_seen_sec": 0.0,
            "scoreboard_last_seen_sec": 2.0,  # triggers active_end <= active_start + 5.0 fallback
            "left_athlete_bar_color": "Blue",
            "right_athlete_bar_color": "White",
            "final_clock_str": "00:00",
            "winner_side": "tie",
            "ended_before_time_expired": False,
            "readings": [
                {"rel_sec": 1.0, "clock_str": "04:00", "pts_a": 0, "adv_a": 0, "pen_a": 4, "pts_b": 0, "adv_b": 0, "pen_b": 0}
            ],
        },
    )
    assert dq_lbl.winner == "B" and dq_lbl.method == "dq"
    assert dq_lbl.athlete_a_appearance == "Blue Gi" and dq_lbl.athlete_b_appearance == "White Gi"
    assert dq_lbl.active_bout_start_sec == entry.start_sec

    # 2. Points winner B
    pts_lbl = lbl_mod.derive_events_from_readings(
        entry,
        {
            "scoreboard_first_seen_sec": 5.0,
            "scoreboard_last_seen_sec": 120.0,
            "final_clock_str": "00:00",
            "winner_side": "tie",
            "ended_before_time_expired": False,
            "readings": [
                {"rel_sec": 40.0, "clock_str": "03:00", "pts_a": 0, "adv_a": 0, "pen_a": 0, "pts_b": 2, "adv_b": 0, "pen_b": 0}
            ],
        },
    )
    assert pts_lbl.winner == "B" and pts_lbl.method == "points"

    # 3. Advantages tie-breaker
    adv_lbl = lbl_mod.derive_events_from_readings(
        entry,
        {
            "scoreboard_first_seen_sec": 5.0,
            "scoreboard_last_seen_sec": 120.0,
            "final_clock_str": "00:00",
            "winner_side": "tie",
            "ended_before_time_expired": False,
            "readings": [
                {"rel_sec": 60.0, "clock_str": "01:00", "pts_a": 2, "adv_a": 1, "pen_a": 0, "pts_b": 2, "adv_b": 0, "pen_b": 0}
            ],
        },
    )
    assert adv_lbl.winner == "A" and adv_lbl.method == "advantages"

    # 4. Penalties tie-breaker (fewer penalties wins)
    pen_lbl = lbl_mod.derive_events_from_readings(
        entry,
        {
            "scoreboard_first_seen_sec": 5.0,
            "scoreboard_last_seen_sec": 120.0,
            "final_clock_str": "00:00",
            "winner_side": "tie",
            "ended_before_time_expired": False,
            "readings": [
                {"rel_sec": 60.0, "clock_str": "01:00", "pts_a": 0, "adv_a": 0, "pen_a": 1, "pts_b": 0, "adv_b": 0, "pen_b": 0}
            ],
        },
    )
    assert pen_lbl.winner == "B" and pen_lbl.method == "penalties"

    # 5. Tied 0-0 with explicit winner_side at 00:00 -> referee_decision with winner A
    dec_a = lbl_mod.derive_events_from_readings(
        entry,
        {
            "scoreboard_first_seen_sec": 5.0,
            "scoreboard_last_seen_sec": 120.0,
            "final_clock_str": "00:00",
            "winner_side": "A",
            "ended_before_time_expired": False,
            "readings": [],
        },
    )
    assert dec_a.winner == "A" and dec_a.method == "referee_decision"

    # 6. Tied 0-0 with tie winner_side -> referee_decision
    dec_tie = lbl_mod.derive_events_from_readings(
        entry,
        {
            "scoreboard_first_seen_sec": 5.0,
            "scoreboard_last_seen_sec": 120.0,
            "final_clock_str": "00:00",
            "winner_side": "tie",
            "ended_before_time_expired": False,
            "readings": [],
        },
    )
    assert dec_tie.winner == "referee_decision" and dec_tie.method == "referee_decision"


def test_local_ocr_and_cloud_crop_labeler_mocked_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entry = _sample_entry()
    fake_video = tmp_path / "match.mp4"
    fake_video.write_bytes(b"fake-mp4")

    # Missing ffmpeg/ocr_bin raises cleanly
    monkeypatch.setattr(lbl_mod, "_find_binary", lambda _n: None)
    with pytest.raises(RuntimeError):
        lbl_mod.label_match_from_scoreboard_crops_local_ocr(entry, fake_video)
    with pytest.raises(RuntimeError):
        lbl_mod.extract_scoreboard_strip_sheets(fake_video, 0.0, 20.0)

    # Mock ffmpeg frame extraction writing 4 synthetic frames
    monkeypatch.setattr(lbl_mod, "_find_binary", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(lbl_mod, "_get_macos_vision_ocr_bin", lambda: tmp_path / "vision_ocr_v1")

    def _fake_ffmpeg(cmd: list[str], **_kw: Any) -> SimpleNamespace:
        pattern = cmd[-1]
        out_dir = Path(pattern).parent
        prefix = "raw_" if "raw_" in pattern else "f_"
        for i in range(1, 5):
            Image.new("RGB", (640, 360), (20, 40, 60)).save(out_dir / f"{prefix}{i:04d}.jpg")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lbl_mod.subprocess, "run", _fake_ffmpeg)

    # Case A: Empty OCR tokens -> fallback with winner hint
    monkeypatch.setattr(
        lbl_mod.subprocess,
        "check_output",
        lambda cmd, **_kw: json.dumps([{"path": p, "tokens": []} for p in cmd[1:]]),
    )
    empty_lbl = lbl_mod.label_match_from_scoreboard_crops_local_ocr(
        entry,
        fake_video,
        winner_hint_if_early_stoppage="A",
    )
    assert empty_lbl.winner == "A" and empty_lbl.method == "submission"

    # Case B: Valid 2-frame persistent score jump + WINNER banner on Right (B)
    def _fake_vision_ocr(cmd: list[str], **_kw: Any) -> str:
        paths = cmd[1:]
        out = []
        for idx, p in enumerate(paths):
            if Path(p).name.startswith("sb_"):
                out.append({"path": p, "tokens": [{"text": "WINNER", "x": 0.75, "y": 0.5, "h": 0.2}]})
            else:
                pts_b = 0 if idx == 0 else 2
                # Inject 1-frame spike on pts_a=5 at idx==1 that does NOT persist at idx==2
                toks = [
                    {"text": f"04:{50 - idx * 2:02d}", "x": 0.47, "y": 0.22, "h": 0.35},
                    {"text": str(pts_b), "x": 0.65, "y": 0.22, "h": 0.35},
                ]
                if idx == 1:
                    toks.append({"text": "5", "x": 0.28, "y": 0.22, "h": 0.35})
                out.append({"path": p, "tokens": toks})
        return json.dumps(out)

    monkeypatch.setattr(lbl_mod.subprocess, "check_output", _fake_vision_ocr)
    local_lbl = lbl_mod.label_match_from_scoreboard_crops(
        entry=entry,
        video_path=fake_video,
        prefer_local_ocr=True,
    )
    assert local_lbl.final_score_a == CompactScore(points=0, advantages=0, penalties=0)
    assert local_lbl.final_score_b == CompactScore(points=2, advantages=0, penalties=0)
    assert local_lbl.winner == "B" and local_lbl.method == "submission"

    # Case C: extract_scoreboard_strip_sheets + Cloud Gemini crop OCR path (both Gemma and Gemini)
    sheets = lbl_mod.extract_scoreboard_strip_sheets(fake_video, 10.0, 30.0, sample_every_sec=4.0)
    assert len(sheets) == 1 and len(sheets[0]) > 500

    with pytest.raises(RuntimeError):
        lbl_mod.label_match_from_scoreboard_crops(entry, fake_video, genai_client=None, prefer_local_ocr=False)

    class _FakeInteractions:
        def create(self, **kwargs: Any) -> SimpleNamespace:
            self.last_kwargs = kwargs
            return SimpleNamespace(
                output_text=json.dumps(
                    {
                        "scoreboard_first_seen_sec": 0.0,
                        "scoreboard_last_seen_sec": 40.0,
                        "final_clock_str": "00:00",
                        "winner_side": "A",
                        "ended_before_time_expired": False,
                        "readings": [
                            {"rel_sec": 12.0, "clock_str": "03:48", "pts_a": 3, "adv_a": 0, "pen_a": 0, "pts_b": 0, "adv_b": 0, "pen_b": 0}
                        ],
                    }
                )
            )

    fake_client = SimpleNamespace(interactions=_FakeInteractions())
    cloud_lbl = lbl_mod.label_match_from_scoreboard_crops(
        entry=entry,
        video_path=fake_video,
        genai_client=fake_client,
        ocr_model_id="gemini-4-hydrogen",
        prefer_local_ocr=False,
    )
    assert cloud_lbl.labeler_id == "scoreboard_crop_ocr_v1:gemini-4-hydrogen"
    assert cloud_lbl.final_score_a.points == 3 and cloud_lbl.winner == "A"
    assert fake_client.interactions.last_kwargs.get("generation_config") == {"thinking_level": "low"}

    # Spot check skips unmatched IDs cleanly
    other_hv = GroundTruthMatchLabel(
        match_id="missing-id",
        video_id="v_missing",
        active_bout_start_sec=0.0,
        active_bout_end_sec=60.0,
        winner="A",
        method="points",
    )
    spot = lbl_mod.compute_spot_check_report([cloud_lbl], [cloud_lbl, other_hv])
    assert spot["spot_checked_matches"] == 1
    assert spot["labeler_winner_accuracy"] == 1.0
