"""Unit tests for scripts/run_nightly_canary.py (Layer 3 Nightly Canary)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from rollmetrics.config import Settings
from rollmetrics.providers.base import FrameObservation, VideoVisionResult, VisionProvider
from rollmetrics.rules.schema import EventKind, MatchDivisionConfig, RawObservedEvent

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_nightly_canary.py"


def _load_canary_module() -> Any:
    spec = importlib.util.spec_from_file_location("run_nightly_canary", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_nightly_canary"] = mod
    spec.loader.exec_module(mod)
    return mod


class _MockCanaryProvider(VisionProvider):
    profile_name = "cloud"

    def __init__(self) -> None:
        self.calls = 0

    async def analyze_live_frame(
        self,
        jpeg_bytes: bytes,
        division: MatchDivisionConfig,
        elapsed_sec: float = 0.0,
        current_position: str = "Standing",
        audio_transcript: str = "",
    ) -> FrameObservation:
        del division, current_position, audio_transcript
        assert jpeg_bytes.startswith(b"\xff\xd8")
        self.calls += 1
        if self.calls == 1:
            return FrameObservation(
                elapsed_sec=elapsed_sec,
                current_position="Open Guard",
                top_or_dominant_actor="A",
                stabilizing_scoring_kind=None,
                corner_cue="Control the sleeves and establish top base.",
                model_used="gemma-4-26b-a4b-it",
            )
        return FrameObservation(
            elapsed_sec=elapsed_sec,
            current_position="Side Control",
            top_or_dominant_actor="A",
            stabilizing_scoring_kind=EventKind.GUARD_PASS,
            corner_cue="Hold heavy chest pressure for 3 seconds.",
            model_used="gemma-4-26b-a4b-it",
        )

    async def analyze_video(
        self,
        video_path: Path,
        division: MatchDivisionConfig,
        sample_fps: float = 0.5,
        burst_frames: list[tuple[float, bytes]] | None = None,
    ) -> VideoVisionResult:
        del video_path, division, sample_fps, burst_frames
        return VideoVisionResult(model_used="gemma-4-26b-a4b-it")

    async def adjudicate_dispute_or_synthesize(
        self,
        events: list[RawObservedEvent],
        division: MatchDivisionConfig,
        ambiguous_indices: list[int] | None = None,
    ) -> dict[str, Any]:
        del events, division, ambiguous_indices
        return {"rulings": [], "cta_synthesis": []}


def test_nightly_canary_mocked_live_sim_and_step_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_canary_module()
    video = REPO_ROOT / "frontend" / "public" / "landing-preview.mp4"
    assert video.is_file()

    # 1. Skipped cleanly when GEMINI_API_KEY is unset; fails if require_api_key=True
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    skipped = asyncio.run(
        mod.run_live_sim_canary(
            video_path=video,
            settings=Settings(gemini_api_key=""),
            require_api_key=False,
        )
    )
    assert skipped["status"] == "skipped"
    assert skipped["ok"] is True

    required_fail = asyncio.run(
        mod.run_live_sim_canary(
            video_path=video,
            settings=Settings(gemini_api_key=""),
            require_api_key=True,
        )
    )
    assert required_fail["status"] == "failed"
    assert required_fail["ok"] is False

    # 2. Full 3-frame mocked cloud LiveRefereeSession run populates timeline >= 1
    mock_provider = _MockCanaryProvider()
    live_res = asyncio.run(
        mod.run_live_sim_canary(
            video_path=video,
            settings=Settings(gemini_api_key="mock-key"),
            provider=mock_provider,
        )
    )
    assert live_res["ok"] is True
    assert live_res["status"] == "passed"
    assert live_res["frames_processed"] == 3
    assert live_res["timeline_events"] >= 1
    assert live_res["model_used"] == "gemma-4-26b-a4b-it"

    # 3. Markdown step summary + CLI main() with mocked KB eval and offline settings (no live API)
    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    monkeypatch.setattr(mod, "get_settings", lambda: Settings(gemini_api_key=""))
    monkeypatch.setattr(
        mod,
        "run_kb_canary",
        lambda version="v0.1.0": {"status": "passed", "ok": True, "version": version, "manifest_verified": True},
    )
    assert mod.main(["--video", str(video), "--json"]) == 0
    md = summary_file.read_text(encoding="utf-8")
    assert "Nightly Live-Sim & Knowledge Base Canary" in md
    assert "Knowledge Base `v0.1.0` Eval" in md


def test_nightly_canary_kb_eval_v010_real() -> None:
    mod = _load_canary_module()
    kb_res = mod.run_kb_canary("v0.1.0")
    assert kb_res["ok"] is True
    assert kb_res["manifest_verified"] is True
    assert json.dumps(kb_res)
