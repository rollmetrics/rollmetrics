"""Local Apple Silicon M3 (16 GB RAM) Ollama Gemma vision provider."""

from __future__ import annotations

import base64
import io
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image

from rollmetrics.config import Settings, get_settings
from rollmetrics.providers.base import (
    FrameObservation,
    VideoVisionResult,
    VisionProvider,
    build_division_context_prompt,
    coerce_frame_observation,
    coerce_raw_event,
    parse_json_lenient,
)
from rollmetrics.rules.schema import MatchDivisionConfig, RawObservedEvent

PREFERRED_VISION_PREFIXES: tuple[str, ...] = (
    "gemma4:e2b-it-q4_k_m",
    "gemma4:e4b-it-q4_k_m",
    "gemma4",
    "gemma3:4b",
    "gemma3:12b",
    "gemma3:latest",
    "gemma3",
    "qwen2.5vl",
    "qwen3-vl",
    "llava",
    "minicpm-v",
    "llama3.2-vision",
    "moondream",
)


def select_best_ollama_vision_model(installed_models: list[str], configured_model: str) -> str:
    """Chooses the configured vision tag if present, or auto-detects the best installed vision model."""
    if not installed_models:
        return configured_model

    clean_installed = [m.strip() for m in installed_models if m and m.strip()]
    if configured_model in clean_installed:
        return configured_model

    cfg_base = configured_model.split(":")[0].lower()
    for tag in clean_installed:
        low = tag.lower()
        if "embed" in low or low.startswith(("gemma3:1b", "gemma3:270m")):
            continue
        if low == configured_model.lower() or low.startswith(f"{configured_model.lower()}-"):
            return tag
        if cfg_base and low.startswith(f"{cfg_base}:") and "1b" not in low:
            return tag

    for prefix in PREFERRED_VISION_PREFIXES:
        for tag in clean_installed:
            low = tag.lower()
            if "embed" in low or low.startswith(("gemma3:1b", "gemma3:270m")):
                continue
            if low.startswith(prefix):
                return tag

    for tag in clean_installed:
        if "embed" not in tag.lower():
            return tag
    return configured_model


