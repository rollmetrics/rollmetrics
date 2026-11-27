"""Live Mat Referee & CTA Corner HUD session engine (3.0s stabilization + 20.0s stalling)."""

from __future__ import annotations

import asyncio
from typing import Any

from rollmetrics.config import Settings, get_settings
from rollmetrics.providers.base import FrameObservation, VisionProvider
from rollmetrics.providers.factory import get_vision_provider
from rollmetrics.rules.base import get_ruleset_engine
from rollmetrics.rules.schema import (
    AdjudicatedEvent,
    AthleteId,
    EventKind,
    MatchDivisionConfig,
    RawObservedEvent,
)
from rollmetrics.rules.state_machine import (
    MIN_STABILIZATION_SEC,
    POINT_VALUES,
)
from rollmetrics.video.analyzer import (
    load_optional_retriever,
    lookup_cta_recommendations,
)

STALLING_THRESHOLD_SEC = 20.0
ADVANTAGE_MIN_HOLD_SEC = 0.8

_POSITION_SCORING_RULES: tuple[tuple[tuple[str, ...], EventKind], ...] = (
    (("back control", "back mount", "rear mount"), EventKind.BACK_CONTROL),
    (("mount",), EventKind.MOUNT),
    (("knee on belly", "kob"), EventKind.KNEE_ON_BELLY),
    (("side control", "north south", "guard pass", "kesa"), EventKind.GUARD_PASS),
    (("sweep",), EventKind.SWEEP),
    (("takedown",), EventKind.TAKEDOWN),
)


