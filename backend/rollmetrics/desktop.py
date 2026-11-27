"""Offline Gemma 4 Desktop Mode: hardware readiness, local video folder scanner, and academy importer."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

import httpx

from rollmetrics.config import Settings, get_settings
from rollmetrics.knowledge.academies import add_academy_reference, upsert_academy
from rollmetrics.providers.ollama_local import select_best_ollama_vision_model
from rollmetrics.video.ingest import ALLOWED_VIDEO_EXTENSIONS, validate_video_file

LOCAL_NOTE_EXTENSIONS = {".md", ".txt", ".json", ".json3"}


def _detect_total_ram_gb() -> float | None:
    """Returns total system RAM in GB using OS-native APIs without external psutil dependency."""
    try:
        if platform.system() == "Darwin":
            res = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip().isdigit():
                return round(int(res.stdout.strip()) / (1024**3), 1)
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0 and page_size > 0:
                return round((pages * page_size) / (1024**3), 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _detect_cpu_label() -> str:
    try:
        if platform.system() == "Darwin":
            res = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.processor() or platform.machine() or "unknown"


def get_local_hardware_status(
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Reports local Ollama reachability, installed Gemma models, Apple Silicon/CPU/RAM, and offline readiness."""
    cfg = settings or get_settings()
    base_url = cfg.ollama_base_url.rstrip("/")
    ollama_reachable = False
    installed_models: list[str] = []
    gemma_models: list[str] = []
    error_detail = ""

    try:
        with httpx.Client(timeout=3.5, transport=transport) as client:
            resp = client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            payload = resp.json()
        raw_models = payload.get("models", []) or []
        installed_models = [
            str(m.get("name", "")).strip()
            for m in raw_models
            if isinstance(m, dict) and str(m.get("name", "")).strip()
        ]
        gemma_models = [m for m in installed_models if "gemma" in m.lower()]
        ollama_reachable = True
    except (httpx.HTTPError, OSError, ValueError) as exc:
        error_detail = str(exc)

    recommended_model = select_best_ollama_vision_model(installed_models, cfg.ollama_vision_model)
    ram_gb = _detect_total_ram_gb()
    cpu_label = _detect_cpu_label()
    arch = platform.machine()
    is_apple_silicon = platform.system() == "Darwin" and arch.lower() in ("arm64", "aarch64")
    offline_ready = bool(ollama_reachable and (gemma_models or installed_models))

    return {
        "offline_ready": offline_ready,
        "ollama_reachable": ollama_reachable,
        "ollama_base_url": base_url,
        "installed_models": installed_models,
        "gemma_models": gemma_models,
        "recommended_model": recommended_model,
        "os": platform.system(),
        "arch": arch,
        "is_apple_silicon": is_apple_silicon,
        "cpu": cpu_label,
        "cpu_cores": os.cpu_count() or 1,
        "ram_gb": ram_gb,
        "zero_cloud_keys_required": True,
        "error": error_detail,
    }


def scan_local_video_folder(
    folder_path: str | Path,
    settings: Settings | None = None,
    max_files: int = 100,
) -> dict[str, Any]:
    """Scans a local directory for BJJ video files (.mp4/.mov/.mkv), probing duration and analyzed status."""
    cfg = settings or get_settings()
    root = Path(folder_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Local video folder does not exist: {root}")

    matches_dir = cfg.runtime_dir / "matches"
    analyzed_stems: set[str] = set()
    if matches_dir.is_dir():
        for mf in matches_dir.glob("*.json"):
            analyzed_stems.add(mf.stem.lower())

    videos: list[dict[str, Any]] = []
    for candidate in sorted(root.rglob("*")):
        if len(videos) >= max_files:
            break
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
            continue

        size_bytes = candidate.stat().st_size
        duration_sec = 0.0
        width = 0
        height = 0
        codec = "unknown"
        valid = True
        probe_error = ""
        try:
            meta = validate_video_file(candidate)
            duration_sec = round(meta.duration_sec, 2)
            width = meta.width
            height = meta.height
            codec = meta.codec
        except (OSError, ValueError, FileNotFoundError) as exc:
            valid = False
            probe_error = str(exc)

        stem_low = candidate.stem.lower()
        analyzed = stem_low in analyzed_stems or any(stem_low in s for s in analyzed_stems)
        videos.append(
            {
                "filename": candidate.name,
                "path": str(candidate),
                "relative_path": str(candidate.relative_to(root)),
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / (1024 * 1024), 2),
                "duration_sec": duration_sec,
                "width": width,
                "height": height,
                "codec": codec,
                "valid": valid,
                "analyzed": analyzed,
                "error": probe_error,
            }
        )

    return {
        "folder": str(root),
        "count": len(videos),
        "videos": videos,
    }


