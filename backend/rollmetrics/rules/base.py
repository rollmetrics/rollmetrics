"""Pluggable RulesetEngine protocol and factory for IBJJF, ADCC, and Sparring modes."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from rollmetrics.rules.schema import (
    AdjudicatedEvent,
    AthleteId,
    MatchDivisionConfig,
    RawObservedEvent,
    ScoreboardSnapshot,
)

RULESET_CATALOG: list[dict[str, Any]] = [
    {
        "id": "ibjjf",
        "name": "IBJJF Rules v6.1",
        "short_label": "IBJJF v6.1",
        "summary": "Standard IBJJF points (2/3/4), 3.0s stabilization, advantages, and 4-step penalty cascade.",
        "supports_advantages": True,
        "supports_penalties": True,
    },
    {
        "id": "adcc",
        "name": "ADCC Submission Fighting",
        "short_label": "ADCC",
        "summary": "No-points first half, -1 guard pull/stalling penalties, 2/3/4-pt scoring, zero advantages, overtime.",
        "supports_advantages": False,
        "supports_penalties": True,
    },
    {
        "id": "sparring",
        "name": "Sparring Round (Open Mat)",
        "short_label": "Sparring",
        "summary": "Lightweight rolling tracker logging positional control, submission threats, and tapouts without penalties.",
        "supports_advantages": True,
        "supports_penalties": False,
    },
]


@runtime_checkable
class RulesetEngine(Protocol):
    """Protocol implemented by all deterministic grappling ruleset engines."""

    ruleset_id: str
    division: MatchDivisionConfig

    def ingest_event(self, ev: RawObservedEvent) -> AdjudicatedEvent: ...

    def finalize_regulation(self, referee_decision: AthleteId | None = None) -> ScoreboardSnapshot: ...

    def snapshot(self) -> ScoreboardSnapshot: ...


def get_ruleset_engine(
    ruleset: str,
    division: MatchDivisionConfig,
    index_version: str = "v0.1.0",
) -> RulesetEngine:
    """Instantiate a deterministic ruleset engine by identifier ('ibjjf', 'adcc', or 'sparring')."""
    key = (ruleset or "ibjjf").strip().lower()
    if key in ("ibjjf", "ibjjf_v61", "ibjjf-v6.1"):
        from rollmetrics.rules.state_machine import IBJJFRefereeEngine

        engine = IBJJFRefereeEngine(division=division, index_version=index_version)
        engine.ruleset_id = "ibjjf"
        return engine
    if key == "adcc":
        from rollmetrics.rules.adcc import ADCCRefereeEngine

        return ADCCRefereeEngine(division=division, index_version=index_version)
    if key in ("sparring", "rolling", "open_mat"):
        from rollmetrics.rules.adcc import SparringRoundEngine

        return SparringRoundEngine(division=division, index_version=index_version)
    raise ValueError(f"Unsupported ruleset '{ruleset}'. Expected one of: ibjjf, adcc, sparring.")