class OllamaLocalProvider(VisionProvider):
    """Calls local Ollama `/api/chat` with base64 JPEG images, JSON mode, and 10m keep_alive."""

    profile_name = "local"

    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_sec: float = 240.0,
        model_override: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.ollama_base_url.rstrip("/")
        self.model_override = (model_override or "").strip() or None
        self.configured_model = self.model_override or self.settings.ollama_vision_model
        self._resolved_model: str | None = self.model_override
        self._transport = transport
        self.timeout_sec = timeout_sec
        self.last_telemetry: dict[str, Any] = {
            "model_id": "",
            "latency_ms": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout or self.timeout_sec,
            transport=self._transport,
        )

    async def list_installed_models(self) -> list[str]:
        async with self._client(timeout=5.0) as client:
            resp = await client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
            payload = resp.json()
        models = payload.get("models", []) or []
        return [str(m.get("name", "")) for m in models if isinstance(m, dict) and m.get("name")]

    async def resolve_model(self, force_refresh: bool = False) -> str:
        if self.model_override:
            return self.model_override
        if self._resolved_model and not force_refresh:
            return self._resolved_model
        try:
            installed = await self.list_installed_models()
            self._resolved_model = select_best_ollama_vision_model(installed, self.configured_model)
        except (httpx.HTTPError, OSError, ValueError):
            self._resolved_model = self.configured_model
        return self._resolved_model

    async def chat_json(
        self,
        prompt: str,
        images_jpeg: list[bytes] | None = None,
        system: str = "",
        model: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        target_model = model or await self.resolve_model()
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})

        user_msg: dict[str, Any] = {"role": "user", "content": prompt}
        if images_jpeg:
            user_msg["images"] = [base64.b64encode(img).decode("ascii") for img in images_jpeg]
        messages.append(user_msg)

        body: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "format": "json",
            "stream": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.1},
        }
        t0 = time.perf_counter()
        async with self._client() as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=body)
            resp.raise_for_status()
            raw = resp.json()
        latency_ms = round(max(0.01, (time.perf_counter() - t0) * 1000.0), 2)

        content = ""
        in_tok = 0
        out_tok = 0
        if isinstance(raw, dict):
            in_tok = int(raw.get("prompt_eval_count") or 0)
            out_tok = int(raw.get("eval_count") or 0)
            msg = raw.get("message")
            if isinstance(msg, dict):
                content = str(msg.get("content", "") or "")
            elif "response" in raw:
                content = str(raw.get("response", "") or "")

        self.last_telemetry = {
            "model_id": target_model,
            "latency_ms": latency_ms,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
        }
        return parse_json_lenient(content), target_model

    async def analyze_live_frame(
        self,
        jpeg_bytes: bytes,
        division: MatchDivisionConfig,
        elapsed_sec: float = 0.0,
        current_position: str = "Standing",
        audio_transcript: str = "",
    ) -> FrameObservation:
        system = (
            "You are an IBJJF Black Belt Referee and Caio Terra Association (CTA) corner coach. "
            "Respond strictly with a single JSON object."
        )
        audio_line = f"\nReferee/Mat Audio: {audio_transcript}" if audio_transcript else ""
        prompt = (
            f"{build_division_context_prompt(division)}\n"
            f"Elapsed Match Time: {elapsed_sec:.1f}s. Prior Position: {current_position}.{audio_line}\n"
            "Return JSON keys: current_position (string), top_or_dominant_actor ('A'|'B'|'none'), "
            "stabilizing_scoring_kind ('none'|'takedown'|'sweep'|'knee_on_belly'|'guard_pass'|'mount'|'back_control'), "
            "active_combativeness_a (bool), active_combativeness_b (bool), in_bounds (bool), "
            "occluded (bool), confidence (0..1), corner_cue (short CTA grip/hip/frame instruction), "
            "detected_events (array of completed discrete events if a tap or foul just occurred, else [])."
        )
        parsed, used_model = await self.chat_json(
            prompt=prompt,
            images_jpeg=[jpeg_bytes],
            system=system,
        )
        tel = dict(self.last_telemetry)
        return coerce_frame_observation(
            parsed,
            elapsed_sec=elapsed_sec,
            model_used=used_model,
            latency_ms=float(tel.get("latency_ms", 0.0)),
            input_tokens=int(tel.get("input_tokens", 0)),
            output_tokens=int(tel.get("output_tokens", 0)),
            telemetry=tel,
        )

    @staticmethod
    def _extract_single_frame_jpeg(
        video_path: Path,
        timestamp_sec: float,
        ffmpeg_bin: str,
    ) -> bytes | None:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-ss",
            f"{max(0.0, timestamp_sec):.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(960,iw)':-2",
            "-q:v",
            "3",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        proc = subprocess.run(cmd, capture_output=True, check=False)
        if proc.returncode != 0 or not proc.stdout:
            return None
        return bytes(proc.stdout)

    @staticmethod
    def _compute_motion_peaks(frames: list[tuple[float, bytes]], top_k: int = 3) -> list[tuple[float, float]]:
        if len(frames) < 2:
            return []
        arrays: list[tuple[float, np.ndarray]] = []
        for ts, jpg in frames:
            try:
                with Image.open(io.BytesIO(jpg)) as im:
                    arr = np.asarray(im.convert("L").resize((64, 36)), dtype=np.float32)
                    arrays.append((ts, arr))
            except OSError:
                continue
        scores: list[tuple[float, float]] = []
        for idx in range(1, len(arrays)):
            ts_prev, a_prev = arrays[idx - 1]
            ts_curr, a_curr = arrays[idx]
            diff = float(np.mean(np.abs(a_curr - a_prev)))
            mid_ts = round((ts_prev + ts_curr) / 2.0, 2)
            scores.append((mid_ts, round(diff, 2)))
        scores.sort(key=lambda pair: pair[1], reverse=True)
        return scores[:top_k]

    @staticmethod
    def _normalize_inspect_windows(
        raw_windows: list[dict[str, Any]],
        duration_sec: float,
        max_windows: int = 4,
        max_span_sec: float = 8.0,
    ) -> list[dict[str, Any]]:
        dur = max(1.0, float(duration_sec))
        cleaned: list[dict[str, Any]] = []
        for item in raw_windows:
            if not isinstance(item, dict):
                continue
            try:
                s_val = float(item.get("start_sec", 0.0))
                e_val = float(item.get("end_sec", s_val + 6.0))
            except (TypeError, ValueError):
                continue
            if e_val < s_val:
                s_val, e_val = e_val, s_val
            s_val = max(0.0, min(s_val, max(0.0, dur - 1.0)))
            e_val = max(s_val + min(4.0, dur), min(e_val, dur))
            if e_val - s_val > max_span_sec:
                mid = (s_val + e_val) / 2.0
                s_val = max(0.0, mid - max_span_sec / 2.0)
                e_val = min(dur, s_val + max_span_sec)
            reason = str(item.get("reason", "") or "verify scoring transition / referee signal").strip()
            cleaned.append(
                {
                    "start_sec": round(s_val, 2),
                    "end_sec": round(min(dur, e_val), 2),
                    "reason": reason,
                }
            )

        cleaned.sort(key=lambda w: w["start_sec"])
        merged: list[dict[str, Any]] = []
        for win in cleaned:
            if merged and win["start_sec"] <= merged[-1]["end_sec"] + 1.0:
                prev = merged[-1]
                new_end = min(dur, max(prev["end_sec"], win["end_sec"]))
                if new_end - prev["start_sec"] <= max_span_sec + 2.0:
                    prev["end_sec"] = round(new_end, 2)
                    if win["reason"] and win["reason"] not in prev["reason"]:
                        prev["reason"] = f"{prev['reason']}; {win['reason']}"
                    continue
            merged.append(win)
        return merged[: max(1, max_windows)]

    @staticmethod
    def _deduplicate_events(events: list[RawObservedEvent]) -> list[RawObservedEvent]:
        ordered = sorted(events, key=lambda ev: ev.timestamp_sec)
        deduped: list[RawObservedEvent] = []
        for ev in ordered:
            duplicate = False
            for idx, existing in enumerate(deduped):
                if (
                    existing.actor == ev.actor
                    and existing.kind == ev.kind
                    and abs(existing.timestamp_sec - ev.timestamp_sec) <= 2.5
                ):
                    if ev.confidence >= existing.confidence:
                        deduped[idx] = ev
                    duplicate = True
                    break
            if not duplicate:
                deduped.append(ev)
        return deduped

    async def analyze_video_agentic(
        self,
        video_path: Path,
        division: MatchDivisionConfig,
        *,
        coarse_frames: int = 12,
        max_windows: int = 4,
        zoom_fps: float = 1.0,
        max_frames_per_window: int = 8,
        frame_mask_fn: Callable[[bytes], bytes] | None = None,
        duration_sec: float | None = None,
        start_sec: float = 0.0,
        end_sec: float | None = None,
    ) -> VideoVisionResult:
        """3-Stage Local Agentic Video Understanding loop for Ollama Gemma 4.

        Stage 1: Coarse scan (~12-16 evenly spaced frames + final 20s finish/hand-raise frames)
                 with frame-diff motion energy hints.
        Stage 2: Gemma 4 autonomously proposes up to `max_windows` candidate zoom windows.
        Stage 3: High-FPS (`1 fps`) window extraction (honoring `frame_mask_fn`) and zoomed
                 adjudication for >= 3.0s stabilization, athlete attribution, and finish.
        """
        from rollmetrics.video.ingest import extract_frame_burst, resolve_binary, validate_video_file

        meta = validate_video_file(video_path)
        clip_start = max(0.0, float(start_sec))
        clip_end = (
            min(meta.duration_sec, float(end_sec))
            if end_sec is not None and end_sec > clip_start
            else meta.duration_sec
        )
        dur = max(1.0, float(duration_sec if duration_sec is not None else max(1.0, clip_end - clip_start)))
        ffmpeg_bin = resolve_binary("ffmpeg")

        # Stage 1: Coarse scan timestamps (relative to clip_start) + mandatory final 20s finish/hand-raise
        n_even = max(4, min(14, int(coarse_frames)))
        step = dur / float(n_even + 1)
        coarse_ts: list[float] = [round(step * (i + 1), 2) for i in range(n_even)]
        tail_offsets = [min(18.0, dur * 0.25), min(10.0, dur * 0.12), min(2.0, dur * 0.04)]
        for offset in tail_offsets:
            ts_tail = round(max(0.0, dur - offset), 2)
            if all(abs(ts_tail - existing) >= 1.0 for existing in coarse_ts):
                coarse_ts.append(ts_tail)
        coarse_ts = sorted(set(coarse_ts))[:16]

        coarse_list: list[tuple[float, bytes]] = []
        for rel_ts in coarse_ts:
            jpg = self._extract_single_frame_jpeg(meta.path, clip_start + rel_ts, ffmpeg_bin)
            if jpg is None:
                continue
            if frame_mask_fn is not None:
                jpg = frame_mask_fn(jpg)
            coarse_list.append((rel_ts, jpg))

        used_model = await self.resolve_model()
        if not coarse_list:
            return VideoVisionResult(
                events=[],
                model_used=used_model,
                model_id=used_model,
                summary="No coarse frames extracted for local agentic video analysis.",
                telemetry={
                    "model_id": used_model,
                    "latency_ms": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "agentic_mode": True,
                    "coarse_frames_scanned": 0,
                    "agentic_windows_inspected": [],
                    "agentic_windows_count": 0,
                },
            )

        motion_peaks = self._compute_motion_peaks(coarse_list, top_k=3)
        motion_hint = (
            ", ".join(f"{ts:.1f}s (energy={e:.1f})" for ts, e in motion_peaks)
            if motion_peaks
            else "none"
        )

        # Stage 2: Autonomous Zoom Requests from Gemma 4
        total_latency_ms = 0.0
        total_in_tok = 0
        total_out_tok = 0
        proposed_raw_windows: list[dict[str, Any]] = []
        prelim_events: list[RawObservedEvent] = []
        coaching_notes: list[dict[str, Any]] = []
        coarse_summaries: list[str] = []

        coarse_batch_size = 8
        for idx in range(0, len(coarse_list), coarse_batch_size):
            chunk = coarse_list[idx : idx + coarse_batch_size]
            ts_labels = ", ".join(f"img#{i + 1}={ts:.1f}s" for i, (ts, _) in enumerate(chunk))
            prompt = (
                f"{build_division_context_prompt(division)}\n"
                f"Match video duration: {dur:.1f}s. High-motion peaks: {motion_hint}.\n"
                f"STAGE 1 COARSE SCAN frames ({ts_labels}).\n"
                f"Propose up to {max_windows} high-value timestamp windows to zoom in at 1 fps "
                "to verify >= 3.0s IBJJF positional stabilization, referee hand signals, or the final hand-raise.\n"
                "Return JSON with keys:\n"
                "- summary (string)\n"
                "- inspect_windows: list of {start_sec, end_sec, reason}\n"
                "- preliminary_events: list of {timestamp_sec, actor ('A'|'B'), kind, technique_slug, "
                "hold_duration_sec, in_bounds, confidence, summary, position_after}\n"
                "- coaching_notes: list of {timestamp_sec, position, headline, cta_detail}"
            )
            parsed, used_model = await self.chat_json(
                prompt=prompt,
                images_jpeg=[b for _, b in chunk],
                system=(
                    "IBJJF Black Belt Referee and CTA Coach performing Stage-1 coarse video triage. "
                    "Output valid JSON only."
                ),
            )
            total_latency_ms = round(total_latency_ms + float(self.last_telemetry.get("latency_ms", 0.0)), 2)
            total_in_tok += int(self.last_telemetry.get("input_tokens", 0))
            total_out_tok += int(self.last_telemetry.get("output_tokens", 0))

            if parsed.get("summary"):
                coarse_summaries.append(str(parsed["summary"]))
            for win in parsed.get("inspect_windows", []) or []:
                if isinstance(win, dict):
                    proposed_raw_windows.append(win)
            for item in (parsed.get("preliminary_events") or parsed.get("events") or []):
                ev = coerce_raw_event(item, default_ts=chunk[0][0])
                if ev is not None:
                    prelim_events.append(ev)
            for note in parsed.get("coaching_notes", []) or []:
                if isinstance(note, dict) and note.get("headline"):
                    coaching_notes.append(note)

        if not proposed_raw_windows:
            if motion_peaks:
                peak_ts = motion_peaks[0][0]
                proposed_raw_windows.append(
                    {
                        "start_sec": max(0.0, peak_ts - 3.0),
                        "end_sec": min(dur, peak_ts + 4.0),
                        "reason": "highest motion exchange window",
                    }
                )
            proposed_raw_windows.append(
                {
                    "start_sec": max(0.0, dur - min(8.0, dur)),
                    "end_sec": dur,
                    "reason": "final finish and referee hand-raise verification",
                }
            )

        windows = self._normalize_inspect_windows(
            proposed_raw_windows,
            duration_sec=dur,
            max_windows=max_windows,
            max_span_sec=max(4.0, float(max_frames_per_window) / max(0.5, float(zoom_fps))),
        )

        # Stage 3: High-FPS (1 fps) Window Extraction + Zoomed Adjudication
        verified_events: list[RawObservedEvent] = []
        inspected_windows: list[dict[str, Any]] = []
        observed_winner = ""
        observed_win_method = ""

        for win in windows:
            w_start = float(win["start_sec"])
            w_end = float(win["end_sec"])
            w_dur = max(1.0, w_end - w_start)
            extracted = extract_frame_burst(
                meta.path,
                sample_fps=zoom_fps,
                start_sec=clip_start + w_start,
                duration_sec=w_dur,
                max_frames=max_frames_per_window,
            )
            zoom_frames: list[tuple[float, bytes]] = []
            for ef in extracted:
                img_bytes = frame_mask_fn(ef.jpeg_bytes) if frame_mask_fn is not None else ef.jpeg_bytes
                zoom_frames.append((round(max(0.0, ef.timestamp_sec - clip_start), 2), img_bytes))

            if not zoom_frames:
                continue

            ts_labels = ", ".join(f"img#{i + 1}={ts:.1f}s" for i, (ts, _) in enumerate(zoom_frames))
            prompt = (
                f"{build_division_context_prompt(division)}\n"
                f"STAGE 2 ZOOMED WINDOW [{w_start:.1f}s .. {w_end:.1f}s] at {zoom_fps:.1f} fps "
                f"(Reason: {win['reason']}). Frames: {ts_labels}.\n"
                "Adjudicate this high-resolution window strictly under IBJJF rules:\n"
                "- Confirm >= 3.0s positional stabilization across 3 consecutive 1 fps frames before awarding points.\n"
                "- Verify athlete A (starts LEFT / green-red band) vs B (starts RIGHT / yellow-blue band) by appearance.\n"
                "- Check referee hand signals, submission tap, disqualification, and final hand-raise.\n"
                "Return JSON with keys:\n"
                "- window_summary (string)\n"
                "- observed_winner ('A'|'B'|'referee_decision'|'')\n"
                "- observed_win_method ('points'|'advantages'|'penalties'|'submission'|'dq'|'referee_decision'|'')\n"
                "- events: list of {timestamp_sec, actor ('A'|'B'), kind ('takedown'|'sweep'|"
                "'knee_on_belly'|'guard_pass'|'mount'|'back_control'|'advantage'|'minor_foul'|"
                "'serious_foul'|'submission'|'position_change'), technique_slug, hold_duration_sec, "
                "in_bounds, confidence, summary, position_after}\n"
                "- coaching_notes: list of {timestamp_sec, position, headline, cta_detail}"
            )
            parsed, used_model = await self.chat_json(
                prompt=prompt,
                images_jpeg=[b for _, b in zoom_frames],
                system=(
                    "IBJJF Head Referee and CTA Black Belt verifying a 1-fps zoomed match window. "
                    "Output valid JSON only."
                ),
            )
            total_latency_ms = round(total_latency_ms + float(self.last_telemetry.get("latency_ms", 0.0)), 2)
            total_in_tok += int(self.last_telemetry.get("input_tokens", 0))
            total_out_tok += int(self.last_telemetry.get("output_tokens", 0))

            cand_win = str(parsed.get("observed_winner", "") or "").strip().upper()
            cand_meth = str(parsed.get("observed_win_method", "") or "").strip().lower()
            if cand_win in ("A", "B"):
                observed_winner = cand_win
                if cand_meth in ("points", "advantages", "penalties", "submission", "dq", "referee_decision"):
                    observed_win_method = cand_meth

            win_events_count = 0
            for item in parsed.get("events", []) or []:
                ev = coerce_raw_event(item, default_ts=zoom_frames[0][0])
                if ev is not None:
                    verified_events.append(ev)
                    win_events_count += 1
            for note in parsed.get("coaching_notes", []) or []:
                if isinstance(note, dict) and note.get("headline"):
                    coaching_notes.append(note)

            inspected_windows.append(
                {
                    "start_sec": w_start,
                    "end_sec": w_end,
                    "reason": win["reason"],
                    "frames_extracted": len(zoom_frames),
                    "events_found": win_events_count,
                    "window_summary": str(parsed.get("window_summary", "") or ""),
                }
            )

        final_events = self._deduplicate_events(verified_events if verified_events else prelim_events)
        agg_tel = {
            "model_id": used_model,
            "latency_ms": total_latency_ms,
            "input_tokens": total_in_tok,
            "output_tokens": total_out_tok,
            "agentic_mode": True,
            "coarse_frames_scanned": len(coarse_list),
            "agentic_windows_inspected": inspected_windows,
            "agentic_windows_count": len(inspected_windows),
            "observed_winner": observed_winner,
            "observed_win_method": observed_win_method,
        }
        self.last_telemetry = dict(agg_tel)
        summary_text = (
            f"Local agentic video analysis with {used_model}: scanned {len(coarse_list)} coarse frames "
            f"and zoomed into {len(inspected_windows)} high-FPS windows."
        )
        if coarse_summaries:
            summary_text = f"{summary_text} {coarse_summaries[0]}"
        return VideoVisionResult(
            events=final_events,
            coaching_notes=coaching_notes,
            model_used=used_model,
            model_id=used_model,
            summary=summary_text,
            latency_ms=total_latency_ms,
            input_tokens=total_in_tok,
            output_tokens=total_out_tok,
            telemetry=agg_tel,
        )

    async def analyze_video(
        self,
        video_path: Path,
        division: MatchDivisionConfig,
        sample_fps: float = 0.5,
        burst_frames: list[tuple[float, bytes]] | None = None,
        use_agentic_loop: bool = True,
        frame_mask_fn: Callable[[bytes], bytes] | None = None,
    ) -> VideoVisionResult:
        if burst_frames is None and use_agentic_loop:
            return await self.analyze_video_agentic(
                video_path=video_path,
                division=division,
                frame_mask_fn=frame_mask_fn,
            )

        if burst_frames is None:
            from rollmetrics.video.ingest import extract_frame_burst

            extracted = extract_frame_burst(video_path, sample_fps=sample_fps, max_frames=16)
            burst_frames = [
                (
                    f.timestamp_sec,
                    frame_mask_fn(f.jpeg_bytes) if frame_mask_fn is not None else f.jpeg_bytes,
                )
                for f in extracted
            ]
        elif frame_mask_fn is not None:
            burst_frames = [(ts, frame_mask_fn(jpg)) for ts, jpg in burst_frames]

        if not burst_frames:
            model_tag = await self.resolve_model()
            return VideoVisionResult(
                events=[],
                model_used=model_tag,
                model_id=model_tag,
                summary="No frames extracted.",
            )

        all_events: list[RawObservedEvent] = []
        coaching_notes: list[dict[str, Any]] = []
        used_model = await self.resolve_model()
        total_latency_ms = 0.0
        total_in_tok = 0
        total_out_tok = 0
        batch_size = 4

        for idx in range(0, len(burst_frames), batch_size):
            chunk = burst_frames[idx : idx + batch_size]
            ts_labels = ", ".join(f"img#{i+1}={ts:.1f}s" for i, (ts, _) in enumerate(chunk))
            prompt = (
                f"{build_division_context_prompt(division)}\n"
                f"Analyze these sequential BJJ match frames ({ts_labels}).\n"
                "Return JSON with keys:\n"
                "- summary (string)\n"
                "- events: list of {timestamp_sec, actor ('A'|'B'), kind ('takedown'|'sweep'|"
                "'knee_on_belly'|'guard_pass'|'mount'|'back_control'|'advantage'|'minor_foul'|"
                "'serious_foul'|'submission'|'position_change'), technique_slug, hold_duration_sec, "
                "in_bounds, confidence, summary, position_after}\n"
                "- coaching_notes: list of {timestamp_sec, position, headline, cta_detail}"
            )
            parsed, used_model = await self.chat_json(
                prompt=prompt,
                images_jpeg=[b for _, b in chunk],
                system="IBJJF Referee and CTA Professor video analyzer. Output valid JSON only.",
            )
            total_latency_ms = round(total_latency_ms + float(self.last_telemetry.get("latency_ms", 0.0)), 2)
            total_in_tok += int(self.last_telemetry.get("input_tokens", 0))
            total_out_tok += int(self.last_telemetry.get("output_tokens", 0))
            for item in parsed.get("events", []) or []:
                ev = coerce_raw_event(item, default_ts=chunk[0][0])
                if ev is not None:
                    all_events.append(ev)
            for note in parsed.get("coaching_notes", []) or []:
                if isinstance(note, dict) and note.get("headline"):
                    coaching_notes.append(note)

        agg_tel = {
            "model_id": used_model,
            "latency_ms": total_latency_ms,
            "input_tokens": total_in_tok,
            "output_tokens": total_out_tok,
        }
        self.last_telemetry = dict(agg_tel)
        return VideoVisionResult(
            events=all_events,
            coaching_notes=coaching_notes,
            model_used=used_model,
            model_id=used_model,
            summary=f"Analyzed {len(burst_frames)} sampled frames locally with {used_model}.",
            latency_ms=total_latency_ms,
            input_tokens=total_in_tok,
            output_tokens=total_out_tok,
            telemetry=agg_tel,
        )

    async def adjudicate_dispute_or_synthesize(
        self,
        events: list[RawObservedEvent],
        division: MatchDivisionConfig,
        ambiguous_indices: list[int] | None = None,
    ) -> dict[str, Any]:
        serialized = [e.model_dump(mode="json") for e in events]
        prompt = (
            f"{build_division_context_prompt(division)}\n"
            f"Events: {serialized}\n"
            f"Ambiguous event indices: {ambiguous_indices or []}\n"
            "Return JSON with keys 'rulings' (list) and 'cta_synthesis' (list of "
            "{position, headline, cta_detail})."
        )
        parsed, used_model = await self.chat_json(
            prompt=prompt,
            system="Senior CTA Black Belt & IBJJF Head Referee. Output JSON only.",
        )
        tel = dict(self.last_telemetry)
        parsed.setdefault("rulings", [])
        parsed.setdefault("cta_synthesis", [])
        parsed["model_used"] = used_model
        parsed["model_id"] = used_model
        parsed["latency_ms"] = float(tel.get("latency_ms", 0.0))
        parsed["input_tokens"] = int(tel.get("input_tokens", 0))
        parsed["output_tokens"] = int(tel.get("output_tokens", 0))
        parsed["telemetry"] = tel
        return parsed
