"""Cloud Gemini 4 (Hydrogen tracker + Neon adjudicator) and Gemma 4 Interactions API provider."""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from typing import Any

from google import genai

from rollmetrics.config import Settings, get_settings
from rollmetrics.providers.base import (
    ADJUDICATION_JSON_SCHEMA,
    FRAME_OBSERVATION_JSON_SCHEMA,
    VIDEO_ANALYSIS_JSON_SCHEMA,
    FrameObservation,
    VideoVisionResult,
    VisionProvider,
    build_division_context_prompt,
    coerce_frame_observation,
    coerce_raw_event,
    parse_json_lenient,
)
from rollmetrics.rules.schema import EventKind, MatchDivisionConfig, RawObservedEvent


def extract_interaction_text(interaction: Any) -> str:
    """Extracts JSON text blocks from a google-genai Interactions API response."""
    if interaction is None:
        return ""
    if isinstance(interaction, dict):
        outputs = interaction.get("outputs") or []
        parts = [
            str(b.get("text", ""))
            for b in outputs
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        ]
        if parts:
            return "\n".join(parts)
        return str(interaction.get("text") or interaction.get("output_text") or "")

    outputs = getattr(interaction, "outputs", None)
    if outputs:
        parts = []
        for block in outputs:
            b_type = (
                block.get("type")
                if isinstance(block, dict)
                else getattr(block, "type", None)
            )
            if b_type == "text":
                b_text = (
                    block.get("text")
                    if isinstance(block, dict)
                    else getattr(block, "text", None)
                )
                if b_text:
                    parts.append(str(b_text))
        if parts:
            return "\n".join(parts)

    for attr in ("text", "output_text"):
        val = getattr(interaction, attr, None)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def extract_interaction_usage(interaction: Any) -> tuple[int, int, int]:
    """Extracts (input_tokens, output_tokens, thought_tokens) from an Interaction response."""
    if interaction is None:
        return 0, 0, 0
    usage = (
        interaction.get("usage")
        if isinstance(interaction, dict)
        else getattr(interaction, "usage", None)
    )
    if usage is None:
        return 0, 0, 0

    def _uget(key: str) -> int:
        val = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        return int(val) if isinstance(val, (int, float)) else 0

    in_tok = _uget("total_input_tokens") or _uget("input_tokens") or _uget("prompt_token_count")
    raw_out = _uget("total_output_tokens") or _uget("output_tokens") or _uget("candidates_token_count")
    thought_tok = _uget("total_thought_tokens") or _uget("thought_tokens")
    total_tok = _uget("total_tokens")
    if thought_tok > 0 and total_tok >= (in_tok + raw_out + thought_tok):
        out_tok = raw_out + thought_tok
    else:
        out_tok = raw_out
    return in_tok, out_tok, thought_tok


def _file_state_str(file_obj: Any) -> str:
    state = (
        file_obj.get("state")
        if isinstance(file_obj, dict)
        else getattr(file_obj, "state", None)
    )
    if state is None:
        return "ACTIVE"
    for attr in ("name", "value"):
        sub = getattr(state, attr, None)
        if isinstance(sub, str) and sub:
            return sub.upper()
    return str(state).upper()


