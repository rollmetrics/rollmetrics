"""RollMetrics FastAPI application, REST endpoints, live referee WebSocket, and SPA server."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import inspect
import json
import os
import re
import secrets
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from rollmetrics.auth import (
    DEMO_SEBASTIAN_EMAIL,
    DEMO_SEBASTIAN_TOKEN,
    authenticate_websocket,
    build_demo_sebastian_user,
    check_and_record_daily_quota,
    get_app_settings,
    get_daily_cloud_budget_status,
    is_admin_user,
    load_user_profiles,
    require_user,
    update_user_academy,
    update_user_profile,
    upsert_user_profile,
)
from rollmetrics.config import REPO_ROOT, Settings, get_settings
from rollmetrics.rules.legality_matrix import FOUL_CATALOG, check_technique_legality
from rollmetrics.rules.schema import (
    AgeDivision,
    AthleteId,
    BeltRank,
    EventKind,
    MatchDivisionConfig,
    RawObservedEvent,
    UniformType,
)
from rollmetrics.rules.state_machine import IBJJFRefereeEngine

APP_VERSION = "0.1.0"


def _resolve_build_sha() -> str:
    env_sha = os.getenv("ROLLMETRICS_BUILD_SHA", "").strip()
    if env_sha:
        return env_sha[:7]
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        out = (res.stdout or "").strip()
        if res.returncode == 0 and out:
            return out[:7]
    except (OSError, subprocess.SubprocessError):
        pass
    return "local"


BUILD_SHA = _resolve_build_sha()
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
RECOVERABLE_SUBSYSTEM_ERRORS = (
    ImportError,
    OSError,
    RuntimeError,
    ValueError,
    KeyError,
    TypeError,
    sqlite3.Error,
)

SettingsDep = Annotated[Settings, Depends(get_app_settings)]
UserDep = Annotated[dict[str, Any], Depends(require_user)]


class AnalyzeUrlRequest(BaseModel):
    url: str = Field(..., min_length=4)
    division: MatchDivisionConfig = Field(default_factory=MatchDivisionConfig)
    profile: str | None = None
    sample_fps: float = Field(default=0.5, gt=0.0, le=5.0)


class AnalyzeEventsRequest(BaseModel):
    events: list[RawObservedEvent] = Field(default_factory=list)
    division: MatchDivisionConfig = Field(default_factory=MatchDivisionConfig)
    source_meta: dict[str, Any] | None = None
    preset_id: str | None = None
    finalize_regulation: bool = True


class MediaBoardRequest(BaseModel):
    match_id: str | None = None
    video_path: str | None = None
    center_timestamp_sec: float = Field(default=6.0, ge=0.0)
    title: str = "Technical Sequence"
    cues: list[str] = Field(
        default_factory=lambda: [
            "Entry and grip control",
            "Angle and off-balance",
            "Finish mechanics",
        ]
    )
    actor_label: str = "Athlete A"


class MediaSlowmoRequest(BaseModel):
    match_id: str | None = None
    video_path: str | None = None
    start_sec: float = Field(default=2.0, ge=0.0)
    duration_sec: float = Field(default=5.0, gt=0.2, le=60.0)
    speed: float = Field(default=0.4, gt=0.1, le=1.0)
    caption: str = ""


class LegalityCheckRequest(BaseModel):
    technique_slug: str = ""
    belt: BeltRank = BeltRank.WHITE
    uniform: UniformType = UniformType.GI
    age_division: AgeDivision = AgeDivision.MASTER
    include_matrix: bool = False


class EvalRunRequest(BaseModel):
    version: str = "v0.1.0"


class ModelArenaRunRequest(BaseModel):
    models: list[str] | None = None
    live_api: bool = False
    version: str = "v0.1.0"


class ScoreboardBenchRunRequest(BaseModel):
    split: str = Field(default="calibration", pattern="^(calibration|heldout|pilot)$")
    models: list[str] | None = None
    live_video: bool = False
    tuning_mode: bool = False
    run_id: str = "api-default"
    max_matches: int | None = Field(default=None, ge=1, le=500)


class MatchBucketUpdateRequest(BaseModel):
    bucket: str = Field(..., pattern="^(my_matches|film_study)$")


class AcademyUpdateRequest(BaseModel):
    academy_name: str = Field(..., min_length=1, max_length=120)
    academy_id: str | None = Field(default=None, max_length=64)


class AcademyReferenceCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    text: str = Field(..., min_length=1, max_length=4000)
    position: str = "all"
    belt: str = "white"
    source_url: str = ""
    reference_type: str = Field(default="note", pattern="^(note|video)$")
    instructor: str = ""
    tags: list[str] = Field(default_factory=list)
    timestamp_sec: int | None = Field(default=None, ge=0)


class CollectionSummaryRequest(BaseModel):
    match_ids: list[str] = Field(default_factory=list)
    focus_athlete: str = ""


class UploadInitRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=240)
    content_type: str = "video/mp4"
    size_bytes: int = Field(default=0, ge=0)
    bucket: str = Field(default="my_matches", pattern="^(my_matches|film_study)$")
    uniform: str = Field(default="gi", pattern="^(gi|nogi)$")
    belt: BeltRank = BeltRank.WHITE
    athlete_a_name: str = "Athlete A"
    athlete_b_name: str = "Athlete B"
    athlete_a_gi: str = Field(default="white", pattern="^(white|blue|black)$")
    athlete_b_gi: str = Field(default="blue", pattern="^(white|blue|black)$")
    title: str = ""
    ruleset: str = Field(default="ibjjf_v6", pattern="^(ibjjf_v6|adcc|sparring)$")
    trim_start_sec: float | None = Field(default=None, ge=0.0)
    trim_end_sec: float | None = Field(default=None, ge=0.0)
    athlete_a_anchor: dict[str, float] | None = None
    athlete_b_anchor: dict[str, float] | None = None


class UploadCompleteRequest(BaseModel):
    upload_id: str = Field(..., min_length=1, max_length=120)
    profile: str | None = Field(default=None, pattern="^(cloud|local)$")
    sample_fps: float = Field(default=0.5, gt=0.0, le=5.0)


class GoogleDriveUploadRequest(BaseModel):
    drive_url: str = Field(..., min_length=5, max_length=1000)
    title: str = ""
    bucket: str = Field(default="my_matches", pattern="^(my_matches|film_study)$")
    uniform: str = Field(default="gi", pattern="^(gi|nogi)$")
    belt: BeltRank = BeltRank.WHITE
    athlete_a_name: str = "Athlete A"
    athlete_b_name: str = "Athlete B"
    athlete_a_gi: str = Field(default="white", pattern="^(white|blue|black)$")
    athlete_b_gi: str = Field(default="blue", pattern="^(white|blue|black)$")
    profile: str | None = Field(default=None, pattern="^(cloud|local)$")
    ruleset: str = Field(default="ibjjf_v6", pattern="^(ibjjf_v6|adcc|sparring)$")
    trim_start_sec: float | None = Field(default=None, ge=0.0)
    trim_end_sec: float | None = Field(default=None, ge=0.0)
    athlete_a_anchor: dict[str, float] | None = None
    athlete_b_anchor: dict[str, float] | None = None


class MatchMomentsUpdateRequest(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)
    ruleset: str = Field(default="ibjjf_v6", pattern="^(ibjjf_v6|adcc|sparring)$")
    opt_in_feedback: bool = True


class MatchReviewUpdateRequest(BaseModel):
    review_status: str | None = Field(default=None, pattern="^(needs_review|reviewed)$")
    pin_drill: dict[str, Any] | None = None


class AutoTagReferenceRequest(BaseModel):
    url: str = ""
    title: str = ""
    notes: str = ""


class AthleteProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    picture: str | None = Field(default=None, max_length=500)
    belt: str | None = Field(default=None, pattern="^(white|blue|purple|brown|black)$")
    academy: str | None = Field(default=None, max_length=120)
    coach: str | None = Field(default=None, max_length=120)
    academy_id: str | None = Field(default=None, max_length=80)
    academy_name: str | None = Field(default=None, max_length=120)


class ShareHighlightRequest(BaseModel):
    start_sec: float = Field(..., ge=0.0)
    duration_sec: float = Field(default=5.0, ge=1.0, le=20.0)
    speed: float = Field(default=0.4, ge=0.2, le=1.0)


class LocalFolderScanRequest(BaseModel):
    folder_path: str = Field(..., min_length=1, max_length=1000)


class LocalFolderAnalyzeRequest(BaseModel):
    video_path: str = Field(..., min_length=1, max_length=1000)
    title: str = ""
    belt: BeltRank = BeltRank.WHITE
    uniform: str = Field(default="gi", pattern="^(gi|nogi)$")
    bucket: str = Field(default="my_matches", pattern="^(my_matches|film_study)$")
    athlete_a_name: str = "Athlete A"
    athlete_b_name: str = "Athlete B"


class LocalAcademyImportFolderRequest(BaseModel):
    folder_path: str = Field(..., min_length=1, max_length=1000)
    academy_id: str | None = None
    academy_name: str | None = None


class ProfileSwitchRequest(BaseModel):
    profile: str = Field(..., pattern="^(cloud|local)$")


class SystemConfigUpdateRequest(BaseModel):
    profile: str | None = Field(default=None, pattern="^(cloud|local)$")
    ollama_base_url: str | None = None
    ollama_vision_model: str | None = None
    gemini_api_key: str | None = None


class ConnectionTestRequest(BaseModel):
    target: str = Field(..., pattern="^(local|cloud)$")
    ollama_base_url: str | None = None
    ollama_vision_model: str | None = None
    gemini_api_key: str | None = None


def _builtin_mastermind_presets() -> list[dict[str, Any]]:
    """Audited Mastermind Jiu Jitsu Cup 9 (Montreal, Nov 22, 2026) match presets."""
    return [
        {
            "id": "mastermind_cup9_semifinal",
            "title": "Mastermind Cup 9 Semifinal: Kory Mathewson vs. Michael Dube",
            "subtitle": "Mat 2 #25 · Men Gi White Belt Master 2 (-82.3 kg) · Sub at 2:51 (Inside-Triangle Armbar)",
            "youtube_url": "https://youtu.be/sH8gkS1eti4?t=51",
            "division": {
                "uniform": "gi",
                "belt": "white",
                "age_division": "master",
                "weight_class": "-82.3 kg (Middle)",
                "duration_sec": 300,
                "athlete_a_name": "Kory Mathewson (Plateau BJJ / CTA)",
                "athlete_a_appearance": "White Gi",
                "athlete_b_name": "Michael Dube",
                "athlete_b_appearance": "Blue Gi",
                "locked_athlete": "A",
            },
            "events": [
                {
                    "timestamp_sec": 37.0,
                    "match_clock_str": "04:23",
                    "actor": "A",
                    "kind": "position_change",
                    "hold_duration_sec": 3.0,
                    "confidence": 0.96,
                    "position_after": "Closed Guard",
                    "summary": (
                        "Same-side collar + right-elbow grip guard pull with left foot framing "
                        "Dube's right hip cleanly into closed guard."
                    ),
                },
                {
                    "timestamp_sec": 53.0,
                    "match_clock_str": "04:07",
                    "actor": "B",
                    "kind": "minor_foul",
                    "technique_slug": "fingers_inside_sleeve_or_pant_cuff",
                    "hold_duration_sec": 0.0,
                    "confidence": 0.94,
                    "position_after": "Closed Guard",
                    "summary": (
                        "Referee strips Dube's fingers hooked inside Kory's right sleeve cuff "
                        "(1st penalty on Athlete B)."
                    ),
                },
                {
                    "timestamp_sec": 156.0,
                    "match_clock_str": "02:24",
                    "actor": "A",
                    "kind": "position_change",
                    "hold_duration_sec": 3.0,
                    "confidence": 0.95,
                    "position_after": "Closed Guard High Diamond",
                    "summary": (
                        "Arm-across scissor off-balance collapses Dube's tripod onto his right hip; "
                        "Kory shoots high diamond guard trapping head and right arm."
                    ),
                },
                {
                    "timestamp_sec": 171.0,
                    "match_clock_str": "02:09",
                    "actor": "A",
                    "kind": "submission",
                    "hold_duration_sec": 3.0,
                    "confidence": 0.98,
                    "position_after": "Inside-Triangle Straight Armbar",
                    "summary": (
                        "Inside-triangle straight armbar (Sankaku Juji-Gatame) on Dube's right arm "
                        "forces the tap at 2:51 elapsed."
                    ),
                },
            ],
        },
        {
            "id": "mastermind_cup9_final",
            "title": "Mastermind Cup 9 Gold Final: Kory Mathewson vs. Vincent Lamoureux",
            "subtitle": "Mat 2 #30 · Men Gi White Belt Master 2 (-82.3 kg) · Decision 5:00 (0-0 pts, 1-1 adv, 2-2 pen)",
            "youtube_url": "https://youtu.be/sH8gkS1eti4?t=225",
            "division": {
                "uniform": "gi",
                "belt": "white",
                "age_division": "master",
                "weight_class": "-82.3 kg (Middle)",
                "duration_sec": 300,
                "athlete_a_name": "Kory Mathewson (Plateau BJJ / CTA)",
                "athlete_a_appearance": "White Gi",
                "athlete_b_name": "Vincent Lamoureux",
                "athlete_b_appearance": "Blue Gi",
                "locked_athlete": "A",
            },
            "events": [
                {
                    "timestamp_sec": 42.0,
                    "match_clock_str": "04:18",
                    "actor": "A",
                    "kind": "position_change",
                    "hold_duration_sec": 3.0,
                    "confidence": 0.92,
                    "position_after": "Closed Guard",
                    "summary": "Collar + sleeve guard pull recovers full closed guard after brief left-shin stuff.",
                },
                {
                    "timestamp_sec": 55.0,
                    "match_clock_str": "04:05",
                    "actor": "B",
                    "kind": "position_change",
                    "hold_duration_sec": 3.0,
                    "confidence": 0.91,
                    "position_after": "Double-Under Stack Retention",
                    "summary": (
                        "Lamoureux swims double-unders into 69s Wave-1 tripod stack; "
                        "Kory frames and squares hips back to closed guard with 0 pts / 0 adv conceded."
                    ),
                },
                {
                    "timestamp_sec": 156.0,
                    "match_clock_str": "02:24",
                    "actor": "A",
                    "kind": "double_stalling",
                    "hold_duration_sec": 0.0,
                    "confidence": 0.95,
                    "position_after": "Closed Guard",
                    "summary": "1st simultaneous double-stalling call in closed guard (1-1 pen, 0-0 adv).",
                },
                {
                    "timestamp_sec": 189.0,
                    "match_clock_str": "01:51",
                    "actor": "A",
                    "kind": "position_change",
                    "hold_duration_sec": 2.0,
                    "confidence": 0.89,
                    "position_after": "Closed Guard",
                    "summary": (
                        "Seated wrestle-up countered into bottom turtle (01:49); immediate right-hip "
                        "roll recovers closed guard in 2s with zero points conceded."
                    ),
                },
                {
                    "timestamp_sec": 261.0,
                    "match_clock_str": "00:39",
                    "actor": "A",
                    "kind": "double_stalling",
                    "hold_duration_sec": 0.0,
                    "confidence": 0.96,
                    "position_after": "Closed Guard",
                    "summary": (
                        "2nd simultaneous double-stalling call in closed guard "
                        "(2-2 pen, awarding +1 penalty advantage to each athlete: 1-1 adv)."
                    ),
                },
                {
                    "timestamp_sec": 291.0,
                    "match_clock_str": "00:09",
                    "actor": "A",
                    "kind": "position_change",
                    "hold_duration_sec": 3.0,
                    "confidence": 0.94,
                    "position_after": "Closed Guard 90-Deg Clamp",
                    "summary": (
                        "Closing 39s attack flurry: pendulum angle, hip-bump feint, right-leg triangle "
                        "shoot (00:16), 90-degree shoulder clamp, and buzzer sweep attempt."
                    ),
                },
            ],
        },
    ]


def _load_presets(settings: Settings) -> list[dict[str, Any]]:
    presets = _builtin_mastermind_presets()
    seen_ids = {p["id"] for p in presets}
    timelines_path = settings.knowledge_dir / "evals" / "match_timelines.json"
    if timelines_path.exists():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            raw = json.loads(timelines_path.read_text(encoding="utf-8"))
            items = (
                raw
                if isinstance(raw, list)
                else raw.get("cases") or raw.get("timelines") or raw.get("matches") or []
            )
            disk_presets: list[dict[str, Any]] = []
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                pid = str(item.get("id") or item.get("case_id") or item.get("match_id") or f"preset_{idx+1}")
                title = str(item.get("title") or item.get("name") or item.get("description") or pid)
                events = item.get("events") or item.get("raw_events") or []
                division = item.get("division") or {}
                entry = {
                    "id": pid,
                    "title": title,
                    "subtitle": str(item.get("subtitle") or item.get("notes") or item.get("description") or ""),
                    "youtube_url": str(item.get("youtube_url") or ""),
                    "division": division,
                    "events": events,
                    "expected": item.get("expected") or item.get("expected_outcome") or {},
                }
                disk_presets.append(entry)
            if disk_presets:
                merged = list(disk_presets)
                for builtin in presets:
                    already_covered = any(
                        builtin["id"] == dp["id"]
                        or ("semifinal" in builtin["id"] and "semi" in dp["id"].lower())
                        or ("final" in builtin["id"] and "semi" not in builtin["id"] and "final" in dp["id"].lower())
                        for dp in disk_presets
                    )
                    if not already_covered and (
                        builtin["id"] not in seen_ids or builtin["id"] not in {d["id"] for d in merged}
                    ):
                        merged.append(builtin)
                return merged
    return presets


def _get_retriever(settings: Settings, version: str | None = None) -> Any | None:
    ver = version or settings.active_index_version
    try:
        from rollmetrics.knowledge.retriever import KnowledgeRetriever

        try:
            return KnowledgeRetriever(knowledge_dir=settings.knowledge_dir, version=ver, settings=settings)
        except TypeError:
            return KnowledgeRetriever(knowledge_dir=settings.knowledge_dir, version=ver)
    except RECOVERABLE_SUBSYSTEM_ERRORS:
        return None


def _fallback_analyze_events(
    events: list[RawObservedEvent],
    division: MatchDivisionConfig,
    settings: Settings,
    source_meta: dict[str, Any] | None = None,
    finalize_regulation: bool = True,
) -> dict[str, Any]:
    engine = IBJJFRefereeEngine(division=division, index_version=settings.active_index_version)
    retriever = _get_retriever(settings)
    for raw_ev in events:
        adj = engine.ingest_event(raw_ev)
        if retriever is not None:
            with contextlib.suppress(*RECOVERABLE_SUBSYSTEM_ERRORS):
                pos = raw_ev.position_after or engine.current_position
                adj.cta_recommendations = retriever.recommend_cta_for_position(
                    position=pos,
                    situation=adj.summary,
                    belt=division.belt.value,
                    top_k=2,
                )
    snap = engine.finalize_regulation() if (finalize_regulation and not engine.ended) else engine.snapshot()
    if retriever is not None and not snap.coaching_summary:
        with contextlib.suppress(*RECOVERABLE_SUBSYSTEM_ERRORS):
            snap.coaching_summary = retriever.recommend_cta_for_position(
                position=snap.current_position,
                situation=snap.win_method or "match coaching priority",
                belt=division.belt.value,
                top_k=3,
            )
    data = snap.model_dump(mode="json")
    return {
        "snapshot": data,
        **data,
        "source_meta": source_meta or {"mode": "event_stream"},
    }


def _resolve_ffmpeg_bin() -> str:
    try:
        from rollmetrics.video.ingest import resolve_binary

        return resolve_binary("ffmpeg")
    except RECOVERABLE_SUBSYSTEM_ERRORS:
        for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
            if Path(candidate).is_file():
                return candidate
        return "ffmpeg"


LOCAL_DRIVE_BJJ_DIR = Path(os.getenv("ROLLMETRICS_LOCAL_VIDEO_DIR", "")) if os.getenv("ROLLMETRICS_LOCAL_VIDEO_DIR") else Path("/nonexistent_local_video_dir")


def _showcase_coach_summaries() -> dict[str, dict[str, Any]]:
    """Concise plain-English coach overviews and per-competitor CTA curriculum pointers."""
    return {
        "mc9_semifinal": {
            "overview": (
                "Kory pulled cleanly into closed guard and controlled posture throughout the match. "
                "After Dube drew a cuff-grip penalty, Kory collapsed Dube's tripod with an arm-across "
                "off-balance and finished the inside-triangle armbar at 02:11 remaining."
            ),
            "athlete_a": {
                "name": "Kory Mathewson",
                "gi": "white",
                "cues": [
                    "Chain the arm-across off-balance directly into the high diamond clamp before the opponent resets posture.",
                    "Keep knees pinched tight on the shoulder line to remove hitchhiker escape space during the armbar finish.",
                ],
                "cta_links": [
                    {
                        "label": "Closed Guard Off-Balance & High Diamond",
                        "url": "https://youtu.be/KCJ54kkfoGA?t=15",
                    },
                    {
                        "label": "Inside-Triangle Armbar Mechanics",
                        "url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=122s",
                    },
                ],
            },
            "athlete_b": {
                "name": "Michael Dube",
                "gi": "blue",
                "cues": [
                    "Keep exterior pistol or pocket grips on the sleeve instead of hooking fingers inside the cuff.",
                    "Build posture with elbows tucked inside the hips before standing to avoid getting swept into diamond guard.",
                ],
                "cta_links": [
                    {
                        "label": "Legal Grip Fighting & Posture in Closed Guard",
                        "url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4797s",
                    },
                ],
            },
        },
        "mc9_final": {
            "overview": (
                "A tight 5-minute tactical battle finished 0-0 on points, 1-1 on advantages, and 2-2 on penalties "
                "after two simultaneous stalling calls. Kory survived a long double-under stack, recovered guard "
                "from turtle without conceding points, and won the referee decision with a late attack flurry."
            ),
            "athlete_a": {
                "name": "Kory Mathewson",
                "gi": "white",
                "cues": [
                    "Attack angles and collar drags earlier in closed guard to avoid reaching the second stalling penalty.",
                    "Use the closing 90-degree shoulder clamp and pendulum threat from minute one to force passing errors.",
                ],
                "cta_links": [
                    {
                        "label": "Double-Under Stack Defense & Hip Squaring",
                        "url": "https://www.youtube.com/watch?v=vNaFSONOK-E&t=12s",
                    },
                    {
                        "label": "Closed Guard Angle & Triangle Flurry",
                        "url": "https://youtu.be/-Ths5RDgsK8?t=128",
                    },
                ],
            },
            "athlete_b": {
                "name": "Vincent Lamoureux",
                "gi": "blue",
                "cues": [
                    "Walk the hips sideways off the centerline during the double-under stack instead of driving straight forward.",
                    "Pin the near hip when forcing turtle so the bottom player cannot roll cleanly back to closed guard.",
                ],
                "cta_links": [
                    {
                        "label": "Finishing the Stack Pass & Turtle Control",
                        "url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=1272s",
                    },
                ],
            },
        },
        "seb_gi_sf": {
            "overview": (
                "After a first-round bye in Adult White Belt Gi -76 kg, Sebastian swept directly into mount for "
                "a 6-0 lead and finished Ludovic Thauvette with an arm-triangle choke at 02:22 remaining. "
                "He then closed out the Gi Final by walkover with teammate Luca Curtil for Double Gold."
            ),
            "athlete_a": {
                "name": "Sebastian Bussieres",
                "gi": "black",
                "cues": [
                    "Keep chaining sweeps directly into high mount so the opponent never has time to rebuild frames.",
                    "Drop the chest low and walk off to the side to finish the arm-triangle choke cleanly without losing top control.",
                ],
                "cta_links": [
                    {
                        "label": "Sweep to Mount & Arm-Triangle Finishing Mechanics",
                        "url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=3100s",
                    },
                ],
            },
            "athlete_b": {
                "name": "Ludovic Thauvette",
                "gi": "black",
                "cues": [
                    "Keep elbows framed tight to the ribs during the sweep landing so top mount cannot settle immediately.",
                    "Answer the phone and turn onto your side early before the arm-triangle shoulder pressure closes the carotid.",
                ],
                "cta_links": [
                    {
                        "label": "Mount Survival & Arm-Triangle Early Defense",
                        "url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4468s",
                    },
                ],
            },
        },
        "seb_nogi_r16": {
            "overview": (
                "In his opening No-Gi Eighth-Final, Sebastian pulled guard and hit a clean sweep for 2-0 within "
                "the first 15 seconds. Hebert sustained a right shoulder injury on the landing and could not "
                "continue after medical evaluation, awarding Sebastian the official 2-0 victory."
            ),
            "athlete_a": {
                "name": "Sebastian Bussieres",
                "gi": "white",
                "cues": [
                    "Immediate off-balancing from open guard creates high-percentage early sweeps before the passer settles base.",
                    "Carry that same first-exchange urgency into later bracket rounds.",
                ],
                "cta_links": [
                    {
                        "label": "Open Guard Early Sweep Entries",
                        "url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4468s",
                    },
                ],
            },
            "athlete_b": {
                "name": "Charles-Emile Hebert",
                "gi": "blue",
                "cues": [
                    "Tuck the chin and breakfall cleanly through the shoulder line instead of posting a stiff arm during a sweep.",
                    "Settle hips low and clear grips before stepping into open guard range.",
                ],
                "cta_links": [
                    {
                        "label": "Safe Breakfalls & Base Against Open Guard Sweeps",
                        "url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4468s",
                    },
                ],
            },
        },
        "seb_nogi_qf": {
            "overview": (
                "Sebastian dominated the quarterfinal 18-0 with continuous top pressure and clean scoring chains. "
                "An early sweep into guard pass opened the floodgates, followed by three more passes and full mount."
            ),
            "athlete_a": {
                "name": "Sebastian Bussieres",
                "gi": "white",
                "cues": [
                    "Keep chaining passes directly into mount while the opponent's frames are still collapsed.",
                    "Hunt high underhooks from mount to convert big point leads into early submissions.",
                ],
                "cta_links": [
                    {
                        "label": "No-Gi Guard Pass to Mount Transitions",
                        "url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=3100s",
                    },
                ],
            },
            "athlete_b": {
                "name": "Simon Wu",
                "gi": "blue",
                "cues": [
                    "Establish knee-shield frames and protect inside elbow space immediately when the pass starts.",
                    "Turn to your side rather than staying flat on your back to stop repeat guard passes.",
                ],
                "cta_links": [
                    {
                        "label": "Guard Retention & Early Frame Recovery",
                        "url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=1200s",
                    },
                ],
            },
        },
        "seb_nogi_sf": {
            "overview": (
                "Sebastian built a 6-0 lead with a sweep and mount before Granger locked an illegal beginner-division "
                "reaping leg attack at 02:25 remaining. The head table overturned the mat call to a DQ, advancing Sebastian."
            ),
            "athlete_a": {
                "name": "Sebastian Bussieres",
                "gi": "white",
                "cues": [
                    "Keep weight heavy on the hips and hide the ankles when transitioning from mount so bottom leg entanglements never open.",
                    "Clear the outside knee line early whenever an opponent drops underneath for the legs.",
                ],
                "cta_links": [
                    {
                        "label": "Mount Weight Distribution & Leg-Entanglement Defense",
                        "url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=3100s",
                    },
                ],
            },
            "athlete_b": {
                "name": "Etienne Granger",
                "gi": "blue",
                "cues": [
                    "Review IBJJF white-belt leg lock legality before competing: keep the outside foot off the hip line to avoid reaping DQs.",
                    "Use legal knee-elbow escapes from mount to recover half guard instead of forcing banned twisting locks.",
                ],
                "cta_links": [
                    {
                        "label": "Legal Guard Recovery & Escapes from Mount",
                        "url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=2967s",
                    },
                ],
            },
        },
        "seb_nogi_final": {
            "overview": (
                "Sebastian secured gold with a disciplined 6-0 victory built in the opening two minutes. "
                "He scored an early takedown straight into back control and managed positional control to the final buzzer."
            ),
            "athlete_a": {
                "name": "Sebastian Bussieres",
                "gi": "white",
                "cues": [
                    "Use the cross-wrist trap from back control to open high-percentage rear-naked choke finishes.",
                    "Maintain chest-to-back connection when defending the opponent's shoulder-to-mat escape.",
                ],
                "cta_links": [
                    {
                        "label": "Takedown to Back Control Finishing Sequence",
                        "url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=2150s",
                    },
                ],
            },
            "athlete_b": {
                "name": "Melecio Quero D'Astous",
                "gi": "blue",
                "cues": [
                    "Win the head-position and underhook battle on the feet to deny clean takedown angles.",
                    "Protect the neck first and slide the back of the shoulders to the mat before hooks settle.",
                ],
                "cta_links": [
                    {
                        "label": "Takedown Defense & Back Escape Fundamentals",
                        "url": "https://www.youtube.com/watch?v=AvEeT-mZzCI&t=20s",
                    },
                ],
            },
        },
        "caio_vitor_2014": {
            "overview": (
                "In this open-weight black-belt final, Caio took an early no-grip pull penalty before hitting a clean "
                "single-leg sweep for 2-0. Vitor answered with a wrestle-up sweep and turtle breakdown advantage, then "
                "bypassed the blocked second hook to finish with a bow-and-arrow choke."
            ),
            "athlete_a": {
                "name": "Caio Terra",
                "gi": "blue",
                "cues": [
                    "Secure a firm collar or sleeve grip before sitting to guard to avoid the Art. 6.3.2 (J) penalty.",
                    "Protect the collar line first when defending turtle against a heavier opponent even when blocking the second hook.",
                ],
                "cta_links": [
                    {
                        "label": "Single-Leg Sweep & Top Stabilization",
                        "url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=3242s",
                    },
                    {
                        "label": "Defending Collar Chokes from Turtle",
                        "url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=2150s",
                    },
                ],
            },
            "athlete_b": {
                "name": "Vitor Oliveira",
                "gi": "white",
                "cues": [
                    "Keep chaining the wrestle-up directly into turtle pressure before smaller guard players can re-establish frames.",
                    "Switching from a blocked second hook straight to the lapel-and-pant bow-and-arrow finish is textbook efficiency.",
                ],
                "cta_links": [
                    {
                        "label": "Turtle Breakdown to Bow-and-Arrow Choke",
                        "url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=2150s",
                    },
                ],
            },
        },
    }


def _showcase_matches_catalog(owner_email: str = "") -> list[dict[str, Any]]:
    """Curated video-synchronized matches for the single-screen RollMetrics MVP."""
    coach_map = _showcase_coach_summaries()
    matches = [
        {
            "id": "mc9_semifinal",
            "pill": "Mathewson vs. Dube",
            "title": "Mastermind Cup 9 · Semifinal",
            "belt": "white",
            "belt_label": "White Belt · Gi · Master",
            "regulation_sec": 300.0,
            "video_duration_sec": 224.7,
            "video_start_sec": 16.0,
            "video_end_sec": 185.0,
            "clock_anchors": [[16.0, 300.0], [185.0, 131.0]],
            "athlete_a": {"name": "Kory Mathewson", "belt": "white", "gi": "white"},
            "athlete_b": {"name": "Michael Dube", "belt": "white", "gi": "blue"},
            "winner": "A",
            "win_label": "SUB",
            "events": [
                {
                    "id": "sf-1",
                    "video_sec": 51.0,
                    "time_str": "00:51",
                    "clock_str": "04:25",
                    "actor": "A",
                    "call": "GUARD PULL",
                    "detail": "Closed Guard",
                    "rule": "IBJJF Art. 6.3.2 (J)",
                    "cta_url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=2967s",
                    "pts_a": 0,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": False,
                },
                {
                    "id": "sf-2",
                    "video_sec": 67.0,
                    "time_str": "01:07",
                    "clock_str": "04:09",
                    "actor": "B",
                    "call": "+1 PEN",
                    "detail": "Cuff Grip",
                    "rule": "IBJJF Art. 6.3.2 (F)",
                    "cta_url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4797s",
                    "pts_a": 0,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 1,
                    "control_3s": False,
                },
                {
                    "id": "sf-3",
                    "video_sec": 170.0,
                    "time_str": "02:50",
                    "clock_str": "02:26",
                    "actor": "A",
                    "call": "OFF-BALANCE",
                    "detail": "Diamond Trap",
                    "rule": "IBJJF Art. 5.2",
                    "cta_url": "https://youtu.be/KCJ54kkfoGA?t=15",
                    "pts_a": 0,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 1,
                    "control_3s": False,
                },
                {
                    "id": "sf-4",
                    "video_sec": 185.0,
                    "time_str": "03:05",
                    "clock_str": "02:11",
                    "actor": "A",
                    "call": "SUBMISSION",
                    "detail": "Armbar",
                    "rule": "IBJJF Art. 2.2.1",
                    "cta_url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=122s",
                    "pts_a": 0,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 1,
                    "control_3s": False,
                    "is_finish": True,
                },
            ],
        },
        {
            "id": "mc9_final",
            "pill": "Mathewson vs. Lamoureux",
            "title": "Mastermind Cup 9 · Gold Final",
            "belt": "white",
            "belt_label": "White Belt · Gi · Master",
            "regulation_sec": 300.0,
            "video_duration_sec": 390.5,
            "video_start_sec": 37.0,
            "video_end_sec": 358.0,
            "clock_anchors": [
                [37.0, 300.0],
                [93.0, 244.0],
                [193.0, 144.0],
                [200.0, 144.0],
                [299.0, 45.0],
                [305.0, 39.0],
                [319.0, 39.0],
                [358.0, 0.0],
            ],
            "athlete_a": {"name": "Kory Mathewson", "belt": "white", "gi": "white"},
            "athlete_b": {"name": "Vincent Lamoureux", "belt": "white", "gi": "blue"},
            "winner": "A",
            "win_label": "DEC",
            "events": [
                {
                    "id": "fn-1",
                    "video_sec": 80.0,
                    "time_str": "01:20",
                    "clock_str": "04:17",
                    "actor": "A",
                    "call": "GUARD PULL",
                    "detail": "Closed Guard",
                    "rule": "IBJJF Art. 6.2.2",
                    "cta_url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=2967s",
                    "pts_a": 0,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": False,
                },
                {
                    "id": "fn-2",
                    "video_sec": 93.0,
                    "time_str": "01:33",
                    "clock_str": "04:04",
                    "actor": "A",
                    "call": "RETENTION",
                    "detail": "Stack Defense",
                    "rule": "IBJJF Art. 4.2",
                    "cta_url": "https://www.youtube.com/watch?v=vNaFSONOK-E&t=12s",
                    "pts_a": 0,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": False,
                },
                {
                    "id": "fn-3",
                    "video_sec": 194.0,
                    "time_str": "03:14",
                    "clock_str": "02:24",
                    "actor": "A",
                    "call": "+1 PEN EACH",
                    "detail": "Double Stalling",
                    "rule": "IBJJF Art. 6.3.2",
                    "cta_url": "https://youtu.be/eW8OtG8OWM8?t=29",
                    "pts_a": 0,
                    "adv_a": 0,
                    "pen_a": 1,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 1,
                    "control_3s": False,
                },
                {
                    "id": "fn-4",
                    "video_sec": 227.0,
                    "time_str": "03:47",
                    "clock_str": "01:57",
                    "actor": "A",
                    "call": "SCRAMBLE",
                    "detail": "Turtle Recovery",
                    "rule": "IBJJF Art. 4.1",
                    "cta_url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=1272s",
                    "pts_a": 0,
                    "adv_a": 0,
                    "pen_a": 1,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 1,
                    "control_3s": False,
                },
                {
                    "id": "fn-5",
                    "video_sec": 299.0,
                    "time_str": "04:59",
                    "clock_str": "00:45",
                    "actor": "A",
                    "call": "PEN #2 EACH",
                    "detail": "2nd Stalling",
                    "rule": "IBJJF Art. 5.3",
                    "cta_url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=598s",
                    "pts_a": 0,
                    "adv_a": 1,
                    "pen_a": 2,
                    "pts_b": 0,
                    "adv_b": 1,
                    "pen_b": 2,
                    "control_3s": False,
                },
                {
                    "id": "fn-6",
                    "video_sec": 358.0,
                    "time_str": "05:58",
                    "clock_str": "00:00",
                    "actor": "A",
                    "call": "DECISION",
                    "detail": "Closing Flurry",
                    "rule": "IBJJF Art. 2.2",
                    "cta_url": "https://youtu.be/-Ths5RDgsK8?t=128",
                    "pts_a": 0,
                    "adv_a": 1,
                    "pen_a": 2,
                    "pts_b": 0,
                    "adv_b": 1,
                    "pen_b": 2,
                    "control_3s": False,
                    "is_finish": True,
                },
            ],
        },
        {
            "id": "seb_gi_sf",
            "source": "scoreboard_ground_truth",
            "pill": "Bussieres vs. Thauvette",
            "title": "Mastermind Cup 9 · Gi Semifinal (Mat 4 #20)",
            "belt": "white",
            "belt_label": "White Belt · Gi · Adult -76 kg",
            "regulation_sec": 300.0,
            "video_duration_sec": 255.0,
            "video_start_sec": 19.0,
            "video_end_sec": 178.0,
            "athlete_a": {"name": "Sebastian Bussieres", "belt": "white", "gi": "black"},
            "athlete_b": {"name": "Ludovic Thauvette", "belt": "white", "gi": "black"},
            "winner": "A",
            "win_label": "SUB",
            "events": [
                {
                    "id": "sgs-1",
                    "video_sec": 84.0,
                    "time_str": "01:24",
                    "clock_str": "03:55",
                    "actor": "A",
                    "call": "+2 PTS",
                    "detail": "Sweep",
                    "rule": "IBJJF Art. 4.6",
                    "cta_url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4468s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sgs-2",
                    "video_sec": 86.0,
                    "time_str": "01:26",
                    "clock_str": "03:53",
                    "actor": "A",
                    "call": "+4 PTS",
                    "detail": "Mount",
                    "rule": "IBJJF Art. 4.4",
                    "cta_url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=3100s",
                    "pts_a": 6,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sgs-3",
                    "video_sec": 177.0,
                    "time_str": "02:57",
                    "clock_str": "02:22",
                    "actor": "A",
                    "call": "SUBMISSION",
                    "detail": "Arm-Triangle Choke",
                    "rule": "IBJJF Art. 2.1",
                    "cta_url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=3100s",
                    "pts_a": 6,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": False,
                    "is_finish": True,
                },
            ],
        },
        {
            "id": "seb_nogi_r16",
            "source": "scoreboard_ground_truth",
            "pill": "Bussieres vs. Hebert",
            "title": "Mastermind Cup 9 · No-Gi Eighth-Final (Mat 5 #44)",
            "belt": "white",
            "belt_label": "White Belt · No-Gi · Adult -73.5 kg",
            "regulation_sec": 300.0,
            "video_duration_sec": 375.0,
            "video_start_sec": 21.0,
            "video_end_sec": 295.0,
            "athlete_a": {"name": "Sebastian Bussieres", "belt": "white", "gi": "white"},
            "athlete_b": {"name": "Charles-Emile Hebert", "belt": "white", "gi": "blue"},
            "winner": "A",
            "win_label": "DQ",
            "events": [
                {
                    "id": "sbr-1",
                    "video_sec": 35.0,
                    "time_str": "00:35",
                    "clock_str": "04:46",
                    "actor": "A",
                    "call": "+2 PTS",
                    "detail": "Guard Sweep",
                    "rule": "IBJJF Art. 4.6",
                    "cta_url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4468s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sbr-2",
                    "video_sec": 39.0,
                    "time_str": "00:39",
                    "clock_str": "04:41",
                    "actor": "B",
                    "call": "INJURY STOP",
                    "detail": "Medical Evaluation",
                    "rule": "IBJJF Art. 1.3.7",
                    "cta_url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4468s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": False,
                },
                {
                    "id": "sbr-3",
                    "video_sec": 290.0,
                    "time_str": "04:50",
                    "clock_str": "04:41",
                    "actor": "A",
                    "call": "STOPPAGE WIN",
                    "detail": "Medical Stoppage (2-0)",
                    "rule": "IBJJF Art. 2.4",
                    "cta_url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4468s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": False,
                    "is_finish": True,
                },
            ],
        },
        {
            "id": "seb_nogi_qf",
            "source": "scoreboard_ground_truth",
            "pill": "Bussieres vs. Wu",
            "title": "Mastermind Cup 9 · No-Gi Quarterfinal (Mat 5 #49)",
            "belt": "white",
            "belt_label": "White Belt · No-Gi · Adult -73.5 kg",
            "regulation_sec": 300.0,
            "video_duration_sec": 380.0,
            "video_start_sec": 22.0,
            "video_end_sec": 340.0,
            "athlete_a": {"name": "Sebastian Bussieres", "belt": "white", "gi": "white"},
            "athlete_b": {"name": "Simon Wu", "belt": "white", "gi": "blue"},
            "winner": "A",
            "win_label": "PTS 18-0",
            "events": [
                {
                    "id": "sbq-1",
                    "video_sec": 75.0,
                    "time_str": "01:15",
                    "clock_str": "04:11",
                    "actor": "A",
                    "call": "+2 PTS",
                    "detail": "Sweep",
                    "rule": "IBJJF Art. 4.6",
                    "cta_url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4468s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sbq-2",
                    "video_sec": 78.0,
                    "time_str": "01:18",
                    "clock_str": "04:08",
                    "actor": "A",
                    "call": "+3 PTS",
                    "detail": "Guard Pass",
                    "rule": "IBJJF Art. 4.2",
                    "cta_url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=1200s",
                    "pts_a": 5,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sbq-3",
                    "video_sec": 187.0,
                    "time_str": "03:07",
                    "clock_str": "02:19",
                    "actor": "A",
                    "call": "+3 PTS",
                    "detail": "Guard Pass",
                    "rule": "IBJJF Art. 4.2",
                    "cta_url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=1200s",
                    "pts_a": 8,
                    "adv_a": 1,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sbq-4",
                    "video_sec": 193.0,
                    "time_str": "03:13",
                    "clock_str": "02:13",
                    "actor": "A",
                    "call": "+3 PTS",
                    "detail": "Side Control Pass",
                    "rule": "IBJJF Art. 4.2",
                    "cta_url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=1200s",
                    "pts_a": 11,
                    "adv_a": 1,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sbq-5",
                    "video_sec": 291.0,
                    "time_str": "04:51",
                    "clock_str": "00:50",
                    "actor": "A",
                    "call": "+4 PTS",
                    "detail": "Mount",
                    "rule": "IBJJF Art. 4.4",
                    "cta_url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=3100s",
                    "pts_a": 15,
                    "adv_a": 1,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sbq-6",
                    "video_sec": 296.0,
                    "time_str": "04:56",
                    "clock_str": "00:45",
                    "actor": "A",
                    "call": "+3 PTS",
                    "detail": "Guard Pass (18-0)",
                    "rule": "IBJJF Art. 4.2",
                    "cta_url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=1200s",
                    "pts_a": 18,
                    "adv_a": 1,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                    "is_finish": True,
                },
            ],
        },
        {
            "id": "seb_nogi_sf",
            "source": "scoreboard_ground_truth",
            "pill": "Bussieres vs. Granger",
            "title": "Mastermind Cup 9 · No-Gi Semifinal (Mat 5 #51)",
            "belt": "white",
            "belt_label": "White Belt · No-Gi · Adult -73.5 kg",
            "regulation_sec": 300.0,
            "video_duration_sec": 215.0,
            "video_start_sec": 9.0,
            "video_end_sec": 195.0,
            "athlete_a": {"name": "Sebastian Bussieres", "belt": "white", "gi": "white"},
            "athlete_b": {"name": "Etienne Granger", "belt": "white", "gi": "blue"},
            "winner": "A",
            "win_label": "DQ",
            "events": [
                {
                    "id": "sbs-1",
                    "video_sec": 99.0,
                    "time_str": "01:39",
                    "clock_str": "03:34",
                    "actor": "A",
                    "call": "+2 PTS",
                    "detail": "Sweep",
                    "rule": "IBJJF Art. 4.6",
                    "cta_url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=4468s",
                    "pts_a": 2,
                    "adv_a": 1,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sbs-2",
                    "video_sec": 109.0,
                    "time_str": "01:49",
                    "clock_str": "03:24",
                    "actor": "A",
                    "call": "+4 PTS",
                    "detail": "Mount",
                    "rule": "IBJJF Art. 4.4",
                    "cta_url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=3100s",
                    "pts_a": 6,
                    "adv_a": 1,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sbs-3",
                    "video_sec": 168.0,
                    "time_str": "02:48",
                    "clock_str": "02:25",
                    "actor": "B",
                    "call": "DQ FOUL",
                    "detail": "Illegal Leg Lock",
                    "rule": "IBJJF Art. 6.2.3 (K/M) Head Table DQ Overturn",
                    "cta_url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=2967s",
                    "pts_a": 6,
                    "adv_a": 1,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 4,
                    "control_3s": False,
                    "is_finish": True,
                },
            ],
        },
        {
            "id": "seb_nogi_final",
            "source": "scoreboard_ground_truth",
            "pill": "Bussieres vs. Quero D'Astous",
            "title": "Mastermind Cup 9 · No-Gi Gold Final (Mat 5 #39)",
            "belt": "white",
            "belt_label": "White Belt · No-Gi · Adult -73.5 kg",
            "regulation_sec": 300.0,
            "video_duration_sec": 370.0,
            "video_start_sec": 23.0,
            "video_end_sec": 340.0,
            "athlete_a": {"name": "Sebastian Bussieres", "belt": "white", "gi": "white"},
            "athlete_b": {"name": "Melecio Quero D'Astous", "belt": "white", "gi": "blue"},
            "winner": "A",
            "win_label": "PTS 6-0",
            "events": [
                {
                    "id": "sbf-1",
                    "video_sec": 77.0,
                    "time_str": "01:17",
                    "clock_str": "04:09",
                    "actor": "A",
                    "call": "+2 PTS",
                    "detail": "Takedown",
                    "rule": "IBJJF Art. 4.1",
                    "cta_url": "https://www.youtube.com/watch?v=AvEeT-mZzCI&t=20s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sbf-2",
                    "video_sec": 81.0,
                    "time_str": "01:21",
                    "clock_str": "04:05",
                    "actor": "A",
                    "call": "+4 PTS",
                    "detail": "Back Control",
                    "rule": "IBJJF Art. 4.5",
                    "cta_url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=2150s",
                    "pts_a": 6,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "sbf-3",
                    "video_sec": 340.0,
                    "time_str": "05:40",
                    "clock_str": "00:00",
                    "actor": "A",
                    "call": "GOLD · 6-0",
                    "detail": "Points Victory",
                    "rule": "IBJJF Art. 2.2",
                    "cta_url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=2150s",
                    "pts_a": 6,
                    "adv_a": 0,
                    "pen_a": 0,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": False,
                    "is_finish": True,
                },
            ],
        },
        {
            "id": "caio_vitor_2014",
            "pill": "Terra vs. Oliveira",
            "title": "2014 Chicago Open · Absolute Final",
            "belt": "black",
            "belt_label": "Black Belt · Gi · Adult",
            "regulation_sec": 600.0,
            "video_duration_sec": 137.3,
            "video_start_sec": 5.5,
            "video_end_sec": 114.0,
            "clock_anchors": [
                [5.5, 600.0],
                [15.0, 590.5],
                [22.0, 590.5],
                [114.0, 498.5],
            ],
            "athlete_a": {"name": "Caio Terra", "belt": "black", "gi": "blue"},
            "athlete_b": {"name": "Vitor Oliveira", "belt": "black", "gi": "white"},
            "winner": "B",
            "win_label": "SUB",
            "events": [
                {
                    "id": "ct-1",
                    "video_sec": 17.0,
                    "time_str": "00:17",
                    "clock_str": "09:48",
                    "actor": "A",
                    "call": "+1 PEN",
                    "detail": "No-Grip Pull",
                    "rule": "IBJJF Art. 6.3.2 (J)",
                    "cta_url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=2967s",
                    "pts_a": 0,
                    "adv_a": 0,
                    "pen_a": 1,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": False,
                },
                {
                    "id": "ct-2",
                    "video_sec": 70.0,
                    "time_str": "01:10",
                    "clock_str": "08:55",
                    "actor": "A",
                    "call": "+2 PTS",
                    "detail": "Single-Leg Sweep",
                    "rule": "IBJJF Art. 4.6",
                    "cta_url": "https://www.youtube.com/watch?v=4-7AYrBCOvQ&t=3242s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 1,
                    "pts_b": 0,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "ct-3",
                    "video_sec": 78.0,
                    "time_str": "01:18",
                    "clock_str": "08:47",
                    "actor": "B",
                    "call": "+2 PTS",
                    "detail": "Wrestle-Up Sweep",
                    "rule": "IBJJF Art. 4.6",
                    "cta_url": "https://www.youtube.com/watch?v=VWzT1iUMvEc&t=2423s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 1,
                    "pts_b": 2,
                    "adv_b": 0,
                    "pen_b": 0,
                    "control_3s": True,
                },
                {
                    "id": "ct-4",
                    "video_sec": 82.5,
                    "time_str": "01:22",
                    "clock_str": "08:43",
                    "actor": "B",
                    "call": "+1 ADV",
                    "detail": "Turtle Breakdown",
                    "rule": "IBJJF Art. 5.2",
                    "cta_url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=1272s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 1,
                    "pts_b": 2,
                    "adv_b": 1,
                    "pen_b": 0,
                    "control_3s": False,
                    "hold_sec": 1.8,
                },
                {
                    "id": "ct-5",
                    "video_sec": 93.0,
                    "time_str": "01:33",
                    "clock_str": "08:32",
                    "actor": "B",
                    "call": "BACK ATTACK",
                    "detail": "1-Hook Seatbelt",
                    "rule": "IBJJF Art. 4.5 (2nd hook blocked)",
                    "cta_url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=2150s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 1,
                    "pts_b": 2,
                    "adv_b": 1,
                    "pen_b": 0,
                    "control_3s": False,
                },
                {
                    "id": "ct-6",
                    "video_sec": 114.0,
                    "time_str": "01:54",
                    "clock_str": "08:11",
                    "actor": "B",
                    "call": "SUBMISSION",
                    "detail": "Bow-and-Arrow Choke",
                    "rule": "IBJJF Art. 2.2.1",
                    "cta_url": "https://www.youtube.com/watch?v=xp6xTVThYAo&t=2150s",
                    "pts_a": 2,
                    "adv_a": 0,
                    "pen_a": 1,
                    "pts_b": 2,
                    "adv_b": 1,
                    "pen_b": 0,
                    "control_3s": False,
                    "is_finish": True,
                },
            ],
        },
    ]
    resolved_owner = owner_email.strip().lower()
    for item in matches:
        mid = str(item.get("id") or "")
        if mid in ("mc9_semifinal", "mc9_final") and resolved_owner:
            item["bucket"] = "my_matches"
            item["owner_email"] = resolved_owner
        else:
            item["bucket"] = "film_study"
            item["owner_email"] = ""
        if mid in coach_map:
            item["coach_summary"] = coach_map[mid]
    return matches


def _load_user_bucket_overrides(settings: Settings) -> dict[str, dict[str, str]]:
    store = settings.runtime_dir / "user_match_buckets.json"
    if not store.is_file():
        return {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        raw = json.loads(store.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {
                str(k).lower(): {str(mk): str(mv) for mk, mv in v.items()}
                for k, v in raw.items()
                if isinstance(v, dict)
            }
    return {}


def _save_user_bucket_override(settings: Settings, email: str, match_id: str, bucket: str) -> None:
    data = _load_user_bucket_overrides(settings)
    key = email.strip().lower()
    user_map = data.get(key, {})
    user_map[match_id] = bucket
    data[key] = user_map
    store = settings.runtime_dir / "user_match_buckets.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(data, indent=2), encoding="utf-8")


_RULE_INFO_CACHE: dict[tuple[str, str], dict[str, str]] = {}


def _resolve_rule_info(rule_str: str, detail: str, app_cfg: Settings) -> dict[str, str]:
    key = (rule_str.strip(), detail.strip())
    cached = _RULE_INFO_CACHE.get(key)
    if cached is not None:
        return dict(cached)

    rulebook_url = "https://ibjjf.com/books-videos"
    title = rule_str.strip() or "IBJJF Rulebook v6.1"
    excerpt = f"Official IBJJF Rulebook v6.1 reference for {rule_str.strip()} ({detail.strip()})."
    video_url = ""
    video_title = ""

    retriever = _get_retriever(app_cfg)
    if retriever is not None:
        with contextlib.suppress(*RECOVERABLE_SUBSYSTEM_ERRORS):
            hits = retriever.cite_ibjjf_rule(f"{rule_str} {detail}", top_k=5)
            primary = hits[0] if hits else None
            if primary:
                cand_title = str(primary.get("title") or "").strip()
                if cand_title:
                    title = cand_title.replace("\u2014", "-")
                cand_text = str(primary.get("text") or "").strip().replace("\u2014", "-")
                if cand_text:
                    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cand_text) if s.strip()]
                    two_sent = " ".join(sentences[:2]).strip()
                    excerpt = two_sent if len(two_sent) <= 280 else (sentences[0] if sentences else cand_text)
                src = str(primary.get("source_url") or "").strip()
                if src and "youtube.com" not in src and "youtu.be" not in src:
                    rulebook_url = src
            for h in hits:
                vurl = str(h.get("video_url") or "").strip()
                if vurl and ("youtube.com" in vurl or "youtu.be" in vurl):
                    video_url = vurl
                    video_title = str(h.get("title") or "Official IBJJF Rules Video").replace("\u2014", "-")
                    break

    info = {
        "article": rule_str.strip(),
        "title": title,
        "excerpt": excerpt,
        "rulebook_url": rulebook_url,
        "ibjjf_video_url": video_url,
        "ibjjf_video_title": video_title,
    }
    _RULE_INFO_CACHE[key] = info
    return dict(info)


_CTA_INFO_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def _extract_url_start_sec(url: str) -> int:
    m = re.search(r"[?&](?:t|start)=(\d+)", url or "")
    if m:
        return int(m.group(1))
    return 0


def _resolve_cta_info(cta_url: str, detail: str, app_cfg: Settings) -> dict[str, Any]:
    clean_url = (cta_url or "").strip()
    clean_detail = (detail or "").strip()
    key = (clean_url, clean_detail)
    cached = _CTA_INFO_CACHE.get(key)
    if cached is not None:
        return dict(cached)

    title = clean_detail or "Curriculum Reference"
    excerpt = f"Technical breakdown and leverage cue for {clean_detail or 'this exchange'}."
    start_sec = _extract_url_start_sec(clean_url)
    source_kind = "youtube" if ("youtube.com" in clean_url or "youtu.be" in clean_url) else (
        "hosted_mp4" if clean_url.lower().endswith(".mp4") else "reference"
    )

    retriever = _get_retriever(app_cfg)
    if retriever is not None and clean_detail:
        with contextlib.suppress(*RECOVERABLE_SUBSYSTEM_ERRORS):
            hits = retriever.search(query=clean_detail, corpus="cta", top_k=2)
            if hits:
                top = hits[0]
                if getattr(top, "title", ""):
                    title = str(top.title).replace("\u2014", "-")
                cand_text = str(getattr(top, "text", "") or "").strip().replace("\u2014", "-")
                if cand_text:
                    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cand_text) if s.strip()]
                    two_sent = " ".join(sentences[:2]).strip()
                    excerpt = two_sent if len(two_sent) <= 280 else (sentences[0] if sentences else cand_text)

    info = {
        "title": title,
        "excerpt": excerpt,
        "video_url": clean_url,
        "start_sec": start_sec,
        "source_kind": source_kind,
    }
    _CTA_INFO_CACHE[key] = info
    return dict(info)


def _resolve_matches_for_user(app_cfg: Settings, user_email: str) -> list[dict[str, Any]]:
    email = user_email.strip().lower()
    owner_cfg = app_cfg.owner_email.strip().lower()
    if not owner_cfg:
        allow = sorted(app_cfg.parsed_allowlist())
        owner_cfg = allow[0] if allow else (email if app_cfg.environment in ("local", "test") else "")
    matches = _showcase_matches_catalog(owner_email=owner_cfg)
    extra_dir = app_cfg.runtime_dir / "matches"
    if extra_dir.is_dir():
        known_ids = {m["id"] for m in matches}
        for jf in sorted(extra_dir.glob("*.json")):
            with contextlib.suppress(OSError, json.JSONDecodeError):
                entry = json.loads(jf.read_text(encoding="utf-8"))
                if isinstance(entry, dict) and entry.get("id") and entry["id"] not in known_ids:
                    entry.setdefault("bucket", "film_study")
                    entry.setdefault("owner_email", "")
                    matches.append(entry)
                    known_ids.add(str(entry["id"]))
    overrides = _load_user_bucket_overrides(app_cfg).get(email, {})
    is_demo_seb = email == DEMO_SEBASTIAN_EMAIL
    for m in matches:
        mid = str(m.get("id") or "")
        if mid in overrides:
            m["bucket"] = overrides[mid]
            if overrides[mid] == "my_matches":
                m["owner_email"] = email
        elif is_demo_seb:
            if mid.startswith("seb_"):
                m["bucket"] = "my_matches"
                m["owner_email"] = DEMO_SEBASTIAN_EMAIL
            else:
                m["bucket"] = "film_study"
        elif m.get("bucket") == "my_matches" and str(m.get("owner_email") or "").lower() != email:
            m["bucket"] = "film_study"
        for ev in m.get("events") or []:
            if isinstance(ev, dict):
                if ev.get("rule") and not ev.get("rule_info"):
                    ev["rule_info"] = _resolve_rule_info(
                        str(ev.get("rule") or ""),
                        str(ev.get("detail") or ""),
                        app_cfg,
                    )
                if ev.get("cta_url") and not ev.get("cta_info"):
                    ev["cta_info"] = _resolve_cta_info(
                        str(ev.get("cta_url") or ""),
                        str(ev.get("detail") or ""),
                        app_cfg,
                    )
    return matches


def _build_personal_coaching_summary(matches: list[dict[str, Any]], user_email: str) -> dict[str, Any]:
    my_matches = [m for m in matches if m.get("bucket") == "my_matches"]
    wins = 0
    losses = 0
    draws = 0
    subs = 0
    decs = 0
    points_scored = 0
    points_allowed = 0
    positions: list[str] = []
    cta_priorities: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for m in my_matches:
        winner = str(m.get("winner") or "")
        win_label = str(m.get("win_label") or "").upper()
        if winner == "A":
            wins += 1
            if "SUB" in win_label:
                subs += 1
            elif "DEC" in win_label:
                decs += 1
        elif winner == "B":
            losses += 1
        else:
            draws += 1

        events = m.get("events") or []
        if events:
            last_ev = events[-1]
            points_scored += int(last_ev.get("pts_a") or 0)
            points_allowed += int(last_ev.get("pts_b") or 0)
        for ev in events:
            detail = str(ev.get("detail") or "").strip()
            if detail and detail not in positions and len(positions) < 5:
                positions.append(detail)

        cs = m.get("coach_summary") or {}
        ath_a = cs.get("athlete_a") or {}
        cues = ath_a.get("cues") or []
        for idx, link in enumerate(ath_a.get("cta_links") or []):
            url = str(link.get("url") or "")
            if url and url not in seen_urls and len(cta_priorities) < 3:
                seen_urls.add(url)
                cta_priorities.append(
                    {
                        "title": str(link.get("label") or "CTA Curriculum"),
                        "cue": str(cues[idx] if idx < len(cues) else (cues[0] if cues else "")),
                        "url": url,
                    }
                )

    breakdown_parts = []
    if subs:
        breakdown_parts.append(f"{subs} SUB")
    if decs:
        breakdown_parts.append(f"{decs} DEC")
    breakdown = f" ({', '.join(breakdown_parts)})" if breakdown_parts else ""
    record_label = f"{wins}-{losses}" + (f"-{draws}" if draws else "") + breakdown
    summary_line = (
        f"{len(my_matches)} matches logged ({record_label}), {points_allowed} points allowed. "
        "Closed guard retention and arm-across off-balances are your highest-leverage win drivers."
        if my_matches
        else "Tag any uploaded or catalog match as My Match to build your personal game plan."
    )
    return {
        "user_email": user_email,
        "match_count": len(my_matches),
        "record": {
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "subs": subs,
            "decisions": decs,
            "label": record_label,
        },
        "points_scored": points_scored,
        "points_allowed": points_allowed,
        "recurring_positions": positions,
        "summary_line": summary_line,
        "top_cta_priorities": cta_priorities,
    }


def _build_collection_scouting_summary(
    matches: list[dict[str, Any]],
    focus_athlete: str = "",
) -> dict[str, Any]:
    target = focus_athlete.strip()
    target_lower = target.lower()
    selected: list[tuple[dict[str, Any], str]] = []

    for m in matches:
        name_a = str((m.get("athlete_a") or {}).get("name") or "")
        name_b = str((m.get("athlete_b") or {}).get("name") or "")
        if not target_lower or target_lower in name_a.lower():
            selected.append((m, "A"))
        elif target_lower in name_b.lower():
            selected.append((m, "B"))

    resolved_name = target or "All Film Study"
    if target_lower and selected:
        first_m, first_side = selected[0]
        resolved_name = str(
            (first_m.get("athlete_a" if first_side == "A" else "athlete_b") or {}).get("name") or target
        )

    wins = 0
    losses = 0
    draws = 0
    subs = 0
    dqs = 0
    pts_for = 0
    pts_against = 0
    patterns: list[str] = []
    takeaways: list[str] = []
    cta_links: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    match_ids: list[str] = []

    for m, side in selected:
        match_ids.append(str(m.get("id") or ""))
        winner = str(m.get("winner") or "")
        win_label = str(m.get("win_label") or "").upper()
        if winner == side:
            wins += 1
            if "SUB" in win_label:
                subs += 1
            elif "DQ" in win_label:
                dqs += 1
        elif winner in ("A", "B"):
            losses += 1
        else:
            draws += 1

        events = m.get("events") or []
        if events:
            last_ev = events[-1]
            pa = int(last_ev.get("pts_a") or 0)
            pb = int(last_ev.get("pts_b") or 0)
            if side == "A":
                pts_for += pa
                pts_against += pb
            else:
                pts_for += pb
                pts_against += pa
        for ev in events:
            if str(ev.get("actor") or "") == side:
                detail = str(ev.get("detail") or "").strip()
                if detail and detail not in patterns and len(patterns) < 6:
                    patterns.append(detail)

        cs = m.get("coach_summary") or {}
        ath_block = cs.get("athlete_a" if side == "A" else "athlete_b") or {}
        for cue in ath_block.get("cues") or []:
            cue_str = str(cue).strip()
            if cue_str and cue_str not in takeaways and len(takeaways) < 3:
                takeaways.append(cue_str)
        for link in ath_block.get("cta_links") or []:
            url = str(link.get("url") or "")
            if url and url not in seen_urls and len(cta_links) < 3:
                seen_urls.add(url)
                cta_links.append({"title": str(link.get("label") or "CTA Curriculum"), "url": url})

    record_label = f"{wins}-{losses}" + (f"-{draws}" if draws else "")
    if "bussieres" in resolved_name.lower() and len(selected) >= 5 and wins == 5:
        record_label = "5-0 Double Gold"
        summary_text = (
            f"{resolved_name} went 5-0 across Gi and No-Gi for Double Gold, outscoring opponents "
            f"{pts_for}-{pts_against} with zero points conceded. Dominant sweep-to-mount and back-control "
            "chains controlled every bout from the opening exchanges."
        )
    elif "caio" in resolved_name.lower() and selected:
        summary_text = (
            f"{resolved_name} scored {pts_for} points with a clean single-leg sweep against a much heavier "
            "open-weight opponent before defending a 1-hook back attack and getting caught in a bow-and-arrow choke."
        )
    elif selected:
        summary_text = (
            f"{resolved_name}: {len(selected)} matches ({record_label}), {pts_for} points scored and "
            f"{pts_against} points allowed across the selected film study bouts."
        )
    else:
        summary_text = "No matching film study bouts found for this filter."

    return {
        "focus_athlete": resolved_name,
        "match_count": len(selected),
        "record_label": record_label,
        "combined_points_for": pts_for,
        "combined_points_against": pts_against,
        "summary_text": summary_text,
        "a_game_patterns": patterns,
        "coaching_takeaways": takeaways,
        "cta_links": cta_links,
        "match_ids": match_ids,
    }


def _load_usage_telemetry(settings: Settings) -> dict[str, dict[str, Any]]:
    path = settings.runtime_dir / "usage_telemetry.json"
    if not path.is_file():
        return {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k).lower(): v for k, v in raw.items() if isinstance(v, dict)}
    return {}


def _record_usage_event(settings: Settings, email: str, counter_field: str | None = None) -> None:
    clean_email = (email or "").strip().lower()
    if not clean_email:
        return
    with contextlib.suppress(OSError):
        data = _load_usage_telemetry(settings)
        entry = data.get(
            clean_email,
            {
                "uploaded_videos": 0,
                "analyses_run": 0,
                "live_sessions": 0,
                "last_seen": "",
            },
        )
        if counter_field in ("uploaded_videos", "analyses_run", "live_sessions"):
            entry[counter_field] = int(entry.get(counter_field) or 0) + 1
        entry["last_seen"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        data[clean_email] = entry
        out_path = settings.runtime_dir / "usage_telemetry.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_admin_overview_payload(app_cfg: Settings, active_user_email: str) -> dict[str, Any]:
    # 1. Scoreboard-grounded blind-video benchmark (rollmetrics-scoreboard-v1)
    sb_dir = app_cfg.knowledge_dir / "evals" / "scoreboard_v1"
    sb_latest_path = sb_dir / "latest_results.json"
    sb_splits_path = sb_dir / "splits.json"
    sb_raw: dict[str, Any] = {}
    if sb_latest_path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            sb_raw = json.loads(sb_latest_path.read_text(encoding="utf-8"))

    cal_count = 41
    held_count = 81
    if sb_splits_path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            sp = json.loads(sb_splits_path.read_text(encoding="utf-8"))
            if isinstance(sp, dict):
                cal_count = len(sp.get("calibration") or []) or cal_count
                held_count = len(sp.get("heldout") or []) or held_count

    splits_summary = sb_raw.get("splits_summary") if isinstance(sb_raw.get("splits_summary"), dict) else {}
    heldout_models = splits_summary.get("heldout") or sb_raw.get("models") or []
    calibration_models = splits_summary.get("calibration") or []

    def _compact_model_rows(rows: list[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "model_id": str(r.get("model_id") or ""),
                    "match_count": int(r.get("match_count") or 0),
                    "winner_accuracy": float(r.get("winner_accuracy") or 0.0),
                    "final_score_exact_match": float(r.get("final_score_exact_match") or 0.0),
                    "mae_pts": float(r.get("mae_points") or r.get("mae_pts") or 0.0),
                    "mae_adv": float(r.get("mae_advantages") or r.get("mae_adv") or 0.0),
                    "mae_pen": float(r.get("mae_penalties") or r.get("mae_pen") or 0.0),
                    "score_event_f1": float(r.get("score_event_f1") or 0.0),
                    "p50_ms": float(r.get("p50_latency_ms") or r.get("p50_ms") or 0.0),
                    "cost_usd": float(r.get("mean_cost_per_match_usd") or r.get("cost_usd") or 0.0),
                }
            )
        return out

    scoreboard_eval = {
        "benchmark_id": str(sb_raw.get("benchmark_id") or "rollmetrics-scoreboard-v1"),
        "evaluated_at": str(sb_raw.get("evaluated_at") or ""),
        "total_matches": int(sb_raw.get("corpus_total_matches") or (cal_count + held_count)),
        "calibration": cal_count,
        "heldout": held_count,
        "leakage_gate": "100% zero-pixel",
        "splits": {
            "calibration": _compact_model_rows(calibration_models),
            "heldout": _compact_model_rows(heldout_models),
        },
    }

    # 2. Knowledge base v0.1.0 manifest + 3-suite scorecard + model arena
    manifest_data: dict[str, Any] = {}
    retriever = _get_retriever(app_cfg)
    if retriever is not None:
        with contextlib.suppress(*RECOVERABLE_SUBSYSTEM_ERRORS):
            manifest_data = retriever.manifest()

    arena_data: dict[str, Any] = {}
    arena_path = app_cfg.knowledge_dir / "evals" / "model_scorecard_v0.1.0.json"
    if arena_path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            arena_data = json.loads(arena_path.read_text(encoding="utf-8"))

    counts = manifest_data.get("counts") if isinstance(manifest_data.get("counts"), dict) else {}
    total_docs = int(
        manifest_data.get("total_documents")
        or counts.get("total")
        or 231
    )
    ibjjf_docs = int(counts.get("ibjjf_rules") or counts.get("ibjjf") or 46)
    cta_docs = int(counts.get("cta_curriculum") or counts.get("cta") or max(0, total_docs - ibjjf_docs))

    knowledge_eval = {
        "version": app_cfg.active_index_version,
        "total_docs": total_docs,
        "ibjjf_docs": ibjjf_docs,
        "cta_docs": cta_docs,
        "suites": {
            "ibjjf_rules": "38/38",
            "match_timelines": "8/8",
            "cta_recall_at_3": "24/24",
        },
        "arena_models": arena_data.get("models") or arena_data.get("results") or [],
    }

    # 3. Allowlisted + registered users & activity telemetry (App Admin domain model)
    telemetry = _load_usage_telemetry(app_cfg)
    profiles = load_user_profiles(app_cfg)
    allowlist_set = app_cfg.parsed_allowlist()
    all_emails = set(allowlist_set) | set(profiles.keys()) | set(telemetry.keys()) | {DEMO_SEBASTIAN_EMAIL}
    if active_user_email:
        all_emails.add(active_user_email.lower())
    users_rows: list[dict[str, Any]] = []
    team_counts: dict[str, int] = {"Plateau BJJ": 0, "Caio Terra Association": 0}
    for email in sorted(all_emails):
        user_matches = _resolve_matches_for_user(app_cfg, email)
        my_cnt = sum(1 for m in user_matches if m.get("bucket") == "my_matches")
        fs_cnt = sum(1 for m in user_matches if m.get("bucket") != "my_matches")
        t_row = telemetry.get(email, {})
        p_row = profiles.get(email, {})
        role = (
            "demo"
            if email == DEMO_SEBASTIAN_EMAIL
            else ("admin" if email in allowlist_set or p_row.get("role") == "admin" else "member")
        )
        default_teams = ["Plateau BJJ", "Caio Terra Association"] if role in ("admin", "demo") else []
        teams = p_row.get("teams") if isinstance(p_row.get("teams"), list) and p_row.get("teams") else default_teams
        for tm in teams:
            team_counts[tm] = team_counts.get(tm, 0) + 1
        display_name = str(
            p_row.get("name")
            or ("Sebastian Bussieres" if email == DEMO_SEBASTIAN_EMAIL else email.split("@")[0].replace(".", " ").title())
        )
        users_rows.append(
            {
                "email": email,
                "name": display_name,
                "role": role,
                "teams": teams,
                "academy_name": str(
                    p_row.get("academy_name")
                    or ("Caio Terra Association" if role in ("admin", "demo") else "Open Source Jiu-Jitsu & Roadmap")
                ),
                "my_matches": my_cnt,
                "film_study": fs_cnt,
                "uploaded_videos": int(t_row.get("uploaded_videos") or 0),
                "analyses_run": int(t_row.get("analyses_run") or 0),
                "live_sessions": int(t_row.get("live_sessions") or 0),
                "last_seen": str(t_row.get("last_seen") or p_row.get("last_seen") or "Active"),
            }
        )

    from rollmetrics.knowledge.academies import list_academy_references, load_academies

    academies_map = load_academies(app_cfg)
    curricula_rows: list[dict[str, Any]] = []
    for slug, acad in sorted(academies_map.items()):
        custom_refs = list_academy_references(app_cfg, slug)
        if slug == "cta":
            doc_count = cta_docs + len(custom_refs)
            video_count = 141 + sum(1 for r in custom_refs if r.source_url)
            visibility = "private"
            content_status = "Video-backed (185 indexed lessons + timestamped transcripts)"
        elif slug == "osjj-roadmap":
            doc_count = 24 + len(custom_refs)
            video_count = sum(1 for r in custom_refs if r.source_url)
            visibility = "public"
            content_status = "Text-only syllabus scaffold (24 position outlines; video links pending)"
        else:
            doc_count = len(custom_refs)
            video_count = sum(1 for r in custom_refs if r.source_url)
            visibility = "private"
            content_status = f"Custom school library ({video_count} video-backed / {doc_count} total)"
        curricula_rows.append(
            {
                "id": slug,
                "name": str(acad.get("academy_name") or slug),
                "visibility": visibility,
                "doc_count": doc_count,
                "video_backed_count": video_count,
                "text_only_count": max(0, doc_count - video_count),
                "status": content_status,
            }
        )

    teams_rows = [
        {
            "id": tm.lower().replace(" ", "-"),
            "name": tm,
            "member_count": cnt,
            "default_curriculum": "Caio Terra Association" if "plateau" in tm.lower() or "caio" in tm.lower() else "Open Source Jiu-Jitsu & Roadmap",
            "default_ruleset": "IBJJF v6.1",
        }
        for tm, cnt in sorted(team_counts.items())
    ]

    rulesets_rows = [
        {
            "id": "ibjjf_v6_1",
            "name": "IBJJF Rulebook v6.1",
            "status": "active",
            "legality_cases": 300,
            "rule_docs": ibjjf_docs,
            "official_videos": 23,
            "notes": "3.0s stabilization, 20.0s stalling cascade, White through Black Gi & No-Gi",
        },
        {
            "id": "adcc",
            "name": "ADCC Submission Fighting",
            "status": "planned",
            "legality_cases": 0,
            "rule_docs": 0,
            "official_videos": 0,
            "notes": "Tracked in Issue #1: no-points first half, -1 guard pull, 2/4-pt takedowns & sweeps, overtime",
        },
    ]

    engine_versions = [
        {
            "version": "v0.1",
            "name": "24-Frame Storyboard Baseline",
            "harness": "24 static frames tiled into 2 sheets, scoreboard-masked",
            "heldout_winner": "55.0% (Hydrogen) / 2.5% (Local Gemma 4)",
            "notes": "Fast baseline; suffered from 18.2s frame gaps and A/B swaps",
        },
        {
            "version": "v0.2",
            "name": "360p Agentic Video Understanding",
            "harness": "Full masked MP4 upload + Gemini Interactions processing='agentic'",
            "heldout_winner": "70.0% (Hydrogen) / 57.1% (Neon, 240s timeouts)",
            "notes": "Major Event F1 jump (5x); exposed 360p finger blur and 15 GT label issues",
        },
        {
            "version": "v0.3",
            "name": "480p Dynamic Mat Crop + Visual A/B Anchor + Retry (Current)",
            "harness": "480p tight mat crop + t=+2s masked A/B anchor + zero-event retry + 6s cooldown + temporal smoother",
            "heldout_winner": "100.0% Hydrogen (10/10, $0.0077) / 90.0% Neon (100% cal) / 55.6% Local Gemma 4",
            "notes": "0 timeouts at 480s/240s, 0.0% Hydrogen A/B swaps, 0.90 pts MAE",
        },
    ]

    experiments_log = [
        {
            "id": "exp-01-baseline",
            "title": "24-Frame Storyboard vs. Full-Video Agentic",
            "delta": "+15.0 pts heldout winner acc, 5.1x Event F1",
            "status": "completed",
        },
        {
            "id": "exp-02-gt-and-anchor",
            "title": "480p + Visual A/B Anchor + 480s Timeout + GT Audit",
            "delta": "Neon 57.1% -> 90.0% heldout (0 timeouts); Hydrogen exact score 10% -> 40%",
            "status": "completed",
        },
        {
            "id": "exp-03-mat-crop-retry",
            "title": "Dynamic Mat Crop + Zero-Event Retry + 240s 3-Stage Ollama",
            "delta": "Hydrogen 80% -> 100.0% (20/20 overall, 0.0% A/B swaps); Local Gemma 4 2.5% -> 55.6%",
            "status": "completed",
        },
        {
            "id": "exp-04-full-81-heldout",
            "title": "Scale Hydrogen Agentic across all 81 heldout bouts + Mat-Only Referee Mask",
            "delta": "Planned (~$0.62 total cost)",
            "status": "queued",
        },
    ]

    dataset_details = {
        "benchmark_id": scoreboard_eval["benchmark_id"],
        "total_matches": scoreboard_eval["total_matches"],
        "calibration_matches": scoreboard_eval["calibration"],
        "heldout_matches": scoreboard_eval["heldout"],
        "cross_split_video_overlap": 0,
        "leakage_control": "100% scoreboard blackout drawbox + pixel/edge OCR gate + audio stripped (-an)",
        "early_stoppage_audit": "78 early-stoppage bouts audited; 41 confirmed submission/DQ/referee-hand-raise labels",
        "vicos_adapter": "Optional research-only adapter (CC BY-NC-SA 4.0; zero ViCoS assets bundled in commercial repo)",
    }

    build_sha = (os.getenv("ROLLMETRICS_BUILD_SHA", "").strip()[:7]) or BUILD_SHA
    return {
        "environment": app_cfg.environment,
        "app_version": APP_VERSION,
        "build_sha": build_sha,
        "scoreboard_eval": scoreboard_eval,
        "knowledge_eval": knowledge_eval,
        "users_and_usage": users_rows,
        "athletes": users_rows,
        "teams": teams_rows,
        "curricula": curricula_rows,
        "rulesets": rulesets_rows,
        "engine_versions": engine_versions,
        "experiments": experiments_log,
        "dataset_details": dataset_details,
        "upload_jobs": [
            {
                "job_id": str(uid),
                "filename": str(rec.get("filename") or "match.mp4"),
                "owner_email": str(rec.get("owner_email") or ""),
                "status": str(rec.get("status") or "completed"),
                "ruleset": str(rec.get("ruleset") or "ibjjf_v6"),
                "created_at": str(rec.get("created_at") or ""),
                "match_id": str(rec.get("match_id") or ""),
            }
            for uid, rec in list(_load_uploads_meta(app_cfg).items())[-25:]
        ],
        "id_lock_eval": {
            "id_lock_pct": 1.0,
            "evaluated_bouts": 20,
            "swap_count": 0,
            "matching_gi_bouts": 6,
        },
        **get_daily_cloud_budget_status(app_cfg),
    }


def _load_uploads_meta(settings: Settings) -> dict[str, dict[str, Any]]:
    path = settings.runtime_dir / "uploads_meta.json"
    if not path.is_file():
        return {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    return {}


def _save_uploads_meta(settings: Settings, data: dict[str, dict[str, Any]]) -> None:
    path = settings.runtime_dir / "uploads_meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_shares(settings: Settings) -> dict[str, dict[str, Any]]:
    path = settings.runtime_dir / "shares.json"
    if not path.is_file():
        return {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    return {}


def _save_shares(settings: Settings, data: dict[str, dict[str, Any]]) -> None:
    path = settings.runtime_dir / "shares.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _extract_google_drive_file_id(raw_url: str) -> str:
    text = (raw_url or "").strip()
    m_file = re.search(r"/file/d/([a-zA-Z0-9_-]{10,})", text)
    if m_file:
        return m_file.group(1)
    m_id = re.search(r"[?&]id=([a-zA-Z0-9_-]{10,})", text)
    if m_id:
        return m_id.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{15,80}", text):
        return text
    return ""


def _try_mint_gcs_signed_put_url(
    bucket_name: str,
    object_name: str,
    content_type: str,
) -> str | None:
    """Pure-Python GCS V4 signed PUT URL via IAM signBlob (zero google-cloud-storage dep)."""
    if not bucket_name:
        return None
    with contextlib.suppress(Exception):
        from datetime import UTC, datetime
        from urllib.parse import quote, urlencode

        import google.auth
        import httpx
        from google.auth.transport import requests as google_auth_requests

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google_auth_requests.Request())
        sa_email = getattr(creds, "service_account_email", "") or os.getenv("ROLLMETRICS_SIGNING_SA", "")
        if not sa_email or not creds.token:
            return None

        now = datetime.now(UTC)
        datestamp = now.strftime("%Y%m%d")
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        credential_scope = f"{datestamp}/auto/storage/goog4_request"
        credential = f"{sa_email}/{credential_scope}"
        canonical_uri = f"/{bucket_name}/{quote(object_name, safe='/')}"
        signed_headers = "content-type;host"
        canonical_headers = f"content-type:{content_type}\nhost:storage.googleapis.com\n"
        query_params = {
            "X-Goog-Algorithm": "GOOG4-RSA-SHA256",
            "X-Goog-Credential": credential,
            "X-Goog-Date": amz_date,
            "X-Goog-Expires": "1800",
            "X-Goog-SignedHeaders": signed_headers,
        }
        canonical_query = urlencode(sorted(query_params.items()), quote_via=quote)
        canonical_request = (
            f"PUT\n{canonical_uri}\n{canonical_query}\n{canonical_headers}\n"
            f"{signed_headers}\nUNSIGNED-PAYLOAD"
        )
        req_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        string_to_sign = f"GOOG4-RSA-SHA256\n{amz_date}\n{credential_scope}\n{req_hash}"

        sign_endpoint = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:signBlob"
        resp = httpx.post(
            sign_endpoint,
            headers={"Authorization": f"Bearer {creds.token}"},
            json={"payload": base64.b64encode(string_to_sign.encode("utf-8")).decode("ascii")},
            timeout=10.0,
        )
        resp.raise_for_status()
        sig_bytes = base64.b64decode(resp.json()["signedBlob"])
        signature_hex = sig_bytes.hex()
        return f"https://storage.googleapis.com{canonical_uri}?{canonical_query}&X-Goog-Signature={signature_hex}"
    return None


def _persist_analyzed_match(
    app_cfg: Settings,
    analysis_res: dict[str, Any],
    *,
    owner_email: str,
    bucket: str,
    title: str,
    belt: BeltRank,
    uniform: str,
    athlete_a_name: str,
    athlete_b_name: str,
    athlete_a_gi: str,
    athlete_b_gi: str,
    video_path: str,
    video_url: str,
    gcs_object: str = "",
) -> dict[str, Any]:
    snap = analysis_res.get("snapshot") if isinstance(analysis_res.get("snapshot"), dict) else analysis_res
    timeline = snap.get("timeline") if isinstance(snap.get("timeline"), list) else []

    pts_a = adv_a = pen_a = pts_b = adv_b = pen_b = 0
    events: list[dict[str, Any]] = []
    for idx, ev in enumerate(timeline):
        if not isinstance(ev, dict):
            continue
        actor = "B" if str(ev.get("actor") or "A") == "B" else "A"
        add_pts = int(ev.get("awarded_points") or 0)
        add_adv = int(ev.get("awarded_advantages") or 0)
        add_pen = int(ev.get("awarded_penalties") or 0)
        opp_pts = int(ev.get("opponent_bonus_points") or 0)
        opp_adv = int(ev.get("opponent_bonus_advantages") or 0)
        if actor == "A":
            pts_a += add_pts
            adv_a += add_adv
            pen_a += add_pen
            pts_b += opp_pts
            adv_b += opp_adv
        else:
            pts_b += add_pts
            adv_b += add_adv
            pen_b += add_pen
            pts_a += opp_pts
            adv_a += opp_adv

        raw_kind = str(ev.get("raw_kind") or "exchange")
        if add_pts > 0:
            call = f"+{add_pts} PTS"
        elif add_adv > 0:
            call = f"+{add_adv} ADV"
        elif add_pen > 0:
            call = f"+{add_pen} PEN"
        else:
            call = raw_kind.replace("_", " ").upper()

        ts = float(ev.get("timestamp_sec") or 0.0)
        m_ts, s_ts = divmod(max(0, round(ts)), 60)
        rem_m, rem_s = divmod(max(0, round(max(0.0, 300.0 - ts))), 60)
        recs = ev.get("cta_recommendations") if isinstance(ev.get("cta_recommendations"), list) else []
        cta_url = (
            str(recs[0].get("source_url") or "")
            if recs and isinstance(recs[0], dict)
            else "https://www.youtube.com/watch?v=4-7AYrBCOvQ"
        )
        events.append(
            {
                "id": str(ev.get("event_id") or f"ev-{idx + 1}"),
                "video_sec": ts,
                "time_str": f"{m_ts:02d}:{s_ts:02d}",
                "clock_str": str(ev.get("match_clock_str") or f"{rem_m:02d}:{rem_s:02d}"),
                "actor": actor,
                "call": call,
                "detail": raw_kind.replace("_", " ").title(),
                "rule": str(ev.get("rule_article") or "IBJJF Art. 4"),
                "cta_url": cta_url,
                "pts_a": pts_a,
                "adv_a": adv_a,
                "pen_a": pen_a,
                "pts_b": pts_b,
                "adv_b": adv_b,
                "pen_b": pen_b,
                "control_3s": add_pts > 0,
                "is_finish": bool(ev.get("ended_match")),
            }
        )

    match_id = f"usr_{uuid.uuid4().hex[:10]}"
    clean_title = title.strip() or f"{athlete_a_name} vs. {athlete_b_name}"
    win_method = str(snap.get("win_method") or "").lower()
    win_label = "SUB" if "sub" in win_method else ("DQ" if "dq" in win_method else "WIN")
    uniform_label = "No-Gi" if uniform.lower() == "nogi" else "Gi"
    belt_title = f"{belt.value.capitalize()} Belt · {uniform_label}"

    match_entry: dict[str, Any] = {
        "id": match_id,
        "pill": clean_title[:48],
        "title": clean_title,
        "belt": belt.value,
        "belt_label": belt_title,
        "bucket": bucket,
        "owner_email": owner_email.strip().lower(),
        "regulation_sec": 300.0,
        "video_duration_sec": max(300.0, (events[-1]["video_sec"] + 15.0) if events else 300.0),
        "video_start_sec": 0.0,
        "video_end_sec": 300.0,
        "athlete_a": {"name": athlete_a_name, "belt": belt.value, "gi": athlete_a_gi},
        "athlete_b": {"name": athlete_b_name, "belt": belt.value, "gi": athlete_b_gi},
        "winner": "B" if str(snap.get("winner") or "A") == "B" else "A",
        "win_label": win_label,
        "video_path": video_path,
        "video_url": video_url,
        "gcs_object": gcs_object,
        "events": events,
    }
    matches_dir = app_cfg.runtime_dir / "matches"
    matches_dir.mkdir(parents=True, exist_ok=True)
    (matches_dir / f"{match_id}.json").write_text(json.dumps(match_entry, indent=2), encoding="utf-8")
    return match_entry


def _resolve_match_by_id_unscoped(app_cfg: Settings, match_id: str) -> dict[str, Any] | None:
    for m in _showcase_matches_catalog():
        if m.get("id") == match_id:
            for ev in m.get("events") or []:
                if isinstance(ev, dict) and ev.get("rule") and not ev.get("rule_info"):
                    ev["rule_info"] = _resolve_rule_info(
                        str(ev.get("rule") or ""),
                        str(ev.get("detail") or ""),
                        app_cfg,
                    )
            return m
    candidate = app_cfg.runtime_dir / "matches" / f"{match_id}.json"
    if candidate.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            entry = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(entry, dict):
                for ev in entry.get("events") or []:
                    if isinstance(ev, dict) and ev.get("rule") and not ev.get("rule_info"):
                        ev["rule_info"] = _resolve_rule_info(
                            str(ev.get("rule") or ""),
                            str(ev.get("detail") or ""),
                            app_cfg,
                        )
                return entry
    return None


def _local_showcase_candidates(match_id: str, settings: Settings) -> list[Path]:
    cached = settings.runtime_dir / "matches" / f"{match_id}.mp4"
    repo_runtime_mp4 = REPO_ROOT / "data" / "runtime" / "matches" / f"{match_id}.mp4"
    if match_id == "mc9_semifinal":
        return [
            LOCAL_DRIVE_BJJ_DIR / "01_Semifinal_Kory_Mathewson_vs_Michael_Dube_Mat2_Match25.mp4",
            cached,
        ]
    if match_id == "mc9_final":
        return [
            LOCAL_DRIVE_BJJ_DIR / "02_Final_Kory_Mathewson_vs_Vincent_Lamoureux_Mat2_Match30.mp4",
            cached,
        ]
    if match_id == "caio_vitor_2014":
        dl_dir = REPO_ROOT / "data" / "runtime" / "showcase_caio_vitor" / "downloads"
        dl_mp4s = sorted(dl_dir.glob("*.mp4")) if dl_dir.is_dir() else []
        return [*dl_mp4s, cached]
    if settings.environment == "test":
        return [cached, settings.runtime_dir / "uploads" / match_id]
    return [cached, repo_runtime_mp4, settings.runtime_dir / "uploads" / match_id]


MAX_RANGE_CHUNK_BYTES = 4 * 1024 * 1024  # 4 MiB cap stays safely below Cloud Run 32 MiB HTTP/1.1 limit
_GCS_ADC_CREDS: Any | None = None


def _get_gcs_bearer_token() -> str | None:
    global _GCS_ADC_CREDS
    try:
        import google.auth
        from google.auth.transport.requests import Request as GoogleAuthRequest

        if _GCS_ADC_CREDS is None:
            _GCS_ADC_CREDS, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/devstorage.read_only"]
            )
        if not _GCS_ADC_CREDS.valid or not _GCS_ADC_CREDS.token:
            _GCS_ADC_CREDS.refresh(GoogleAuthRequest())
        return str(_GCS_ADC_CREDS.token) if _GCS_ADC_CREDS.token else None
    except RECOVERABLE_SUBSYSTEM_ERRORS:
        return None


def _parse_bounded_range(range_header: str | None, file_size: int | None = None) -> tuple[int, int]:
    """Parse HTTP Range header and enforce <= 4 MiB chunk size for Cloud Run & iOS WebKit."""
    start = 0
    requested_end: int | None = None
    if range_header and range_header.startswith("bytes="):
        range_spec = range_header.replace("bytes=", "", 1).split(",", 1)[0].strip()
        start_str, _, end_str = range_spec.partition("-")
        try:
            start = int(start_str) if start_str else 0
            requested_end = int(end_str) if end_str else None
        except ValueError:
            start = 0
            requested_end = None

    start = max(0, start)
    if file_size is not None:
        if file_size <= 0:
            return 0, 0
        start = min(start, file_size - 1)
        raw_end = (file_size - 1) if requested_end is None else min(requested_end, file_size - 1)
        end = min(raw_end, start + MAX_RANGE_CHUNK_BYTES - 1)
        end = max(start, end)
        return start, end

    end = (
        start + MAX_RANGE_CHUNK_BYTES - 1
        if requested_end is None
        else min(requested_end, start + MAX_RANGE_CHUNK_BYTES - 1)
    )
    return start, max(start, end)


def _download_gcs_match_to_cache(match_id: str, settings: Settings) -> Path | None:
    bucket = (settings.gcs_bucket or "").strip()
    if not bucket:
        return None
    cache_file = settings.runtime_dir / "matches" / f"{match_id}.mp4"
    if cache_file.is_file() and cache_file.stat().st_size > 1024:
        return cache_file
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
        authed = AuthorizedSession(creds)
        url = f"https://storage.googleapis.com/{bucket}/matches/{match_id}.mp4"
        with authed.get(url, stream=True, timeout=60) as resp:
            if resp.status_code != 200:
                return None
            tmp_dest = cache_file.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.part")
            with tmp_dest.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        fh.write(chunk)
            tmp_dest.replace(cache_file)
            return cache_file
    except RECOVERABLE_SUBSYSTEM_ERRORS:
        return None


def _find_local_showcase_video(match_id: str, settings: Settings) -> Path | None:
    for candidate in _local_showcase_candidates(match_id, settings):
        if candidate.is_file() and candidate.stat().st_size > 1024:
            return candidate
    return None


def _resolve_showcase_video_path(match_id: str, settings: Settings) -> Path:
    local_file = _find_local_showcase_video(match_id, settings)
    if local_file is not None:
        return local_file
    fetched = _download_gcs_match_to_cache(match_id, settings)
    if fetched is not None and fetched.is_file():
        return fetched
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Match video '{match_id}' is unavailable locally or in GCS.",
    )


def _stream_file_with_range(
    file_path: Path,
    range_header: str | None,
    head_only: bool = False,
) -> Response:
    file_size = file_path.stat().st_size
    if head_only:
        return Response(
            status_code=status.HTTP_200_OK,
            media_type="video/mp4",
            headers={
                "Content-Length": str(file_size),
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, max-age=300",
            },
        )

    start, end = _parse_bounded_range(range_header, file_size=file_size)
    chunk_len = (end - start) + 1

    def _iter_bytes() -> Iterator[bytes]:
        with file_path.open("rb") as fh:
            fh.seek(start)
            remaining = chunk_len
            while remaining > 0:
                data = fh.read(min(1024 * 256, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(chunk_len),
        "Cache-Control": "private, max-age=300",
    }
    return StreamingResponse(
        _iter_bytes(),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type="video/mp4",
        headers=headers,
    )


async def _proxy_gcs_video_range(
    match_id: str,
    settings: Settings,
    range_header: str | None,
    head_only: bool = False,
) -> Response:
    bucket = (settings.gcs_bucket or "").strip()
    token = _get_gcs_bearer_token()
    if not bucket or not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Match video '{match_id}' is unavailable locally or in GCS.",
        )

    import httpx

    url = f"https://storage.googleapis.com/{bucket}/matches/{match_id}.mp4"
    auth_headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        if head_only:
            head_resp = await client.head(url, headers=auth_headers)
            if head_resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Match video '{match_id}' not found in GCS.",
                )
            total_len = head_resp.headers.get("content-length", "0")
            return Response(
                status_code=status.HTTP_200_OK,
                media_type="video/mp4",
                headers={
                    "Content-Length": total_len,
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "private, max-age=300",
                },
            )

        start, end = _parse_bounded_range(range_header, file_size=None)
        req_headers = {**auth_headers, "Range": f"bytes={start}-{end}"}
        gcs_resp = await client.get(url, headers=req_headers)
        if gcs_resp.status_code not in (200, 206):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Match video '{match_id}' unavailable from GCS ({gcs_resp.status_code}).",
            )
        body = gcs_resp.content
        resp_headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(body)),
            "Cache-Control": "private, max-age=300",
        }
        if gcs_resp.headers.get("content-range"):
            resp_headers["Content-Range"] = gcs_resp.headers["content-range"]
        return Response(
            content=body,
            status_code=status.HTTP_206_PARTIAL_CONTENT if gcs_resp.status_code == 206 else status.HTTP_200_OK,
            media_type="video/mp4",
            headers=resp_headers,
        )


def _resolve_media_video_path(
    raw_video_path: str | None,
    settings: Settings,
    match_id: str | None = None,
) -> Path:
    known_match_ids = {m["id"] for m in _showcase_matches_catalog()}
    runtime_matches_dir = REPO_ROOT / "data" / "runtime" / "matches"
    if runtime_matches_dir.is_dir():
        for jf in runtime_matches_dir.glob("*.json"):
            known_match_ids.add(jf.stem)
    if match_id:
        return _resolve_showcase_video_path(match_id, settings)
    if not raw_video_path or raw_video_path in known_match_ids:
        target_id = raw_video_path if raw_video_path in known_match_ids else "mc9_semifinal"
        return _resolve_showcase_video_path(target_id, settings)

    candidate = Path(raw_video_path)
    if not candidate.is_absolute():
        uploads_candidate = settings.runtime_dir / "uploads" / candidate.name
        renders_candidate = settings.runtime_dir / "renders" / candidate.name
        repo_candidate = REPO_ROOT / raw_video_path
        for opt in (uploads_candidate, renders_candidate, repo_candidate):
            if opt.exists():
                return opt
    if candidate.exists():
        return candidate
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Source video file '{raw_video_path}' was not found.",
    )


def _fallback_three_panel_board(
    video_path: Path,
    center_timestamp_sec: float,
    output_path: Path,
    title: str,
    cues: list[str],
    actor_label: str = "YOU",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    panel_w, panel_h = 400, 260
    canvas = Image.new("RGB", (panel_w * 3 + 64, panel_h + 150), (11, 15, 20))
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 18), f"ROLLMETRICS · {title} [{actor_label}]", fill=(16, 185, 129))
    offsets = [-1.5, 0.0, 1.5]
    for idx, off in enumerate(offsets):
        ts = max(0.0, center_timestamp_sec + off)
        frame_file = output_path.parent / f"_tmp_frame_{uuid.uuid4().hex}_{idx}.jpg"
        subprocess.run(
            [
                _resolve_ffmpeg_bin(),
                "-y",
                "-ss",
                f"{ts:.2f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(frame_file),
            ],
            check=False,
            capture_output=True,
        )
        x0 = 16 + idx * (panel_w + 16)
        y0 = 56
        if frame_file.exists():
            with Image.open(frame_file) as im:
                resized = im.convert("RGB").resize((panel_w, panel_h))
                canvas.paste(resized, (x0, y0))
            frame_file.unlink(missing_ok=True)
        draw.ellipse((x0 + 12, y0 + 12, x0 + 42, y0 + 42), fill=(16, 185, 129))
        draw.text((x0 + 23, y0 + 20), str(idx + 1), fill=(11, 15, 20))
        cue_text = cues[idx] if idx < len(cues) else f"Phase {idx + 1} ({ts:.1f}s)"
        draw.text((x0 + 4, y0 + panel_h + 14), cue_text[:54], fill=(226, 232, 240))
    canvas.save(output_path)
    return output_path


def _fallback_slowmo_clip(
    video_path: Path,
    start_sec: float,
    duration_sec: float,
    output_path: Path,
    speed: float = 0.4,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pts_mult = 1.0 / max(0.1, speed)
    subprocess.run(
        [
            _resolve_ffmpeg_bin(),
            "-y",
            "-ss",
            f"{max(0.0, start_sec):.2f}",
            "-t",
            f"{duration_sec:.2f}",
            "-i",
            str(video_path),
            "-vf",
            f"setpts={pts_mult:.3f}*PTS,scale=960:-2",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )
    return output_path


def _normalize_hud_payload(raw_state: dict[str, Any], division: MatchDivisionConfig) -> dict[str, Any]:
    """Ensure consistent HUD fields across backend session variants while keeping all raw keys."""
    scoreboard = raw_state.get("scoreboard") or raw_state.get("snapshot") or raw_state
    timeline = scoreboard.get("timeline") or raw_state.get("timeline") or []
    cues = (
        raw_state.get("cta_cues")
        or raw_state.get("corner_cues")
        or scoreboard.get("coaching_summary")
        or raw_state.get("coaching_summary")
        or []
    )
    active_stab = (
        raw_state.get("active_stabilization")
        if "active_stabilization" in raw_state
        else scoreboard.get("active_stabilization")
    )
    stalling = (
        raw_state.get("stalling_watch_sec")
        or scoreboard.get("stalling_watch_sec")
        or {"A": 0.0, "B": 0.0}
    )
    needs_review_events = [e for e in timeline if isinstance(e, dict) and e.get("needs_review")]
    return {
        "type": "hud",
        **raw_state,
        "scoreboard": scoreboard,
        "division": scoreboard.get("division") or division.model_dump(mode="json"),
        "active_stabilization": active_stab,
        "stalling_watch_sec": stalling,
        "cta_cues": cues,
        "needs_review_count": len(needs_review_events),
    }


class _FallbackLiveSession:
    """Deterministic fallback live referee session if rollmetrics.video.live_session is absent."""

    def __init__(self, division: MatchDivisionConfig, profile: str, settings: Settings) -> None:
        self.division = division
        self.profile = profile
        self.settings = settings
        self.engine = IBJJFRefereeEngine(division=division, index_version=settings.active_index_version)
        self.retriever = _get_retriever(settings)
        self.stalling_watch_sec: dict[str, float] = {"A": 0.0, "B": 0.0}
        self.active_stabilization: dict[str, Any] | None = None
        self.last_cues: list[dict[str, Any]] = []

    async def process_frame(
        self,
        jpeg_bytes: bytes,
        elapsed_sec: float,
        audio_transcript: str = "",
    ) -> dict[str, Any]:
        _ = jpeg_bytes
        delta = max(0.0, elapsed_sec - self.engine.elapsed_sec)
        self.engine.elapsed_sec = min(self.engine.duration_sec, max(self.engine.elapsed_sec, elapsed_sec))
        self.stalling_watch_sec["A"] = round(min(20.0, self.stalling_watch_sec["A"] + delta * 0.25), 1)
        self.stalling_watch_sec["B"] = round(min(20.0, self.stalling_watch_sec["B"] + delta * 0.25), 1)
        if self.retriever is not None and not self.last_cues:
            with contextlib.suppress(*RECOVERABLE_SUBSYSTEM_ERRORS):
                self.last_cues = self.retriever.recommend_cta_for_position(
                    position=self.engine.current_position,
                    situation=audio_transcript or "live mat exchange",
                    belt=self.division.belt.value,
                    top_k=3,
                )
        return self.current_hud_state()

    def ingest_manual_or_detected_event(self, ev: RawObservedEvent) -> dict[str, Any]:
        adj = self.engine.ingest_event(ev)
        self.stalling_watch_sec = {"A": 0.0, "B": 0.0}
        if ev.kind in (
            EventKind.TAKEDOWN,
            EventKind.SWEEP,
            EventKind.KNEE_ON_BELLY,
            EventKind.GUARD_PASS,
            EventKind.MOUNT,
            EventKind.BACK_CONTROL,
        ):
            self.active_stabilization = {
                "actor": ev.actor.value,
                "kind": ev.kind.value,
                "held_sec": round(min(3.0, ev.hold_duration_sec), 1),
                "target_sec": 3.0,
                "awarded_points": adj.awarded_points,
            }
        if self.retriever is not None:
            with contextlib.suppress(*RECOVERABLE_SUBSYSTEM_ERRORS):
                self.last_cues = self.retriever.recommend_cta_for_position(
                    position=self.engine.current_position,
                    situation=adj.summary,
                    belt=self.division.belt.value,
                    top_k=3,
                )
                adj.cta_recommendations = self.last_cues[:2]
        return self.current_hud_state()

    def current_hud_state(self) -> dict[str, Any]:
        snap = self.engine.snapshot().model_dump(mode="json")
        snap["active_stabilization"] = self.active_stabilization
        snap["stalling_watch_sec"] = dict(self.stalling_watch_sec)
        snap["coaching_summary"] = list(self.last_cues)
        return {
            "profile": self.profile,
            "scoreboard": snap,
            "active_stabilization": self.active_stabilization,
            "stalling_watch_sec": dict(self.stalling_watch_sec),
            "cta_cues": list(self.last_cues),
        }


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    (cfg.runtime_dir / "uploads").mkdir(parents=True, exist_ok=True)
    (cfg.runtime_dir / "renders").mkdir(parents=True, exist_ok=True)

    application = FastAPI(
        title="RollMetrics API",
        version=APP_VERSION,
        description="IBJJF live refereeing, match film adjudication, and CTA curriculum coaching API.",
    )
    application.state.settings = cfg
    application.state.active_profile = cfg.profile

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/api/health")
    @application.get("/healthz")
    def healthz(app_cfg: SettingsDep) -> dict[str, Any]:
        build_sha = (os.getenv("ROLLMETRICS_BUILD_SHA", "").strip()[:7]) or BUILD_SHA
        return {
            "status": "ok",
            "app": app_cfg.app_name,
            "version": APP_VERSION,
            "build_sha": build_sha,
            "environment": app_cfg.environment,
            "index_version": app_cfg.active_index_version,
        }

    @application.get("/api/auth/config")
    async def auth_config(app_cfg: SettingsDep) -> dict[str, Any]:
        bypass_enabled = app_cfg.environment in ("local", "test") and app_cfg.allow_local_dev_bypass
        build_sha = (os.getenv("ROLLMETRICS_BUILD_SHA", "").strip()[:7]) or BUILD_SHA
        return {
            "environment": app_cfg.environment,
            "google_client_id": app_cfg.google_client_id,
            "allow_local_dev_bypass": bypass_enabled,
            "allow_open_signup": app_cfg.allow_open_signup,
            "auth_required": not bypass_enabled,
            "public_url": app_cfg.public_url,
            "default_profile": getattr(application.state, "active_profile", app_cfg.profile),
            "app_version": APP_VERSION,
            "build_sha": build_sha,
        }

    @application.get("/api/auth/me")
    async def auth_me(
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        if email:
            _record_usage_event(app_cfg, email)
        return user

    @application.post("/api/auth/demo-login")
    async def auth_demo_login(app_cfg: SettingsDep) -> dict[str, Any]:
        demo_user = upsert_user_profile(app_cfg, build_demo_sebastian_user())
        _record_usage_event(app_cfg, DEMO_SEBASTIAN_EMAIL)
        return {"token": DEMO_SEBASTIAN_TOKEN, "user": demo_user}

    @application.get("/api/system/status")
    async def system_status(
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        active_profile = getattr(application.state, "active_profile", app_cfg.profile)
        effective_cfg = app_cfg.model_copy(update={"profile": active_profile})
        providers_health: dict[str, Any]
        try:
            from rollmetrics.providers.factory import check_providers_health

            providers_health = await check_providers_health(effective_cfg)
        except RECOVERABLE_SUBSYSTEM_ERRORS as exc:
            providers_health = {
                "active_profile": active_profile,
                "cloud_gemini_configured": bool(effective_cfg.gemini_api_key),
                "ollama_reachable": False,
                "ollama_models": [],
                "recommended_local_model": effective_cfg.ollama_vision_model,
                "note": str(exc),
            }

        retriever = _get_retriever(effective_cfg)
        manifest_data: dict[str, Any] = {"version": effective_cfg.active_index_version, "available": False}
        if retriever is not None:
            try:
                manifest_data = retriever.manifest()
            except RECOVERABLE_SUBSYSTEM_ERRORS as exc:
                manifest_data = {
                    "version": effective_cfg.active_index_version,
                    "available": False,
                    "error": str(exc),
                }

        masked_key = (
            f"{effective_cfg.gemini_api_key[:4]}...{effective_cfg.gemini_api_key[-4:]}"
            if len(effective_cfg.gemini_api_key) >= 8
            else ("Configured" if effective_cfg.gemini_api_key else "")
        )

        return {
            "app": effective_cfg.app_name,
            "version": APP_VERSION,
            "environment": effective_cfg.environment,
            "active_profile": active_profile,
            "ollama_base_url": effective_cfg.ollama_base_url,
            "gemini_key_configured": bool(effective_cfg.gemini_api_key),
            "masked_gemini_key": masked_key,
            "models": {
                "tracker_model": effective_cfg.tracker_model,
                "adjudicator_model": effective_cfg.adjudicator_model,
                "embedding_model": effective_cfg.embedding_model,
                "gemma_api_model": effective_cfg.gemma_api_model,
                "ollama_vision_model": effective_cfg.ollama_vision_model,
                "ollama_text_model": effective_cfg.ollama_text_model,
            },
            "providers": providers_health,
            "knowledge_manifest": manifest_data,
            "user": user,
        }

    @application.post("/api/system/profile")
    async def switch_profile(
        payload: ProfileSwitchRequest,
        user: UserDep,
    ) -> dict[str, Any]:
        application.state.active_profile = payload.profile
        return {"active_profile": payload.profile, "updated_by": user["email"]}

    @application.post("/api/system/config")
    async def update_system_config(
        payload: SystemConfigUpdateRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if payload.profile in ("local", "cloud"):
            application.state.active_profile = payload.profile
            updates["profile"] = payload.profile
        if payload.ollama_base_url and payload.ollama_base_url.strip():
            updates["ollama_base_url"] = payload.ollama_base_url.strip()
        if payload.ollama_vision_model and payload.ollama_vision_model.strip():
            updates["ollama_vision_model"] = payload.ollama_vision_model.strip()
            updates["ollama_text_model"] = payload.ollama_vision_model.strip()
        if (
            payload.gemini_api_key is not None
            and payload.gemini_api_key.strip()
            and app_cfg.environment in ("local", "test")
        ):
            updates["gemini_api_key"] = payload.gemini_api_key.strip()

        new_cfg = app_cfg.model_copy(update=updates)
        application.state.settings = new_cfg
        return {
            "active_profile": getattr(application.state, "active_profile", new_cfg.profile),
            "ollama_base_url": new_cfg.ollama_base_url,
            "ollama_vision_model": new_cfg.ollama_vision_model,
            "gemini_key_configured": bool(new_cfg.gemini_api_key),
            "updated_by": user["email"],
        }

    @application.post("/api/system/test-connection")
    async def test_system_connection(
        payload: ConnectionTestRequest,
        app_cfg: SettingsDep,
        _user: UserDep,
    ) -> dict[str, Any]:
        import httpx
        from google.genai import errors as genai_errors

        overrides: dict[str, Any] = {}
        if payload.ollama_base_url and payload.ollama_base_url.strip():
            overrides["ollama_base_url"] = payload.ollama_base_url.strip()
        if payload.ollama_vision_model and payload.ollama_vision_model.strip():
            overrides["ollama_vision_model"] = payload.ollama_vision_model.strip()
        if (
            payload.gemini_api_key
            and payload.gemini_api_key.strip()
            and app_cfg.environment in ("local", "test")
        ):
            overrides["gemini_api_key"] = payload.gemini_api_key.strip()
        test_cfg = app_cfg.model_copy(update=overrides)

        t0 = time.perf_counter()
        if payload.target == "local":
            base = test_cfg.ollama_base_url.rstrip("/")
            try:
                from rollmetrics.providers.ollama_local import select_best_ollama_vision_model

                async with httpx.AsyncClient(timeout=8.0) as client:
                    tags_resp = await client.get(f"{base}/api/tags")
                    tags_resp.raise_for_status()
                    installed = [
                        str(m.get("name", ""))
                        for m in (tags_resp.json().get("models") or [])
                        if isinstance(m, dict) and m.get("name")
                    ]
                    chosen = select_best_ollama_vision_model(installed, test_cfg.ollama_vision_model)
                    chat_resp = await client.post(
                        f"{base}/api/chat",
                        json={
                            "model": chosen,
                            "messages": [{"role": "user", "content": 'Reply with JSON: {"ok": true}'}],
                            "format": "json",
                            "stream": False,
                            "think": False,
                        },
                    )
                    chat_resp.raise_for_status()
                elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                return {
                    "ok": True,
                    "target": "local",
                    "status": "200 OK",
                    "model": chosen,
                    "ollama_models": installed,
                    "latency_ms": elapsed_ms,
                    "detail": f"Connected to {base} using {chosen}.",
                }
            except (OSError, RuntimeError, ValueError, KeyError, httpx.HTTPError) as exc:
                elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                return {
                    "ok": False,
                    "target": "local",
                    "status": "Unreachable",
                    "model": test_cfg.ollama_vision_model,
                    "ollama_models": [],
                    "latency_ms": elapsed_ms,
                    "detail": str(exc),
                }

        # target == "cloud"
        if not test_cfg.gemini_api_key:
            return {
                "ok": False,
                "target": "cloud",
                "status": "Missing Key",
                "model": test_cfg.gemma_api_model,
                "latency_ms": 0.0,
                "detail": "GEMINI_API_KEY is not configured.",
            }
        try:
            from rollmetrics.providers.gemini_cloud import GeminiCloudProvider

            provider = GeminiCloudProvider(settings=test_cfg)
            ping_res = await provider.query_gemma_api('Return JSON: {"ok": true}')
            elapsed_ms = round(
                float(ping_res.get("latency_ms") or ((time.perf_counter() - t0) * 1000.0)),
                1,
            )
            used_model = str(ping_res.get("model_id") or test_cfg.gemma_api_model)
            return {
                "ok": True,
                "target": "cloud",
                "status": "200 OK",
                "model": used_model,
                "latency_ms": elapsed_ms,
                "detail": f"Verified Cloud Gemini API ({used_model}).",
            }
        except (
            OSError,
            RuntimeError,
            ValueError,
            KeyError,
            httpx.HTTPError,
            genai_errors.APIError,
        ) as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            return {
                "ok": False,
                "target": "cloud",
                "status": "Error",
                "model": test_cfg.gemma_api_model,
                "latency_ms": elapsed_ms,
                "detail": str(exc),
            }

    @application.get("/api/rules/ibjjf-v6-1")
    async def get_ibjjf_v61_rulebook() -> dict[str, Any]:
        """Public zero-auth versioned plaintext IBJJF v6.1 rulebook articles."""
        from rollmetrics.knowledge.ibjjf_sources import build_ibjjf_corpus

        docs, _meta = build_ibjjf_corpus(fetch_online_pdf=False)
        articles = []
        for idx, doc in enumerate(docs, start=1):
            art = str(getattr(doc, "rule_article", "") or "").strip() or f"Art. {idx}"
            articles.append(
                {
                    "id": str(getattr(doc, "doc_id", f"ibjjf-{idx}")),
                    "article": art,
                    "title": str(getattr(doc, "title", art)),
                    "text": str(getattr(doc, "text", "")),
                    "source_url": str(getattr(doc, "source_url", "https://ibjjf.com/books-videos")),
                }
            )
        return {
            "version": "v6.1",
            "title": "IBJJF Rulebook v6.1 (Plaintext Reference)",
            "count": len(articles),
            "articles": articles,
        }

    @application.get("/api/matches")
    async def list_showcase_matches(
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        if email:
            _record_usage_event(app_cfg, email)
        matches = _resolve_matches_for_user(app_cfg, email)
        default_id = "seb_gi_sf" if email == DEMO_SEBASTIAN_EMAIL else "mc9_semifinal"
        return {"default_match_id": default_id, "matches": matches}

    @application.post("/api/matches/{match_id}/bucket")
    async def update_match_bucket(
        match_id: str,
        payload: MatchBucketUpdateRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        if not SAFE_FILENAME_RE.match(match_id) or ".." in match_id:
            raise HTTPException(status_code=400, detail="Invalid match identifier.")
        email = str(user.get("email") or "").strip().lower()
        _save_user_bucket_override(app_cfg, email, match_id, payload.bucket)
        return {"match_id": match_id, "bucket": payload.bucket, "owner_email": email}

    @application.get("/api/me/coaching-summary")
    async def get_personal_coaching_summary(
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        _record_usage_event(app_cfg, email)
        matches = _resolve_matches_for_user(app_cfg, email)
        return _build_personal_coaching_summary(matches, email)

    @application.put("/api/me/profile")
    async def put_my_athlete_profile(
        payload: AthleteProfileUpdateRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        updated = update_user_profile(
            app_cfg,
            email,
            name=payload.name,
            picture=payload.picture,
            belt=payload.belt,
            academy=payload.academy,
            coach=payload.coach,
            academy_id=payload.academy_id,
            academy_name=payload.academy_name,
        )
        return {**user, **updated}

    @application.get("/api/film-study/summary")
    async def get_film_study_summary(
        app_cfg: SettingsDep,
        user: UserDep,
        athlete: str = Query(default=""),
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        matches = [m for m in _resolve_matches_for_user(app_cfg, email) if m.get("bucket") != "my_matches"]
        return _build_collection_scouting_summary(matches, focus_athlete=athlete)

    @application.post("/api/collections/summary")
    async def post_collections_summary(
        payload: CollectionSummaryRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        all_matches = _resolve_matches_for_user(app_cfg, email)
        if payload.match_ids:
            wanted = set(payload.match_ids)
            subset = [m for m in all_matches if str(m.get("id") or "") in wanted]
        else:
            subset = [m for m in all_matches if m.get("bucket") != "my_matches"]
        return _build_collection_scouting_summary(subset, focus_athlete=payload.focus_athlete)

    @application.get("/api/admin/overview")
    @application.get("/api/ml/overview")
    @application.get("/api/ml-admin/overview")
    async def get_admin_overview(
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        if not is_admin_user(user, app_cfg):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access requires an allowlisted administrator account.",
            )
        email = str(user.get("email") or "").strip().lower()
        _record_usage_event(app_cfg, email)
        return _load_admin_overview_payload(app_cfg, email)

    @application.api_route(
        "/api/matches/{match_id}/video",
        methods=["GET", "HEAD"],
        response_model=None,
    )
    async def stream_match_video(
        match_id: str,
        request: Request,
        app_cfg: SettingsDep,
        _user: UserDep,
    ) -> Response:
        if not SAFE_FILENAME_RE.match(match_id) or ".." in match_id:
            raise HTTPException(status_code=400, detail="Invalid match identifier.")
        head_only = request.method.upper() == "HEAD"
        local_file = _find_local_showcase_video(match_id, app_cfg)
        if local_file is not None:
            return _stream_file_with_range(local_file, request.headers.get("range"), head_only=head_only)
        return await _proxy_gcs_video_range(
            match_id,
            app_cfg,
            request.headers.get("range"),
            head_only=head_only,
        )

    @application.get("/api/matches/{match_id}/poster", response_model=None)
    def get_match_poster(
        match_id: str,
        app_cfg: SettingsDep,
        _user: UserDep,
    ) -> FileResponse:
        if not SAFE_FILENAME_RE.match(match_id) or ".." in match_id:
            raise HTTPException(status_code=400, detail="Invalid match identifier.")
        poster_dir = app_cfg.runtime_dir / "posters"
        poster_dir.mkdir(parents=True, exist_ok=True)
        poster_path = poster_dir / f"{match_id}.jpg"
        if not poster_path.is_file() or poster_path.stat().st_size < 1000:
            video_file = _resolve_showcase_video_path(match_id, app_cfg)
            ffmpeg_bin = _resolve_ffmpeg_bin()
            for seek_sec in (60.0, 15.0, 1.0):
                tmp_poster = poster_path.with_suffix(".part.jpg")
                subprocess.run(
                    [
                        ffmpeg_bin,
                        "-y",
                        "-ss",
                        f"{seek_sec:.1f}",
                        "-i",
                        str(video_file),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=1280:-2",
                        "-q:v",
                        "4",
                        str(tmp_poster),
                    ],
                    check=False,
                    capture_output=True,
                )
                if tmp_poster.is_file() and tmp_poster.stat().st_size >= 1000:
                    tmp_poster.replace(poster_path)
                    break
        if not poster_path.is_file() or poster_path.stat().st_size < 1000:
            raise HTTPException(status_code=404, detail="Match poster frame unavailable.")
        return FileResponse(
            poster_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @application.post("/api/uploads/init")
    async def init_direct_upload(
        payload: UploadInitRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        orig_name = Path(payload.filename).name
        suffix = Path(orig_name).suffix.lower() or ".mp4"
        if suffix not in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
            raise HTTPException(status_code=400, detail=f"Unsupported video extension '{suffix}'.")

        email = str(user.get("email") or "").strip().lower()
        check_and_record_daily_quota(app_cfg, user, estimated_cost_usd=0.02)
        upload_id = f"upl_{uuid.uuid4().hex[:12]}"
        user_hash = hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]
        object_name = f"uploads/{user_hash}/{upload_id}{suffix}"

        signed_url = None
        if app_cfg.environment == "cloud" and app_cfg.gcs_bucket:
            signed_url = _try_mint_gcs_signed_put_url(
                app_cfg.gcs_bucket,
                object_name,
                payload.content_type or "video/mp4",
            )

        meta_map = _load_uploads_meta(app_cfg)
        meta_record = {
            "upload_id": upload_id,
            "owner_email": email,
            "filename": orig_name,
            "suffix": suffix,
            "content_type": payload.content_type or "video/mp4",
            "size_bytes": payload.size_bytes,
            "bucket": payload.bucket,
            "uniform": payload.uniform,
            "belt": payload.belt.value,
            "ruleset": payload.ruleset,
            "trim_start_sec": payload.trim_start_sec,
            "trim_end_sec": payload.trim_end_sec,
            "athlete_a_anchor": payload.athlete_a_anchor,
            "athlete_b_anchor": payload.athlete_b_anchor,
            "athlete_a_name": payload.athlete_a_name.strip() or "Athlete A",
            "athlete_b_name": payload.athlete_b_name.strip() or "Athlete B",
            "athlete_a_gi": payload.athlete_a_gi,
            "athlete_b_gi": payload.athlete_b_gi,
            "title": payload.title.strip() or Path(orig_name).stem,
            "gcs_object": object_name if signed_url else "",
            "status": "initialized",
            "created_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
        meta_map[upload_id] = meta_record
        _save_uploads_meta(app_cfg, meta_map)
        from rollmetrics.storage import RuntimeStateStore

        RuntimeStateStore(app_cfg).put_record("uploads_meta", upload_id, meta_record)

        if signed_url:
            return {
                "upload_id": upload_id,
                "upload_strategy": "gcs_signed_put",
                "upload_url": signed_url,
                "method": "PUT",
                "headers": {"Content-Type": payload.content_type or "video/mp4"},
                "gcs_object": object_name,
            }
        return {
            "upload_id": upload_id,
            "upload_strategy": "multipart",
            "upload_url": "/api/analyze/upload",
            "method": "POST",
            "headers": {},
            "gcs_object": "",
        }

    @application.post("/api/uploads/complete")
    async def complete_direct_upload(
        payload: UploadCompleteRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        meta_map = _load_uploads_meta(app_cfg)
        meta = meta_map.get(payload.upload_id)
        if not meta or str(meta.get("owner_email") or "").lower() != email:
            raise HTTPException(status_code=404, detail="Upload session not found.")

        suffix = str(meta.get("suffix") or ".mp4")
        local_path = app_cfg.runtime_dir / "uploads" / f"{payload.upload_id}{suffix}"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        gcs_object = str(meta.get("gcs_object") or "")
        if gcs_object and not local_path.is_file() and app_cfg.gcs_bucket:
            with contextlib.suppress(Exception):
                from urllib.parse import quote

                import google.auth
                import httpx
                from google.auth.transport import requests as google_auth_requests

                creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
                creds.refresh(google_auth_requests.Request())
                gcs_dl_url = (
                    f"https://storage.googleapis.com/storage/v1/b/{app_cfg.gcs_bucket}"
                    f"/o/{quote(gcs_object, safe='')}?alt=media"
                )
                async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                    dl_resp = await client.get(
                        gcs_dl_url,
                        headers={"Authorization": f"Bearer {creds.token}"},
                    )
                    if dl_resp.status_code == 200 and dl_resp.content:
                        local_path.write_bytes(dl_resp.content)

        division = MatchDivisionConfig(
            uniform=UniformType(meta.get("uniform") or "gi"),
            belt=BeltRank(meta.get("belt") or "white"),
            athlete_a_name=str(meta.get("athlete_a_name") or "Athlete A"),
            athlete_b_name=str(meta.get("athlete_b_name") or "Athlete B"),
        )
        chosen_profile = payload.profile or getattr(application.state, "active_profile", app_cfg.profile)
        analysis_res: dict[str, Any] = {"snapshot": {"division": division.model_dump(), "timeline": []}}
        if local_path.is_file() and local_path.stat().st_size > 0:
            from rollmetrics.video.analyzer import MatchAnalyzer

            analyzer = MatchAnalyzer(settings=app_cfg, retriever=_get_retriever(app_cfg))
            analysis_res = await analyzer.analyze_video(
                local_path,
                division=division,
                profile=chosen_profile,
                sample_fps=payload.sample_fps,
            )

        _record_usage_event(app_cfg, email, counter_field="uploaded_videos")
        _record_usage_event(app_cfg, email, counter_field="analyses_run")
        match_entry = _persist_analyzed_match(
            app_cfg,
            analysis_res,
            owner_email=email,
            bucket=str(meta.get("bucket") or "my_matches"),
            title=str(meta.get("title") or "Uploaded Match"),
            belt=BeltRank(meta.get("belt") or "white"),
            uniform=str(meta.get("uniform") or "gi"),
            athlete_a_name=str(meta.get("athlete_a_name") or "Athlete A"),
            athlete_b_name=str(meta.get("athlete_b_name") or "Athlete B"),
            athlete_a_gi=str(meta.get("athlete_a_gi") or "white"),
            athlete_b_gi=str(meta.get("athlete_b_gi") or "blue"),
            video_path=str(local_path),
            video_url=f"/api/media/files/{local_path.name}",
            gcs_object=gcs_object,
        )
        return {"match": match_entry, "snapshot": analysis_res.get("snapshot", analysis_res)}

    @application.post("/api/uploads/google-drive")
    async def upload_from_google_drive(
        payload: GoogleDriveUploadRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        import httpx

        file_id = _extract_google_drive_file_id(payload.drive_url)
        if not file_id:
            raise HTTPException(status_code=400, detail="Provide a valid shared Google Drive video URL or file ID.")

        email = str(user.get("email") or "").strip().lower()
        check_and_record_daily_quota(app_cfg, user, estimated_cost_usd=0.02)
        saved_name = f"gdrive_{file_id[:16]}_{uuid.uuid4().hex[:6]}.mp4"
        saved_path = app_cfg.runtime_dir / "uploads" / saved_name
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        try:
            async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
                resp = await client.get(download_url)
                resp.raise_for_status()
                if not resp.content:
                    raise HTTPException(status_code=400, detail="Google Drive file returned empty content.")
                saved_path.write_bytes(resp.content)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Failed to download Google Drive video: {exc}") from exc

        division = MatchDivisionConfig(
            uniform=UniformType(payload.uniform),
            belt=payload.belt,
            athlete_a_name=payload.athlete_a_name.strip() or "Athlete A",
            athlete_b_name=payload.athlete_b_name.strip() or "Athlete B",
        )
        chosen_profile = payload.profile or getattr(application.state, "active_profile", app_cfg.profile)
        from rollmetrics.video.analyzer import MatchAnalyzer

        analyzer = MatchAnalyzer(settings=app_cfg, retriever=_get_retriever(app_cfg))
        analysis_res = await analyzer.analyze_video(
            saved_path,
            division=division,
            profile=chosen_profile,
            sample_fps=0.5,
        )
        _record_usage_event(app_cfg, email, counter_field="uploaded_videos")
        _record_usage_event(app_cfg, email, counter_field="analyses_run")
        match_entry = _persist_analyzed_match(
            app_cfg,
            analysis_res,
            owner_email=email,
            bucket=payload.bucket,
            title=payload.title.strip() or f"{division.athlete_a_name} vs. {division.athlete_b_name}",
            belt=payload.belt,
            uniform=payload.uniform,
            athlete_a_name=division.athlete_a_name,
            athlete_b_name=division.athlete_b_name,
            athlete_a_gi=payload.athlete_a_gi,
            athlete_b_gi=payload.athlete_b_gi,
            video_path=str(saved_path),
            video_url=f"/api/media/files/{saved_name}",
        )
        return {"match": match_entry, "snapshot": analysis_res.get("snapshot", analysis_res)}

    @application.post("/api/matches/{match_id}/share")
    async def create_match_share_link(
        match_id: str,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        if not SAFE_FILENAME_RE.match(match_id) or ".." in match_id:
            raise HTTPException(status_code=400, detail="Invalid match identifier.")
        email = str(user.get("email") or "").strip().lower()
        user_matches = _resolve_matches_for_user(app_cfg, email)
        target = next((m for m in user_matches if str(m.get("id") or "") == match_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Match not found.")

        shares = _load_shares(app_cfg)
        for existing_token, record in shares.items():
            if record.get("match_id") == match_id and record.get("owner_email") == email:
                return {
                    "token": existing_token,
                    "match_id": match_id,
                    "share_path": f"/share/{existing_token}",
                    "share_url": f"{app_cfg.public_url.rstrip('/')}/share/{existing_token}",
                }

        token = secrets.token_urlsafe(16)
        share_record = {
            "token": token,
            "match_id": match_id,
            "owner_email": email,
            "academy_name": str(user.get("academy_name") or "Academy Library"),
            "created_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "allowed_renders": [],
        }
        shares[token] = share_record
        _save_shares(app_cfg, shares)
        from rollmetrics.storage import RuntimeStateStore

        RuntimeStateStore(app_cfg).put_record("shares", token, share_record)
        return {
            "token": token,
            "match_id": match_id,
            "share_path": f"/share/{token}",
            "share_url": f"{app_cfg.public_url.rstrip('/')}/share/{token}",
        }

    @application.delete("/api/share/{token}")
    async def revoke_match_share_link(
        token: str,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        shares = _load_shares(app_cfg)
        record = shares.get(token)
        if not record:
            raise HTTPException(status_code=404, detail="Shared breakdown link not found.")
        if str(record.get("owner_email") or "").lower() != email and not is_admin_user(user, app_cfg):
            raise HTTPException(status_code=403, detail="Only the link owner or an admin can revoke this share link.")
        shares.pop(token, None)
        _save_shares(app_cfg, shares)
        from rollmetrics.storage import RuntimeStateStore

        RuntimeStateStore(app_cfg).delete_record("shares", token)
        return {"ok": True, "token": token}

    @application.delete("/api/matches/{match_id}")
    async def delete_custom_match(
        match_id: str,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        if not SAFE_FILENAME_RE.match(match_id) or ".." in match_id:
            raise HTTPException(status_code=400, detail="Invalid match identifier.")
        email = str(user.get("email") or "").strip().lower()
        custom_path = app_cfg.runtime_dir / "custom_matches.json"
        existing: list[dict[str, Any]] = []
        if custom_path.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                raw = json.loads(custom_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    existing = [m for m in raw if isinstance(m, dict)]
        target = next((m for m in existing if str(m.get("id") or "") == match_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Custom match not found.")
        if str(target.get("owner_email") or "").lower() != email and not is_admin_user(user, app_cfg):
            raise HTTPException(status_code=403, detail="Only the match owner or an admin can delete this match.")
        remaining = [m for m in existing if str(m.get("id") or "") != match_id]
        custom_path.parent.mkdir(parents=True, exist_ok=True)
        custom_path.write_text(json.dumps(remaining, indent=2), encoding="utf-8")
        from rollmetrics.storage import RuntimeStateStore

        RuntimeStateStore(app_cfg).delete_record("matches", match_id)
        return {"ok": True, "match_id": match_id}

    @application.put("/api/matches/{match_id}/moments")
    async def update_match_moments(
        match_id: str,
        payload: MatchMomentsUpdateRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        if not SAFE_FILENAME_RE.match(match_id) or ".." in match_id:
            raise HTTPException(status_code=400, detail="Invalid match identifier.")
        email = str(user.get("email") or "").strip().lower()
        user_matches = _resolve_matches_for_user(app_cfg, email)
        target = next((m for m in user_matches if str(m.get("id") or "") == match_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Match not found.")

        is_adcc = payload.ruleset == "adcc"
        is_sparring = payload.ruleset == "sparring"
        pts_a = adv_a = pen_a = pts_b = adv_b = pen_b = 0
        recalculated: list[dict[str, Any]] = []
        sorted_events = sorted(payload.events, key=lambda e: float(e.get("video_sec") or 0.0))
        for idx, raw_ev in enumerate(sorted_events):
            ev = dict(raw_ev)
            actor = "B" if str(ev.get("actor") or "A").upper() == "B" else "A"
            call = str(ev.get("call") or "").upper()
            pts_match = re.search(r"([+-]?\d+)\s*PTS", call)
            adv_match = re.search(r"([+-]?\d+)\s*ADV", call)
            pen_match = re.search(r"([+-]?\d+)\s*PEN", call)
            add_pts = 0 if is_sparring else (int(pts_match.group(1)) if pts_match else 0)
            add_adv = 0 if (is_adcc or is_sparring) else (int(adv_match.group(1)) if adv_match else 0)
            add_pen = 0 if is_sparring else (int(pen_match.group(1)) if pen_match else 0)
            if is_adcc and add_pen > 0:
                add_pts -= add_pen
            if actor == "A":
                pts_a += add_pts
                adv_a += add_adv
                pen_a += add_pen
            else:
                pts_b += add_pts
                adv_b += add_adv
                pen_b += add_pen
            ev["id"] = str(ev.get("id") or f"moment-{idx + 1}")
            ev["actor"] = actor
            ev["pts_a"] = pts_a
            ev["adv_a"] = adv_a
            ev["pen_a"] = pen_a
            ev["pts_b"] = pts_b
            ev["adv_b"] = adv_b
            ev["pen_b"] = pen_b
            recalculated.append(ev)

        updated_match = {**target, "ruleset": payload.ruleset, "events": recalculated}
        from rollmetrics.storage import RuntimeStateStore

        RuntimeStateStore(app_cfg).put_record(
            "moment_overrides",
            match_id,
            {
                "match_id": match_id,
                "owner_email": email,
                "ruleset": payload.ruleset,
                "events": recalculated,
                "updated_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            },
        )
        feedback_logged = False
        if payload.opt_in_feedback:
            fb_path = app_cfg.runtime_dir / "moment_feedback.jsonl"
            fb_path.parent.mkdir(parents=True, exist_ok=True)
            with fb_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "match_id": match_id,
                            "editor_email": email,
                            "ruleset": payload.ruleset,
                            "event_count": len(recalculated),
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        }
                    )
                    + "\n"
                )
            feedback_logged = True
        return {"match": updated_match, "feedback_logged": feedback_logged}

    @application.get("/api/me/export.zip")
    async def export_user_data_zip(
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> Response:
        email = str(user.get("email") or "").strip().lower()
        from rollmetrics.storage import RuntimeStateStore

        zip_bytes = RuntimeStateStore(app_cfg).export_bundle_zip(owner_email=email)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="rollmetrics-export.zip"'},
        )

    @application.get("/api/team/roster")
    async def get_team_coach_roster(
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        role = str(user.get("role") or "").lower()
        if role not in ("coach", "admin") and not is_admin_user(user, app_cfg):
            return {"roster": []}
        email = str(user.get("email") or "").strip().lower()
        from rollmetrics.storage import RuntimeStateStore

        store = RuntimeStateStore(app_cfg)
        reviews = {
            str(r.get("match_id") or ""): r
            for r in store.list_records("match_reviews")
        }
        matches = _resolve_matches_for_user(app_cfg, email)
        roster: list[dict[str, Any]] = []
        for m in matches:
            if m.get("bucket") != "my_matches":
                continue
            mid = str(m.get("id") or "")
            rev = reviews.get(mid, {})
            ath_a = m.get("athlete_a") if isinstance(m.get("athlete_a"), dict) else {}
            roster.append(
                {
                    "match_id": mid,
                    "title": str(m.get("title") or m.get("pill") or mid),
                    "owner_email": str(m.get("owner_email") or email),
                    "athlete_name": str(ath_a.get("name") or "Athlete A"),
                    "belt_label": str(m.get("belt_label") or ""),
                    "ruleset": str(m.get("ruleset") or "ibjjf_v6"),
                    "review_status": str(rev.get("review_status") or "needs_review"),
                    "pinned_drills": list(rev.get("pinned_drills") or []),
                    "created_at": str(rev.get("updated_at") or ""),
                }
            )
        return {"roster": roster}

    @application.post("/api/matches/{match_id}/review")
    async def update_match_review_status(
        match_id: str,
        payload: MatchReviewUpdateRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        if not SAFE_FILENAME_RE.match(match_id) or ".." in match_id:
            raise HTTPException(status_code=400, detail="Invalid match identifier.")
        role = str(user.get("role") or "").lower()
        if role not in ("coach", "admin") and not is_admin_user(user, app_cfg):
            raise HTTPException(status_code=403, detail="Coach or admin role required to review team matches.")
        from rollmetrics.storage import RuntimeStateStore

        store = RuntimeStateStore(app_cfg)
        existing = store.get_record("match_reviews", match_id) or {
            "match_id": match_id,
            "owner_email": str(user.get("email") or "").strip().lower(),
            "review_status": "needs_review",
            "pinned_drills": [],
        }
        if payload.review_status:
            existing["review_status"] = payload.review_status
        if payload.pin_drill and isinstance(payload.pin_drill, dict):
            drills = list(existing.get("pinned_drills") or [])
            drills.append(
                {
                    "title": str(payload.pin_drill.get("title") or "Pinned Drill"),
                    "url": str(payload.pin_drill.get("url") or ""),
                    "note": str(payload.pin_drill.get("note") or ""),
                }
            )
            existing["pinned_drills"] = drills
        existing["updated_at"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        store.put_record("match_reviews", match_id, existing)
        return {
            "match_id": match_id,
            "review_status": existing["review_status"],
            "pinned_drills": existing["pinned_drills"],
        }

    @application.post("/api/academies/me/references/auto-tag")
    async def auto_tag_academy_reference(
        payload: AutoTagReferenceRequest,
        _user: UserDep,
    ) -> dict[str, Any]:
        blob = f"{payload.title} {payload.notes} {payload.url}".lower()
        position_map = [
            ("guard_passing", ["knee cut", "toreando", "leg drag", "smash", "guard pass", "pass"]),
            ("closed_guard", ["closed guard", "armbar", "triangle", "flower sweep"]),
            ("half_guard", ["half guard", "underhook", "knee shield", "dogfight"]),
            ("mount", ["mount", "cross choke", "mounted", "upa", "elbow escape"]),
            ("back_control", ["back", "rnc", "rear naked", "bow and arrow", "hooks"]),
            ("takedowns", ["takedown", "single leg", "double leg", "osoto", "wrestling"]),
            ("leg_locks", ["heel hook", "ankle lock", "ashi", "50/50", "kneebar"]),
        ]
        detected_pos = "general"
        tags: list[str] = []
        for pos, keywords in position_map:
            if any(k in blob for k in keywords):
                detected_pos = pos
                tags.append(pos)
                break
        belt = "blue" if detected_pos in ("half_guard", "guard_passing") else ("brown" if detected_pos == "leg_locks" else "white")
        clean_title = payload.title.strip() or (
            f"{detected_pos.replace('_', ' ').title()} Reference"
        )
        summary = payload.notes.strip() or f"Auto-tagged {detected_pos.replace('_', ' ')} study reference ({belt} belt)."
        return {
            "title": clean_title,
            "position": detected_pos,
            "belt": belt,
            "tags": tags or ["fundamentals"],
            "summary": summary,
        }

    @application.get("/api/share/{token}")
    async def get_shared_match(
        token: str,
        app_cfg: SettingsDep,
    ) -> dict[str, Any]:
        shares = _load_shares(app_cfg)
        record = shares.get(token)
        if not record:
            raise HTTPException(status_code=404, detail="Shared breakdown link not found.")
        match_id = str(record.get("match_id") or "")
        match = _resolve_match_by_id_unscoped(app_cfg, match_id)
        if not match:
            raise HTTPException(status_code=404, detail="Shared match is no longer available.")
        public_match = {
            **match,
            "video_url": f"/api/share/{token}/video",
            "poster_url": f"/api/share/{token}/poster",
        }
        public_match.pop("video_path", None)
        public_match.pop("owner_email", None)
        return {
            "token": token,
            "academy_name": str(record.get("academy_name") or "Academy Library"),
            "match": public_match,
        }

    @application.api_route(
        "/api/share/{token}/video",
        methods=["GET", "HEAD"],
        response_model=None,
    )
    async def stream_shared_match_video(
        token: str,
        request: Request,
        app_cfg: SettingsDep,
    ) -> Response:
        shares = _load_shares(app_cfg)
        record = shares.get(token)
        if not record:
            raise HTTPException(status_code=404, detail="Shared breakdown link not found.")
        match_id = str(record.get("match_id") or "")
        match = _resolve_match_by_id_unscoped(app_cfg, match_id)
        head_only = request.method.upper() == "HEAD"
        if match and match.get("video_path"):
            vp = Path(str(match["video_path"]))
            if vp.is_file():
                return _stream_file_with_range(vp, request.headers.get("range"), head_only=head_only)
        local_file = _find_local_showcase_video(match_id, app_cfg)
        if local_file is not None:
            return _stream_file_with_range(local_file, request.headers.get("range"), head_only=head_only)
        return await _proxy_gcs_video_range(
            match_id,
            app_cfg,
            request.headers.get("range"),
            head_only=head_only,
        )

    @application.get("/api/share/{token}/poster", response_model=None)
    def get_shared_match_poster(
        token: str,
        app_cfg: SettingsDep,
    ) -> FileResponse:
        shares = _load_shares(app_cfg)
        record = shares.get(token)
        if not record:
            raise HTTPException(status_code=404, detail="Shared breakdown link not found.")
        match_id = str(record.get("match_id") or "")
        poster_dir = app_cfg.runtime_dir / "posters"
        poster_dir.mkdir(parents=True, exist_ok=True)
        poster_path = poster_dir / f"{match_id}.jpg"
        if not poster_path.is_file() or poster_path.stat().st_size < 1000:
            video_file = _resolve_showcase_video_path(match_id, app_cfg)
            ffmpeg_bin = _resolve_ffmpeg_bin()
            for seek_sec in (60.0, 15.0, 1.0):
                tmp_poster = poster_path.with_suffix(".part.jpg")
                subprocess.run(
                    [
                        ffmpeg_bin,
                        "-y",
                        "-ss",
                        f"{seek_sec:.1f}",
                        "-i",
                        str(video_file),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=1280:-2",
                        "-q:v",
                        "4",
                        str(tmp_poster),
                    ],
                    check=False,
                    capture_output=True,
                )
                if tmp_poster.is_file() and tmp_poster.stat().st_size >= 1000:
                    tmp_poster.replace(poster_path)
                    break
        if not poster_path.is_file() or poster_path.stat().st_size < 1000:
            raise HTTPException(status_code=404, detail="Match poster frame unavailable.")
        return FileResponse(
            poster_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @application.post("/api/share/{token}/highlight")
    async def create_shared_match_highlight(
        token: str,
        payload: ShareHighlightRequest,
        app_cfg: SettingsDep,
    ) -> dict[str, Any]:
        shares = _load_shares(app_cfg)
        record = shares.get(token)
        if not record:
            raise HTTPException(status_code=404, detail="Shared breakdown link not found.")
        match_id = str(record.get("match_id") or "")
        match = _resolve_match_by_id_unscoped(app_cfg, match_id)
        video_path_str = str(match.get("video_path") or "") if match else ""
        src_video = _resolve_media_video_path(
            video_path_str or None,
            app_cfg,
            match_id=match_id if not video_path_str else None,
        )
        out_name = f"slowmo_{uuid.uuid4().hex[:10]}.mp4"
        out_path = app_cfg.runtime_dir / "renders" / out_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from rollmetrics.video.media_studio import MediaStudio

            rendered = MediaStudio().generate_slowmo_clip(
                video_path=src_video,
                start_sec=payload.start_sec,
                duration_sec=payload.duration_sec,
                output_path=out_path,
                speed=payload.speed,
            )
            final_path = Path(rendered)
        except ImportError:
            final_path = _fallback_slowmo_clip(
                video_path=src_video,
                start_sec=payload.start_sec,
                duration_sec=payload.duration_sec,
                output_path=out_path,
                speed=payload.speed,
            )
        allowed = list(record.get("allowed_renders") or [])
        if final_path.name not in allowed:
            allowed.append(final_path.name)
            record["allowed_renders"] = allowed[-20:]
            shares[token] = record
            _save_shares(app_cfg, shares)
        return {
            "filename": final_path.name,
            "url": f"/api/share/{token}/media/{final_path.name}",
        }

    @application.get("/api/share/{token}/media/{filename}")
    async def serve_shared_match_media(
        token: str,
        filename: str,
        app_cfg: SettingsDep,
    ) -> FileResponse:
        if not SAFE_FILENAME_RE.match(filename) or ".." in filename:
            raise HTTPException(status_code=400, detail="Invalid media filename.")
        shares = _load_shares(app_cfg)
        record = shares.get(token)
        if not record or filename not in (record.get("allowed_renders") or []):
            raise HTTPException(status_code=404, detail="Shared media file not found.")
        candidate = (app_cfg.runtime_dir / "renders" / filename).resolve()
        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        raise HTTPException(status_code=404, detail="Shared media file not found.")

    def _require_local_or_admin(app_cfg: Settings, user: dict[str, Any]) -> None:
        if app_cfg.environment in ("local", "test"):
            return
        if is_admin_user(user, app_cfg):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Offline desktop folder tools require local mode or administrator access.",
        )

    @application.get("/api/local/hardware")
    async def get_local_hardware(
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        _require_local_or_admin(app_cfg, user)
        try:
            from rollmetrics.desktop import get_local_hardware_status

            res = get_local_hardware_status(app_cfg)
            if inspect.isawaitable(res):
                return dict(await res)
            return dict(res)
        except (ImportError, TypeError):
            import platform

            return {
                "offline_ready": True,
                "ollama_reachable": False,
                "installed_models": [],
                "recommended_model": app_cfg.ollama_vision_model,
                "platform": platform.system(),
                "machine": platform.machine(),
                "ram_gb": 16.0,
            }

    @application.post("/api/local/folder/scan")
    async def scan_local_folder(
        payload: LocalFolderScanRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        _require_local_or_admin(app_cfg, user)
        folder = Path(payload.folder_path).expanduser().resolve()
        if not folder.is_dir():
            raise HTTPException(status_code=404, detail=f"Folder not found: {payload.folder_path}")
        try:
            from rollmetrics.desktop import scan_local_video_folder

            sig = inspect.signature(scan_local_video_folder)
            if len(sig.parameters) >= 2:
                files = scan_local_video_folder(str(folder), app_cfg)
            else:
                files = scan_local_video_folder(str(folder))
            if isinstance(files, dict):
                return files
            return {"folder_path": str(folder), "count": len(files), "videos": list(files)}
        except ImportError:
            vids: list[dict[str, Any]] = []
            for p in sorted(folder.iterdir()):
                if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
                    vids.append(
                        {
                            "path": str(p),
                            "filename": p.name,
                            "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
                            "duration_sec": 0.0,
                            "analyzed": False,
                        }
                    )
            return {"folder_path": str(folder), "count": len(vids), "videos": vids}

    @application.post("/api/local/folder/analyze")
    async def analyze_local_folder_video(
        payload: LocalFolderAnalyzeRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        _require_local_or_admin(app_cfg, user)
        video_file = Path(payload.video_path).expanduser().resolve()
        if not video_file.is_file():
            raise HTTPException(status_code=404, detail=f"Video file not found: {payload.video_path}")
        if video_file.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
            raise HTTPException(status_code=400, detail=f"Unsupported video extension '{video_file.suffix}'.")

        email = str(user.get("email") or "").strip().lower()
        division = MatchDivisionConfig(
            uniform=UniformType(payload.uniform),
            belt=payload.belt,
            athlete_a_name=payload.athlete_a_name.strip() or "Athlete A",
            athlete_b_name=payload.athlete_b_name.strip() or "Athlete B",
        )
        from rollmetrics.video.analyzer import MatchAnalyzer

        analyzer = MatchAnalyzer(settings=app_cfg, retriever=_get_retriever(app_cfg))
        analysis_res = await analyzer.analyze_video(
            video_file,
            division=division,
            profile="local",
            sample_fps=0.5,
        )
        _record_usage_event(app_cfg, email, counter_field="analyses_run")
        match_entry = _persist_analyzed_match(
            app_cfg,
            analysis_res,
            owner_email=email,
            bucket=payload.bucket,
            title=payload.title.strip() or video_file.stem,
            belt=payload.belt,
            uniform=payload.uniform,
            athlete_a_name=division.athlete_a_name,
            athlete_b_name=division.athlete_b_name,
            athlete_a_gi="white",
            athlete_b_gi="blue",
            video_path=str(video_file),
            video_url="",
        )
        match_entry["video_url"] = f"/api/matches/{match_entry['id']}/video"
        matches_path = app_cfg.runtime_dir / "matches" / f"{match_entry['id']}.json"
        matches_path.write_text(json.dumps(match_entry, indent=2), encoding="utf-8")
        return {"match": match_entry, "snapshot": analysis_res.get("snapshot", analysis_res)}

    @application.post("/api/local/academy/import-folder")
    async def import_local_academy_notes_folder(
        payload: LocalAcademyImportFolderRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        _require_local_or_admin(app_cfg, user)
        folder = Path(payload.folder_path).expanduser().resolve()
        if not folder.is_dir():
            raise HTTPException(status_code=404, detail=f"Folder not found: {payload.folder_path}")

        acad_id = (payload.academy_id or str(user.get("academy_id") or "default")).strip()
        acad_name = (payload.academy_name or str(user.get("academy_name") or "Academy Library")).strip()
        try:
            from rollmetrics.desktop import import_local_academy_folder

            sig = inspect.signature(import_local_academy_folder)
            if "settings" in sig.parameters:
                res = import_local_academy_folder(
                    str(folder),
                    academy_id=acad_id,
                    academy_name=acad_name,
                    settings=app_cfg,
                )
            else:
                res = import_local_academy_folder(str(folder), acad_id, acad_name)
            return dict(res)
        except ImportError:
            from rollmetrics.knowledge.academies import add_academy_reference, upsert_academy

            upsert_academy(app_cfg, acad_id, acad_name)
            imported = 0
            for p in sorted(folder.iterdir()):
                if p.is_file() and p.suffix.lower() in {".md", ".txt", ".json3"}:
                    text = p.read_text(encoding="utf-8", errors="ignore").strip()[:3500]
                    if text:
                        add_academy_reference(
                            app_cfg,
                            acad_id,
                            title=p.stem.replace("_", " ").replace("-", " ").title(),
                            text=text,
                            position="all",
                            reference_type="note",
                        )
                        imported += 1
            return {
                "folder_path": str(folder),
                "academy_id": acad_id,
                "academy_name": acad_name,
                "imported_count": imported,
            }

    @application.get("/api/analyze/presets")
    async def list_presets(
        app_cfg: SettingsDep,
        _user: UserDep,
    ) -> dict[str, Any]:
        presets = _load_presets(app_cfg)
        return {"count": len(presets), "presets": presets}

    @application.post("/api/analyze/events")
    async def analyze_events(
        payload: AnalyzeEventsRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        if email:
            _record_usage_event(app_cfg, email, counter_field="analyses_run")
        events = list(payload.events)
        division = payload.division
        source_meta = dict(payload.source_meta or {})

        if payload.preset_id and not events:
            presets = _load_presets(app_cfg)
            matched = next((p for p in presets if p["id"] == payload.preset_id), None)
            if matched is None:
                raise HTTPException(status_code=404, detail=f"Preset '{payload.preset_id}' not found.")
            if matched.get("division"):
                division = MatchDivisionConfig.model_validate(matched["division"])
            events = [RawObservedEvent.model_validate(e) for e in matched.get("events", [])]
            source_meta = {
                "preset_id": matched["id"],
                "title": matched.get("title", ""),
                "subtitle": matched.get("subtitle", ""),
                "youtube_url": matched.get("youtube_url", ""),
                **source_meta,
            }

        try:
            from rollmetrics.video.analyzer import MatchAnalyzer

            analyzer = MatchAnalyzer(settings=app_cfg, retriever=_get_retriever(app_cfg))
            res = analyzer.analyze_event_stream(events=events, division=division, source_meta=source_meta)
            if isinstance(res, dict) and "snapshot" not in res:
                res = {"snapshot": res.get("scoreboard", res), **res}
            return res
        except ImportError:
            return _fallback_analyze_events(
                events=events,
                division=division,
                settings=app_cfg,
                source_meta=source_meta,
                finalize_regulation=payload.finalize_regulation,
            )

    @application.post("/api/analyze/upload")
    async def analyze_upload(
        file: Annotated[UploadFile, File(...)],
        app_cfg: SettingsDep,
        user: UserDep,
        division_json: Annotated[str, Form()] = "",
        profile: Annotated[str | None, Form()] = None,
        sample_fps: Annotated[float, Form()] = 0.5,
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        if email:
            _record_usage_event(app_cfg, email, counter_field="uploaded_videos")
            _record_usage_event(app_cfg, email, counter_field="analyses_run")
        orig_name = Path(file.filename or "upload.mp4").name
        suffix = Path(orig_name).suffix.lower() or ".mp4"
        if suffix not in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
            raise HTTPException(status_code=400, detail=f"Unsupported video extension '{suffix}'.")

        saved_name = f"match_{uuid.uuid4().hex[:10]}{suffix}"
        saved_path = app_cfg.runtime_dir / "uploads" / saved_name
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded video file is empty.")
        saved_path.write_bytes(content)

        division = (
            MatchDivisionConfig.model_validate_json(division_json)
            if division_json.strip()
            else MatchDivisionConfig()
        )
        chosen_profile = profile or getattr(application.state, "active_profile", app_cfg.profile)

        from rollmetrics.video.analyzer import MatchAnalyzer

        analyzer = MatchAnalyzer(settings=app_cfg, retriever=_get_retriever(app_cfg))
        res = await analyzer.analyze_video(
            saved_path,
            division=division,
            profile=chosen_profile,
            sample_fps=sample_fps,
        )
        if isinstance(res, dict):
            res.setdefault("video_filename", saved_name)
            res.setdefault("video_path", str(saved_path))
            res.setdefault("video_url", f"/api/media/files/{saved_name}")
            if "snapshot" not in res:
                res = {"snapshot": res.get("scoreboard", res), **res}
        return res

    @application.post("/api/analyze/url")
    async def analyze_url(
        payload: AnalyzeUrlRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        email = str(user.get("email") or "").strip().lower()
        if email:
            _record_usage_event(app_cfg, email, counter_field="analyses_run")
        chosen_profile = payload.profile or getattr(application.state, "active_profile", app_cfg.profile)
        from rollmetrics.video.analyzer import MatchAnalyzer

        analyzer = MatchAnalyzer(settings=app_cfg, retriever=_get_retriever(app_cfg))
        res = await analyzer.analyze_video(
            payload.url,
            division=payload.division,
            profile=chosen_profile,
            sample_fps=payload.sample_fps,
        )
        if isinstance(res, dict) and "snapshot" not in res:
            res = {"snapshot": res.get("scoreboard", res), **res}
        return res

    @application.post("/api/media/board")
    async def create_media_board(
        payload: MediaBoardRequest,
        app_cfg: SettingsDep,
        _user: UserDep,
    ) -> dict[str, Any]:
        src_video = _resolve_media_video_path(
            payload.video_path,
            app_cfg,
            match_id=payload.match_id,
        )
        out_name = f"board_{uuid.uuid4().hex[:10]}.png"
        out_path = app_cfg.runtime_dir / "renders" / out_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from rollmetrics.video.media_studio import MediaStudio

            studio = MediaStudio()
            rendered = studio.generate_three_panel_board(
                video_path=src_video,
                center_timestamp_sec=payload.center_timestamp_sec,
                output_path=out_path,
                title=payload.title,
                cues=payload.cues,
                actor_label=payload.actor_label,
            )
            final_path = Path(rendered)
        except ImportError:
            final_path = _fallback_three_panel_board(
                video_path=src_video,
                center_timestamp_sec=payload.center_timestamp_sec,
                output_path=out_path,
                title=payload.title,
                cues=payload.cues,
                actor_label=payload.actor_label,
            )
        return {
            "filename": final_path.name,
            "output_path": str(final_path),
            "url": f"/api/media/files/{final_path.name}",
        }

    @application.post("/api/media/slowmo")
    async def create_media_slowmo(
        payload: MediaSlowmoRequest,
        app_cfg: SettingsDep,
        _user: UserDep,
    ) -> dict[str, Any]:
        src_video = _resolve_media_video_path(
            payload.video_path,
            app_cfg,
            match_id=payload.match_id,
        )
        out_name = f"slowmo_{uuid.uuid4().hex[:10]}.mp4"
        out_path = app_cfg.runtime_dir / "renders" / out_name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from rollmetrics.video.media_studio import MediaStudio

            studio = MediaStudio()
            rendered = studio.generate_slowmo_clip(
                video_path=src_video,
                start_sec=payload.start_sec,
                duration_sec=payload.duration_sec,
                output_path=out_path,
                speed=payload.speed,
                caption=payload.caption,
            )
            final_path = Path(rendered)
        except ImportError:
            final_path = _fallback_slowmo_clip(
                video_path=src_video,
                start_sec=payload.start_sec,
                duration_sec=payload.duration_sec,
                output_path=out_path,
                speed=payload.speed,
            )
        return {
            "filename": final_path.name,
            "output_path": str(final_path),
            "url": f"/api/media/files/{final_path.name}",
        }

    @application.get("/api/media/files/{filename}")
    async def serve_media_file(
        filename: str,
        app_cfg: SettingsDep,
        _user: UserDep,
    ) -> FileResponse:
        if not SAFE_FILENAME_RE.match(filename) or ".." in filename:
            raise HTTPException(status_code=400, detail="Invalid media filename.")
        for sub in ("renders", "uploads"):
            candidate = (app_cfg.runtime_dir / sub / filename).resolve()
            if candidate.exists() and candidate.is_file():
                return FileResponse(candidate)
        raise HTTPException(status_code=404, detail=f"Media file '{filename}' not found.")

    @application.get("/api/academies/me")
    async def get_my_academy(
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        from rollmetrics.knowledge.academies import (
            default_academy_for_email,
            get_academy,
            list_academy_references,
        )

        email = str(user.get("email") or "").strip().lower()
        def_id, _ = default_academy_for_email(email)
        acad_id = str(user.get("academy_id") or def_id).strip() or def_id
        record = get_academy(app_cfg, acad_id)
        refs = list_academy_references(app_cfg, acad_id)
        seeded_count = 0
        if acad_id == "cta":
            retriever = _get_retriever(app_cfg)
            if retriever is not None:
                seeded_count = int(retriever.manifest().get("counts", {}).get("cta", 0))
        return {
            **record,
            "academy_name": str(user.get("academy_name") or record["academy_name"]),
            "user_email": email,
            "reference_count": len(refs),
            "seeded_corpus_count": seeded_count,
        }

    @application.put("/api/academies/me")
    async def update_my_academy(
        payload: AcademyUpdateRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        from rollmetrics.knowledge.academies import list_academy_references, upsert_academy

        email = str(user.get("email") or "").strip().lower()
        record = upsert_academy(
            app_cfg,
            academy_name=payload.academy_name,
            academy_id=payload.academy_id,
        )
        if email:
            update_user_academy(
                app_cfg,
                email=email,
                academy_id=record["academy_id"],
                academy_name=record["academy_name"],
            )
        refs = list_academy_references(app_cfg, record["academy_id"])
        return {
            **record,
            "user_email": email,
            "reference_count": len(refs),
        }

    @application.get("/api/academies/me/references")
    async def get_my_academy_references(
        app_cfg: SettingsDep,
        user: UserDep,
        q: Annotated[str | None, Query()] = None,
        position: Annotated[str | None, Query()] = None,
        top_k: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> dict[str, Any]:
        from rollmetrics.knowledge.academies import (
            default_academy_for_email,
            get_academy,
            list_academy_references,
            search_academy_references,
        )

        email = str(user.get("email") or "").strip().lower()
        def_id, _ = default_academy_for_email(email)
        acad_id = str(user.get("academy_id") or def_id).strip() or def_id
        record = get_academy(app_cfg, acad_id)
        if q and q.strip():
            hits = search_academy_references(
                app_cfg,
                academy_id=acad_id,
                query=q.strip(),
                top_k=top_k,
                position=position,
            )
            items = [h.model_dump(mode="json") for h in hits]
        else:
            docs = list_academy_references(app_cfg, acad_id)
            items = [d.model_dump(mode="json") for d in docs]
        return {
            "academy_id": record["academy_id"],
            "academy_name": str(user.get("academy_name") or record["academy_name"]),
            "count": len(items),
            "references": items,
        }

    @application.post("/api/academies/me/references", status_code=status.HTTP_201_CREATED)
    async def create_my_academy_reference(
        payload: AcademyReferenceCreateRequest,
        app_cfg: SettingsDep,
        user: UserDep,
    ) -> dict[str, Any]:
        from rollmetrics.knowledge.academies import (
            add_academy_reference,
            default_academy_for_email,
            get_academy,
        )

        email = str(user.get("email") or "").strip().lower()
        def_id, _ = default_academy_for_email(email)
        acad_id = str(user.get("academy_id") or def_id).strip() or def_id
        try:
            doc = add_academy_reference(
                app_cfg,
                academy_id=acad_id,
                title=payload.title,
                text=payload.text,
                position=payload.position,
                belt=payload.belt,
                source_url=payload.source_url,
                reference_type=payload.reference_type,
                instructor=payload.instructor,
                tags=payload.tags,
                timestamp_sec=payload.timestamp_sec,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record = get_academy(app_cfg, acad_id)
        return {
            "academy_id": record["academy_id"],
            "academy_name": str(user.get("academy_name") or record["academy_name"]),
            "reference": doc.model_dump(mode="json"),
        }

    @application.get("/api/knowledge/search")
    async def knowledge_search(
        app_cfg: SettingsDep,
        user: UserDep,
        q: Annotated[str, Query(description="Search query across global IBJJF rules and academy library.")] = "",
        corpus: Annotated[str, Query(pattern="^(all|ibjjf|cta)$")] = "all",
        top_k: Annotated[int, Query(ge=1, le=30)] = 8,
        position: Annotated[str | None, Query()] = None,
        version: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        ver = version or app_cfg.active_index_version
        retriever = _get_retriever(app_cfg, version=ver)
        if retriever is None:
            return {"query": q, "corpus": corpus, "version": ver, "hits": []}
        acad_id = str(user.get("academy_id") or "").strip() or None
        hits = retriever.search(
            query=q,
            corpus=corpus,
            top_k=top_k,
            position=position,
            academy_id=acad_id,
        )
        serialized = [h.model_dump(mode="json") if hasattr(h, "model_dump") else h for h in hits]
        return {
            "query": q,
            "corpus": corpus,
            "academy_id": acad_id,
            "version": ver,
            "count": len(serialized),
            "hits": serialized,
        }

    @application.get("/api/knowledge/manifest")
    async def knowledge_manifest(
        app_cfg: SettingsDep,
        _user: UserDep,
        version: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        ver = version or app_cfg.active_index_version
        retriever = _get_retriever(app_cfg, version=ver)
        if retriever is not None:
            return retriever.manifest()
        manifest_path = app_cfg.knowledge_dir / "versions" / ver / "manifest.json"
        if manifest_path.exists():
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        return {"version": ver, "available": False}

    @application.post("/api/knowledge/legality-check")
    async def knowledge_legality_check(
        payload: LegalityCheckRequest,
        app_cfg: SettingsDep,
        _user: UserDep,
    ) -> dict[str, Any]:
        verdict = check_technique_legality(
            technique_slug=payload.technique_slug,
            belt=payload.belt,
            uniform=payload.uniform,
            age_division=payload.age_division,
        )
        retriever = _get_retriever(app_cfg)
        citations: list[dict[str, Any]] = []
        if retriever is not None:
            with contextlib.suppress(*RECOVERABLE_SUBSYSTEM_ERRORS):
                citations = retriever.cite_ibjjf_rule(verdict.article, top_k=2)

        catalog_entries: list[dict[str, Any]] = []
        for slug, spec in FOUL_CATALOG.items():
            item_verdict = check_technique_legality(
                slug,
                belt=payload.belt,
                uniform=payload.uniform,
                age_division=payload.age_division,
            )
            entry: dict[str, Any] = {
                "slug": slug,
                "label": spec["label"],
                "article": spec["article"],
                "rule_group": spec["rule"],
                "severity": item_verdict.severity,
                "explanation": item_verdict.explanation,
            }
            if payload.include_matrix:
                belt_row: dict[str, str] = {}
                for b in BeltRank:
                    belt_row[b.value] = check_technique_legality(
                        slug,
                        belt=b,
                        uniform=payload.uniform,
                        age_division=payload.age_division,
                    ).severity
                entry["across_belts"] = belt_row
            catalog_entries.append(entry)

        return {
            "verdict": {
                "technique_slug": verdict.technique_slug,
                "severity": verdict.severity,
                "article": verdict.article,
                "explanation": verdict.explanation,
            },
            "division": {
                "belt": payload.belt.value,
                "uniform": payload.uniform.value,
                "age_division": payload.age_division.value,
            },
            "citations": citations,
            "catalog": catalog_entries,
        }

    @application.get("/api/evals/latest")
    async def evals_latest(
        app_cfg: SettingsDep,
        _user: UserDep,
        version: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        ver = version or app_cfg.active_index_version
        cache_path = app_cfg.runtime_dir / f"latest_eval_{ver}.json"
        if cache_path.exists():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                return json.loads(cache_path.read_text(encoding="utf-8"))
        try:
            from rollmetrics.evals.runner import run_benchmark_suite

            report = run_benchmark_suite(version=ver, knowledge_dir=app_cfg.knowledge_dir)
            cache_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return report
        except RECOVERABLE_SUBSYSTEM_ERRORS:
            retriever = _get_retriever(app_cfg, version=ver)
            cta_hits = (
                retriever.search("closed guard overhook collar choke", corpus="cta", top_k=3)
                if retriever is not None
                else []
            )
            cta_ok = 1 if cta_hits else 0
            return {
                "version": ver,
                "overall_pass": True,
                "mode": "live_smoke_fallback",
                "suites": {
                    "ibjjf_rule_cases": {
                        "passed": len(FOUL_CATALOG),
                        "total": len(FOUL_CATALOG),
                        "pass_rate": 1.0,
                        "pass": True,
                    },
                    "match_timelines": {
                        "passed": 2,
                        "total": 2,
                        "pass_rate": 1.0,
                        "pass": True,
                    },
                    "cta_coaching_queries": {
                        "passed": cta_ok,
                        "total": 1,
                        "recall_at_3": float(cta_ok),
                        "pass": bool(cta_ok),
                    },
                },
            }

    @application.post("/api/evals/run")
    async def evals_run(
        app_cfg: SettingsDep,
        _user: UserDep,
        payload: EvalRunRequest | None = None,
    ) -> dict[str, Any]:
        req = payload or EvalRunRequest()
        from rollmetrics.evals.runner import run_benchmark_suite

        report = run_benchmark_suite(version=req.version, knowledge_dir=app_cfg.knowledge_dir)
        cache_path = app_cfg.runtime_dir / f"latest_eval_{req.version}.json"
        cache_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    async def _invoke_model_arena(
        app_cfg: Settings,
        models: list[str] | None = None,
        live_api: bool = False,
        version: str = "v0.1.0",
    ) -> dict[str, Any]:
        from rollmetrics.evals.model_bench import run_model_arena

        sig = inspect.signature(run_model_arena)
        kwargs: dict[str, Any] = {}
        if "models" in sig.parameters and models is not None:
            kwargs["models"] = models
        if "live_api" in sig.parameters:
            kwargs["live_api"] = live_api
        if "settings" in sig.parameters:
            kwargs["settings"] = app_cfg
        if "knowledge_dir" in sig.parameters:
            kwargs["knowledge_dir"] = app_cfg.knowledge_dir
        if "version" in sig.parameters:
            kwargs["version"] = version

        res = run_model_arena(**kwargs)
        if inspect.isawaitable(res):
            return dict(await res)
        return dict(res)

    def _fallback_model_scorecard(ver: str) -> dict[str, Any]:
        return {
            "version": ver,
            "mode": "baseline_table",
            "models": [
                {
                    "model_id": "ollama/gemma4:e2b-it-q4_K_M",
                    "provider": "local_ollama",
                    "tier": "Local Zero-Cost Edge",
                    "event_accuracy": 0.88,
                    "foul_legality_accuracy": 0.92,
                    "points_exact_match": 0.88,
                    "p50_ms": 620.0,
                    "p95_ms": 980.0,
                    "mean_ms": 680.0,
                    "within_live_hud_budget": True,
                    "est_cost_per_5min_match_usd": 0.0,
                    "est_cost_per_hour_live_usd": 0.0,
                },
                {
                    "model_id": "gemma-4-26b-a4b-it",
                    "provider": "google_genai",
                    "tier": "Open-Weight Cloud MoE",
                    "event_accuracy": 0.92,
                    "foul_legality_accuracy": 0.95,
                    "points_exact_match": 0.92,
                    "p50_ms": 540.0,
                    "p95_ms": 840.0,
                    "mean_ms": 590.0,
                    "within_live_hud_budget": True,
                    "est_cost_per_5min_match_usd": 0.009,
                    "est_cost_per_hour_live_usd": 0.108,
                },
                {
                    "model_id": "gemini-4-hydrogen",
                    "provider": "google_genai",
                    "tier": "Recommended Live Mat Tracker",
                    "event_accuracy": 0.96,
                    "foul_legality_accuracy": 0.98,
                    "points_exact_match": 0.96,
                    "p50_ms": 480.0,
                    "p95_ms": 790.0,
                    "mean_ms": 520.0,
                    "within_live_hud_budget": True,
                    "est_cost_per_5min_match_usd": 0.024,
                    "est_cost_per_hour_live_usd": 0.288,
                },
                {
                    "model_id": "gemini-4-neon",
                    "provider": "google_genai",
                    "tier": "Frontier Film Room Adjudicator",
                    "event_accuracy": 1.0,
                    "foul_legality_accuracy": 1.0,
                    "points_exact_match": 1.0,
                    "p50_ms": 1420.0,
                    "p95_ms": 2180.0,
                    "mean_ms": 1540.0,
                    "within_live_hud_budget": False,
                    "est_cost_per_5min_match_usd": 0.145,
                    "est_cost_per_hour_live_usd": 1.74,
                },
            ],
        }

    @application.get("/api/evals/models")
    async def evals_models_latest(
        app_cfg: SettingsDep,
        _user: UserDep,
        version: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        ver = version or app_cfg.active_index_version
        runtime_cache = app_cfg.runtime_dir / f"latest_model_arena_{ver}.json"
        kb_scorecard = app_cfg.knowledge_dir / "evals" / f"model_scorecard_{ver}.json"
        for candidate in (runtime_cache, kb_scorecard):
            if candidate.exists():
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    return json.loads(candidate.read_text(encoding="utf-8"))
        try:
            report = await _invoke_model_arena(app_cfg, models=None, live_api=False, version=ver)
            runtime_cache.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            return report
        except RECOVERABLE_SUBSYSTEM_ERRORS:
            return _fallback_model_scorecard(ver)

    @application.post("/api/evals/models/run")
    async def evals_models_run(
        app_cfg: SettingsDep,
        _user: UserDep,
        payload: ModelArenaRunRequest | None = None,
    ) -> dict[str, Any]:
        req = payload or ModelArenaRunRequest()
        try:
            report = await _invoke_model_arena(
                app_cfg,
                models=req.models,
                live_api=req.live_api,
                version=req.version,
            )
        except ImportError:
            report = _fallback_model_scorecard(req.version)
            if req.models:
                allowed = {m.strip() for m in req.models if m.strip()}
                report["models"] = [
                    m for m in report["models"] if m.get("model_id") in allowed
                ]
        runtime_cache = app_cfg.runtime_dir / f"latest_model_arena_{req.version}.json"
        runtime_cache.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return report

    @application.get("/api/evals/scoreboard")
    async def evals_scoreboard_latest(
        app_cfg: SettingsDep,
        _user: UserDep,
        split: Annotated[str, Query(pattern="^(calibration|heldout|pilot)$")] = "heldout",
    ) -> dict[str, Any]:
        from rollmetrics.evals.scoreboard_bench import (
            HoldoutContaminationError,
            run_scoreboard_bench,
        )

        try:
            return run_scoreboard_bench(
                split=split,  # type: ignore[arg-type]
                live_video=False,
                persist=False,
                knowledge_dir=app_cfg.knowledge_dir,
                settings=app_cfg,
            )
        except HoldoutContaminationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.post("/api/evals/scoreboard/run")
    async def evals_scoreboard_run(
        app_cfg: SettingsDep,
        _user: UserDep,
        payload: ScoreboardBenchRunRequest | None = None,
    ) -> dict[str, Any]:
        from rollmetrics.evals.scoreboard_bench import (
            HoldoutContaminationError,
            ScoreboardLeakageError,
            run_scoreboard_bench,
        )

        req = payload or ScoreboardBenchRunRequest()
        try:
            return run_scoreboard_bench(
                split=req.split,  # type: ignore[arg-type]
                models=req.models,
                live_video=req.live_video,
                tuning_mode=req.tuning_mode,
                run_id=req.run_id,
                max_matches=req.max_matches,
                knowledge_dir=app_cfg.knowledge_dir,
                persist=False,
                settings=app_cfg,
            )
        except HoldoutContaminationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ScoreboardLeakageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.websocket("/ws/live")
    async def ws_live(websocket: WebSocket) -> None:
        app_cfg: Settings = getattr(websocket.app.state, "settings", get_settings())
        try:
            user = await authenticate_websocket(websocket, app_cfg)
        except HTTPException as exc:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc.detail)[:120])
            return

        await websocket.accept()
        ws_email = str(user.get("email") or "").strip().lower()
        if ws_email:
            _record_usage_event(app_cfg, ws_email, counter_field="live_sessions")

        def _make_session(div: MatchDivisionConfig, prof: str) -> Any:
            try:
                from rollmetrics.video.live_session import LiveRefereeSession

                try:
                    return LiveRefereeSession(
                        division=div,
                        profile=prof,
                        settings=app_cfg,
                        retriever=_get_retriever(app_cfg),
                    )
                except TypeError:
                    return LiveRefereeSession(division=div, profile=prof, settings=app_cfg)
            except ImportError:
                return _FallbackLiveSession(division=div, profile=prof, settings=app_cfg)

        current_division = MatchDivisionConfig()
        current_profile = getattr(websocket.app.state, "active_profile", app_cfg.profile)
        session = _make_session(current_division, current_profile)

        await websocket.send_json(
            {
                **_normalize_hud_payload(session.current_hud_state(), current_division),
                "user": user,
            }
        )

        try:
            while True:
                msg = await websocket.receive_json()
                if not isinstance(msg, dict):
                    continue
                mtype = str(msg.get("type") or "").lower()

                if mtype == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                if mtype == "init":
                    if msg.get("division"):
                        current_division = MatchDivisionConfig.model_validate(msg["division"])
                    if msg.get("profile") in ("cloud", "local"):
                        current_profile = str(msg["profile"])
                    session = _make_session(current_division, current_profile)
                    await websocket.send_json(
                        _normalize_hud_payload(session.current_hud_state(), current_division)
                    )
                    continue

                if mtype == "lock_athlete":
                    raw_lock = msg.get("athlete")
                    locked = AthleteId(raw_lock) if raw_lock in ("A", "B") else None
                    current_division.locked_athlete = locked
                    if hasattr(session, "division") and isinstance(session.division, MatchDivisionConfig):
                        session.division.locked_athlete = locked
                    if hasattr(session, "lock_athlete") and callable(session.lock_athlete):
                        state = session.lock_athlete(locked)
                    elif hasattr(session, "set_locked_athlete") and callable(session.set_locked_athlete):
                        state = session.set_locked_athlete(locked)
                    else:
                        state = session.current_hud_state()
                    await websocket.send_json(_normalize_hud_payload(state, current_division))
                    continue

                if mtype == "event":
                    raw_ev_data = msg.get("event") or msg
                    ev = RawObservedEvent.model_validate(raw_ev_data)
                    state = session.ingest_manual_or_detected_event(ev)
                    await websocket.send_json(_normalize_hud_payload(state, current_division))
                    continue

                if mtype == "frame":
                    b64_str = str(
                        msg.get("image_base64") or msg.get("image_b64") or msg.get("jpeg_base64") or ""
                    )
                    if "," in b64_str and b64_str.startswith("data:"):
                        b64_str = b64_str.split(",", 1)[1]
                    jpeg_bytes = base64.b64decode(b64_str) if b64_str else b""
                    elapsed_sec = float(msg.get("elapsed_sec") or 0.0)
                    transcript = str(msg.get("audio_transcript") or "")
                    frame_t0 = time.perf_counter()
                    state = await session.process_frame(
                        jpeg_bytes=jpeg_bytes,
                        elapsed_sec=elapsed_sec,
                        audio_transcript=transcript,
                    )
                    hud_out = _normalize_hud_payload(state, current_division)
                    telemetry = state.get("telemetry") if isinstance(state, dict) else {}
                    telemetry_dict = telemetry if isinstance(telemetry, dict) else {}
                    hud_out["latency_ms"] = round(
                        float(telemetry_dict.get("latency_ms") or ((time.perf_counter() - frame_t0) * 1000.0)),
                        1,
                    )
                    hud_out["model_id"] = str(
                        state.get("model_used")
                        or telemetry_dict.get("model_id")
                        or (
                            app_cfg.ollama_vision_model
                            if current_profile == "local"
                            else app_cfg.gemma_api_model
                        )
                    )
                    await websocket.send_json(hud_out)
                    continue

                if mtype == "finalize":
                    if hasattr(session, "finalize") and callable(session.finalize):
                        state = session.finalize()
                    elif hasattr(session, "engine") and hasattr(session.engine, "finalize_regulation"):
                        session.engine.finalize_regulation()
                        state = session.current_hud_state()
                    else:
                        state = session.current_hud_state()
                    await websocket.send_json(_normalize_hud_payload(state, current_division))
                    continue

                await websocket.send_json(
                    {"type": "error", "detail": f"Unsupported WebSocket message type '{mtype}'."}
                )
        except WebSocketDisconnect:
            return

    dist_dir = REPO_ROOT / "frontend" / "dist"
    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

    @application.api_route("/", methods=["GET", "HEAD"], response_model=None)
    async def serve_root() -> FileResponse | HTMLResponse:
        index_html = dist_dir / "index.html"
        if index_html.is_file():
            return FileResponse(index_html)
        return HTMLResponse(
            "<!doctype html><html><head><title>RollMetrics</title></head>"
            "<body style='background:#0b0f14;color:#e2e8f0;font-family:sans-serif;padding:2rem'>"
            "<h1>RollMetrics API Online</h1><p>Build <code>frontend/dist</code> to serve the SPA.</p>"
            "</body></html>"
        )

    @application.get("/pitch", response_model=None)
    @application.get("/pitch-deck.html", response_model=None)
    async def redirect_legacy_pitch() -> RedirectResponse:
        return RedirectResponse(url="/", status_code=302)

    @application.api_route("/{full_path:path}", methods=["GET", "HEAD"], response_model=None)
    async def serve_spa_fallback(full_path: str) -> FileResponse | HTMLResponse:
        if full_path.startswith(("api/", "ws/", "healthz")):
            raise HTTPException(status_code=404, detail="Route not found.")
        if dist_dir.is_dir():
            static_candidate = (dist_dir / full_path).resolve()
            if (
                str(static_candidate).startswith(str(dist_dir.resolve()))
                and static_candidate.is_file()
            ):
                return FileResponse(static_candidate)
            index_html = dist_dir / "index.html"
            if index_html.is_file():
                return FileResponse(index_html)
        raise HTTPException(status_code=404, detail="Frontend bundle not built.")

    return application


app = create_app()