def _extract_json3_transcript(raw_text: str) -> str:
    """Extracts plain transcript text from YouTube .json3 caption payloads."""
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return ""
    events = parsed.get("events", []) if isinstance(parsed, dict) else []
    chunks: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        for seg in ev.get("segs", []) or []:
            if isinstance(seg, dict) and seg.get("utf8"):
                piece = str(seg["utf8"]).replace("\n", " ").strip()
                if piece:
                    chunks.append(piece)
    return " ".join(chunks).strip()


def import_local_academy_folder(
    folder_path: str | Path,
    academy_id: str,
    academy_name: str = "",
    settings: Settings | None = None,
    max_files: int = 200,
) -> dict[str, Any]:
    """Imports local .md/.txt/.json3 notes and reference .mp4 metadata into an isolated academy library."""
    cfg = settings or get_settings()
    root = Path(folder_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Local academy folder does not exist: {root}")

    acad_record = upsert_academy(
        cfg,
        academy_name=academy_name or academy_id.replace("-", " ").replace("_", " ").title(),
        academy_id=academy_id,
    )
    resolved_id = acad_record["academy_id"]
    resolved_name = acad_record["academy_name"]

    imported: list[dict[str, Any]] = []
    skipped: list[str] = []

    for fpath in sorted(root.rglob("*")):
        if len(imported) >= max_files:
            break
        if not fpath.is_file():
            continue
        suf = fpath.suffix.lower()
        title = fpath.stem.replace("_", " ").replace("-", " ").strip().title()

        if suf in LOCAL_NOTE_EXTENSIONS:
            raw = fpath.read_text(encoding="utf-8", errors="ignore").strip()
            if not raw:
                skipped.append(fpath.name)
                continue
            if suf == ".json3":
                body = _extract_json3_transcript(raw)
                ref_type = "video"
            elif suf == ".json":
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        title = str(obj.get("title") or title)
                        body = str(obj.get("text") or obj.get("transcript") or obj.get("summary") or raw)
                    else:
                        body = raw
                except json.JSONDecodeError:
                    body = raw
                ref_type = "note"
            else:
                body = raw
                ref_type = "note"

            if not body.strip():
                skipped.append(fpath.name)
                continue
            doc = add_academy_reference(
                cfg,
                resolved_id,
                title=title,
                text=body[:3500],
                position=title,
                belt="white",
                source_url=f"file://{fpath}",
                reference_type=ref_type,
                instructor=resolved_name,
                tags=["offline-import", suf.lstrip(".")],
            )
            imported.append({"doc_id": doc.doc_id, "title": doc.title, "type": ref_type, "file": fpath.name})
        elif suf in ALLOWED_VIDEO_EXTENSIONS:
            duration_label = ""
            try:
                meta = validate_video_file(fpath)
                duration_label = f" ({meta.duration_sec:.0f}s, {meta.width}x{meta.height})"
            except (OSError, ValueError, FileNotFoundError):
                duration_label = ""
            doc = add_academy_reference(
                cfg,
                resolved_id,
                title=f"Reference Video: {title}",
                text=f"Local academy reference video {fpath.name}{duration_label} stored at {fpath}.",
                position=title,
                belt="white",
                source_url=f"file://{fpath}",
                reference_type="video",
                instructor=resolved_name,
                tags=["offline-import", "local-video"],
            )
            imported.append({"doc_id": doc.doc_id, "title": doc.title, "type": "video", "file": fpath.name})

    return {
        "academy_id": resolved_id,
        "academy_name": resolved_name,
        "folder": str(root),
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "imported": imported,
        "zero_cloud_keys_required": True,
    }
