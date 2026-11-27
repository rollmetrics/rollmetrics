"""Focused behavioral branch-coverage tests for the 5 critical backend modules (Layer 5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from rollmetrics import auth as auth_mod
from rollmetrics.config import Settings
from rollmetrics.providers.base import FrameObservation
from rollmetrics.rules.adcc import ADCCRefereeEngine
from rollmetrics.rules.schema import AthleteId, BeltRank, EventKind, MatchDivisionConfig, RawObservedEvent
from rollmetrics.rules.state_machine import IBJJFRefereeEngine
from rollmetrics.storage import RuntimeStateStore
from rollmetrics.video.live_session import LiveRefereeSession


def test_auth_critical_branches(tmp_path: Path) -> None:
    # Non-bearer Authorization header falls back to query token (auth.py:22->24)
    assert auth_mod._extract_bearer_token("Basic abc123", query_token="q-tok") == "q-tok"
    assert auth_mod._extract_bearer_token("Bearer   ", query_token="") == ""

    # update_user_academy creates and updates users.json cleanly (auth.py:248-264)
    settings = Settings(runtime_dir=tmp_path / "rt_auth")
    updated = auth_mod.update_user_academy(
        settings=settings,
        email="athlete@example.com",
        academy_id="cta_hq",
        academy_name="CTA HQ",
    )
    assert updated["academy_id"] == "cta_hq"
    assert updated["academy_name"] == "CTA HQ"
    assert (settings.runtime_dir / "users.json").is_file()


def test_adcc_and_ibjjf_critical_branches() -> None:
    # ADCC finalize_regulation called when already ended (adcc.py:349->366)
    adcc = ADCCRefereeEngine(division=MatchDivisionConfig(ruleset="adcc", duration_sec=600.0))
    adcc.ingest_event(RawObservedEvent(timestamp_sec=100.0, actor=AthleteId.ATHLETE_A, kind=EventKind.SUBMISSION))
    assert adcc.ended is True
    snap_after = adcc.finalize_regulation()
    assert snap_after.winner == "A"

    # IBJJF position rescore cooldown deduplication within 6.0s (state_machine.py:251-267)
    ibjjf = IBJJFRefereeEngine(division=MatchDivisionConfig(belt=BeltRank.BLACK, duration_sec=600.0))
    ev1 = ibjjf.ingest_event(
        RawObservedEvent(timestamp_sec=40.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MOUNT, hold_duration_sec=3.5)
    )
    assert ev1.awarded_points == 4
    ev2 = ibjjf.ingest_event(
        RawObservedEvent(timestamp_sec=43.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MOUNT, hold_duration_sec=3.5)
    )
    assert ev2.awarded_points == 0
    assert "Deduplicated" in ev2.ruling_reason


def test_storage_critical_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        environment="cloud",
        gcs_bucket="example-bucket",
        runtime_dir=tmp_path / "rt_store",
    )
    store = RuntimeStateStore(settings=settings)
    monkeypatch.setattr(store, "_gcs_headers", lambda: {"Authorization": "Bearer tok"})

    # _gcs_get returning non-dict JSON (storage.py:113->115) & _gcs_list_objects non-200 (143->146)
    def _fake_get(url: str, **_kw: Any) -> httpx.Response:
        if "alt=media" in url:
            return httpx.Response(200, json=["not-a-dict"])
        return httpx.Response(500, json={"error": "boom"})

    monkeypatch.setattr(httpx, "get", _fake_get)
    assert store._gcs_get("state/users/a.json") is None
    assert store._gcs_list_objects("users") == []

    # Local file with non-dict / missing 'data' falls through to cloud check (storage.py:176->178)
    bad_local = store._local_path("users", "corrupt")
    bad_local.parent.mkdir(parents=True, exist_ok=True)
    bad_local.write_text('{"schema_version": 1, "data": "not-a-dict"}', encoding="utf-8")
    assert store.get_record("users", "corrupt") is None

    # list_records skips remote object whose _gcs_get returns None (storage.py:217->213)
    monkeypatch.setattr(store, "_gcs_list_objects", lambda _col: ["state/users/remote_bad.json"])
    monkeypatch.setattr(store, "_gcs_get", lambda _obj: None)
    assert store.list_records("users", hydrate_cloud=True) == []


@pytest.mark.asyncio
async def test_live_session_critical_branches() -> None:
    # 1. _apply_observation with empty current_position + _update_stabilization when ended
    class _EmptyPosProvider:
        profile_name = "local"

        async def analyze_live_frame(self, **_kw: Any) -> FrameObservation:
            return FrameObservation(
                current_position="",
                top_or_dominant_actor="none",
                stabilizing_scoring_kind=None,
                corner_cue="",
            )

    sess = LiveRefereeSession(
        division=MatchDivisionConfig(duration_sec=600.0),
        profile="local",
        retriever=False,
        provider=_EmptyPosProvider(),
        min_vision_interval_sec=0.0,
    )
    hud = await sess.process_frame(b"jpg", elapsed_sec=5.0)
    assert hud["scoreboard"]["current_position"] == "Standing"

    # Interrupt active stabilization before 0.8s -> no advantage awarded (live_session.py:212->225)
    sess._update_stabilization(AthleteId.ATHLETE_A, EventKind.SWEEP, elapsed_sec=10.0, in_bounds=True)
    sess._update_stabilization(None, None, elapsed_sec=10.3, in_bounds=True)
    assert sess.engine.scores[AthleteId.ATHLETE_A].advantages == 0

    # Non-scoring kind when already awarded does not crash (live_session.py:228->230)
    sess._last_awarded_stab_key = ("A", "mount")
    sess._update_stabilization(AthleteId.ATHLETE_A, EventKind.MOUNT, elapsed_sec=12.0, in_bounds=True)
    assert sess._active_stab is None

    # Finalize then call _update_stabilization and enter_overtime on IBJJF (no enter_overtime attr)
    sess.finalize()
    sess._update_stabilization(AthleteId.ATHLETE_A, EventKind.MOUNT, elapsed_sec=15.0, in_bounds=True)
    ot_hud = sess.enter_overtime()
    assert sess.engine.ended is True
    assert ot_hud["overtime"] is False
