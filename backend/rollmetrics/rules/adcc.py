"""Deterministic ADCC Submission Fighting & Lightweight Sparring Round Engines."""

from __future__ import annotations

from rollmetrics.rules.schema import (
    AdjudicatedEvent,
    AthleteId,
    AthleteScore,
    EventKind,
    MatchDivisionConfig,
    RawObservedEvent,
    ScoreboardSnapshot,
)

ADCC_MIN_CONTROL_SEC = 3.0
ADCC_POSITION_COOLDOWN_SEC = 6.0
ADCC_CONFIDENCE_REVIEW_THRESHOLD = 0.72

_CLEAN_PAST_GUARD_SLUGS = frozenset(
    {"clean_takedown", "clean_sweep", "takedown_past_guard", "sweep_past_guard"}
)
_PAST_GUARD_POSITIONS = frozenset(
    {"side control", "mount", "back", "back control", "back mount", "knee on belly"}
)
_GUARD_PULL_SLUGS = frozenset({"guard_pull", "pulling_guard", "sitting_to_guard"})

_SPARRING_CONTROL_KINDS = frozenset(
    {
        EventKind.TAKEDOWN,
        EventKind.SWEEP,
        EventKind.GUARD_PASS,
        EventKind.MOUNT,
        EventKind.BACK_CONTROL,
        EventKind.KNEE_ON_BELLY,
    }
)


