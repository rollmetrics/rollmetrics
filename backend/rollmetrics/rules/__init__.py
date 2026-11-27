"""IBJJF, ADCC, and Sparring rules engines, technique legality matrix, and scoreboard schemas."""

from rollmetrics.rules.adcc import ADCCRefereeEngine, SparringRoundEngine
from rollmetrics.rules.base import RULESET_CATALOG, RulesetEngine, get_ruleset_engine
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
    "RULESET_CATALOG",
    "ADCCRefereeEngine",
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
    "RulesetEngine",
    "ScoreboardSnapshot",
    "SparringRoundEngine",
    "UniformType",
    "check_technique_legality",
    "get_ruleset_engine",
]
