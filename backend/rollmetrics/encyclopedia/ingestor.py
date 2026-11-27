"""Tier 1A stream-download -> mask -> upload -> purge worker for GCS heavy vault."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from rollmetrics.encyclopedia.gcs_vault import GCSVault
from rollmetrics.encyclopedia.schema import MatchRecord
from rollmetrics.encyclopedia.store import EncyclopediaStore
from rollmetrics.evals.scoreboard_bench.masking import create_masked_video_clip
from rollmetrics.evals.scoreboard_bench.schema import OVERLAY_PRESETS


def _media_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
    return env


def _resolve_tool(name: str) -> str:
    binary = shutil.which(name, path=_media_env()["PATH"])
    if not binary:
        raise FileNotFoundError(f"{name} not found in PATH")
    return binary


def download_match_video(url: str, output_mp4: Path, *, max_height: int = 720, timeout: int = 600) -> Path:
    """Downloads a single YouTube video at <=720p MP4 into a temporary local path."""
    ytdlp = _resolve_tool("yt-dlp")
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    fmt = f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={max_height}][ext=mp4]/best[ext=mp4]/best"
    subprocess.run(
        [
            ytdlp,
            "-f",
            fmt,
            "--merge-output-format",
            "mp4",
            "--no-playlist",
            "--no-warnings",
            "-o",
            str(output_mp4),
            url,
        ],
        env=_media_env(),
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    if not output_mp4.exists():
        raise FileNotFoundError(f"yt-dlp finished without creating {output_mp4}")
    return output_mp4


def ingest_match_to_gcs(
    match: MatchRecord,
    *,
    store: EncyclopediaStore,
    vault: GCSVault,
    scratch_root: Path,
    render_masked: bool = True,
    max_height: int = 720,
) -> dict[str, Any]:
    """Downloads one match, renders zero-leakage masked clip if applicable, uploads to GCS, and purges local files."""
    work_dir = scratch_root / match.match_id
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_mp4 = work_dir / "raw.mp4"
    masked_mp4 = work_dir / "masked.mp4"
    meta_json = work_dir / "match.json"

    result: dict[str, Any] = {
        "match_id": match.match_id,
        "video_id": match.video_id,
        "status": "ok",
        "assets": {},
    }

    try:
        download_match_video(
            f"https://www.youtube.com/watch?v={match.video_id}",
            raw_mp4,
            max_height=max_height,
        )
        raw_uri, raw_size, raw_sha = vault.upload_file(raw_mp4, f"clips/{match.match_id}/raw.mp4")
        store.record_gcs_asset(match.match_id, "raw_video", raw_uri, raw_size, raw_sha)
        result["assets"]["raw_video"] = {"uri": raw_uri, "size_bytes": raw_size, "sha256": raw_sha}

        if render_masked and match.overlay_preset in OVERLAY_PRESETS:
            end_sec = match.end_sec if match.end_sec > match.start_sec else max(match.duration_sec, match.start_sec + 60.0)
            audit = create_masked_video_clip(
                src_video=raw_mp4,
                start_sec=match.start_sec,
                end_sec=end_sec,
                dst_video=masked_mp4,
                preset_name=match.overlay_preset,
            )
            masked_uri, masked_size, masked_sha = vault.upload_file(masked_mp4, f"clips/{match.match_id}/masked.mp4")
            store.record_gcs_asset(match.match_id, "masked_video", masked_uri, masked_size, masked_sha)
            result["assets"]["masked_video"] = {
                "uri": masked_uri,
                "size_bytes": masked_size,
                "sha256": masked_sha,
                "leakage_audit": audit,
            }

        meta_json.write_text(match.model_dump_json(indent=2) + "\n", encoding="utf-8")
        meta_uri, meta_size, meta_sha = vault.upload_file(meta_json, f"clips/{match.match_id}/match.json")
        store.record_gcs_asset(match.match_id, "metadata_json", meta_uri, meta_size, meta_sha)
        result["assets"]["metadata_json"] = {"uri": meta_uri, "size_bytes": meta_size, "sha256": meta_sha}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return result
