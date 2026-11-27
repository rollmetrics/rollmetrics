"""Next-Wave RollMetrics Evaluation Suites: Stabilization Edge Cases, Mat-Only Ablation, and Position Segmentation."""

from __future__ import annotations

from typing import Any, Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from rollmetrics.rules.schema import (
    AgeDivision,
    AthleteId,
    BeltRank,
    EventKind,
    MatchDivisionConfig,
    RawObservedEvent,
    UniformType,
)
from rollmetrics.rules.state_machine import IBJJFRefereeEngine

CANONICAL_GRAPPLING_STATES: tuple[str, ...] = (
    "standing",
    "closed_guard",
    "open_guard",
    "half_guard",
    "side_control",
    "mount",
    "back_control",
    "turtle",
)


class StabilizationEdgeCase(BaseModel):
    case_id: str
    title: str
    division: MatchDivisionConfig
    events: list[RawObservedEvent]
    expected_points_a: int = 0
    expected_points_b: int = 0
    expected_advantages_a: int = 0
    expected_advantages_b: int = 0
    expected_penalties_a: int = 0
    expected_penalties_b: int = 0
    expected_winner: Literal["A", "B", "draw", "referee_decision"] = "referee_decision"
    expected_dq: bool = False


STABILIZATION_EDGE_CASES: list[StabilizationEdgeCase] = [
    StabilizationEdgeCase(
        case_id="edge-01-pass-2s-unstabilized-advantage",
        title="2.0s Unstabilized Guard Pass yields +1 Advantage (0 pts)",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.BLUE, age_division=AgeDivision.ADULT),
        events=[RawObservedEvent(timestamp_sec=30.0, actor=AthleteId.ATHLETE_A, kind=EventKind.GUARD_PASS, hold_duration_sec=2.0)],
        expected_advantages_a=1,
        expected_winner="A",
    ),
    StabilizationEdgeCase(
        case_id="edge-02-pass-35s-stabilized-3pts",
        title="3.5s Stabilized Guard Pass yields +3 Points (0 adv)",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.BLUE, age_division=AgeDivision.ADULT),
        events=[RawObservedEvent(timestamp_sec=30.0, actor=AthleteId.ATHLETE_A, kind=EventKind.GUARD_PASS, hold_duration_sec=3.5)],
        expected_points_a=3,
        expected_winner="A",
    ),
    StabilizationEdgeCase(
        case_id="edge-03-exact-30s-boundary-mount",
        title="Exact 3.0s Boundary Mount yields +4 Points",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.PURPLE, age_division=AgeDivision.ADULT),
        events=[RawObservedEvent(timestamp_sec=50.0, actor=AthleteId.ATHLETE_B, kind=EventKind.MOUNT, hold_duration_sec=3.0)],
        expected_points_b=4,
        expected_winner="B",
    ),
    StabilizationEdgeCase(
        case_id="edge-04-29s-just-under-mount",
        title="2.9s Just-Under Boundary Mount yields +1 Advantage",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.PURPLE, age_division=AgeDivision.ADULT),
        events=[RawObservedEvent(timestamp_sec=50.0, actor=AthleteId.ATHLETE_B, kind=EventKind.MOUNT, hold_duration_sec=2.9)],
        expected_advantages_b=1,
        expected_winner="B",
    ),
    StabilizationEdgeCase(
        case_id="edge-05-oob-takedown-advantage-only",
        title="Out-of-Bounds Takedown (4.0s hold) downgraded to +1 Advantage",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.WHITE, age_division=AgeDivision.ADULT),
        events=[RawObservedEvent(timestamp_sec=22.0, actor=AthleteId.ATHLETE_A, kind=EventKind.TAKEDOWN, hold_duration_sec=4.0, in_bounds=False)],
        expected_advantages_a=1,
        expected_winner="A",
    ),
    StabilizationEdgeCase(
        case_id="edge-06-pass-inside-locked-guillotine",
        title="Guard Pass held 4.0s inside Opponent Locked Submission yields +1 Advantage",
        division=MatchDivisionConfig(uniform=UniformType.NOGI, belt=BeltRank.BROWN, age_division=AgeDivision.ADULT),
        events=[
            RawObservedEvent(
                timestamp_sec=60.0,
                actor=AthleteId.ATHLETE_A,
                kind=EventKind.GUARD_PASS,
                hold_duration_sec=4.0,
                opponent_locked_submission=True,
            )
        ],
        expected_advantages_a=1,
        expected_winner="A",
    ),
    StabilizationEdgeCase(
        case_id="edge-07-sub-08s-scramble-no-score",
        title="0.5s Sweep Scramble awards 0 Points and 0 Advantages",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.BLACK, age_division=AgeDivision.ADULT),
        events=[RawObservedEvent(timestamp_sec=45.0, actor=AthleteId.ATHLETE_A, kind=EventKind.SWEEP, hold_duration_sec=0.5)],
        expected_winner="referee_decision",
    ),
    StabilizationEdgeCase(
        case_id="edge-08-double-guard-pull-double-stalling",
        title="Double Guard Pull Stalling penalizes both athletes equally (referee decision)",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.BLACK, age_division=AgeDivision.ADULT),
        events=[
            RawObservedEvent(timestamp_sec=25.0, actor=AthleteId.ATHLETE_A, kind=EventKind.DOUBLE_STALLING, technique_slug="stalling"),
        ],
        expected_penalties_a=1,
        expected_penalties_b=1,
        expected_winner="referee_decision",
    ),
    StabilizationEdgeCase(
        case_id="edge-09-fleeing-mat-submission-dq",
        title="Fleeing Mat Bounds out of Locked Submission triggers immediate Serious Foul DQ",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.BLUE, age_division=AgeDivision.ADULT),
        events=[
            RawObservedEvent(
                timestamp_sec=90.0,
                actor=AthleteId.ATHLETE_B,
                kind=EventKind.SERIOUS_FOUL,
                technique_slug="fleeing_mat_bounds_submission",
            )
        ],
        expected_winner="A",
        expected_dq=True,
    ),
    StabilizationEdgeCase(
        case_id="edge-10-illegal-reaping-gi-black-dq",
        title="Knee Reap in Adult Black Belt Gi triggers immediate DQ",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.BLACK, age_division=AgeDivision.ADULT),
        events=[
            RawObservedEvent(
                timestamp_sec=110.0,
                actor=AthleteId.ATHLETE_A,
                kind=EventKind.POSITION_CHANGE,
                technique_slug="knee_reap",
            )
        ],
        expected_winner="B",
        expected_dq=True,
    ),
    StabilizationEdgeCase(
        case_id="edge-11-legal-reaping-nogi-black",
        title="Knee Reap in Adult Black Belt No-Gi is Legal (0 DQ)",
        division=MatchDivisionConfig(uniform=UniformType.NOGI, belt=BeltRank.BLACK, age_division=AgeDivision.ADULT),
        events=[
            RawObservedEvent(
                timestamp_sec=110.0,
                actor=AthleteId.ATHLETE_A,
                kind=EventKind.POSITION_CHANGE,
                technique_slug="knee_reap",
            )
        ],
        expected_winner="referee_decision",
        expected_dq=False,
    ),
    StabilizationEdgeCase(
        case_id="edge-12-duplicate-mount-cooldown-dedup",
        title="Duplicate Mount call within 6.0s cooldown without reset is deduplicated (4 pts, not 8)",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.WHITE, age_division=AgeDivision.ADULT),
        events=[
            RawObservedEvent(timestamp_sec=40.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MOUNT, hold_duration_sec=3.5),
            RawObservedEvent(timestamp_sec=44.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MOUNT, hold_duration_sec=3.5),
        ],
        expected_points_a=4,
        expected_winner="A",
    ),
    StabilizationEdgeCase(
        case_id="edge-13-guard-pull-without-grip-penalty",
        title="Guard Pull without Grip yields Minor Foul penalty (tie-breaker loss on 0-0)",
        division=MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.WHITE, age_division=AgeDivision.MASTER),
        events=[
            RawObservedEvent(
                timestamp_sec=15.0,
                actor=AthleteId.ATHLETE_A,
                kind=EventKind.MINOR_FOUL,
                technique_slug="guard_pull_without_grip",
            )
        ],
        expected_penalties_a=1,
        expected_winner="B",
    ),
]


