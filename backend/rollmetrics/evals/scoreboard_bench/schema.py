"""Typed schemas and overlay presets for the Scoreboard-Grounded Video Benchmark (v1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from rollmetrics.knowledge.schema import sanitize_text


class ScoreboardLeakageError(RuntimeError):
    """Raised when unmasked scoreboard pixels or readable text survive leakage masking."""


class HoldoutContaminationError(RuntimeError):
    """Raised when a tuning run attempts to evaluate on or report contaminated heldout splits."""


class NormalizedBox(BaseModel):
    """Normalized [0.0, 1.0] rectangle (x0, y0, x1, y1)."""

    x0: float = Field(..., ge=0.0, le=1.0)
    y0: float = Field(..., ge=0.0, le=1.0)
    x1: float = Field(..., ge=0.0, le=1.0)
    y1: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_bounds(self) -> NormalizedBox:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError(f"Invalid NormalizedBox coordinates: ({self.x0}, {self.y0}, {self.x1}, {self.y1})")
        return self

    def to_pixel_rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        px0 = max(0, min(width - 1, round(self.x0 * width)))
        py0 = max(0, min(height - 1, round(self.y0 * height)))
        px1 = max(px0 + 1, min(width, round(self.x1 * width)))
        py1 = max(py0 + 1, min(height, round(self.y1 * height)))
        return px0, py0, px1, py1


class OverlayPreset(BaseModel):
    """Defines both the labeler crop region and the system-under-test blackout mask boxes."""

    name: str
    scoreboard_crop: NormalizedBox
    mask_boxes: list[NormalizedBox]
    description: str = ""


OVERLAY_PRESETS: dict[str, OverlayPreset] = {
    "flograppling_lower_bar": OverlayPreset(
        name="flograppling_lower_bar",
        scoreboard_crop=NormalizedBox(x0=0.08, y0=0.81, x1=0.92, y1=0.97),
        mask_boxes=[
            # Full lower scoreboard bar + athlete names + division header
            NormalizedBox(x0=0.05, y0=0.79, x1=0.95, y1=0.99),
            # Left athlete walkout / accomplishment lower-third card
            NormalizedBox(x0=0.01, y0=0.63, x1=0.42, y1=0.95),
            # Top-right broadcaster watermark bug
            NormalizedBox(x0=0.87, y0=0.02, x1=0.98, y1=0.11),
        ],
        description="FloGrappling IBJJF Worlds, Pans, Euros, Brasileiros, and Crown lower scoreboard bar.",
    ),
    "smoothcomp_lower_bar": OverlayPreset(
        name="smoothcomp_lower_bar",
        scoreboard_crop=NormalizedBox(x0=0.06, y0=0.83, x1=0.94, y1=0.98),
        mask_boxes=[
            NormalizedBox(x0=0.00, y0=0.81, x1=1.00, y1=1.00),
        ],
        description="Smoothcomp livestream overlay displaying names, flags, pts/adv/pen, and clock.",
    ),
    "ibjjf_top_left_bug": OverlayPreset(
        name="ibjjf_top_left_bug",
        scoreboard_crop=NormalizedBox(x0=0.02, y0=0.03, x1=0.52, y1=0.23),
        mask_boxes=[
            NormalizedBox(x0=0.00, y0=0.00, x1=0.56, y1=0.26),
        ],
        description="Upper-left broadcast scoreboard bug layout.",
    ),
}


class MatchManifestEntry(BaseModel):
    """Metadata row stored in knowledge_base/evals/scoreboard_v1/matches.jsonl."""

    match_id: str
    video_id: str
    url: str
    start_sec: float = Field(..., ge=0.0)
    end_sec: float = Field(..., gt=0.0)
    athlete_a: str
    athlete_b: str
    athlete_a_appearance: str = "Blue Gi"
    athlete_b_appearance: str = "White Gi"
    division: str
    belt: Literal["white", "blue", "purple", "brown", "black"] = "black"
    uniform: Literal["gi", "nogi"] = "gi"
    age_division: Literal["juvenile", "adult", "master"] = "adult"
    source: str = "FloGrappling"
    event_name: str = "IBJJF Championship"
    event_group: str = ""
    overlay_preset: str = "flograppling_lower_bar"
    license_note: str = (
        "Public YouTube broadcast reference metadata only; raw video stored locally in "
        "gitignored data/runtime/eval_media/ and never redistributed."
    )

    @model_validator(mode="after")
    def _clean(self) -> MatchManifestEntry:
        self.athlete_a = sanitize_text(self.athlete_a)
        self.athlete_b = sanitize_text(self.athlete_b)
        self.division = sanitize_text(self.division)
        self.event_name = sanitize_text(self.event_name)
        self.license_note = sanitize_text(self.license_note)
        if self.end_sec <= self.start_sec:
            raise ValueError(f"end_sec ({self.end_sec}) must exceed start_sec ({self.start_sec})")
        return self

    def split_group_key(self) -> str:
        """Groups by tournament event/video source so shared-event or multi-match videos never split."""
        grp = (self.event_group or self.event_name or self.video_id).strip().lower()
        return f"{self.source.lower()}::{grp}"


class CompactScore(BaseModel):
    points: int = 0
    advantages: int = 0
    penalties: int = 0


class ScoreDeltaEvent(BaseModel):
    """Single scoreboard change extracted solely from the broadcast scoreboard crop."""

    match_elapsed_sec: float = Field(..., ge=0.0)
    video_sec: float = Field(..., ge=0.0)
    match_clock_str: str = ""
    athlete: Literal["A", "B"]
    pts_delta: int = 0
    adv_delta: int = 0
    pen_delta: int = 0
    score_after: dict[str, int] = Field(default_factory=dict)


class GroundTruthMatchLabel(BaseModel):
    """Scoreboard-crop derived ground-truth timeline and outcome for one match."""

    match_id: str
    video_id: str
    active_bout_start_sec: float = Field(..., ge=0.0)
    active_bout_end_sec: float = Field(..., gt=0.0)
    athlete_a_appearance: str = "Blue Gi"
    athlete_b_appearance: str = "White Gi"
    events: list[ScoreDeltaEvent] = Field(default_factory=list)
    final_score_a: CompactScore = Field(default_factory=CompactScore)
    final_score_b: CompactScore = Field(default_factory=CompactScore)
    winner: Literal["A", "B", "draw", "referee_decision"]
    method: Literal["points", "advantages", "penalties", "submission", "dq", "referee_decision"]
    labeler_id: str = "scoreboard_crop_ocr_v1"
    human_spot_checked: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def _clean_notes(self) -> GroundTruthMatchLabel:
        self.notes = sanitize_text(self.notes)
        return self


class PredictedScoreEvent(BaseModel):
    """Predicted score delta emitted by running a model's RawObservedEvents through IBJJFRefereeEngine."""

    match_elapsed_sec: float = Field(..., ge=0.0)
    athlete: Literal["A", "B"]
    pts_delta: int = 0
    adv_delta: int = 0
    pen_delta: int = 0
    raw_kind: str = ""


