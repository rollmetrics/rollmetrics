"""Typed schemas for IBJJF divisions, belts, match events, scoreboard state, and CTA coaching."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class BeltRank(str, Enum):
    WHITE = "white"
    BLUE = "blue"
    PURPLE = "purple"
    BROWN = "brown"
    BLACK = "black"


class UniformType(str, Enum):
    GI = "gi"
    NOGI = "nogi"


class AgeDivision(str, Enum):
    JUVENILE = "juvenile"
    ADULT = "adult"
    MASTER = "master"


class AthleteId(str, Enum):
    ATHLETE_A = "A"
    ATHLETE_B = "B"


class EventKind(str, Enum):
    TAKEDOWN = "takedown"
    SWEEP = "sweep"
    KNEE_ON_BELLY = "knee_on_belly"
    GUARD_PASS = "guard_pass"
    MOUNT = "mount"
    BACK_CONTROL = "back_control"
    ADVANTAGE = "advantage"
    MINOR_FOUL = "minor_foul"
    DOUBLE_STALLING = "double_stalling"
    SERIOUS_FOUL = "serious_foul"
    SUBMISSION = "submission"
    POSITION_CHANGE = "position_change"


class MatchDivisionConfig(BaseModel):
    ruleset: Literal["ibjjf", "ibjjf_v6", "ibjjf_v6_1", "adcc", "sparring"] = "ibjjf"
    uniform: UniformType = UniformType.GI
    belt: BeltRank = BeltRank.WHITE
    age_division: AgeDivision = AgeDivision.MASTER
    weight_class: str = "-82.3 kg (Middle)"
    duration_sec: int | None = None
    athlete_a_name: str = "Athlete A"
    athlete_a_appearance: str = "White Gi"
    athlete_b_name: str = "Athlete B"
    athlete_b_appearance: str = "Blue Gi"
    locked_athlete: AthleteId | None = AthleteId.ATHLETE_A

    def resolved_duration_sec(self) -> int:
        if self.duration_sec is not None:
            return self.duration_sec
        if self.ruleset == "adcc":
            return 600 if self.age_division == AgeDivision.ADULT else 360
        if self.age_division == AgeDivision.MASTER:
            return 300
        if self.age_division == AgeDivision.JUVENILE:
            return 300
        # Adult IBJJF standard regulation times
        return {
            BeltRank.WHITE: 300,
            BeltRank.BLUE: 360,
            BeltRank.PURPLE: 420,
            BeltRank.BROWN: 480,
            BeltRank.BLACK: 600,
        }[self.belt]


class AthleteScore(BaseModel):
    points: int = 0
    advantages: int = 0
    penalties: int = 0
    disqualified: bool = False
    submitted_opponent: bool = False


class RawObservedEvent(BaseModel):
    timestamp_sec: float = Field(..., ge=0.0, description="Elapsed match seconds when exchange completes.")
    match_clock_str: str = Field(default="", description="Optional broadcast countdown clock (MM:SS).")
    actor: AthleteId
    kind: EventKind
    technique_slug: str = Field(default="", description="Optional illegal/foul or technique identifier.")
    hold_duration_sec: float = Field(
        default=3.0,
        ge=0.0,
        description="Seconds top/scoring position was stabilized in-bounds.",
    )
    in_bounds: bool = True
    opponent_locked_submission: bool = False
    confidence: float = Field(default=0.90, ge=0.0, le=1.0)
    occluded: bool = False
    summary: str = ""
    position_after: str = ""


class AdjudicatedEvent(BaseModel):
    event_id: str
    timestamp_sec: float
    match_clock_str: str
    actor: AthleteId
    raw_kind: EventKind
    awarded_points: int = 0
    awarded_advantages: int = 0
    awarded_penalties: int = 0
    opponent_bonus_points: int = 0
    opponent_bonus_advantages: int = 0
    caused_dq: bool = False
    ended_match: bool = False
    needs_review: bool = False
    confidence: float = 0.90
    rule_article: str = ""
    ruling_reason: str = ""
    summary: str = ""
    cta_recommendations: list[dict] = Field(default_factory=list)


class ScoreboardSnapshot(BaseModel):
    division: MatchDivisionConfig
    elapsed_sec: float = 0.0
    remaining_sec: float = 300.0
    current_position: str = "Standing"
    active_stabilization: dict | None = None
    stalling_watch_sec: dict[str, float] = Field(default_factory=lambda: {"A": 0.0, "B": 0.0})
    athlete_a: AthleteScore = Field(default_factory=AthleteScore)
    athlete_b: AthleteScore = Field(default_factory=AthleteScore)
    winner: Literal["A", "B", "draw", "in_progress", "referee_decision"] = "in_progress"
    win_method: str = ""
    timeline: list[AdjudicatedEvent] = Field(default_factory=list)
    coaching_summary: list[dict] = Field(default_factory=list)
    index_version: str = "v0.1.0"