class ADCCRefereeEngine:
    """Deterministic ADCC ruleset state machine (no-points first half, -1 penalties, 2/3/4 pts, overtime)."""

    ruleset_id: str = "adcc"

    def __init__(
        self,
        division: MatchDivisionConfig,
        index_version: str = "v0.1.0",
        no_points_sec: float | None = None,
    ) -> None:
        self.division = division
        self.duration_sec = float(division.resolved_duration_sec())
        self.no_points_sec = self.duration_sec / 2.0 if no_points_sec is None else max(0.0, float(no_points_sec))
        self.index_version = index_version
        self.overtime: bool = False
        self.scores: dict[AthleteId, AthleteScore] = {
            AthleteId.ATHLETE_A: AthleteScore(),
            AthleteId.ATHLETE_B: AthleteScore(),
        }
        self.current_position: str = "Standing"
        self.elapsed_sec: float = 0.0
        self.timeline: list[AdjudicatedEvent] = []
        self.ended: bool = False
        self.winner: str = "in_progress"
        self.win_method: str = ""
        self._last_scored_position: dict[tuple[AthleteId, EventKind], float] = {}

    @staticmethod
    def _other(actor: AthleteId) -> AthleteId:
        return AthleteId.ATHLETE_B if actor == AthleteId.ATHLETE_A else AthleteId.ATHLETE_A

    def _fmt_clock(self, elapsed: float) -> str:
        rem = max(0, round(self.duration_sec - elapsed))
        return f"{rem // 60:02d}:{rem % 60:02d}"

    def is_points_period(self, timestamp_sec: float) -> bool:
        return self.overtime or (timestamp_sec >= self.no_points_sec)

    def enter_overtime(self, overtime_sec: float = 300.0) -> None:
        """Start an ADCC overtime period where points and guard-pull penalties apply immediately."""
        self.overtime = True
        self.ended = False
        self.winner = "in_progress"
        self.win_method = ""
        self.duration_sec += max(60.0, float(overtime_sec))

    @staticmethod
    def _is_clean_past_guard(ev: RawObservedEvent) -> bool:
        slug = ev.technique_slug.strip().lower()
        pos = ev.position_after.strip().lower()
        summary = ev.summary.strip().lower()
        return slug in _CLEAN_PAST_GUARD_SLUGS or pos in _PAST_GUARD_POSITIONS or "past guard" in summary

    def ingest_event(self, ev: RawObservedEvent) -> AdjudicatedEvent:
        event_id = f"adcc-{len(self.timeline) + 1:03d}"
        clock_str = ev.match_clock_str or self._fmt_clock(ev.timestamp_sec)
        if self.ended:
            return AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                needs_review=True,
                confidence=ev.confidence,
                rule_article="ADCC Match Termination",
                ruling_reason="Ignored: match had already ended prior to this event.",
                summary=ev.summary,
            )

        self.elapsed_sec = max(self.elapsed_sec, min(self.duration_sec, ev.timestamp_sec))
        if ev.position_after:
            self.current_position = ev.position_after

        needs_review = ev.occluded or (ev.confidence < ADCC_CONFIDENCE_REVIEW_THRESHOLD)
        points_active = self.is_points_period(ev.timestamp_sec)

        # 1. Submission ends match immediately in any period
        if ev.kind == EventKind.SUBMISSION:
            self.scores[ev.actor].submitted_opponent = True
            self.ended = True
            self.winner = ev.actor.value
            self.win_method = f"Submission ({ev.summary or ev.technique_slug or 'Tapout'})"
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                ended_match=True,
                needs_review=needs_review,
                confidence=ev.confidence,
                rule_article="ADCC Submission Victory",
                ruling_reason="Match ended by submission.",
                summary=ev.summary,
            )
            self.timeline.append(adj)
            return adj

        # 2. Serious foul -> immediate DQ
        if ev.kind == EventKind.SERIOUS_FOUL:
            self.scores[ev.actor].disqualified = True
            self.ended = True
            self.winner = self._other(ev.actor).value
            self.win_method = f"Disqualification ({ev.technique_slug or ev.summary or 'Illegal technique'})"
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                caused_dq=True,
                ended_match=True,
                needs_review=needs_review,
                confidence=ev.confidence,
                rule_article="ADCC Illegal Techniques",
                ruling_reason="Immediate disqualification for illegal technique.",
                summary=ev.summary,
            )
            self.timeline.append(adj)
            return adj

        # 3. Double stalling -> -1 penalty to both athletes
        if ev.kind == EventKind.DOUBLE_STALLING:
            self.scores[AthleteId.ATHLETE_A].penalties += 1
            self.scores[AthleteId.ATHLETE_A].points -= 1
            self.scores[AthleteId.ATHLETE_B].penalties += 1
            self.scores[AthleteId.ATHLETE_B].points -= 1
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                awarded_points=-1,
                awarded_penalties=1,
                opponent_bonus_points=-1,
                needs_review=needs_review,
                confidence=ev.confidence,
                rule_article="ADCC Negative Points (Double Passivity)",
                ruling_reason="Double stalling: -1 point assessed to both athletes.",
                summary=ev.summary,
            )
            self.timeline.append(adj)
            return adj

        # 4. Minor foul / guard pull / stalling / fleeing (-1 point)
        if ev.kind == EventKind.MINOR_FOUL:
            slug = ev.technique_slug.strip().lower()
            is_guard_pull = slug in _GUARD_PULL_SLUGS or "guard pull" in ev.summary.lower()
            if is_guard_pull and not points_active:
                adj = AdjudicatedEvent(
                    event_id=event_id,
                    timestamp_sec=ev.timestamp_sec,
                    match_clock_str=clock_str,
                    actor=ev.actor,
                    raw_kind=ev.kind,
                    needs_review=needs_review,
                    confidence=ev.confidence,
                    rule_article="ADCC Guard Pull (No-Points Period)",
                    ruling_reason="Guard pull during no-points first half carries 0 penalty.",
                    summary=ev.summary,
                )
                self.timeline.append(adj)
                return adj

            self.scores[ev.actor].penalties += 1
            self.scores[ev.actor].points -= 1
            reason = (
                "Guard pull during points period: -1 point."
                if is_guard_pull
                else "Stalling / fleeing penalty: -1 point."
            )
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                awarded_points=-1,
                awarded_penalties=1,
                needs_review=needs_review,
                confidence=ev.confidence,
                rule_article="ADCC Negative Points (-1)",
                ruling_reason=reason,
                summary=ev.summary,
            )
            self.timeline.append(adj)
            return adj

        # 5. ADCC has zero advantages
        if ev.kind == EventKind.ADVANTAGE:
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                needs_review=needs_review,
                confidence=ev.confidence,
                rule_article="ADCC Scoring (No Advantages)",
                ruling_reason="ADCC rules do not award advantages.",
                summary=ev.summary,
            )
            self.timeline.append(adj)
            return adj

        # 6. Positional scoring (2, 3, or 4 points)
        if ev.kind in _SPARRING_CONTROL_KINDS:
            if not points_active:
                adj = AdjudicatedEvent(
                    event_id=event_id,
                    timestamp_sec=ev.timestamp_sec,
                    match_clock_str=clock_str,
                    actor=ev.actor,
                    raw_kind=ev.kind,
                    needs_review=needs_review,
                    confidence=ev.confidence,
                    rule_article="ADCC No-Points First Half",
                    ruling_reason="Position achieved during no-points period (0 pts).",
                    summary=ev.summary,
                )
                self.timeline.append(adj)
                return adj

            if not ev.in_bounds or ev.hold_duration_sec < ADCC_MIN_CONTROL_SEC:
                adj = AdjudicatedEvent(
                    event_id=event_id,
                    timestamp_sec=ev.timestamp_sec,
                    match_clock_str=clock_str,
                    actor=ev.actor,
                    raw_kind=ev.kind,
                    needs_review=needs_review,
                    confidence=ev.confidence,
                    rule_article="ADCC 3-Second Control Rule",
                    ruling_reason="Position held under 3.0s or out of bounds (0 pts, no advantage in ADCC).",
                    summary=ev.summary,
                )
                self.timeline.append(adj)
                return adj

            cooldown_key = (ev.actor, ev.kind)
            prev_ts = self._last_scored_position.get(cooldown_key)
            if prev_ts is not None and (ev.timestamp_sec - prev_ts) < ADCC_POSITION_COOLDOWN_SEC:
                adj = AdjudicatedEvent(
                    event_id=event_id,
                    timestamp_sec=ev.timestamp_sec,
                    match_clock_str=clock_str,
                    actor=ev.actor,
                    raw_kind=ev.kind,
                    needs_review=needs_review,
                    confidence=ev.confidence,
                    rule_article="ADCC Duplicate Suppression",
                    ruling_reason="Duplicate position within 6.0s cooldown suppressed.",
                    summary=ev.summary,
                )
                self.timeline.append(adj)
                return adj

            if ev.kind in (EventKind.TAKEDOWN, EventKind.SWEEP):
                clean = self._is_clean_past_guard(ev)
                pts = 4 if clean else 2
                article = (
                    f"ADCC Clean {ev.kind.value.title()} Past Guard (+4 pts)"
                    if clean
                    else f"ADCC {ev.kind.value.title()} (+2 pts)"
                )
            elif ev.kind in (EventKind.GUARD_PASS, EventKind.BACK_CONTROL):
                pts = 3
                article = f"ADCC {ev.kind.value.replace('_', ' ').title()} (+3 pts)"
            else:
                pts = 2
                article = f"ADCC {ev.kind.value.replace('_', ' ').title()} (+2 pts)"

            self._last_scored_position[cooldown_key] = ev.timestamp_sec
            self.scores[ev.actor].points += pts
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                awarded_points=pts,
                needs_review=needs_review,
                confidence=ev.confidence,
                rule_article=article,
                ruling_reason=f"Awarded +{pts} ADCC points after {ev.hold_duration_sec:.1f}s control.",
                summary=ev.summary,
            )
            self.timeline.append(adj)
            return adj

        # 7. Non-scoring position change
        adj = AdjudicatedEvent(
            event_id=event_id,
            timestamp_sec=ev.timestamp_sec,
            match_clock_str=clock_str,
            actor=ev.actor,
            raw_kind=ev.kind,
            needs_review=needs_review,
            confidence=ev.confidence,
            rule_article="ADCC Position Transition",
            ruling_reason="Position change logged (non-scoring).",
            summary=ev.summary,
        )
        self.timeline.append(adj)
        return adj

    def finalize_regulation(self, referee_decision: AthleteId | None = None) -> ScoreboardSnapshot:
        if not self.ended:
            self.ended = True
            self.elapsed_sec = self.duration_sec
            a_pts = self.scores[AthleteId.ATHLETE_A].points
            b_pts = self.scores[AthleteId.ATHLETE_B].points
            if a_pts > b_pts:
                self.winner = "A"
                self.win_method = f"ADCC Points ({a_pts} to {b_pts})"
            elif b_pts > a_pts:
                self.winner = "B"
                self.win_method = f"ADCC Points ({b_pts} to {a_pts})"
            elif referee_decision is not None:
                self.winner = referee_decision.value
                self.win_method = "ADCC Referee Decision"
            else:
                self.winner = "referee_decision"
                self.win_method = "ADCC Overtime / Referee Decision Required"
        return self.snapshot()

    def snapshot(self) -> ScoreboardSnapshot:
        return ScoreboardSnapshot(
            division=self.division,
            elapsed_sec=round(self.elapsed_sec, 2),
            remaining_sec=round(max(0.0, self.duration_sec - self.elapsed_sec), 2),
            current_position=self.current_position,
            athlete_a=self.scores[AthleteId.ATHLETE_A].model_copy(),
            athlete_b=self.scores[AthleteId.ATHLETE_B].model_copy(),
            winner=self.winner,  # type: ignore[arg-type]
            win_method=self.win_method,
            timeline=list(self.timeline),
            index_version=self.index_version,
        )