def _normalize_position_key(position: str) -> str:
    return " ".join((position or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _infer_scoring_kind_from_position(position: str) -> EventKind | None:
    norm = _normalize_position_key(position)
    if not norm:
        return None
    for needles, kind in _POSITION_SCORING_RULES:
        if any(needle in norm for needle in needles):
            return kind
    return None


class LiveRefereeSession:
    """Real-time IBJJF referee state tracker and locked-athlete CTA corner assistant."""

    def __init__(
        self,
        division: MatchDivisionConfig | dict[str, Any] | None = None,
        profile: str = "local",
        settings: Settings | None = None,
        retriever: Any | None = None,
        provider: VisionProvider | None = None,
        min_vision_interval_sec: float = 1.0,
    ) -> None:
        self.settings = settings or get_settings()
        if division is None:
            self.division = MatchDivisionConfig()
        elif isinstance(division, dict):
            self.division = MatchDivisionConfig.model_validate(division)
        else:
            self.division = division

        self.profile = (profile or self.settings.profile or "local").strip().lower()
        self._explicit_retriever = retriever
        self._lazy_retriever_loaded = False
        self._lazy_retriever: Any | None = None
        self._provider = provider
        self.min_vision_interval_sec = max(0.0, float(min_vision_interval_sec))

        self.engine = get_ruleset_engine(
            self.division.ruleset,
            division=self.division,
            index_version=self.settings.active_index_version,
        )
        self.stalling_watch_sec: dict[str, float] = {"A": 0.0, "B": 0.0}
        self._combative_state: dict[str, bool] = {"A": True, "B": True}
        self._active_stab: dict[str, Any] | None = None
        self._last_awarded_stab_key: tuple[str, str] | None = None

        self._last_frame_sec: float | None = None
        self._last_vision_sec: float | None = None
        self._vision_in_flight: bool = False
        self._vision_lock = asyncio.Lock()
        self._latest_corner_cue: str = ""
        self._latest_model_used: str = ""
        self._latest_telemetry: dict[str, Any] = {}
        self._recent_events: list[dict[str, Any]] = []
        self._audio_alerts: list[str] = []

    @property
    def retriever(self) -> Any | None:
        if self._explicit_retriever is not None:
            return self._explicit_retriever
        if not self._lazy_retriever_loaded:
            self._lazy_retriever = load_optional_retriever(self.settings)
            self._lazy_retriever_loaded = True
        return self._lazy_retriever

    @property
    def provider(self) -> VisionProvider:
        if self._provider is None:
            self._provider = get_vision_provider(profile=self.profile, settings=self.settings)
        return self._provider

    def _enrich_adjudicated(self, adj: AdjudicatedEvent, position: str) -> AdjudicatedEvent:
        locked = self.division.locked_athlete or AthleteId.ATHLETE_A
        situation = (
            f"live corner finish {adj.raw_kind.value}"
            if adj.actor == locked
            else f"live corner escape defend {adj.raw_kind.value}"
        )
        recs = lookup_cta_recommendations(
            self.retriever,
            position=position,
            situation=situation,
            belt=self.division.belt.value,
            top_k=2,
        )
        enriched = adj.model_copy(update={"cta_recommendations": recs})
        if self.engine.timeline and self.engine.timeline[-1].event_id == adj.event_id:
            self.engine.timeline[-1] = enriched
        return enriched

    def _emit_internal_event(self, ev: RawObservedEvent) -> AdjudicatedEvent:
        adj = self.engine.ingest_event(ev)
        enriched = self._enrich_adjudicated(adj, self.engine.current_position)
        self._recent_events.append(enriched.model_dump(mode="json"))
        return enriched

    def _advance_stalling_clocks(self, dt: float, elapsed_sec: float) -> None:
        if dt <= 0 or self.engine.ended:
            return
        for key in ("A", "B"):
            if not self._combative_state[key]:
                self.stalling_watch_sec[key] = round(self.stalling_watch_sec[key] + dt, 2)
            else:
                self.stalling_watch_sec[key] = 0.0

        a_stall = self.stalling_watch_sec["A"] >= STALLING_THRESHOLD_SEC
        b_stall = self.stalling_watch_sec["B"] >= STALLING_THRESHOLD_SEC
        if a_stall and b_stall:
            self.stalling_watch_sec["A"] = 0.0
            self.stalling_watch_sec["B"] = 0.0
            self._emit_internal_event(
                RawObservedEvent(
                    timestamp_sec=elapsed_sec,
                    actor=AthleteId.ATHLETE_A,
                    kind=EventKind.DOUBLE_STALLING,
                    technique_slug="stalling",
                    summary="20.0s simultaneous lack of combativeness on both athletes.",
                    position_after=self.engine.current_position,
                )
            )
        elif a_stall:
            self.stalling_watch_sec["A"] = 0.0
            self._emit_internal_event(
                RawObservedEvent(
                    timestamp_sec=elapsed_sec,
                    actor=AthleteId.ATHLETE_A,
                    kind=EventKind.MINOR_FOUL,
                    technique_slug="stalling",
                    summary="20.0s stalling clock expired on Athlete A.",
                    position_after=self.engine.current_position,
                )
            )
        elif b_stall:
            self.stalling_watch_sec["B"] = 0.0
            self._emit_internal_event(
                RawObservedEvent(
                    timestamp_sec=elapsed_sec,
                    actor=AthleteId.ATHLETE_B,
                    kind=EventKind.MINOR_FOUL,
                    technique_slug="stalling",
                    summary="20.0s stalling clock expired on Athlete B.",
                    position_after=self.engine.current_position,
                )
            )

    def _update_stabilization(
        self,
        actor: AthleteId | None,
        kind: EventKind | None,
        elapsed_sec: float,
        in_bounds: bool,
        confidence: float = 0.90,
        occluded: bool = False,
    ) -> None:
        if self.engine.ended:
            self._active_stab = None
            return

        valid_scoring = actor is not None and kind in POINT_VALUES
        current_key = (actor.value, kind.value) if (actor and kind) else None

        # Check if previous active stabilization was interrupted before 3.0s
        if self._active_stab is not None:
            prev_key = (self._active_stab["actor"], self._active_stab["kind"])
            if not valid_scoring or current_key != prev_key or not in_bounds:
                held = max(0.0, elapsed_sec - float(self._active_stab["started_sec"]))
                if held >= ADVANTAGE_MIN_HOLD_SEC or not in_bounds:
                    self._emit_internal_event(
                        RawObservedEvent(
                            timestamp_sec=elapsed_sec,
                            actor=AthleteId(self._active_stab["actor"]),
                            kind=EventKind(self._active_stab["kind"]),
                            hold_duration_sec=min(held, 2.9),
                            in_bounds=in_bounds,
                            confidence=confidence,
                            occluded=occluded,
                            position_after=self.engine.current_position,
                        )
                    )
                self._active_stab = None

        if not valid_scoring or not in_bounds or actor is None or kind is None:
            if not valid_scoring:
                self._last_awarded_stab_key = None
            return

        # Prevent re-awarding the exact same already-scored hold until transition changes
        if self._last_awarded_stab_key == current_key:
            return

        if self._active_stab is None:
            self._active_stab = {
                "actor": actor.value,
                "kind": kind.value,
                "started_sec": elapsed_sec,
                "held_sec": 0.0,
                "target_sec": MIN_STABILIZATION_SEC,
                "progress": 0.0,
                "in_bounds": True,
                "confidence": confidence,
                "occluded": occluded,
            }
            return

        held_sec = round(max(0.0, elapsed_sec - float(self._active_stab["started_sec"])), 2)
        progress = round(min(1.0, held_sec / MIN_STABILIZATION_SEC), 3)
        self._active_stab["held_sec"] = held_sec
        self._active_stab["progress"] = progress
        self._active_stab["confidence"] = confidence
        self._active_stab["occluded"] = occluded

        if held_sec >= MIN_STABILIZATION_SEC:
            self._emit_internal_event(
                RawObservedEvent(
                    timestamp_sec=elapsed_sec,
                    actor=actor,
                    kind=kind,
                    hold_duration_sec=held_sec,
                    in_bounds=True,
                    confidence=confidence,
                    occluded=occluded,
                    position_after=self.engine.current_position,
                )
            )
            self._last_awarded_stab_key = current_key
            self._active_stab = None
            self.stalling_watch_sec[actor.value] = 0.0

    def _tick_ongoing_stabilization_without_vision(self, elapsed_sec: float) -> None:
        if self._active_stab is None or self.engine.ended:
            return
        actor = AthleteId(self._active_stab["actor"])
        kind = EventKind(self._active_stab["kind"])
        self._update_stabilization(
            actor=actor,
            kind=kind,
            elapsed_sec=elapsed_sec,
            in_bounds=bool(self._active_stab.get("in_bounds", True)),
            confidence=float(self._active_stab.get("confidence", 0.9)),
            occluded=bool(self._active_stab.get("occluded", False)),
        )

    def _process_audio_transcript(self, transcript: str, elapsed_sec: float) -> None:
        clean = (transcript or "").strip().lower()
        if not clean:
            return
        if any(tok in clean for tok in ("parou", "stop", "mate", "time")):
            self._audio_alerts.append(f"[{elapsed_sec:.1f}s] Referee command detected: '{transcript.strip()}'")
        if "tap" in clean:
            self._audio_alerts.append(f"[{elapsed_sec:.1f}s] Potential verbal tap heard: '{transcript.strip()}'")
        if "lute" in clean or "combate" in clean:
            self._audio_alerts.append(f"[{elapsed_sec:.1f}s] Referee combativeness cue: '{transcript.strip()}'")

    def _apply_observation(self, obs: FrameObservation, elapsed_sec: float) -> None:
        prev_position = self.engine.current_position
        prior_recent_count = len(self._recent_events)
        resolved_model = obs.model_id or obs.model_used
        self._latest_model_used = resolved_model
        self._latest_telemetry = {
            **obs.telemetry,
            "model_id": resolved_model,
            "latency_ms": round(float(obs.latency_ms), 2),
            "input_tokens": int(obs.input_tokens),
            "output_tokens": int(obs.output_tokens),
        }
        if obs.current_position:
            self.engine.current_position = obs.current_position
        if obs.corner_cue:
            self._latest_corner_cue = obs.corner_cue

        self._combative_state["A"] = obs.active_combativeness_a
        self._combative_state["B"] = obs.active_combativeness_b

        stab_actor: AthleteId | None = None
        if obs.top_or_dominant_actor == "A":
            stab_actor = AthleteId.ATHLETE_A
        elif obs.top_or_dominant_actor == "B":
            stab_actor = AthleteId.ATHLETE_B

        stab_kind = obs.stabilizing_scoring_kind
        if stab_kind is None and stab_actor is not None:
            stab_kind = _infer_scoring_kind_from_position(obs.current_position)

        self._update_stabilization(
            actor=stab_actor,
            kind=stab_kind,
            elapsed_sec=elapsed_sec,
            in_bounds=obs.in_bounds,
            confidence=obs.confidence,
            occluded=obs.occluded,
        )

        for ev in obs.detected_events:
            self._emit_internal_event(ev)

        if (
            not self.engine.ended
            and len(self._recent_events) == prior_recent_count
            and obs.current_position
            and _normalize_position_key(obs.current_position) != _normalize_position_key(prev_position)
        ):
            self._emit_internal_event(
                RawObservedEvent(
                    timestamp_sec=elapsed_sec,
                    match_clock_str=obs.match_clock_str,
                    actor=stab_actor or AthleteId.ATHLETE_A,
                    kind=EventKind.POSITION_CHANGE,
                    technique_slug=_normalize_position_key(obs.current_position).replace(" ", "_"),
                    confidence=obs.confidence,
                    occluded=obs.occluded,
                    summary=f"Transition from {prev_position} to {obs.current_position}.",
                    position_after=obs.current_position,
                )
            )

    async def process_frame(
        self,
        jpeg_bytes: bytes,
        elapsed_sec: float,
        audio_transcript: str = "",
    ) -> dict[str, Any]:
        """Processes a live frame with throttled vision calls, 3s stabilization, and 20s stalling."""
        self._recent_events = []
        clamped_elapsed = max(0.0, float(elapsed_sec))
        dt = (
            max(0.0, clamped_elapsed - self._last_frame_sec)
            if self._last_frame_sec is not None
            else 0.0
        )
        self._last_frame_sec = clamped_elapsed
        self.engine.elapsed_sec = max(
            self.engine.elapsed_sec,
            min(self.engine.duration_sec, clamped_elapsed),
        )

        self._process_audio_transcript(audio_transcript, clamped_elapsed)

        should_call_vision = (
            not self.engine.ended
            and bool(jpeg_bytes)
            and not self._vision_in_flight
            and (
                self._last_vision_sec is None
                or (clamped_elapsed - self._last_vision_sec) >= self.min_vision_interval_sec
            )
        )

        vision_throttled = not should_call_vision
        if should_call_vision:
            self._vision_in_flight = True
            try:
                async with self._vision_lock:
                    obs = await self.provider.analyze_live_frame(
                        jpeg_bytes=jpeg_bytes,
                        division=self.division,
                        elapsed_sec=clamped_elapsed,
                        current_position=self.engine.current_position,
                        audio_transcript=audio_transcript,
                    )
                    self._last_vision_sec = clamped_elapsed
                    self._apply_observation(obs, clamped_elapsed)
            except Exception as exc:  # noqa: BLE001
                self._audio_alerts.append(f"[{clamped_elapsed:.1f}s] Vision fallback ({type(exc).__name__})")
                self._latest_telemetry["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                self._tick_ongoing_stabilization_without_vision(clamped_elapsed)
            finally:
                self._vision_in_flight = False
        else:
            self._tick_ongoing_stabilization_without_vision(clamped_elapsed)

        self._advance_stalling_clocks(dt, clamped_elapsed)
        hud = self.current_hud_state()
        hud["vision_throttled"] = vision_throttled
        return hud

    def ingest_manual_or_detected_event(
        self,
        ev: RawObservedEvent | dict[str, Any],
    ) -> dict[str, Any]:
        """Immediately applies a manual referee trigger or external event to the live scoreboard."""
        self._recent_events = []
        parsed = RawObservedEvent.model_validate(ev) if isinstance(ev, dict) else ev
        self._last_frame_sec = max(self._last_frame_sec or 0.0, parsed.timestamp_sec)
        self.stalling_watch_sec[parsed.actor.value] = 0.0
        self._emit_internal_event(parsed)
        return self.current_hud_state()

    def set_locked_athlete(self, athlete: AthleteId | str | None) -> dict[str, Any]:
        """Updates which athlete receives live CTA corner coaching cues."""
        if athlete is None or athlete == "":
            resolved: AthleteId | None = None
        elif isinstance(athlete, AthleteId):
            resolved = athlete
        else:
            resolved = AthleteId(str(athlete).strip().upper())
        self.division = self.division.model_copy(update={"locked_athlete": resolved})
        self.engine.division = self.division
        return self.current_hud_state()

    def lock_athlete(self, athlete: AthleteId | str | None) -> dict[str, Any]:
        return self.set_locked_athlete(athlete)

    def enter_overtime(self, overtime_sec: float = 300.0) -> dict[str, Any]:
        """Enters overtime when the active ruleset supports it (e.g. ADCC)."""
        if hasattr(self.engine, "enter_overtime") and callable(self.engine.enter_overtime):
            self.engine.enter_overtime(overtime_sec=overtime_sec)
        self._active_stab = None
        return self.current_hud_state()

    def finalize(self) -> dict[str, Any]:
        """Ends regulation time and resolves winner per active ruleset."""
        self.engine.finalize_regulation()
        self._active_stab = None
        return self.current_hud_state()

    def _build_live_corner_cues(self) -> list[dict[str, Any]]:
        locked = self.division.locked_athlete or AthleteId.ATHLETE_A
        locked_name = (
            self.division.athlete_a_name
            if locked == AthleteId.ATHLETE_A
            else self.division.athlete_b_name
        )
        my_score = self.engine.scores[locked]
        opp = AthleteId.ATHLETE_B if locked == AthleteId.ATHLETE_A else AthleteId.ATHLETE_A
        opp_score = self.engine.scores[opp]

        score_diff = my_score.points - opp_score.points
        if score_diff < 0:
            situation = "trailing on points urgent sweep pass submission attack"
        elif score_diff > 0:
            situation = "leading on points heavy base posture control finish"
        else:
            situation = "tied match first grip angle initiative"

        kb_cues = lookup_cta_recommendations(
            self.retriever,
            position=self.engine.current_position,
            situation=situation,
            belt=self.division.belt.value,
            top_k=2,
        )

        cues: list[dict[str, Any]] = []
        if self._latest_corner_cue:
            cues.append(
                {
                    "source": "live_vision",
                    "athlete": locked_name,
                    "position": self.engine.current_position,
                    "title": "Live Mat Corner Read",
                    "cue": self._latest_corner_cue,
                    "text": self._latest_corner_cue,
                }
            )
        for item in kb_cues:
            cue_txt = str(item.get("text", "") or "")
            cues.append(
                {
                    "source": "cta_curriculum",
                    "athlete": locked_name,
                    "position": item.get("position") or self.engine.current_position,
                    "title": item.get("title", "CTA Technical Cue"),
                    "cue": cue_txt,
                    "text": cue_txt,
                    "source_url": item.get("source_url", ""),
                }
            )
        return cues

    def current_hud_state(self) -> dict[str, Any]:
        snap = self.engine.snapshot().model_copy(
            update={
                "active_stabilization": dict(self._active_stab) if self._active_stab else None,
                "stalling_watch_sec": dict(self.stalling_watch_sec),
            }
        )
        dumped = snap.model_dump(mode="json")
        review_flags = [
            ev.model_dump(mode="json")
            for ev in self.engine.timeline
            if ev.needs_review
        ]
        locked = self.division.locked_athlete.value if self.division.locked_athlete else None
        points_period_active = (
            bool(self.engine.is_points_period(self.engine.elapsed_sec))
            if hasattr(self.engine, "is_points_period") and callable(self.engine.is_points_period)
            else True
        )
        return {
            **dumped,
            "ruleset": self.engine.ruleset_id,
            "points_period_active": points_period_active,
            "overtime": bool(getattr(self.engine, "overtime", False)),
            "scoreboard": dumped,
            "profile": self.profile,
            "model_used": self._latest_model_used,
            "telemetry": dict(self._latest_telemetry),
            "locked_athlete": locked,
            "corner_cues": self._build_live_corner_cues(),
            "review_flags": review_flags,
            "recent_events": list(self._recent_events),
            "audio_alerts": list(self._audio_alerts[-5:]),
            "vision_throttled": False,
        }