class GeminiCloudProvider(VisionProvider):
    """Gemini 4 Hydrogen fast tracker + Gemini 4 Neon frontier adjudicator via Interactions API."""

    profile_name = "cloud"

    def __init__(
        self,
        settings: Settings | None = None,
        client: Any | None = None,
        use_gemma_api: bool = False,
        model_override: str | None = None,
        poll_interval_sec: float = 1.5,
        max_upload_wait_sec: float = 90.0,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self.use_gemma_api = use_gemma_api
        self.model_override = (model_override or "").strip() or None
        self.poll_interval_sec = poll_interval_sec
        self.max_upload_wait_sec = max_upload_wait_sec
        self.last_telemetry: dict[str, Any] = {
            "model_id": "",
            "latency_ms": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "thought_tokens": 0,
        }

    @property
    def client(self) -> Any:
        if self._client is None:
            api_key = self.settings.gemini_api_key
            if not api_key:
                raise RuntimeError(
                    "GEMINI_API_KEY is missing. Configure GEMINI_API_KEY or switch to 'local' Ollama profile."
                )
            self._client = genai.Client(api_key=api_key)
        return self._client

    def _live_frame_model_id(self) -> str:
        if self.model_override:
            return self.model_override
        return self.settings.gemma_api_model

    def _tracker_model_id(self) -> str:
        if self.model_override:
            return self.model_override
        return self.settings.gemma_api_model if self.use_gemma_api else self.settings.tracker_model

    def _adjudicator_model_id(self) -> str:
        if self.model_override:
            return self.model_override
        return self.settings.gemma_api_model if self.use_gemma_api else self.settings.adjudicator_model

    async def _create_interaction(
        self,
        model: str,
        input_items: list[dict[str, Any]],
        thinking_level: str | None,
        schema: dict[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
        }
        if thinking_level and "gemma" not in model.lower():
            kwargs["generation_config"] = {"thinking_level": thinking_level}

        t0 = time.perf_counter()
        interaction = await asyncio.to_thread(self.client.interactions.create, **kwargs)
        latency_ms = round(max(0.01, (time.perf_counter() - t0) * 1000.0), 2)
        in_tok, out_tok, thought_tok = extract_interaction_usage(interaction)
        tel: dict[str, Any] = {
            "model_id": model,
            "latency_ms": latency_ms,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "thought_tokens": thought_tok,
        }
        self.last_telemetry = dict(tel)
        return interaction, tel

    async def _wait_for_file_active(self, uploaded_file: Any) -> Any:
        deadline = time.monotonic() + self.max_upload_wait_sec
        current = uploaded_file
        while True:
            state_str = _file_state_str(current)
            if "ACTIVE" in state_str:
                return current
            if "FAILED" in state_str or "ERROR" in state_str:
                name = getattr(current, "name", None) or "uploaded_video"
                raise RuntimeError(f"Gemini File API processing failed for {name} (state={state_str}).")
            if time.monotonic() >= deadline:
                name = getattr(current, "name", None) or "uploaded_video"
                raise TimeoutError(f"Timed out waiting for Gemini File {name} to become ACTIVE.")
            await asyncio.sleep(self.poll_interval_sec)
            file_name = (
                current.get("name")
                if isinstance(current, dict)
                else getattr(current, "name", None)
            )
            current = await asyncio.to_thread(self.client.files.get, name=file_name)

    async def analyze_live_frame(
        self,
        jpeg_bytes: bytes,
        division: MatchDivisionConfig,
        elapsed_sec: float = 0.0,
        current_position: str = "Standing",
        audio_transcript: str = "",
    ) -> FrameObservation:
        model = self._live_frame_model_id()
        b64_jpeg = base64.b64encode(jpeg_bytes).decode("ascii")
        audio_note = f"\nMat Audio Transcript: {audio_transcript}" if audio_transcript else ""
        prompt = (
            f"{build_division_context_prompt(division)}\n"
            f"Live Frame Elapsed Time: {elapsed_sec:.1f}s. Prior Position: {current_position}.{audio_note}\n"
            "Compare this frame against Prior Position. Identify the active BJJ position, set "
            "stabilizing_scoring_kind ('takedown', 'sweep', 'knee_on_belly', 'guard_pass', 'mount', "
            "'back_control', or 'none'; use 'guard_pass' for side control or north-south past the legs) "
            "whenever top_or_dominant_actor is establishing or holding scoring control toward 3.0s "
            "in-bounds, track combativeness for stalling detection, emit detected_events (with "
            f"timestamp_sec={elapsed_sec:.1f}) whenever a scoring move, advantage, foul, submission, "
            "or clear positional transition occurs, and give a sharp CTA corner cue for the locked athlete."
        )
        input_items = [
            {
                "type": "image",
                "data": b64_jpeg,
                "mime_type": "image/jpeg",
            },
            {
                "type": "text",
                "text": prompt,
            },
        ]
        try:
            interaction, tel = await self._create_interaction(
                model=model,
                input_items=input_items,
                thinking_level="low",
                schema=FRAME_OBSERVATION_JSON_SCHEMA,
            )
        except Exception:
            fallback_model = self.settings.tracker_model
            if self.model_override or model == fallback_model:
                raise
            model = fallback_model
            interaction, tel = await self._create_interaction(
                model=model,
                input_items=input_items,
                thinking_level="low",
                schema=FRAME_OBSERVATION_JSON_SCHEMA,
            )
        parsed = parse_json_lenient(extract_interaction_text(interaction))
        return coerce_frame_observation(
            parsed,
            elapsed_sec=elapsed_sec,
            model_used=model,
            latency_ms=tel["latency_ms"],
            input_tokens=tel["input_tokens"],
            output_tokens=tel["output_tokens"],
            telemetry=tel,
        )

    async def analyze_video(
        self,
        video_path: Path,
        division: MatchDivisionConfig,
        sample_fps: float = 0.5,
        burst_frames: list[tuple[float, bytes]] | None = None,
    ) -> VideoVisionResult:
        del sample_fps, burst_frames
        tracker_model = self._tracker_model_id()
        uploaded = await asyncio.to_thread(self.client.files.upload, file=str(video_path))
        active_file = await self._wait_for_file_active(uploaded)
        file_uri = (
            active_file.get("uri")
            if isinstance(active_file, dict)
            else getattr(active_file, "uri", "")
        )

        prompt = (
            f"{build_division_context_prompt(division)}\n"
            "Inspect the full BJJ match video chronologically. Detect every IBJJF scoring transition "
            "(takedown, sweep, knee_on_belly, guard_pass, mount, back_control), exact in-bounds "
            "stabilization duration in seconds (3.0s threshold), advantages, minor or serious technical "
            "fouls with canonical technique_slug, and submissions. Also generate CTA professor "
            "coaching_notes keyed to exact timestamps."
        )
        interaction, tracker_tel = await self._create_interaction(
            model=tracker_model,
            input_items=[
                {
                    "type": "video",
                    "uri": file_uri,
                    "mime_type": "video/mp4",
                    "processing": "agentic",
                },
                {
                    "type": "text",
                    "text": prompt,
                },
            ],
            thinking_level="medium",
            schema=VIDEO_ANALYSIS_JSON_SCHEMA,
        )
        parsed = parse_json_lenient(extract_interaction_text(interaction))
        events: list[RawObservedEvent] = []
        for item in parsed.get("events", []) or []:
            ev = coerce_raw_event(item)
            if ev is not None:
                events.append(ev)

        coaching_notes = [
            n for n in (parsed.get("coaching_notes") or []) if isinstance(n, dict)
        ]

        ambiguous_indices = [
            idx
            for idx, ev in enumerate(events)
            if ev.occluded
            or ev.confidence < 0.72
            or ev.kind in (EventKind.SERIOUS_FOUL, EventKind.MINOR_FOUL)
            or (2.3 <= ev.hold_duration_sec < 3.4)
        ]

        total_latency_ms = float(tracker_tel["latency_ms"])
        total_in_tok = int(tracker_tel["input_tokens"])
        total_out_tok = int(tracker_tel["output_tokens"])
        dispute_rulings: list[dict[str, Any]] = []
        adjudicator_used = ""
        if ambiguous_indices:
            adj_res = await self.adjudicate_dispute_or_synthesize(
                events=events,
                division=division,
                ambiguous_indices=ambiguous_indices,
            )
            adjudicator_used = str(adj_res.get("model_used", self._adjudicator_model_id()))
            total_latency_ms = round(total_latency_ms + float(adj_res.get("latency_ms", 0.0)), 2)
            total_in_tok += int(adj_res.get("input_tokens", 0))
            total_out_tok += int(adj_res.get("output_tokens", 0))
            dispute_rulings = list(adj_res.get("rulings") or [])
            for ruling in dispute_rulings:
                if not isinstance(ruling, dict):
                    continue
                ev_idx = ruling.get("event_index")
                if isinstance(ev_idx, int) and 0 <= ev_idx < len(events):
                    target = events[ev_idx]
                    upd: dict[str, Any] = {}
                    if "confirmed_kind" in ruling:
                        try:
                            upd["kind"] = EventKind(str(ruling["confirmed_kind"]))
                        except ValueError:
                            pass
                    if "hold_duration_sec" in ruling:
                        upd["hold_duration_sec"] = max(0.0, float(ruling["hold_duration_sec"]))
                    if "in_bounds" in ruling:
                        upd["in_bounds"] = bool(ruling["in_bounds"])
                    if ruling.get("technique_slug"):
                        upd["technique_slug"] = str(ruling["technique_slug"])
                    if "confidence" in ruling:
                        upd["confidence"] = min(1.0, max(0.0, float(ruling["confidence"])))
                    if ruling.get("ruling_note"):
                        upd["summary"] = str(ruling["ruling_note"])
                    if upd:
                        events[ev_idx] = target.model_copy(update=upd)
            for synth in adj_res.get("cta_synthesis") or []:
                if isinstance(synth, dict) and synth.get("headline"):
                    coaching_notes.append(synth)

        combined_tel = {
            "model_id": tracker_model,
            "adjudicator_model_id": adjudicator_used,
            "latency_ms": total_latency_ms,
            "input_tokens": total_in_tok,
            "output_tokens": total_out_tok,
        }
        self.last_telemetry = dict(combined_tel)
        return VideoVisionResult(
            events=events,
            coaching_notes=coaching_notes,
            dispute_rulings=dispute_rulings,
            model_used=tracker_model,
            model_id=tracker_model,
            adjudicator_model_used=adjudicator_used,
            summary=str(parsed.get("summary", "") or f"Analyzed video with {tracker_model}."),
            latency_ms=total_latency_ms,
            input_tokens=total_in_tok,
            output_tokens=total_out_tok,
            telemetry=combined_tel,
        )

    async def adjudicate_dispute_or_synthesize(
        self,
        events: list[RawObservedEvent],
        division: MatchDivisionConfig,
        ambiguous_indices: list[int] | None = None,
    ) -> dict[str, Any]:
        model = self._adjudicator_model_id()
        serialized = [e.model_dump(mode="json") for e in events]
        prompt = (
            f"{build_division_context_prompt(division)}\n"
            "You are the IBJJF Head Referee Adjudicator and Senior Caio Terra Association (CTA) "
            "Black Belt Professor.\n"
            f"Observed Match Events: {serialized}\n"
            f"Ambiguous / Boundary Indices to Adjudicate: {ambiguous_indices or []}\n"
            "Verify 3.0s in-bounds stabilization boundaries, belt/age/uniform foul legality, "
            "and synthesize CTA technical study prescriptions (frames, grip geometry, hip angles)."
        )
        interaction, tel = await self._create_interaction(
            model=model,
            input_items=[{"type": "text", "text": prompt}],
            thinking_level="high",
            schema=ADJUDICATION_JSON_SCHEMA,
        )
        parsed = parse_json_lenient(extract_interaction_text(interaction))
        parsed.setdefault("rulings", [])
        parsed.setdefault("cta_synthesis", [])
        parsed["model_used"] = model
        parsed["model_id"] = model
        parsed["latency_ms"] = tel["latency_ms"]
        parsed["input_tokens"] = tel["input_tokens"]
        parsed["output_tokens"] = tel["output_tokens"]
        parsed["telemetry"] = tel
        return parsed

    async def query_gemma_api(
        self,
        prompt: str,
        image_jpeg: bytes | None = None,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Runs open-weight Gemma 4 (`settings.gemma_api_model`) via the Gemini Interactions API."""
        model = self.model_override or self.settings.gemma_api_model
        items: list[dict[str, Any]] = []
        if image_jpeg is not None:
            items.append(
                {
                    "type": "image",
                    "data": base64.b64encode(image_jpeg).decode("ascii"),
                    "mime_type": "image/jpeg",
                }
            )
        items.append({"type": "text", "text": prompt})
        target_schema = schema or {
            "type": "object",
            "properties": {"response": {"type": "string"}},
            "required": ["response"],
        }
        interaction, tel = await self._create_interaction(
            model=model,
            input_items=items,
            thinking_level=None,
            schema=target_schema,
        )
        out = parse_json_lenient(extract_interaction_text(interaction))
        out["model_used"] = model
        out["model_id"] = model
        out["latency_ms"] = tel["latency_ms"]
        out["input_tokens"] = tel["input_tokens"]
        out["output_tokens"] = tel["output_tokens"]
        out["telemetry"] = tel
        return out