class SparringRoundEngine:
    """Lightweight training round tracker (positional control + sub threats, zero referee penalties)."""

    ruleset_id: str = "sparring"

    def __init__(self, division: MatchDivisionConfig, index_version: str = "v0.1.0") -> None:
        self.division = division
        self.duration_sec = float(division.resolved_duration_sec())
        self.index_version = index_version
        self.scores: dict[AthleteId, AthleteScore] = {
            AthleteId.ATHLETE_A: AthleteScore(),
            AthleteId.ATHLETE_B: AthleteScore(),
        }
        self.tapouts: dict[AthleteId, int] = {AthleteId.ATHLETE_A: 0, AthleteId.ATHLETE_B: 0}
        self.current_position: str = "Standing"
        self.elapsed_sec: float = 0.0
        self.timeline: list[AdjudicatedEvent] = []
        self.ended: bool = False
        self.winner: str = "in_progress"
        self.win_method: str = ""

    def _fmt_clock(self, elapsed: float) -> str:
        rem = max(0, round(self.duration_sec - elapsed))
        return f"{rem // 60:02d}:{rem % 60:02d}"

    def ingest_event(self, ev: RawObservedEvent) -> AdjudicatedEvent:
        event_id = f"spar-{len(self.timeline) + 1:03d}"
        clock_str = ev.match_clock_str or self._fmt_clock(ev.timestamp_sec)
        self.elapsed_sec = max(self.elapsed_sec, min(self.duration_sec, ev.timestamp_sec))
        if ev.position_after:
            self.current_position = ev.position_after

        if ev.kind == EventKind.SUBMISSION:
            self.tapouts[ev.actor] += 1
            self.scores[ev.actor].advantages += 1
            self.scores[ev.actor].submitted_opponent = True
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                awarded_advantages=1,
                confidence=ev.confidence,
                rule_article="Sparring Tapout (Reset & Continue)",
                ruling_reason="Tapout recorded; round continues after reset.",
                summary=ev.summary,
            )
            self.timeline.append(adj)
            return adj

        if ev.kind in _SPARRING_CONTROL_KINDS:
            self.scores[ev.actor].points += 1
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                awarded_points=1,
                confidence=ev.confidence,
                rule_article="Sparring Positional Control",
                ruling_reason=f"Logged dominant position ({ev.kind.value.replace('_', ' ')}).",
                summary=ev.summary,
            )
            self.timeline.append(adj)
            return adj

        if ev.kind == EventKind.ADVANTAGE:
            self.scores[ev.actor].advantages += 1
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                awarded_advantages=1,
                confidence=ev.confidence,
                rule_article="Sparring Submission Threat",
                ruling_reason="Logged submission threat / near-finish.",
                summary=ev.summary,
            )
            self.timeline.append(adj)
            return adj

        # Penalties / fouls / position changes are logged cleanly with zero referee penalties
        adj = AdjudicatedEvent(
            event_id=event_id,
            timestamp_sec=ev.timestamp_sec,
            match_clock_str=clock_str,
            actor=ev.actor,
            raw_kind=ev.kind,
            confidence=ev.confidence,
            rule_article="Sparring Exchange",
            ruling_reason="Logged in sparring mode (no referee penalties applied).",
            summary=ev.summary,
        )
        self.timeline.append(adj)
        return adj

    def finalize_regulation(self, referee_decision: AthleteId | None = None) -> ScoreboardSnapshot:
        del referee_decision
        self.ended = True
        self.elapsed_sec = self.duration_sec
        a_key = (self.tapouts[AthleteId.ATHLETE_A], self.scores[AthleteId.ATHLETE_A].points, self.scores[AthleteId.ATHLETE_A].advantages)
        b_key = (self.tapouts[AthleteId.ATHLETE_B], self.scores[AthleteId.ATHLETE_B].points, self.scores[AthleteId.ATHLETE_B].advantages)
        if a_key > b_key:
            self.winner = "A"
            self.win_method = "Sparring Round Control"
        elif b_key > a_key:
            self.winner = "B"
            self.win_method = "Sparring Round Control"
        else:
            self.winner = "draw"
            self.win_method = "Even Sparring Round"
        return self.snapshot()

    def snapshot(self) -> ScoreboardSnapshot:
        return ScoreboardSnapshot(
            division=self.division,
            elapsed_sec=round(self.elapsed_sec, 2),
            remaining_sec=round(max(0.0, self.duration_sec - self.elapsed_sec), 2),
            current_position=self.current_position,
            athlete_a=self.scores[AthleteId.ATHLETE_A].model_copy(),
            athlete_b=self.scores[AthleteId.ATHLETE_B].model_copy(),
            winner=self.winner,  # type: ignore[arg-type]
            win_method=self.win_method,
            timeline=list(self.timeline),
            index_version=self.index_version,
        )