def evaluate_stabilization_edge_cases(
    cases: list[StabilizationEdgeCase] | None = None,
) -> dict[str, Any]:
    """Runs the IBJJF 3-second stabilization, advantage, penalty, and foul boundary edge-case suite."""
    suite = cases if cases is not None else STABILIZATION_EDGE_CASES
    details: list[dict[str, Any]] = []
    passed = 0

    for case in suite:
        engine = IBJJFRefereeEngine(division=case.division)
        saw_dq = False
        for ev in case.events:
            adj = engine.ingest_event(ev)
            if adj.caused_dq:
                saw_dq = True
        snap = engine.finalize_regulation()

        ok = (
            snap.athlete_a.points == case.expected_points_a
            and snap.athlete_b.points == case.expected_points_b
            and snap.athlete_a.advantages == case.expected_advantages_a
            and snap.athlete_b.advantages == case.expected_advantages_b
            and snap.athlete_a.penalties == case.expected_penalties_a
            and snap.athlete_b.penalties == case.expected_penalties_b
            and snap.winner == case.expected_winner
            and saw_dq == case.expected_dq
        )
        if ok:
            passed += 1
        details.append(
            {
                "case_id": case.case_id,
                "title": case.title,
                "pass": ok,
                "actual_score_a": snap.athlete_a.model_dump(),
                "actual_score_b": snap.athlete_b.model_dump(),
                "actual_winner": snap.winner,
                "actual_dq": saw_dq,
            }
        )

    total = len(suite)
    return {
        "suite": "stabilization_edge_cases",
        "passed": passed,
        "total": total,
        "pass_rate": round(passed / total, 4) if total else 1.0,
        "overall_pass": passed == total,
        "details": details,
    }


