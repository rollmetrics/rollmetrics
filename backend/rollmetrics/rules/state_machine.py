"""Deterministic IBJJF Match Scoreboard & Referee State Machine."""

from __future__ import annotations

from rollmetrics.rules.legality_matrix import check_technique_legality
from rollmetrics.rules.schema import (
    AdjudicatedEvent,
    AthleteId,
    AthleteScore,
    EventKind,
    MatchDivisionConfig,
    RawObservedEvent,
    ScoreboardSnapshot,
)

POINT_VALUES: dict[EventKind, tuple[int, str]] = {
    EventKind.TAKEDOWN: (2, "IBJJF Art. 4.1 (2 pts - Takedown stabilized 3s)"),
    EventKind.SWEEP: (2, "IBJJF Art. 4.6 (2 pts - Sweep from guard stabilized 3s)"),
    EventKind.KNEE_ON_BELLY: (2, "IBJJF Art. 4.3 (2 pts - Knee on Belly stabilized 3s)"),
    EventKind.GUARD_PASS: (3, "IBJJF Art. 4.2 (3 pts - Guard Pass stabilized 3s)"),
    EventKind.MOUNT: (4, "IBJJF Art. 4.4 (4 pts - Mount / S-Mount stabilized 3s)"),
    EventKind.BACK_CONTROL: (4, "IBJJF Art. 4.5 (4 pts - Back Control with hooks/body-triangle 3s)"),
}

CONFIDENCE_REVIEW_THRESHOLD = 0.72
MIN_STABILIZATION_SEC = 3.0
POSITION_RESCORE_COOLDOWN_SEC = 6.0


