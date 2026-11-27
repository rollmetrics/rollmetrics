"""Hypothesis property-based fuzz tests for IBJJF and ADCC referee state machines (Layer 4)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from rollmetrics.rules.adcc import ADCCRefereeEngine
from rollmetrics.rules.schema import (
    AthleteId,
    BeltRank,
    EventKind,
    MatchDivisionConfig,
    RawObservedEvent,
    UniformType,
)
from rollmetrics.rules.state_machine import MIN_STABILIZATION_SEC, POINT_VALUES, IBJJFRefereeEngine

_ALL_ACTORS = st.sampled_from([AthleteId.ATHLETE_A, AthleteId.ATHLETE_B])
_ALL_KINDS = st.sampled_from(list(EventKind))
_SCORING_KINDS = st.sampled_from(list(POINT_VALUES.keys()))
_SLUGS = st.sampled_from(
    [
        "",
        "stalling",
        "guard_pull",
        "clean_takedown",
        "sweep",
        "knee_cut_pass",
        "armbar",
        "heel_hook",
        "slam",
    ]
)


@st.composite
def _raw_event_sequence(draw: st.DrawFn, *, max_len: int = 25) -> list[RawObservedEvent]:
    count = draw(st.integers(min_value=1, max_value=max_len))
    events: list[RawObservedEvent] = []
    ts = 0.0
    for _ in range(count):
        ts = round(ts + draw(st.floats(min_value=0.0, max_value=25.0, allow_nan=False, allow_infinity=False)), 2)
        events.append(
            RawObservedEvent(
                timestamp_sec=ts,
                actor=draw(_ALL_ACTORS),
                kind=draw(_ALL_KINDS),
                technique_slug=draw(_SLUGS),
                hold_duration_sec=round(
                    draw(st.floats(min_value=0.0, max_value=6.0, allow_nan=False, allow_infinity=False)),
                    2,
                ),
                in_bounds=draw(st.booleans()),
                opponent_locked_submission=draw(st.booleans()),
                confidence=round(
                    draw(st.floats(min_value=0.2, max_value=1.0, allow_nan=False, allow_infinity=False)),
                    2,
                ),
                occluded=draw(st.booleans()),
            )
        )
    return events


@settings(max_examples=80, deadline=None)
@given(
    belt=st.sampled_from([BeltRank.WHITE, BeltRank.BLUE, BeltRank.PURPLE, BeltRank.BROWN, BeltRank.BLACK]),
    uniform=st.sampled_from([UniformType.GI, UniformType.NOGI]),
    events=_raw_event_sequence(max_len=25),
)
def test_ibjjf_engine_hard_invariants(
    belt: BeltRank,
    uniform: UniformType,
    events: list[RawObservedEvent],
) -> None:
    div = MatchDivisionConfig(belt=belt, uniform=uniform, duration_sec=600.0)
    engine = IBJJFRefereeEngine(division=div)

    prev_a = (0, 0, 0)
    prev_b = (0, 0, 0)
    frozen_state: tuple[tuple[int, int, int], tuple[int, int, int], str, str] | None = None

    for ev in events:
        was_ended = engine.ended
        adj = engine.ingest_event(ev)

        cur_a = (
            engine.scores[AthleteId.ATHLETE_A].points,
            engine.scores[AthleteId.ATHLETE_A].advantages,
            engine.scores[AthleteId.ATHLETE_A].penalties,
        )
        cur_b = (
            engine.scores[AthleteId.ATHLETE_B].points,
            engine.scores[AthleteId.ATHLETE_B].advantages,
            engine.scores[AthleteId.ATHLETE_B].penalties,
        )

        # Invariant 1: Non-negative and monotonically non-decreasing IBJJF scores
        for prev, cur in ((prev_a, cur_a), (prev_b, cur_b)):
            assert all(v >= 0 for v in cur)
            assert cur[0] >= prev[0]
            assert cur[1] >= prev[1]
            assert cur[2] >= prev[2]

        # Invariant 2: No 1-point IBJJF score is ever possible (only +2, +3, +4)
        assert cur_a[0] != 1
        assert cur_b[0] != 1

        # Invariant 3: < 3.0s stabilization never awards positional points
        if not was_ended and ev.kind in POINT_VALUES and ev.hold_duration_sec < MIN_STABILIZATION_SEC:
            assert adj.awarded_points == 0

        # Invariant 4: 4th IBJJF penalty on either athlete triggers immediate DQ
        for aid in (AthleteId.ATHLETE_A, AthleteId.ATHLETE_B):
            if engine.scores[aid].penalties >= 4:
                assert engine.ended is True
                assert engine.scores[aid].disqualified is True
                assert engine.winner != aid.value

        # Invariant 5: Post-end events never mutate score or winner
        if was_ended:
            assert frozen_state is not None
            assert (cur_a, cur_b, engine.winner, engine.win_method) == frozen_state
        elif engine.ended:
            frozen_state = (cur_a, cur_b, engine.winner, engine.win_method)

        prev_a, prev_b = cur_a, cur_b


@settings(max_examples=50, deadline=None)
@given(
    actor=_ALL_ACTORS,
    kind=_SCORING_KINDS,
    hold=st.floats(min_value=0.0, max_value=2.99, allow_nan=False, allow_infinity=False),
    in_bounds=st.booleans(),
)
def test_ibjjf_sub_3s_hold_never_awards_points(
    actor: AthleteId,
    kind: EventKind,
    hold: float,
    in_bounds: bool,
) -> None:
    engine = IBJJFRefereeEngine(division=MatchDivisionConfig(belt=BeltRank.BLACK))
    adj = engine.ingest_event(
        RawObservedEvent(
            timestamp_sec=30.0,
            actor=actor,
            kind=kind,
            hold_duration_sec=hold,
            in_bounds=in_bounds,
        )
    )
    assert adj.awarded_points == 0
    assert engine.scores[actor].points == 0


@settings(max_examples=80, deadline=None)
@given(events=_raw_event_sequence(max_len=25))
def test_adcc_engine_hard_invariants(events: list[RawObservedEvent]) -> None:
    div = MatchDivisionConfig(ruleset="adcc", uniform=UniformType.NOGI, duration_sec=600.0)
    engine = ADCCRefereeEngine(division=div)

    prev_pen_a = 0
    prev_pen_b = 0
    frozen_state: tuple[int, int, int, int, str, str] | None = None

    for ev in events:
        was_ended = engine.ended
        adj = engine.ingest_event(ev)

        a = engine.scores[AthleteId.ATHLETE_A]
        b = engine.scores[AthleteId.ATHLETE_B]

        # Invariant 1: ADCC never awards advantages and penalty counts are non-negative monotonic
        assert a.advantages == 0
        assert b.advantages == 0
        assert a.penalties >= prev_pen_a >= 0
        assert b.penalties >= prev_pen_b >= 0

        # Invariant 2: No-points first half never awards positive positional points
        if not was_ended and not engine.is_points_period(ev.timestamp_sec):
            assert adj.awarded_points <= 0

        # Invariant 3: < 3.0s hold never awards positive positional points in ADCC
        if not was_ended and ev.kind in POINT_VALUES and ev.hold_duration_sec < 3.0:
            assert adj.awarded_points <= 0

        # Invariant 4: Post-end events never mutate score or winner
        cur_state = (a.points, a.penalties, b.points, b.penalties, engine.winner, engine.win_method)
        if was_ended:
            assert frozen_state is not None
            assert cur_state == frozen_state
        elif engine.ended:
            frozen_state = cur_state

        prev_pen_a, prev_pen_b = a.penalties, b.penalties
