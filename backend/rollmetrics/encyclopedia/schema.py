"""Typed Pydantic schemas for the IBJJF Match Encyclopedia and 4-layer annotations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

BeltValue = Literal["white", "blue", "purple", "brown", "black", "unknown"]
UniformValue = Literal["gi", "nogi", "unknown"]
AgeDivisionValue = Literal["juvenile", "adult", "master", "kids", "unknown"]
FootageTypeValue = Literal["broadcast_scoreboard", "matside_raw", "unknown"]
VideoKindValue = Literal["standalone_match", "multi_match_compilation", "full_mat_stream", "non_match"]


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class VideoRecord(BaseModel):
    """Raw harvested YouTube video metadata."""

    video_id: str
    channel: str
    channel_id: str = ""
    url: str
    title: str
    description: str = ""
    duration_sec: float = 0.0
    upload_date: str = ""
    view_count: int = 0
    video_kind: VideoKindValue = "standalone_match"
    chapters: list[dict[str, object]] = Field(default_factory=list)
    harvest_source: str = ""
    harvested_at: str = Field(default_factory=utc_now_iso)


class MatchRecord(BaseModel):
    """Single cataloged IBJJF ruleset match inside the encyclopedia."""

    match_id: str
    video_id: str
    url: str
    start_sec: float = Field(default=0.0, ge=0.0)
    end_sec: float = Field(default=0.0, ge=0.0)
    duration_sec: float = Field(default=0.0, ge=0.0)
    athlete_a: str = "Unknown"
    athlete_b: str = "Unknown"
    event_name: str = "IBJJF Tournament"
    event_year: int | None = None
    event_group: str = ""
    division: str = ""
    weight_class: str = ""
    round_name: str = ""
    belt: BeltValue = "unknown"
    uniform: UniformValue = "unknown"
    age_division: AgeDivisionValue = "unknown"
    source: str = ""
    footage_type: FootageTypeValue = "unknown"
    overlay_preset: str = ""
    tier: int = Field(default=0, ge=0, le=2)
    in_scoreboard_v1: bool = False
    gcs_raw_uri: str = ""
    gcs_masked_uri: str = ""
    gcs_annotation_uri: str = ""
    title_raw: str = ""
    notes: str = ""


class ScoringRuleEvent(BaseModel):
    """Layer 1: IBJJF scoring, advantage, penalty, stalling, stabilization, or finish event."""

    video_sec: float = Field(..., ge=0.0)
    match_elapsed_sec: float = Field(default=0.0, ge=0.0)
    athlete: Literal["A", "B", "both"] = "A"
    event_type: Literal[
        "takedown",
        "sweep",
        "knee_on_belly",
        "guard_pass",
        "mount",
        "back_control",
        "back_mount",
        "advantage",
        "penalty",
        "stalling_clock",
        "stabilization_window",
        "submission",
        "dq",
        "referee_decision",
    ]
    points: int = 0
    advantages: int = 0
    penalties: int = 0
    submission_type: str = ""
    stabilization_sec: float | None = None
    notes: str = ""


class TechniqueEvent(BaseModel):
    """Layer 2: Timestamped positional state and grappling transition."""

    video_sec: float = Field(..., ge=0.0)
    end_sec: float | None = None
    actor: Literal["A", "B", "both"] = "A"
    category: Literal[
        "guard_pull",
        "guard_state",
        "takedown",
        "sweep",
        "pass",
        "mount",
        "back_take",
        "escape",
        "submission_attempt",
        "submission_finish",
        "reset",
    ]
    technique_name: str
    guard_family: str = ""
    notes: str = ""


class RefereeSignalEvent(BaseModel):
    """Referee hand signal observation for vision calibration."""

    video_sec: float = Field(..., ge=0.0)
    signal_type: Literal[
        "start_combate",
        "stop_parou",
        "points_2",
        "points_3",
        "points_4",
        "advantage",
        "penalty",
        "stalling_20s",
        "dq",
        "winner_raise",
    ]
    athlete: Literal["A", "B", "both", "unknown"] = "unknown"


class VisionCalibrationMetadata(BaseModel):
    """Layer 3: Appearance, camera angle, overlay geometry, and referee signals."""

    athlete_a_appearance: str = ""
    athlete_b_appearance: str = ""
    camera_angle: Literal["broadcast_elevated", "matside_static", "matside_handheld", "overhead", "unknown"] = "unknown"
    overlay_preset: str = ""
    overlay_verified: bool = False
    leakage_verified: bool = False
    referee_signals: list[RefereeSignalEvent] = Field(default_factory=list)


class RuleEdgeCaseTag(BaseModel):
    """Layer 4: High-value IBJJF rule edge case or foul span."""

    video_sec: float = Field(..., ge=0.0)
    end_sec: float | None = None
    category: Literal[
        "knee_reaping",
        "illegal_lock_for_belt",
        "double_guard_pull",
        "out_of_bounds_reset",
        "stalling_20s",
        "stabilization_borderline",
        "advantage_vs_pass_borderline",
        "fleeing_mat",
        "slam",
        "other",
    ]
    ibjjf_rule_ref: str = ""
    description: str = ""


class MatchAnnotationBundle(BaseModel):
    """Combined 4-layer annotation payload stored in SQLite and GCS."""

    match_id: str
    annotator_id: str = "tier1_auto_v1"
    annotated_at: str = Field(default_factory=utc_now_iso)
    tier: int = Field(default=1, ge=1, le=2)
    scoring_events: list[ScoringRuleEvent] = Field(default_factory=list)
    technique_events: list[TechniqueEvent] = Field(default_factory=list)
    vision_metadata: VisionCalibrationMetadata = Field(default_factory=VisionCalibrationMetadata)
    rule_edge_cases: list[RuleEdgeCaseTag] = Field(default_factory=list)
    ocr_agreement_score: float | None = None
    summary: str = ""