def mask_referee_region(
    img: Image.Image,
    referee_boxes: list[tuple[float, float, float, float]] | None = None,
) -> Image.Image:
    """Blacks out referee bounding boxes (normalized x0, y0, x1, y1) for Mat-Only Blind Ablation evals."""
    masked = img.convert("RGB").copy()
    w, h = masked.size
    draw = ImageDraw.Draw(masked)
    boxes = referee_boxes if referee_boxes is not None else [(0.0, 0.0, 0.22, 0.78), (0.78, 0.0, 1.0, 0.78)]
    for x0, y0, x1, y1 in boxes:
        px0 = max(0, min(w - 1, round(x0 * w)))
        py0 = max(0, min(h - 1, round(y0 * h)))
        px1 = max(px0 + 1, min(w, round(x1 * w)))
        py1 = max(py0 + 1, min(h, round(y1 * h)))
        draw.rectangle((px0, py0, px1, py1), fill=(0, 0, 0))
    return masked


class PositionWindow(BaseModel):
    start_sec: float = Field(..., ge=0.0)
    end_sec: float = Field(..., gt=0.0)
    state: str
    top_athlete: Literal["A", "B", "neutral"] = "neutral"


def _normalize_state(raw: str) -> str:
    clean = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return clean if clean in CANONICAL_GRAPPLING_STATES else "standing"


def _state_at_time(windows: list[PositionWindow], t_sec: float) -> tuple[str, str]:
    for win in windows:
        if win.start_sec <= t_sec < win.end_sec:
            return _normalize_state(win.state), win.top_athlete
    return "standing", "neutral"


def evaluate_position_segmentation(
    gt_windows: list[PositionWindow] | list[dict[str, Any]],
    pred_windows: list[PositionWindow] | list[dict[str, Any]],
    step_sec: float = 5.0,
) -> dict[str, Any]:
    """Evaluates 5-second window macro-F1, top/bottom accuracy, and transition mIoU across 8 canonical states."""
    gt_parsed = [PositionWindow.model_validate(w) if isinstance(w, dict) else w for w in gt_windows]
    pred_parsed = [PositionWindow.model_validate(w) if isinstance(w, dict) else w for w in pred_windows]

    if not gt_parsed:
        return {
            "suite": "position_segmentation",
            "window_step_sec": step_sec,
            "num_windows": 0,
            "macro_f1": 0.0,
            "top_bottom_accuracy": 0.0,
            "transition_miou": 0.0,
            "per_state_f1": {},
        }

    max_end = max(max(w.end_sec for w in gt_parsed), max((w.end_sec for w in pred_parsed), default=0.0))
    eff_step = max(0.5, float(step_sec))
    sample_times: list[float] = []
    cursor = 0.0
    while cursor < max_end:
        sample_times.append(cursor + eff_step * 0.5)
        cursor += eff_step

    tp: dict[str, int] = {s: 0 for s in CANONICAL_GRAPPLING_STATES}
    fp: dict[str, int] = {s: 0 for s in CANONICAL_GRAPPLING_STATES}
    fn: dict[str, int] = {s: 0 for s in CANONICAL_GRAPPLING_STATES}
    top_matches = 0

    for t in sample_times:
        g_state, g_top = _state_at_time(gt_parsed, t)
        p_state, p_top = _state_at_time(pred_parsed, t)
        if g_state == p_state:
            tp[g_state] += 1
        else:
            fp[p_state] += 1
            fn[g_state] += 1
        if g_top == p_top:
            top_matches += 1

    per_state_f1: dict[str, float] = {}
    per_state_iou: dict[str, float] = {}
    active_states: list[str] = []
    for st in CANONICAL_GRAPPLING_STATES:
        denom_f1 = 2 * tp[st] + fp[st] + fn[st]
        denom_iou = tp[st] + fp[st] + fn[st]
        if denom_iou > 0:
            active_states.append(st)
            per_state_f1[st] = round((2 * tp[st]) / float(denom_f1), 4) if denom_f1 else 0.0
            per_state_iou[st] = round(tp[st] / float(denom_iou), 4)

    macro_f1 = round(sum(per_state_f1[s] for s in active_states) / len(active_states), 4) if active_states else 0.0
    transition_miou = round(sum(per_state_iou[s] for s in active_states) / len(active_states), 4) if active_states else 0.0
    top_acc = round(top_matches / float(len(sample_times)), 4) if sample_times else 0.0

    return {
        "suite": "position_segmentation",
        "window_step_sec": eff_step,
        "num_windows": len(sample_times),
        "macro_f1": macro_f1,
        "top_bottom_accuracy": top_acc,
        "transition_miou": transition_miou,
        "per_state_f1": per_state_f1,
        "per_state_iou": per_state_iou,
    }
