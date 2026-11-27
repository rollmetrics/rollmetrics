"""Tier 1A stream-download -> mask -> local OCR -> GCS upload -> purge worker."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rollmetrics.encyclopedia.gcs_vault import GCSVault
from rollmetrics.encyclopedia.schema import MatchRecord
from rollmetrics.encyclopedia.store import EncyclopediaStore
from rollmetrics.evals.scoreboard_bench.labeler import label_match_from_scoreboard_crops_local_ocr
from rollmetrics.evals.scoreboard_bench.masking import create_masked_video_clip
from rollmetrics.evals.scoreboard_bench.schema import OVERLAY_PRESETS, MatchManifestEntry

logger = logging.getLogger(__name__)


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
    fmt = f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best"
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
            "--",
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


def _match_to_manifest_entry(match: MatchRecord) -> MatchManifestEntry:
    belt = match.belt if match.belt in {"white", "blue", "purple", "brown", "black"} else "black"
    uniform = match.uniform if match.uniform in {"gi", "nogi"} else "gi"
    age = match.age_division if match.age_division in {"juvenile", "adult", "master"} else "adult"
    end_sec = match.end_sec if match.end_sec > match.start_sec else max(match.duration_sec, match.start_sec + 60.0)
    return MatchManifestEntry(
        match_id=match.match_id,
        video_id=match.video_id,
        url=match.url,
        start_sec=match.start_sec,
        end_sec=end_sec,
        athlete_a=match.athlete_a or "Athlete A",
        athlete_b=match.athlete_b or "Athlete B",
        division=match.division or match.title_raw or "IBJJF Division",
        belt=belt,  # type: ignore[arg-type]
        uniform=uniform,  # type: ignore[arg-type]
        age_division=age,  # type: ignore[arg-type]
        source=match.source or "IBJJF",
        event_name=match.event_name or "IBJJF Tournament",
        event_group=match.event_group or match.video_id,
        overlay_preset=match.overlay_preset or "flograppling_lower_bar",
    )


def ingest_match_to_gcs(
    match: MatchRecord,
    *,
    store: EncyclopediaStore,
    vault: GCSVault,
    scratch_root: Path,
    render_masked: bool = True,
    run_local_ocr: bool = True,
    max_height: int = 720,
    db_lock: threading.Lock | None = None,
) -> dict[str, Any]:
    """Downloads one match, masks + OCRs if broadcast scoreboard, uploads to GCS, and purges local files."""
    work_dir = scratch_root / match.match_id
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_mp4 = work_dir / "raw.mp4"
    masked_mp4 = work_dir / "masked.mp4"
    meta_json = work_dir / "match.json"
    ocr_json = work_dir / "ocr_label.json"

    result: dict[str, Any] = {
        "match_id": match.match_id,
        "video_id": match.video_id,
        "footage_type": match.footage_type,
        "status": "ok",
        "assets": {},
    }

    def _record_asset(asset_type: str, uri: str, size_b: int, sha: str) -> None:
        if db_lock:
            with db_lock:
                store.record_gcs_asset(match.match_id, asset_type, uri, size_b, sha)
        else:
            store.record_gcs_asset(match.match_id, asset_type, uri, size_b, sha)

    try:
        download_match_video(
            f"https://www.youtube.com/watch?v={match.video_id}",
            raw_mp4,
            max_height=max_height,
        )
        raw_uri, raw_size, raw_sha = vault.upload_file(raw_mp4, f"clips/{match.match_id}/raw.mp4")
        _record_asset("raw_video", raw_uri, raw_size, raw_sha)
        result["assets"]["raw_video"] = {"uri": raw_uri, "size_bytes": raw_size, "sha256": raw_sha}

        is_broadcast = match.footage_type == "broadcast_scoreboard" and match.overlay_preset in OVERLAY_PRESETS
        if render_masked and is_broadcast:
            end_sec = match.end_sec if match.end_sec > match.start_sec else max(match.duration_sec, match.start_sec + 60.0)
            audit = create_masked_video_clip(
                src_video=raw_mp4,
                start_sec=match.start_sec,
                end_sec=end_sec,
                dst_video=masked_mp4,
                preset_name=match.overlay_preset,
            )
            masked_uri, masked_size, masked_sha = vault.upload_file(masked_mp4, f"clips/{match.match_id}/masked.mp4")
            _record_asset("masked_video", masked_uri, masked_size, masked_sha)
            result["assets"]["masked_video"] = {
                "uri": masked_uri,
                "size_bytes": masked_size,
                "sha256": masked_sha,
                "leakage_audit": audit,
            }

        if run_local_ocr and is_broadcast:
            try:
                entry = _match_to_manifest_entry(match)
                gt_label = label_match_from_scoreboard_crops_local_ocr(
                    entry=entry,
                    video_path=raw_mp4,
                    sample_every_sec=2.0,
                )
                ocr_payload = gt_label.model_dump_json(indent=2)
                ocr_json.write_text(ocr_payload + "\n", encoding="utf-8")
                ocr_uri, ocr_size, ocr_sha = vault.upload_file(ocr_json, f"clips/{match.match_id}/ocr_label.json")
                _record_asset("ocr_label_json", ocr_uri, ocr_size, ocr_sha)
                if db_lock:
                    with db_lock:
                        store.upsert_ocr_label(
                            match.match_id,
                            labeler_id=gt_label.labeler_id,
                            winner=gt_label.winner,
                            method=gt_label.method,
                            event_count=len(gt_label.events),
                            payload_json=ocr_payload,
                        )
                else:
                    store.upsert_ocr_label(
                        match.match_id,
                        labeler_id=gt_label.labeler_id,
                        winner=gt_label.winner,
                        method=gt_label.method,
                        event_count=len(gt_label.events),
                        payload_json=ocr_payload,
                    )
                result["ocr_summary"] = {
                    "winner": gt_label.winner,
                    "method": gt_label.method,
                    "events": len(gt_label.events),
                    "final_a": gt_label.final_score_a.model_dump(),
                    "final_b": gt_label.final_score_b.model_dump(),
                }
            except Exception as ocr_exc:  # noqa: BLE001
                result["ocr_error"] = str(ocr_exc)

        meta_json.write_text(match.model_dump_json(indent=2) + "\n", encoding="utf-8")
        meta_uri, meta_size, meta_sha = vault.upload_file(meta_json, f"clips/{match.match_id}/match.json")
        _record_asset("metadata_json", meta_uri, meta_size, meta_sha)
        result["assets"]["metadata_json"] = {"uri": meta_uri, "size_bytes": meta_size, "sha256": meta_sha}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return result


def ingest_cohort_parallel(
    cohort: list[MatchRecord],
    *,
    db_path: Path,
    vault: GCSVault,
    scratch_root: Path,
    workers: int = 3,
    render_masked: bool = True,
    run_local_ocr: bool = True,
    backup_every: int = 25,
) -> list[dict[str, Any]]:
    """Ingests a cohort into GCS in parallel using thread-safe SQLite access."""
    results: list[dict[str, Any]] = []
    db_lock = threading.Lock()
    completed = 0

    def _worker(m: MatchRecord) -> dict[str, Any]:
        with EncyclopediaStore(db_path) as local_store:
            try:
                return ingest_match_to_gcs(
                    m,
                    store=local_store,
                    vault=vault,
                    scratch_root=scratch_root,
                    render_masked=render_masked,
                    run_local_ocr=run_local_ocr,
                    db_lock=db_lock,
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "match_id": m.match_id,
                    "video_id": m.video_id,
                    "footage_type": m.footage_type,
                    "status": "error",
                    "error": str(exc),
                }

    max_workers = max(1, min(workers, len(cohort) or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_worker, m): m for m in cohort}
        for fut in as_completed(future_map):
            res = fut.result()
            results.append(res)
            completed += 1
            if backup_every > 0 and completed % backup_every == 0:
                with db_lock:
                    try:
                        vault.backup_catalog(db_path)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Periodic catalog backup failed: %s", exc)

    return results
