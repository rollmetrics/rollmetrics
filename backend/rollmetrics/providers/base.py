"""Shared schemas, prompts, JSON parsers, and abstract base class for vision providers."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from rollmetrics.rules.schema import (
    AthleteId,
    EventKind,
    MatchDivisionConfig,
    RawObservedEvent,
)


class ProviderTelemetry(BaseModel):
    model_id: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0

EVENT_KIND_VALUES = [e.value for e in EventKind]

RAW_EVENT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "timestamp_sec": {"type": "number"},
        "match_clock_str": {"type": "string"},
        "actor": {"type": "string", "enum": ["A", "B"]},
        "kind": {"type": "string", "enum": EVENT_KIND_VALUES},
        "technique_slug": {"type": "string"},
        "hold_duration_sec": {"type": "number"},
        "in_bounds": {"type": "boolean"},
        "opponent_locked_submission": {"type": "boolean"},
        "confidence": {"type": "number"},
        "occluded": {"type": "boolean"},
        "summary": {"type": "string"},
        "position_after": {"type": "string"},
    },
    "required": ["timestamp_sec", "actor", "kind", "hold_duration_sec", "in_bounds", "confidence"],
}

FRAME_OBSERVATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "current_position": {"type": "string"},
        "top_or_dominant_actor": {"type": "string", "enum": ["A", "B", "none"]},
        "stabilizing_scoring_kind": {
            "type": "string",
            "enum": [
                "none",
                "takedown",
                "sweep",
                "knee_on_belly",
                "guard_pass",
                "mount",
                "back_control",
            ],
        },
        "active_combativeness_a": {"type": "boolean"},
        "active_combativeness_b": {"type": "boolean"},
        "in_bounds": {"type": "boolean"},
        "occluded": {"type": "boolean"},
        "confidence": {"type": "number"},
        "corner_cue": {"type": "string"},
        "detected_events": {"type": "array", "items": RAW_EVENT_ITEM_SCHEMA},
    },
    "required": [
        "current_position",
        "top_or_dominant_actor",
        "stabilizing_scoring_kind",
        "active_combativeness_a",
        "active_combativeness_b",
        "in_bounds",
        "confidence",
        "corner_cue",
    ],
}

VIDEO_ANALYSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "events": {"type": "array", "items": RAW_EVENT_ITEM_SCHEMA},
        "coaching_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp_sec": {"type": "number"},
                    "position": {"type": "string"},
                    "headline": {"type": "string"},
                    "cta_detail": {"type": "string"},
                },
                "required": ["position", "headline", "cta_detail"],
            },
        },
    },
    "required": ["summary", "events"],
}

ADJUDICATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rulings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_index": {"type": "integer"},
                    "confirmed_kind": {"type": "string", "enum": EVENT_KIND_VALUES},
                    "hold_duration_sec": {"type": "number"},
                    "in_bounds": {"type": "boolean"},
                    "technique_slug": {"type": "string"},
                    "confidence": {"type": "number"},
                    "ruling_note": {"type": "string"},
                },
                "required": [
                    "event_index",
                    "confirmed_kind",
                    "hold_duration_sec",
                    "in_bounds",
                    "confidence",
                    "ruling_note",
                ],
            },
        },
        "cta_synthesis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "position": {"type": "string"},
                    "headline": {"type": "string"},
                    "cta_detail": {"type": "string"},
                },
                "required": ["position", "headline", "cta_detail"],
            },
        },
    },
    "required": ["rulings", "cta_synthesis"],
}


class FrameObservation(BaseModel):
    elapsed_sec: float = 0.0
    match_clock_str: str = ""
    current_position: str = "Standing"
    top_or_dominant_actor: Literal["A", "B", "none"] = "none"
    stabilizing_scoring_kind: EventKind | None = None
    active_combativeness_a: bool = True
    active_combativeness_b: bool = True
    in_bounds: bool = True
    occluded: bool = False
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    detected_events: list[RawObservedEvent] = Field(default_factory=list)
    corner_cue: str = ""
    model_used: str = ""
    model_id: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    telemetry: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sync_telemetry(self) -> FrameObservation:
        if self.model_id and not self.model_used:
            self.model_used = self.model_id
        elif self.model_used and not self.model_id:
            self.model_id = self.model_used
        base_tel = {
            "model_id": self.model_id,
            "latency_ms": round(float(self.latency_ms), 2),
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
        }
        self.telemetry = {**self.telemetry, **base_tel}
        return self


class VideoVisionResult(BaseModel):
    events: list[RawObservedEvent] = Field(default_factory=list)
    coaching_notes: list[dict[str, Any]] = Field(default_factory=list)
    dispute_rulings: list[dict[str, Any]] = Field(default_factory=list)
    model_used: str = ""
    model_id: str = ""
    adjudicator_model_used: str = ""
    summary: str = ""
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    telemetry: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sync_telemetry(self) -> VideoVisionResult:
        if self.model_id and not self.model_used:
            self.model_used = self.model_id
        elif self.model_used and not self.model_id:
            self.model_id = self.model_used
        base_tel = {
            "model_id": self.model_id,
            "latency_ms": round(float(self.latency_ms), 2),
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
        }
        self.telemetry = {**self.telemetry, **base_tel}
        return self


def parse_json_lenient(raw_text: str) -> dict[str, Any]:
    """Extracts a JSON object from model output, tolerating optional markdown code fences."""
    text = (raw_text or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"data": parsed}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def coerce_raw_event(item: dict[str, Any], default_ts: float = 0.0) -> RawObservedEvent | None:
    """Safely converts a model-emitted event dictionary into a validated RawObservedEvent."""
    if not isinstance(item, dict):
        return None
    raw_actor = str(item.get("actor", "A")).strip().upper()
    actor = AthleteId.ATHLETE_B if raw_actor == "B" else AthleteId.ATHLETE_A

    raw_kind = str(item.get("kind", "position_change")).strip().lower()
    try:
        kind = EventKind(raw_kind)
    except ValueError:
        kind = EventKind.POSITION_CHANGE

    ts = max(0.0, float(item.get("timestamp_sec", default_ts) or 0.0))
    hold = max(0.0, float(item.get("hold_duration_sec", 3.0)))
    conf = min(1.0, max(0.0, float(item.get("confidence", 0.85))))

    return RawObservedEvent(
        timestamp_sec=ts,
        match_clock_str=str(item.get("match_clock_str", "") or ""),
        actor=actor,
        kind=kind,
        technique_slug=str(item.get("technique_slug", "") or ""),
        hold_duration_sec=hold,
        in_bounds=bool(item.get("in_bounds", True)),
        opponent_locked_submission=bool(item.get("opponent_locked_submission", False)),
        confidence=conf,
        occluded=bool(item.get("occluded", False)),
        summary=str(item.get("summary", "") or ""),
        position_after=str(item.get("position_after", "") or ""),
    )


def coerce_frame_observation(
    data: dict[str, Any],
    elapsed_sec: float,
    model_used: str,
    latency_ms: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    telemetry: dict[str, Any] | None = None,
) -> FrameObservation:
    """Normalizes model JSON output into a typed FrameObservation."""
    stab_raw = str(data.get("stabilizing_scoring_kind", "none") or "none").strip().lower()
    stab_kind: EventKind | None = None
    if stab_raw and stab_raw != "none":
        try:
            candidate = EventKind(stab_raw)
            if candidate in (
                EventKind.TAKEDOWN,
                EventKind.SWEEP,
                EventKind.KNEE_ON_BELLY,
                EventKind.GUARD_PASS,
                EventKind.MOUNT,
                EventKind.BACK_CONTROL,
            ):
                stab_kind = candidate
        except ValueError:
            stab_kind = None

    dom_raw = str(data.get("top_or_dominant_actor", "none") or "none").strip().upper()
    dom: Literal["A", "B", "none"] = "A" if dom_raw == "A" else ("B" if dom_raw == "B" else "none")

    events: list[RawObservedEvent] = []
    for ev_item in data.get("detected_events", []) or []:
        ev = coerce_raw_event(ev_item, default_ts=elapsed_sec)
        if ev is not None:
            events.append(ev)

    conf = min(1.0, max(0.0, float(data.get("confidence", 0.85))))
    return FrameObservation(
        elapsed_sec=elapsed_sec,
        match_clock_str=str(data.get("match_clock_str", "") or ""),
        current_position=str(data.get("current_position", "Standing") or "Standing"),
        top_or_dominant_actor=dom,
        stabilizing_scoring_kind=stab_kind,
        active_combativeness_a=bool(data.get("active_combativeness_a", True)),
        active_combativeness_b=bool(data.get("active_combativeness_b", True)),
        in_bounds=bool(data.get("in_bounds", True)),
        occluded=bool(data.get("occluded", False)),
        confidence=conf,
        detected_events=events,
        corner_cue=str(data.get("corner_cue", "") or ""),
        model_used=model_used,
        model_id=model_used,
        latency_ms=round(float(latency_ms), 2),
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        telemetry=dict(telemetry or {}),
    )


def build_division_context_prompt(division: MatchDivisionConfig) -> str:
    locked = division.locked_athlete.value if division.locked_athlete else "A"
    locked_name = division.athlete_a_name if locked == "A" else division.athlete_b_name
    return (
        f"IBJJF Division: {division.age_division.value.title()} {division.belt.value.title()} Belt "
        f"({division.uniform.value.upper()}), Weight {division.weight_class}, "
        f"Regulation {division.resolved_duration_sec()}s.\n"
        f"Athlete A: {division.athlete_a_name} ({division.athlete_a_appearance}).\n"
        f"Athlete B: {division.athlete_b_name} ({division.athlete_b_appearance}).\n"
        f"Locked Coaching Target: Athlete {locked} ({locked_name}). "
        f"Provide concise Caio Terra Association (CTA) technical leverage, frame, grip, and hip-angle cues."
    )


class VisionProvider(ABC):
    """Unified async interface across Cloud Gemini 4 Interactions API and Local Ollama Gemma."""

    profile_name: str = "base"

    @abstractmethod
    async def analyze_live_frame(
        self,
        jpeg_bytes: bytes,
        division: MatchDivisionConfig,
        elapsed_sec: float = 0.0,
        current_position: str = "Standing",
        audio_transcript: str = "",
    ) -> FrameObservation:
        """Analyzes a single live mat frame for position, stabilization, stalling, and CTA corner cues."""

    @abstractmethod
    async def analyze_video(
        self,
        video_path: Path,
        division: MatchDivisionConfig,
        sample_fps: float = 0.5,
        burst_frames: list[tuple[float, bytes]] | None = None,
    ) -> VideoVisionResult:
        """Analyzes a pre-recorded match video into chronological IBJJF events and CTA notes."""

    @abstractmethod
    async def adjudicate_dispute_or_synthesize(
        self,
        events: list[RawObservedEvent],
        division: MatchDivisionConfig,
        ambiguous_indices: list[int] | None = None,
    ) -> dict[str, Any]:
        """Reviews ambiguous foul or 3-second boundary events and synthesizes CTA coaching notes."""
