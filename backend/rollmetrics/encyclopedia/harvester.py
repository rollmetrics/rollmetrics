"""Tier 0 YouTube mega-catalog harvester, IBJJF title/chapter parser, and deduplicator."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from rollmetrics.encyclopedia.schema import (
    AgeDivisionValue,
    BeltValue,
    FootageTypeValue,
    MatchRecord,
    UniformValue,
    VideoKindValue,
    VideoRecord,
)
from rollmetrics.encyclopedia.store import EncyclopediaStore

DEFAULT_HARVEST_SOURCES: dict[str, list[dict[str, str]]] = {
    "ibjjf": [
        {"name": "ibjjf_videos", "url": "https://www.youtube.com/@ibjjf/videos", "source_label": "IBJJF"},
        {"name": "ibjjf_streams", "url": "https://www.youtube.com/@ibjjf/streams", "source_label": "IBJJF"},
    ],
    "flograppling": [
        {"name": "flo_videos", "url": "https://www.youtube.com/@FloGrappling/videos", "source_label": "FloGrappling"},
    ],
    "colored_belts": [
        {"name": "cb_white", "url": "ytsearch120:IBJJF white belt full match", "source_label": "YouTube_ColoredBelt"},
        {"name": "cb_blue", "url": "ytsearch120:IBJJF blue belt full match", "source_label": "YouTube_ColoredBelt"},
        {"name": "cb_purple", "url": "ytsearch120:IBJJF purple belt full match", "source_label": "YouTube_ColoredBelt"},
        {"name": "cb_brown", "url": "ytsearch120:IBJJF brown belt full match", "source_label": "YouTube_ColoredBelt"},
        {"name": "cb_nogi", "url": "ytsearch100:IBJJF no gi blue purple brown belt full match", "source_label": "YouTube_ColoredBelt"},
        {"name": "cb_master", "url": "ytsearch100:IBJJF master full match", "source_label": "YouTube_ColoredBelt"},
    ],
}

_NON_IBJJF_PATTERNS = re.compile(
    r"\b("
    r"polaris|wno|who'?s number one|adcc|cji|craig jones invitational|quintet|ebi|"
    r"ufc fight pass|kasai|fight to win|f2w|one championship|mma|bellator|pfl|"
    r"podcast|the flograppling show|episode \d+|ep\.?\s*\d+|vlog|documentary|"
    r"press conference|weigh[- ]?in|interview|trailer|teaser|preview|recap|"
    r"highlight|highlights|top \d+|best submissions|breakdown|seminar|tutorial|"
    r"shows how to|technique|instructional|training|sparring|rolling at"
    r")\b",
    re.IGNORECASE,
)

_IBJJF_SIGNAL_PATTERNS = re.compile(
    r"\b("
    r"ibjjf|world championship|worlds|pan championship|pans|pan ams|european|euros|"
    r"brasileiro|brazilian nationals|the crown|grand prix|jiu[- ]?jitsu con|"
    r"master world|american nationals|rio open|sao paulo open|new york open|"
    r"chicago open|miami open|las vegas open|london open|paris open|rome open|"
    r"tokyo open|dallas open|atlanta open|houston open|boston open|seattle open|"
    r"san diego open|los angeles open|new jersey.*io|international open"
    r")\b",
    re.IGNORECASE,
)

_VS_SPLIT_RE = re.compile(r"\s+(?:vs\.?|v\.?|x)\s+", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20[0-2]\d)\b")
_MAT_STREAM_RE = re.compile(r"\b(?:mat\s*\d+|day\s*\d+|livestream|full\s+stream)\b", re.IGNORECASE)
_COMPILATION_RE = re.compile(
    r"\b(?:every match|all matches|full run|road to gold|all \d+ matches|match compilation)\b",
    re.IGNORECASE,
)
_ROUND_RE = re.compile(
    r"\b(final|finals|semi[- ]?final|quarter[- ]?final|round of \d+|opening round|absolute final|open class)\b",
    re.IGNORECASE,
)
_WEIGHT_RE = re.compile(
    r"\b(rooster|light[- ]?feather|feather|light|middle|medium[- ]?heavy|heavy|super[- ]?heavy|ultra[- ]?heavy|open class|absolute)\b",
    re.IGNORECASE,
)


def _ytdlp_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
    return env


def _resolve_ytdlp() -> str:
    binary = shutil.which("yt-dlp", path=_ytdlp_env()["PATH"])
    if not binary:
        raise FileNotFoundError("yt-dlp not found in PATH")
    return binary


def load_scoreboard_v1_video_ids(knowledge_dir: Path) -> set[str]:
    """Loads existing video_ids from scoreboard_v1/matches.jsonl for overlap tagging."""
    path = knowledge_dir / "evals" / "scoreboard_v1" / "matches.jsonl"
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if vid := row.get("video_id"):
                ids.add(str(vid))
        except json.JSONDecodeError:
            continue
    return ids


def parse_belt(text: str, *, source_label: str = "") -> BeltValue:
    low = text.lower()
    if re.search(r"\b(white belt|faixa branca|branca)\b", low):
        return "white"
    if re.search(r"\b(blue belt|faixa azul)\b", low):
        return "blue"
    if re.search(r"\b(purple belt|faixa roxa)\b", low):
        return "purple"
    if re.search(r"\b(brown belt|faixa marrom)\b", low):
        return "brown"
    if re.search(r"\b(black belt|faixa preta)\b", low):
        return "black"
    if source_label in {"FloGrappling", "IBJJF"} and re.search(
        r"\b(the crown|grand prix|world championship|worlds|pans|euros|brasileiro)\b",
        low,
    ):
        return "black"
    return "unknown"


def parse_uniform(text: str, *, source_label: str = "") -> UniformValue:
    low = text.lower()
    if re.search(r"\b(no[- ]?gi|nogi|sem kimono)\b", low):
        return "nogi"
    if re.search(r"\b(gi|kimono|the crown|brasileiro|pan championship|world championship|european)\b", low):
        return "gi"
    if source_label == "IBJJF" and re.search(r"\b(open|io|championship|worlds|pans|euros)\b", low):
        return "gi"
    return "unknown"


def parse_age_division(text: str) -> AgeDivisionValue:
    low = text.lower()
    if re.search(r"\b(kids|teen|pee[- ]?wee|junior|infantil)\b", low):
        return "kids"
    if re.search(r"\b(juvenile|juvenil)\b", low):
        return "juvenile"
    if re.search(r"\b(master\s*[1-7]?|masters)\b", low):
        return "master"
    if re.search(r"\b(adult|adulto|the crown|grand prix)\b", low):
        return "adult"
    return "unknown"


def parse_athletes(title: str) -> tuple[str, str]:
    head = re.split(r"\s*[|/]\s*", title.strip())[0]
    head = re.sub(r"^\s*(?:full match\s*[-:]?\s*)", "", head, flags=re.IGNORECASE).strip()
    parts = _VS_SPLIT_RE.split(head, maxsplit=1)
    if len(parts) == 2:
        a = re.sub(r"\s*\([^)]*\)\s*", " ", parts[0]).strip(" -:|")
        b = re.sub(r"\s*\([^)]*\)\s*", " ", parts[1]).strip(" -:|")
        if a and b and len(a) <= 64 and len(b) <= 64:
            return a, b
    return "Unknown", "Unknown"


def parse_event_name(title: str) -> str:
    segments = [s.strip() for s in re.split(r"\s*[|/]\s*", title) if s.strip()]
    for seg in reversed(segments):
        if _IBJJF_SIGNAL_PATTERNS.search(seg) or re.search(r"\bopen\b", seg, re.IGNORECASE):
            return seg
    for seg in reversed(segments):
        if "full match" not in seg.lower() and not _VS_SPLIT_RE.search(seg):
            return seg
    return "IBJJF Tournament"


def classify_video(title: str, duration_sec: float, source_label: str) -> VideoKindValue:
    if _NON_IBJJF_PATTERNS.search(title):
        return "non_match"
    if source_label == "FloGrappling" and not _IBJJF_SIGNAL_PATTERNS.search(title):
        return "non_match"
    if source_label == "YouTube_ColoredBelt" and not _IBJJF_SIGNAL_PATTERNS.search(title):
        return "non_match"
    if duration_sec > 0 and duration_sec < 45:
        return "non_match"
    if duration_sec >= 3600 or _MAT_STREAM_RE.search(title):
        return "full_mat_stream"
    if _COMPILATION_RE.search(title):
        return "multi_match_compilation"
    if _VS_SPLIT_RE.search(title) or "full match" in title.lower():
        return "standalone_match"
    if source_label == "IBJJF" and duration_sec >= 90 and _IBJJF_SIGNAL_PATTERNS.search(title):
        return "standalone_match"
    return "non_match"


def infer_footage_and_preset(source_label: str, channel: str) -> tuple[FootageTypeValue, str]:
    ch = f"{source_label} {channel}".lower()
    if "flograppling" in ch:
        return "broadcast_scoreboard", "flograppling_lower_bar"
    if "ibjjf" in ch:
        return "broadcast_scoreboard", "ibjjf_top_left_bug"
    return "matside_raw", ""


def _slugify(value: str, max_len: int = 24) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:max_len] or "ibjjf"


def video_entry_to_records(
    raw: dict[str, Any],
    *,
    source_label: str,
    harvest_source: str,
    v1_video_ids: set[str],
) -> tuple[VideoRecord | None, list[MatchRecord]]:
    video_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not video_id or not title:
        return None, []

    duration = float(raw.get("duration") or 0.0)
    channel = str(raw.get("channel") or raw.get("uploader") or source_label).strip()
    channel_id = str(raw.get("channel_id") or raw.get("uploader_id") or "").strip()
    url = str(raw.get("webpage_url") or raw.get("url") or f"https://www.youtube.com/watch?v={video_id}")
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={video_id}"
    upload_date = str(raw.get("upload_date") or "").strip()
    view_count = int(raw.get("view_count") or 0)
    description = str(raw.get("description") or "").strip()
    chapters = raw.get("chapters") if isinstance(raw.get("chapters"), list) else []

    video_kind = classify_video(title, duration, source_label)
    video_rec = VideoRecord(
        video_id=video_id,
        channel=channel,
        channel_id=channel_id,
        url=url,
        title=title,
        description=description[:2000],
        duration_sec=duration,
        upload_date=upload_date,
        view_count=view_count,
        video_kind=video_kind,
        chapters=chapters,
        harvest_source=harvest_source,
    )

    if video_kind not in {"standalone_match", "multi_match_compilation"}:
        return video_rec, []

    belt = parse_belt(title, source_label=source_label)
    uniform = parse_uniform(title, source_label=source_label)
    age_div = parse_age_division(title)
    event_name = parse_event_name(title)
    year_match = _YEAR_RE.search(title)
    event_year = int(year_match.group(1)) if year_match else (int(upload_date[:4]) if len(upload_date) >= 4 and upload_date[:4].isdigit() else None)
    round_match = _ROUND_RE.search(title)
    round_name = round_match.group(1) if round_match else ""
    weight_match = _WEIGHT_RE.search(title)
    weight_class = weight_match.group(1) if weight_match else ""
    footage_type, overlay_preset = infer_footage_and_preset(source_label, channel)
    event_group = _slugify(f"{event_year or 'na'}_{event_name}", max_len=40)
    in_v1 = video_id in v1_video_ids
    prefix = _slugify(source_label, max_len=12)

    matches: list[MatchRecord] = []
    if video_kind == "multi_match_compilation" and chapters:
        for idx, ch in enumerate(chapters, start=1):
            ch_title = str(ch.get("title") or "").strip()
            start_s = float(ch.get("start_time") or 0.0)
            end_s = float(ch.get("end_time") or 0.0)
            if end_s - start_s < 30.0:
                continue
            a, b = parse_athletes(ch_title)
            matches.append(
                MatchRecord(
                    match_id=f"{prefix}-{video_id}-m{idx:02d}",
                    video_id=video_id,
                    url=f"https://www.youtube.com/watch?v={video_id}&t={int(start_s)}s",
                    start_sec=start_s,
                    end_sec=end_s,
                    duration_sec=round(end_s - start_s, 2),
                    athlete_a=a,
                    athlete_b=b,
                    event_name=event_name,
                    event_year=event_year,
                    event_group=event_group,
                    division=ch_title,
                    weight_class=weight_class,
                    round_name=round_name,
                    belt=belt,
                    uniform=uniform,
                    age_division=age_div,
                    source=source_label,
                    footage_type=footage_type,
                    overlay_preset=overlay_preset,
                    in_scoreboard_v1=in_v1,
                    title_raw=f"{title} :: {ch_title}",
                )
            )
    else:
        a, b = parse_athletes(title)
        matches.append(
            MatchRecord(
                match_id=f"{prefix}-{video_id}",
                video_id=video_id,
                url=url,
                start_sec=0.0,
                end_sec=duration,
                duration_sec=duration,
                athlete_a=a,
                athlete_b=b,
                event_name=event_name,
                event_year=event_year,
                event_group=event_group,
                division=f"{age_div} {belt} {weight_class}".strip(),
                weight_class=weight_class,
                round_name=round_name,
                belt=belt,
                uniform=uniform,
                age_division=age_div,
                source=source_label,
                footage_type=footage_type,
                overlay_preset=overlay_preset,
                in_scoreboard_v1=in_v1,
                title_raw=title,
            )
        )

    return video_rec, matches


def fetch_flat_playlist(
    url: str,
    *,
    max_items: int | None = None,
    timeout: int = 300,
) -> list[dict[str, Any]]:
    """Runs `yt-dlp --flat-playlist --dump-json` for fast, zero-download catalog harvest."""
    ytdlp = _resolve_ytdlp()
    cmd = [
        ytdlp,
        "--flat-playlist",
        "--dump-json",
        "--ignore-errors",
        "--no-warnings",
    ]
    if max_items is not None and max_items > 0:
        cmd.extend(["--playlist-end", str(max_items)])
    cmd.append(url)

    proc = subprocess.run(
        cmd,
        env=_ytdlp_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    entries: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def harvest_sources(
    store: EncyclopediaStore,
    *,
    sources: Iterable[str] = ("ibjjf", "flograppling", "colored_belts"),
    knowledge_dir: Path | None = None,
    max_per_feed: int | None = None,
) -> dict[str, Any]:
    """Harvests selected YouTube feeds into SQLite + FTS5 catalog and returns summary stats."""
    v1_ids = load_scoreboard_v1_video_ids(knowledge_dir) if knowledge_dir else set()
    feed_reports: list[dict[str, Any]] = []
    total_videos = 0
    total_matches = 0

    for group in sources:
        feeds = DEFAULT_HARVEST_SOURCES.get(group, [])
        for feed in feeds:
            entries = fetch_flat_playlist(feed["url"], max_items=max_per_feed)
            feed_videos = 0
            feed_matches = 0
            for raw in entries:
                vid, matches = video_entry_to_records(
                    raw,
                    source_label=feed["source_label"],
                    harvest_source=feed["name"],
                    v1_video_ids=v1_ids,
                )
                if vid is None:
                    continue
                store.upsert_video(vid)
                feed_videos += 1
                for m in matches:
                    store.upsert_match(m, commit=False)
                    feed_matches += 1
            store.commit()
            total_videos += feed_videos
            total_matches += feed_matches
            feed_reports.append(
                {
                    "feed": feed["name"],
                    "source": feed["source_label"],
                    "raw_entries": len(entries),
                    "videos_saved": feed_videos,
                    "matches_parsed": feed_matches,
                }
            )

    return {
        "feeds": feed_reports,
        "harvested_videos": total_videos,
        "harvested_matches": total_matches,
        "catalog_stats": store.stats(),
    }
