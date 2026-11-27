"""Temporal smoothing and hysteresis filtering for live and pre-recorded BJJ position timelines."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from rollmetrics.rules.schema import EventKind, RawObservedEvent


class FramePositionSample(BaseModel):
    """Single timestamped positional state observation emitted by a vision tracker."""

    timestamp_sec: float = Field(..., ge=0.0)
    state: str
    top_athlete: Literal["A", "B", "neutral"] = "neutral"
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)


class StabilizedSegment(BaseModel):
    """Contiguous smoothed grappling position segment with 3.0s IBJJF hold verification."""

    start_sec: float
    end_sec: float
    duration_sec: float
    state: str
    top_athlete: Literal["A", "B", "neutral"]
    stabilized_3s: bool


class TemporalPositionSmoother:
    """Applies majority sliding window, <1.0s flicker-dropout bridging, and >=3.0s hold gating.

    Inspired by temporal hysteresis filtering in BJJ position classification (Hudovernik & Skocaj,
    MMSports '22) so brief occlusion or scramble flicker never triggers spurious 3.0s scoring holds.
    """

    def __init__(
        self,
        window_size: int = 3,
        max_dropout_sec: float = 1.0,
        min_hold_sec: float = 3.0,
    ) -> None:
        self.window_size = max(1, int(window_size))
        self.max_dropout_sec = max(0.0, float(max_dropout_sec))
        self.min_hold_sec = max(0.5, float(min_hold_sec))

    @staticmethod
    def _norm_state(raw: str) -> str:
        return str(raw or "standing").strip().lower().replace("-", "_").replace(" ", "_") or "standing"

    def smooth_samples(self, samples: list[FramePositionSample]) -> list[FramePositionSample]:
        """Runs a centered majority sliding window + short flicker-dropout bridge over frame samples."""
        if not samples:
            return []
        ordered = sorted(samples, key=lambda s: s.timestamp_sec)
        n = len(ordered)
        half = self.window_size // 2

        # Pass 1: Majority sliding window over (state, top_athlete)
        windowed: list[FramePositionSample] = []
        for idx, item in enumerate(ordered):
            lo = max(0, idx - half)
            hi = min(n, idx + half + 1)
            votes = Counter((self._norm_state(ordered[j].state), ordered[j].top_athlete) for j in range(lo, hi))
            best_state, best_top = votes.most_common(1)[0][0]
            windowed.append(
                item.model_copy(
                    update={
                        "state": best_state,
                        "top_athlete": best_top,
                    }
                )
            )

        # Pass 2: Bridge brief < max_dropout_sec flicker runs sandwiched by identical states
        runs: list[list[int]] = []
        for idx, item in enumerate(windowed):
            key = (item.state, item.top_athlete)
            if not runs:
                runs.append([idx])
            else:
                prev_item = windowed[runs[-1][0]]
                if (prev_item.state, prev_item.top_athlete) == key:
                    runs[-1].append(idx)
                else:
                    runs.append([idx])

        bridged = list(windowed)
        for r_idx in range(1, len(runs) - 1):
            prev_run = runs[r_idx - 1]
            curr_run = runs[r_idx]
            next_run = runs[r_idx + 1]
            prev_key = (bridged[prev_run[0]].state, bridged[prev_run[0]].top_athlete)
            next_key = (bridged[next_run[0]].state, bridged[next_run[0]].top_athlete)
            if prev_key != next_key:
                continue
            gap_dur = bridged[next_run[0]].timestamp_sec - bridged[prev_run[-1]].timestamp_sec
            if gap_dur <= self.max_dropout_sec:
                for sample_idx in curr_run:
                    bridged[sample_idx] = bridged[sample_idx].model_copy(
                        update={"state": prev_key[0], "top_athlete": prev_key[1]}
                    )
        return bridged

    def extract_segments(self, samples: list[FramePositionSample]) -> list[StabilizedSegment]:
        """Merges smoothed samples into contiguous segments tagged with >=3.0s IBJJF stabilization."""
        smoothed = self.smooth_samples(samples)
        if not smoothed:
            return []

        segments: list[StabilizedSegment] = []
        start_idx = 0
        for idx in range(1, len(smoothed) + 1):
            boundary = idx == len(smoothed) or (
                smoothed[idx].state != smoothed[start_idx].state
                or smoothed[idx].top_athlete != smoothed[start_idx].top_athlete
            )
            if boundary:
                t_start = smoothed[start_idx].timestamp_sec
                t_end = smoothed[idx - 1].timestamp_sec
                if idx < len(smoothed):
                    t_end = max(t_end, smoothed[idx].timestamp_sec)
                dur = round(max(0.0, t_end - t_start), 2)
                segments.append(
                    StabilizedSegment(
                        start_sec=round(t_start, 2),
                        end_sec=round(t_end, 2),
                        duration_sec=dur,
                        state=smoothed[start_idx].state,
                        top_athlete=smoothed[start_idx].top_athlete,
                        stabilized_3s=(dur >= self.min_hold_sec),
                    )
                )
                start_idx = idx
        return segments


_SCORING_EVENT_KINDS = frozenset(
    {
        EventKind.TAKEDOWN,
        EventKind.SWEEP,
        EventKind.KNEE_ON_BELLY,
        EventKind.GUARD_PASS,
        EventKind.MOUNT,
        EventKind.BACK_CONTROL,
    }
)


def smooth_scoring_events(
    events: list[RawObservedEvent],
    *,
    merge_window_sec: float = 2.0,
    min_hold_sec: float = 3.0,
) -> list[RawObservedEvent]:
    """Merges flicker-split scoring events within merge_window_sec and gates >=3.0s holds cleanly."""
    if not events:
        return []
    ordered = sorted(events, key=lambda ev: ev.timestamp_sec)
    merged: list[RawObservedEvent] = []

    for ev in ordered:
        if (
            merged
            and ev.kind in _SCORING_EVENT_KINDS
            and merged[-1].actor == ev.actor
            and merged[-1].kind == ev.kind
            and (ev.timestamp_sec - merged[-1].timestamp_sec) <= merge_window_sec
        ):
            prev = merged[-1]
            combined_hold = round(
                max(
                    prev.hold_duration_sec,
                    ev.hold_duration_sec,
                    (ev.timestamp_sec - prev.timestamp_sec) + min(prev.hold_duration_sec, ev.hold_duration_sec),
                ),
                2,
            )
            merged[-1] = prev.model_copy(
                update={
                    "hold_duration_sec": combined_hold,
                    "confidence": round(max(prev.confidence, ev.confidence), 4),
                    "in_bounds": prev.in_bounds and ev.in_bounds,
                }
            )
            continue

        # Clamp spurious sub-0.5s occluded flicker on scoring kinds so noise cannot inflate points
        if ev.kind in _SCORING_EVENT_KINDS and ev.occluded and ev.hold_duration_sec < min_hold_sec:
            merged.append(ev.model_copy(update={"hold_duration_sec": min(ev.hold_duration_sec, 0.5)}))
        else:
            merged.append(ev)

    return merged