class MatchPredictionRecord(BaseModel):
    """System-under-test output on a strictly scoreboard-masked match."""

    match_id: str
    model_id: str
    events: list[PredictedScoreEvent] = Field(default_factory=list)
    final_score_a: CompactScore = Field(default_factory=CompactScore)
    final_score_b: CompactScore = Field(default_factory=CompactScore)
    winner: Literal["A", "B", "draw", "referee_decision"] = "referee_decision"
    method: str = ""
    leakage_verified: bool = True
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class ScoreboardSplitManifest(BaseModel):
    """Deterministic source-grouped split manifest with cryptographic content hash."""

    benchmark_id: str = "rollmetrics-scoreboard-v1"
    split_unit: str = "event_video_source"
    calibration_ratio_target: float = 0.30
    total_matches: int
    calibration_count: int
    heldout_count: int
    calibration_match_ids: list[str]
    heldout_match_ids: list[str]
    calibration_video_ids: list[str]
    heldout_video_ids: list[str]
    matches_sha256: str
    splits_content_sha256: str


class ScoreboardBenchMetrics(BaseModel):
    """Aggregate accuracy, MAE, event F1, latency, and cost metrics for one model on one split."""

    model_id: str
    split: Literal["calibration", "heldout", "pilot"]
    match_count: int
    winner_accuracy: float
    final_score_exact_match: float
    mae_points: float
    mae_advantages: float
    mae_penalties: float
    score_event_precision: float
    score_event_recall: float
    score_event_f1: float
    event_tolerance_sec: float = 5.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    mean_latency_ms: float = 0.0
    mean_cost_per_match_usd: float = 0.0
    total_cost_usd: float = 0.0
    leakage_checks_passed: int = 0