class IBJJFRefereeEngine:
    """Applies IBJJF scoring, 3s stabilization, penalty cascade, and legality checks deterministically."""

    def __init__(self, division: MatchDivisionConfig, index_version: str = "v0.1.0"):
        self.division = division
        self.duration_sec = float(division.resolved_duration_sec())
        self.index_version = index_version
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

    def _apply_single_penalty(self, offender: AthleteId) -> tuple[int, int, bool]:
        """Returns (opponent_bonus_pts, opponent_bonus_adv, caused_dq) per IBJJF Art. 5.3 / 7.2."""
        sc = self.scores[offender]
        opp = self.scores[self._other(offender)]
        sc.penalties += 1
        p_num = sc.penalties
        if p_num == 1:
            return 0, 0, False
        if p_num == 2:
            opp.advantages += 1
            return 0, 1, False
        if p_num == 3:
            opp.points += 2
            return 2, 0, False
        sc.disqualified = True
        self.ended = True
        self.winner = self._other(offender).value
        self.win_method = f"Disqualification (4th penalty accumulated by Athlete {offender.value})"
        return 0, 0, True

    def ingest_event(self, ev: RawObservedEvent) -> AdjudicatedEvent:
        if self.ended:
            return AdjudicatedEvent(
                event_id=f"ev-{len(self.timeline)+1:03d}",
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=ev.match_clock_str or self._fmt_clock(ev.timestamp_sec),
                actor=ev.actor,
                raw_kind=ev.kind,
                needs_review=True,
                confidence=ev.confidence,
                rule_article="IBJJF Art. 2.1",
                ruling_reason="Ignored: match had already ended prior to this event.",
                summary=ev.summary,
            )

        self.elapsed_sec = max(self.elapsed_sec, min(self.duration_sec, ev.timestamp_sec))
        if ev.position_after:
            self.current_position = ev.position_after

        clock_str = ev.match_clock_str or self._fmt_clock(ev.timestamp_sec)
        needs_review = ev.occluded or (ev.confidence < CONFIDENCE_REVIEW_THRESHOLD)
        event_id = f"ev-{len(self.timeline)+1:03d}"

        # 1. Check technique legality first if a foul/technique slug is present
        if ev.technique_slug:
            verdict = check_technique_legality(
                ev.technique_slug,
                belt=self.division.belt,
                uniform=self.division.uniform,
                age_division=self.division.age_division,
            )
            if verdict.severity == "serious_foul_dq":
                self.scores[ev.actor].disqualified = True
                self.ended = True
                self.winner = self._other(ev.actor).value
                self.win_method = f"Disqualification ({verdict.article})"
                adj = AdjudicatedEvent(
                    event_id=event_id,
                    timestamp_sec=ev.timestamp_sec,
                    match_clock_str=clock_str,
                    actor=ev.actor,
                    raw_kind=EventKind.SERIOUS_FOUL,
                    caused_dq=True,
                    ended_match=True,
                    needs_review=needs_review,
                    confidence=ev.confidence,
                    rule_article=verdict.article,
                    ruling_reason=verdict.explanation,
                    summary=ev.summary or verdict.explanation,
                )
                self.timeline.append(adj)
                return adj

            if verdict.severity == "minor_foul" and ev.kind not in (
                EventKind.MINOR_FOUL,
                EventKind.DOUBLE_STALLING,
            ):
                ev = ev.model_copy(update={"kind": EventKind.MINOR_FOUL})

        # 2. Submissions
        if ev.kind == EventKind.SUBMISSION:
            self.scores[ev.actor].submitted_opponent = True
            self.ended = True
            self.winner = ev.actor.value
            self.win_method = "Submission"
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                ended_match=True,
                needs_review=needs_review,
                confidence=ev.confidence,
                rule_article="IBJJF Art. 2.2.1 (Victory by Submission)",
                ruling_reason="Tap / verbal submission confirmed.",
                summary=ev.summary or "Won by submission.",
            )
            self.timeline.append(adj)
            return adj

        # 3. Double Stalling (simultaneous stalling call on both athletes)
        if ev.kind == EventKind.DOUBLE_STALLING:
            b_pts_a, b_adv_a, dq_a = self._apply_single_penalty(AthleteId.ATHLETE_A)
            b_pts_b, b_adv_b, dq_b = self._apply_single_penalty(AthleteId.ATHLETE_B)
            # Note: _apply_single_penalty(A) awards bonus to B; _apply_single_penalty(B) awards bonus to A
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                awarded_points=b_pts_b,
                awarded_advantages=b_adv_b,
                awarded_penalties=1,
                opponent_bonus_points=b_pts_a,
                opponent_bonus_advantages=b_adv_a,
                caused_dq=dq_a or dq_b,
                ended_match=self.ended,
                needs_review=needs_review,
                confidence=ev.confidence,
                rule_article="IBJJF Art. 5.3 / 6.3.2 (Double Lack of Combativeness)",
                ruling_reason=(
                    f"Simultaneous stalling penalty on both athletes "
                    f"(A pen={self.scores[AthleteId.ATHLETE_A].penalties}, "
                    f"B pen={self.scores[AthleteId.ATHLETE_B].penalties})."
                ),
                summary=ev.summary or "Double stalling call.",
            )
            self.timeline.append(adj)
            return adj

        # 4. Single Minor Foul
        if ev.kind == EventKind.MINOR_FOUL:
            verdict = check_technique_legality(
                ev.technique_slug or "stalling",
                belt=self.division.belt,
                uniform=self.division.uniform,
                age_division=self.division.age_division,
            )
            opp_pts, opp_adv, caused_dq = self._apply_single_penalty(ev.actor)
            p_count = self.scores[ev.actor].penalties
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                awarded_penalties=1,
                opponent_bonus_points=opp_pts,
                opponent_bonus_advantages=opp_adv,
                caused_dq=caused_dq,
                ended_match=self.ended,
                needs_review=needs_review,
                confidence=ev.confidence,
                rule_article=verdict.article,
                ruling_reason=(
                    f"Penalty #{p_count} on Athlete {ev.actor.value}: {verdict.explanation} "
                    f"(Opponent bonus: +{opp_pts} pts, +{opp_adv} adv)."
                ),
                summary=ev.summary or verdict.explanation,
            )
            self.timeline.append(adj)
            return adj

        # 5. Serious Foul (without slug or explicit)
        if ev.kind == EventKind.SERIOUS_FOUL:
            self.scores[ev.actor].disqualified = True
            self.ended = True
            self.winner = self._other(ev.actor).value
            self.win_method = "Disqualification (Serious Foul)"
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
                rule_article="IBJJF Art. 6.2",
                ruling_reason="Immediate disqualification for serious foul.",
                summary=ev.summary or "Disqualified for serious foul.",
            )
            self.timeline.append(adj)
            return adj

        # 6. Point-Scoring Transitions (with 3s Stabilization, In-Bounds, & 6s Cooldown Deduplication)
        if ev.kind in POINT_VALUES:
            pts_val, art = POINT_VALUES[ev.kind]
            pos_key = (ev.actor, ev.kind)
            last_ts = self._last_scored_position.get(pos_key)
            if last_ts is not None and 0.0 <= (ev.timestamp_sec - last_ts) < POSITION_RESCORE_COOLDOWN_SEC:
                adj = AdjudicatedEvent(
                    event_id=event_id,
                    timestamp_sec=ev.timestamp_sec,
                    match_clock_str=clock_str,
                    actor=ev.actor,
                    raw_kind=ev.kind,
                    needs_review=needs_review,
                    confidence=ev.confidence,
                    rule_article=art,
                    ruling_reason=(
                        f"Deduplicated {ev.kind.value}: identical position already scored within "
                        f"{POSITION_RESCORE_COOLDOWN_SEC:.1f}s cooldown without position reset."
                    ),
                    summary=ev.summary or f"Deduplicated duplicate {ev.kind.value} call.",
                )
                self.timeline.append(adj)
                return adj
            # A new distinct scoring transition resets prior position holds
            self._last_scored_position.clear()
            if (
                ev.hold_duration_sec >= MIN_STABILIZATION_SEC
                and ev.in_bounds
                and not ev.opponent_locked_submission
            ):
                self.scores[ev.actor].points += pts_val
                self._last_scored_position[pos_key] = ev.timestamp_sec
                adj = AdjudicatedEvent(
                    event_id=event_id,
                    timestamp_sec=ev.timestamp_sec,
                    match_clock_str=clock_str,
                    actor=ev.actor,
                    raw_kind=ev.kind,
                    awarded_points=pts_val,
                    needs_review=needs_review,
                    confidence=ev.confidence,
                    rule_article=art,
                    ruling_reason=f"Awarded +{pts_val} pts ({ev.hold_duration_sec:.1f}s in-bounds stabilization).",
                    summary=ev.summary or f"+{pts_val} pts ({ev.kind.value}).",
                )
            elif ev.hold_duration_sec >= 0.8 or not ev.in_bounds:
                # IBJJF Art. 5.2: Incomplete stabilization (<3s) or out-of-bounds after initiating earns +1 Advantage
                self.scores[ev.actor].advantages += 1
                reason_detail = (
                    f"held {ev.hold_duration_sec:.1f}s (<3.0s required)"
                    if ev.in_bounds and not ev.opponent_locked_submission
                    else ("exited bounds mid-transition" if not ev.in_bounds else "caught inside active submission")
                )
                adj = AdjudicatedEvent(
                    event_id=event_id,
                    timestamp_sec=ev.timestamp_sec,
                    match_clock_str=clock_str,
                    actor=ev.actor,
                    raw_kind=ev.kind,
                    awarded_advantages=1,
                    needs_review=needs_review,
                    confidence=ev.confidence,
                    rule_article="IBJJF Art. 5.2 (Advantage instead of Points)",
                    ruling_reason=f"Downgraded {ev.kind.value} (+{pts_val}) to +1 Advantage: {reason_detail}.",
                    summary=ev.summary or f"+1 Advantage ({ev.kind.value} not stabilized 3s).",
                )
            else:
                adj = AdjudicatedEvent(
                    event_id=event_id,
                    timestamp_sec=ev.timestamp_sec,
                    match_clock_str=clock_str,
                    actor=ev.actor,
                    raw_kind=ev.kind,
                    needs_review=needs_review,
                    confidence=ev.confidence,
                    rule_article=art,
                    ruling_reason="Scramble reset under 0.8s with no positional control; 0 pts / 0 adv.",
                    summary=ev.summary or "Brief scramble, no score.",
                )
            self.timeline.append(adj)
            return adj

        # 7. Explicit Advantage (near-pass, near-sweep, credible submission threat)
        if ev.kind == EventKind.ADVANTAGE:
            self.scores[ev.actor].advantages += 1
            adj = AdjudicatedEvent(
                event_id=event_id,
                timestamp_sec=ev.timestamp_sec,
                match_clock_str=clock_str,
                actor=ev.actor,
                raw_kind=ev.kind,
                awarded_advantages=1,
                needs_review=needs_review,
                confidence=ev.confidence,
                rule_article="IBJJF Art. 5.2 (Advantage)",
                ruling_reason="Credible near-scoring attack or submission defense forced.",
                summary=ev.summary or "+1 Advantage.",
            )
            self.timeline.append(adj)
            return adj

        # 8. Positional Transition (telemetry / coaching hook, 0 score, resets position cooldown)
        self._last_scored_position.clear()
        adj = AdjudicatedEvent(
            event_id=event_id,
            timestamp_sec=ev.timestamp_sec,
            match_clock_str=clock_str,
            actor=ev.actor,
            raw_kind=ev.kind,
            needs_review=needs_review,
            confidence=ev.confidence,
            rule_article="IBJJF Telemetry",
            ruling_reason="Positional transition logged.",
            summary=ev.summary or f"Transition to {self.current_position}.",
        )
        self.timeline.append(adj)
        return adj

    def finalize_regulation(self) -> ScoreboardSnapshot:
        """Resolves regulation time winner per IBJJF Art. 2.2 tie-breaker hierarchy."""
        self.elapsed_sec = self.duration_sec
        if not self.ended:
            a = self.scores[AthleteId.ATHLETE_A]
            b = self.scores[AthleteId.ATHLETE_B]
            if a.points != b.points:
                self.winner = "A" if a.points > b.points else "B"
                self.win_method = f"Points ({a.points}-{b.points})"
            elif a.advantages != b.advantages:
                self.winner = "A" if a.advantages > b.advantages else "B"
                self.win_method = f"Advantages ({a.advantages}-{b.advantages}, tied {a.points}-{b.points} pts)"
            elif a.penalties != b.penalties:
                # Fewer penalties wins!
                self.winner = "A" if a.penalties < b.penalties else "B"
                self.win_method = f"Penalties ({a.penalties} vs {b.penalties} pen)"
            else:
                self.winner = "referee_decision"
                self.win_method = (
                    f"Referee Decision (tied {a.points}-{b.points} pts, "
                    f"{a.advantages}-{b.advantages} adv, {a.penalties}-{b.penalties} pen)"
                )
            self.ended = True
        return self.snapshot()

    def snapshot(self) -> ScoreboardSnapshot:
        return ScoreboardSnapshot(
            division=self.division,
            elapsed_sec=round(self.elapsed_sec, 1),
            remaining_sec=round(max(0.0, self.duration_sec - self.elapsed_sec), 1),
            current_position=self.current_position,
            athlete_a=self.scores[AthleteId.ATHLETE_A].model_copy(),
            athlete_b=self.scores[AthleteId.ATHLETE_B].model_copy(),
            winner=self.winner,  # type: ignore[arg-type]
            win_method=self.win_method,
            timeline=list(self.timeline),
            index_version=self.index_version,
        )
