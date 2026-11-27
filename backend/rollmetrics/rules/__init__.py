"""IBJJF rules engine, technique legality matrix, and scoreboard schemas."""

from rollmetrics.rules.legality_matrix import (
    FOUL_CATALOG,
    FoulVerdict,
    check_technique_legality,
)
from rollmetrics.rules.schema import (
    AdjudicatedEvent,
    AgeDivision,
    AthleteId,
    AthleteScore,
    BeltRank,
    EventKind,
    MatchDivisionConfig,
    RawObservedEvent,
    ScoreboardSnapshot,
    UniformType,
)
from rollmetrics.rules.state_machine import (
    CONFIDENCE_REVIEW_THRESHOLD,
    MIN_STABILIZATION_SEC,
    POINT_VALUES,
    IBJJFRefereeEngine,
)

__all__ = [
    "CONFIDENCE_REVIEW_THRESHOLD",
    "FOUL_CATALOG",
    "MIN_STABILIZATION_SEC",
    "POINT_VALUES",
    "AdjudicatedEvent",
    "AgeDivision",
    "AthleteId",
    "AthleteScore",
    "BeltRank",
    "EventKind",
    "FoulVerdict",
    "IBJJFRefereeEngine",
    "MatchDivisionConfig",
    "RawObservedEvent",
    "ScoreboardSnapshot",
    "UniformType",
    "check_technique_legality",
]
