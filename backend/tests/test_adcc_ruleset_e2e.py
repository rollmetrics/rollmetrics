"""End-to-end tests for switchable ADCC ruleset (Issue #1)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from rollmetrics.cli import main as cli_main
from rollmetrics.config import Settings
from rollmetrics.main import create_app
from rollmetrics.providers.base import build_division_context_prompt
from rollmetrics.rules import (
    ADCCRefereeEngine,
    AgeDivision,
    AthleteId,
    BeltRank,
    EventKind,
    IBJJFRefereeEngine,
    MatchDivisionConfig,
    RawObservedEvent,
    UniformType,
    get_ruleset_engine,
)
from rollmetrics.video.analyzer import MatchAnalyzer
from rollmetrics.video.live_session import LiveRefereeSession


def test_division_config_adcc_defaults_and_aliases() -> None:
    adult_adcc = MatchDivisionConfig(
        ruleset="adcc",
        uniform=UniformType.NOGI,
        belt=BeltRank.BLACK,
        age_division=AgeDivision.ADULT,
    )
    assert adult_adcc.resolved_duration_sec() == 600

    master_adcc = MatchDivisionConfig(
        ruleset="adcc",
        uniform=UniformType.NOGI,
        belt=BeltRank.BLACK,
        age_division=AgeDivision.MASTER,
    )
    assert master_adcc.resolved_duration_sec() == 360

    for alias in ("ibjjf", "ibjjf_v6", "ibjjf_v6_1", "ibjjf_v61", "ibjjf-v6.1"):
        eng = get_ruleset_engine(alias, adult_adcc)
        assert isinstance(eng, IBJJFRefereeEngine)
        assert eng.ruleset_id == "ibjjf"

    adcc_eng = get_ruleset_engine("adcc", adult_adcc)
    assert isinstance(adcc_eng, ADCCRefereeEngine)
    assert adcc_eng.no_points_sec == 300.0

    prompt_adcc = build_division_context_prompt(adult_adcc)
    assert "ADCC Submission Fighting" in prompt_adcc
    prompt_sparring = build_division_context_prompt(
        MatchDivisionConfig(ruleset="sparring", uniform=UniformType.NOGI)
    )
    assert "Sparring Round" in prompt_sparring


def test_match_analyzer_diverges_between_ibjjf_and_adcc() -> None:
    events = [
        # First half (t < 300s): guard pull + takedown
        RawObservedEvent(
            timestamp_sec=45.0,
            actor=AthleteId.ATHLETE_A,
            kind=EventKind.MINOR_FOUL,
            technique_slug="guard_pull",
            summary="Athlete A pulls guard in first half",
        ),
        RawObservedEvent(
            timestamp_sec=120.0,
            actor=AthleteId.ATHLETE_B,
            kind=EventKind.TAKEDOWN,
            hold_duration_sec=3.5,
            position_after="Closed Guard",
            summary="Double leg takedown to closed guard",
        ),
        # Second half (t >= 300s): clean takedown past guard, guard pull penalty, advantage attempt
        RawObservedEvent(
            timestamp_sec=320.0,
            actor=AthleteId.ATHLETE_A,
            kind=EventKind.TAKEDOWN,
            hold_duration_sec=3.4,
            technique_slug="clean_takedown",
            position_after="Side Control",
            summary="Clean takedown landing past guard in side control",
        ),
        RawObservedEvent(
            timestamp_sec=360.0,
            actor=AthleteId.ATHLETE_B,
            kind=EventKind.MINOR_FOUL,
            technique_slug="guard_pull",
            summary="Athlete B pulls guard during points period",
        ),
        RawObservedEvent(
            timestamp_sec=400.0,
            actor=AthleteId.ATHLETE_A,
            kind=EventKind.ADVANTAGE,
            summary="Near submission attempt",
        ),
    ]

    analyzer = MatchAnalyzer(retriever=None)
    adcc_res = analyzer.analyze_event_stream(
        events=events,
        division=MatchDivisionConfig(
            ruleset="adcc",
            uniform=UniformType.NOGI,
            belt=BeltRank.BLACK,
            age_division=AgeDivision.ADULT,
            duration_sec=600,
        ),
    )
    ibjjf_res = analyzer.analyze_event_stream(
        events=events,
        division=MatchDivisionConfig(
            ruleset="ibjjf",
            uniform=UniformType.NOGI,
            belt=BeltRank.BLACK,
            age_division=AgeDivision.ADULT,
            duration_sec=600,
        ),
    )

    assert adcc_res["ruleset"] == "adcc"
    assert adcc_res["athlete_a"]["points"] == 4
    assert adcc_res["athlete_a"]["advantages"] == 0
    assert adcc_res["athlete_b"]["points"] == -1
    assert adcc_res["athlete_b"]["penalties"] == 1
    assert adcc_res["winner"] == "A"

    assert ibjjf_res["ruleset"] == "ibjjf"
    assert ibjjf_res["athlete_a"]["points"] == 2
    assert ibjjf_res["athlete_a"]["advantages"] == 1
    assert ibjjf_res["athlete_b"]["points"] == 2


def test_adcc_live_session_and_overtime() -> None:
    div = MatchDivisionConfig(
        ruleset="adcc",
        uniform=UniformType.NOGI,
        belt=BeltRank.BLACK,
        age_division=AgeDivision.MASTER,
        duration_sec=360,
    )
    session = LiveRefereeSession(division=div, retriever=None)

    hud_early = session.ingest_manual_or_detected_event(
        RawObservedEvent(
            timestamp_sec=60.0,
            actor=AthleteId.ATHLETE_A,
            kind=EventKind.TAKEDOWN,
            hold_duration_sec=4.0,
            position_after="Closed Guard",
        )
    )
    assert hud_early["ruleset"] == "adcc"
    assert hud_early["points_period_active"] is False
    assert hud_early["overtime"] is False
    assert hud_early["athlete_a"]["points"] == 0

    hud_pts = session.ingest_manual_or_detected_event(
        RawObservedEvent(
            timestamp_sec=200.0,
            actor=AthleteId.ATHLETE_A,
            kind=EventKind.GUARD_PASS,
            hold_duration_sec=3.5,
            position_after="Side Control",
        )
    )
    assert hud_pts["points_period_active"] is True
    assert hud_pts["athlete_a"]["points"] == 3

    session.ingest_manual_or_detected_event(
        RawObservedEvent(
            timestamp_sec=240.0,
            actor=AthleteId.ATHLETE_B,
            kind=EventKind.BACK_CONTROL,
            hold_duration_sec=3.5,
            position_after="Back Control",
        )
    )
    hud_tie = session.finalize()
    assert hud_tie["winner"] == "referee_decision"

    hud_ot = session.enter_overtime(overtime_sec=300.0)
    assert hud_ot["overtime"] is True
    assert hud_ot["points_period_active"] is True

    hud_ot_win = session.ingest_manual_or_detected_event(
        RawObservedEvent(
            timestamp_sec=400.0,
            actor=AthleteId.ATHLETE_B,
            kind=EventKind.SWEEP,
            hold_duration_sec=3.5,
            position_after="Mount",
        )
    )
    assert hud_ot_win["athlete_b"]["points"] == 7
    assert session.finalize()["winner"] == "B"


def test_cli_analyze_supports_adcc_ruleset(tmp_path: Path) -> None:
    events_path = tmp_path / "adcc_events.json"
    out_path = tmp_path / "adcc_out.json"
    events_path.write_text(
        json.dumps(
            [
                {
                    "timestamp_sec": 60.0,
                    "actor": "A",
                    "kind": "takedown",
                    "hold_duration_sec": 3.5,
                    "position_after": "Closed Guard",
                },
                {
                    "timestamp_sec": 320.0,
                    "actor": "A",
                    "kind": "guard_pass",
                    "hold_duration_sec": 3.5,
                    "position_after": "Side Control",
                },
            ]
        ),
        encoding="utf-8",
    )
    rc = cli_main(
        [
            "analyze",
            str(events_path),
            "--ruleset",
            "adcc",
            "--uniform",
            "nogi",
            "--belt",
            "black",
            "--age",
            "adult",
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["ruleset"] == "adcc"
    assert payload["athlete_a"]["points"] == 3


def test_api_rulesets_moments_adcc_and_ws_overtime(tmp_path: Path) -> None:
    cfg = Settings(
        environment="local",
        runtime_dir=tmp_path / "runtime",
        data_dir=tmp_path / "data",
    )
    app = create_app(cfg)
    client = TestClient(app)

    res = client.get("/api/rulesets")
    assert res.status_code == 200
    ids = {r["id"] for r in res.json()["rulesets"]}
    assert {"ibjjf", "adcc", "sparring"} <= ids

    # Switch a match to ADCC via PUT /api/matches/{id}/moments:
    # advantages are zeroed and penalties deduct -1 point
    matches_res = client.get("/api/matches")
    assert matches_res.status_code == 200
    match_id = matches_res.json()["matches"][0]["id"]
    upd = client.put(
        f"/api/matches/{match_id}/moments",
        json={
            "ruleset": "adcc",
            "opt_in_feedback": False,
            "events": [
                {
                    "id": "e1",
                    "video_sec": 180,
                    "time_str": "03:00",
                    "clock_str": "02:00",
                    "actor": "A",
                    "call": "+1 ADV",
                    "detail": "Near submission (0 adv in ADCC)",
                },
                {
                    "id": "e2",
                    "video_sec": 200,
                    "time_str": "03:20",
                    "clock_str": "01:40",
                    "actor": "A",
                    "call": "+3 PTS",
                    "detail": "Guard pass in points half",
                },
                {
                    "id": "e3",
                    "video_sec": 220,
                    "time_str": "03:40",
                    "clock_str": "01:20",
                    "actor": "B",
                    "call": "+1 PEN",
                    "detail": "Guard pull in points half",
                },
            ],
        },
    )
    assert upd.status_code == 200
    evs = upd.json()["match"]["events"]
    assert evs[0]["adv_a"] == 0
    assert evs[1]["pts_a"] == 3
    assert evs[2]["pen_b"] == 1 and evs[2]["pts_b"] == -1

    # WebSocket /ws/live with ADCC ruleset + overtime
    with client.websocket_connect("/ws/live") as ws:
        _ = ws.receive_json()
        ws.send_json(
            {
                "type": "init",
                "division": {
                    "ruleset": "adcc",
                    "uniform": "nogi",
                    "belt": "black",
                    "age_division": "master",
                    "duration_sec": 360,
                },
            }
        )
        init_hud = ws.receive_json()
        assert init_hud["ruleset"] == "adcc"
        assert init_hud["points_period_active"] is False

        ws.send_json({"type": "finalize"})
        fin_hud = ws.receive_json()
        assert fin_hud["winner"] == "referee_decision"

        ws.send_json({"type": "overtime", "overtime_sec": 300})
        ot_hud = ws.receive_json()
        assert ot_hud["overtime"] is True
        assert ot_hud["points_period_active"] is True
