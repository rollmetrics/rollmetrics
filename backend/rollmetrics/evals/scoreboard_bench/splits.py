"""Deterministic event/source dataset splits and strict anti-holdout-contamination guard."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from rollmetrics.evals.scoreboard_bench.schema import (
    HoldoutContaminationError,
    MatchManifestEntry,
    ScoreboardSplitManifest,
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def compute_splits_content_hash(calibration_ids: list[str], heldout_ids: list[str]) -> str:
    canonical = json.dumps(
        {
            "benchmark_id": "rollmetrics-scoreboard-v1",
            "calibration": sorted(calibration_ids),
            "heldout": sorted(heldout_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(canonical)


def build_deterministic_splits(
    entries: list[MatchManifestEntry],
    matches_sha256: str = "",
    calibration_ratio: float = 0.30,
) -> ScoreboardSplitManifest:
    """Assigns entire video/event sources deterministically to calibration (~30%) vs heldout (~70%)."""
    if not entries:
        raise ValueError("Cannot build splits for empty MatchManifestEntry list.")

    groups: dict[str, list[MatchManifestEntry]] = defaultdict(list)
    for entry in entries:
        groups[entry.split_group_key()].append(entry)

    # Order groups deterministically by SHA256 digest of group key
    ordered_keys = sorted(
        groups.keys(),
        key=lambda gk: hashlib.sha256(f"rollmetrics-scoreboard-v1::{gk}".encode()).hexdigest(),
    )

    total = len(entries)
    target_cal = max(1, round(total * calibration_ratio))

    cal_entries: list[MatchManifestEntry] = []
    held_entries: list[MatchManifestEntry] = []

    for gk in ordered_keys:
        grp = groups[gk]
        # Keep assigning whole source groups to calibration until closest to target_cal,
        # while always leaving at least 1 group in heldout when >=2 groups exist.
        remaining_groups = len(ordered_keys) - (
            len({e.split_group_key() for e in cal_entries})
            + len({e.split_group_key() for e in held_entries})
        )
        if (
            len(cal_entries) < target_cal
            and (len(ordered_keys) == 1 or remaining_groups > 1 or not cal_entries)
            and abs((len(cal_entries) + len(grp)) - target_cal) <= abs(len(cal_entries) - target_cal) + 2
        ):
            cal_entries.extend(grp)
        else:
            held_entries.extend(grp)

    # Guarantee non-empty heldout when >=2 source groups exist
    if not held_entries and len(ordered_keys) >= 2:
        last_gk = ordered_keys[-1]
        moved = [e for e in cal_entries if e.split_group_key() == last_gk]
        cal_entries = [e for e in cal_entries if e.split_group_key() != last_gk]
        held_entries.extend(moved)

    cal_ids = sorted(e.match_id for e in cal_entries)
    held_ids = sorted(e.match_id for e in held_entries)
    cal_vids = sorted({e.video_id for e in cal_entries})
    held_vids = sorted({e.video_id for e in held_entries})

    overlap = set(cal_vids).intersection(held_vids)
    if overlap:
        raise RuntimeError(f"Source leakage across splits for video_ids: {sorted(overlap)}")

    content_sha = compute_splits_content_hash(cal_ids, held_ids)
    return ScoreboardSplitManifest(
        calibration_ratio_target=calibration_ratio,
        total_matches=total,
        calibration_count=len(cal_ids),
        heldout_count=len(held_ids),
        calibration_match_ids=cal_ids,
        heldout_match_ids=held_ids,
        calibration_video_ids=cal_vids,
        heldout_video_ids=held_vids,
        matches_sha256=matches_sha256,
        splits_content_sha256=content_sha,
    )


def write_splits_manifest(splits_path: Path, manifest: ScoreboardSplitManifest) -> None:
    splits_path.parent.mkdir(parents=True, exist_ok=True)
    splits_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_and_verify_splits(splits_path: Path) -> ScoreboardSplitManifest:
    if not splits_path.exists():
        raise FileNotFoundError(f"Missing splits.json at {splits_path}")
    manifest = ScoreboardSplitManifest.model_validate_json(splits_path.read_text(encoding="utf-8"))
    expected_hash = compute_splits_content_hash(
        manifest.calibration_match_ids,
        manifest.heldout_match_ids,
    )
    if manifest.splits_content_sha256 != expected_hash:
        raise HoldoutContaminationError(
            f"splits.json content hash mismatch: expected {expected_hash}, "
            f"found {manifest.splits_content_sha256}"
        )
    overlap = set(manifest.calibration_video_ids).intersection(manifest.heldout_video_ids)
    if overlap:
        raise HoldoutContaminationError(
            f"Invalid splits.json: video_ids present in both calibration and heldout: {sorted(overlap)}"
        )
    return manifest


def enforce_split_discipline(
    splits_path: Path,
    requested_split: Literal["calibration", "heldout", "pilot"],
    tuning_mode: bool = False,
    run_id: str = "default",
    ledger_path: Path | None = None,
) -> ScoreboardSplitManifest:
    """Enforces calibration/heldout isolation and blocks reporting heldout if touched during tuning."""
    manifest = load_and_verify_splits(splits_path)
    effective_ledger = ledger_path or (splits_path.parent / ".tuning_ledger.json")

    ledger: dict[str, Any] = {"contaminated_runs": [], "tuning_events": []}
    if effective_ledger.exists():
        ledger = json.loads(effective_ledger.read_text(encoding="utf-8"))

    if requested_split == "heldout" and tuning_mode:
        contaminated = set(ledger.get("contaminated_runs", []))
        contaminated.add(run_id)
        ledger["contaminated_runs"] = sorted(contaminated)
        ledger.setdefault("tuning_events", []).append(
            {
                "run_id": run_id,
                "split": requested_split,
                "timestamp": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
                "violation": "attempted_heldout_access_during_tuning",
            }
        )
        effective_ledger.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
        raise HoldoutContaminationError(
            f"Run '{run_id}' attempted to evaluate 'heldout' with tuning_mode=True. "
            "Prompt/threshold tuning is strictly restricted to the 'calibration' split."
        )

    if requested_split == "heldout" and run_id in set(ledger.get("contaminated_runs", [])):
        raise HoldoutContaminationError(
            f"Refusing to report 'heldout' numbers for run '{run_id}' because this run previously "
            "touched 'heldout' while tuning_mode=True."
        )

    return manifest
