"""Exports verified encyclopedia cohorts into knowledge_base/evals/scoreboard_v2/."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from rollmetrics.encyclopedia.ingestor import _match_to_manifest_entry
from rollmetrics.encyclopedia.store import EncyclopediaStore
from rollmetrics.evals.scoreboard_bench.schema import GroundTruthMatchLabel, MatchManifestEntry
from rollmetrics.evals.scoreboard_bench.splits import build_deterministic_splits, write_splits_manifest


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_scoreboard_v2(
    store: EncyclopediaStore,
    *,
    target_dir: Path,
    bucket: str = "gs://rollmetrics-ibjjf-vault",
    calibration_ratio: float = 0.30,
) -> dict[str, Any]:
    """Exports all GCS-ingested matches, OCR labels, 4-layer annotations, and deterministic splits."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    ingested = store.list_ingested_matches(unannotated_only=False, limit=5000)
    manifest_entries: list[MatchManifestEntry] = []
    ocr_labels: list[GroundTruthMatchLabel] = []
    annotations_rows: list[str] = []

    for match in ingested:
        entry = _match_to_manifest_entry(match)
        entry.license_note = (
            f"Public YouTube reference metadata only; raw and masked clips stored privately in {bucket}."
        )
        manifest_entries.append(entry)

        ocr_raw = store.get_ocr_label_json(match.match_id)
        if ocr_raw:
            ocr_labels.append(GroundTruthMatchLabel.model_validate_json(ocr_raw))

        ann = store.get_annotation(match.match_id)
        if ann is not None:
            annotations_rows.append(ann.model_dump_json())

    matches_path = target_dir / "matches.jsonl"
    labels_path = target_dir / "labels.jsonl"
    annotations_path = target_dir / "annotations.jsonl"
    splits_path = target_dir / "splits.json"
    manifest_path = target_dir / "manifest.json"

    with matches_path.open("w", encoding="utf-8") as fh:
        for e in manifest_entries:
            fh.write(e.model_dump_json() + "\n")

    with labels_path.open("w", encoding="utf-8") as fh:
        for lbl in ocr_labels:
            fh.write(lbl.model_dump_json() + "\n")

    with annotations_path.open("w", encoding="utf-8") as fh:
        for row in annotations_rows:
            fh.write(row + "\n")

    splits = build_deterministic_splits(
        manifest_entries,
        calibration_ratio=calibration_ratio,
    )
    splits.benchmark_id = "rollmetrics-scoreboard-v2"
    write_splits_manifest(splits_path, splits)

    manifest = {
        "benchmark_id": "rollmetrics-scoreboard-v2",
        "gcs_vault_bucket": bucket,
        "total_matches": len(manifest_entries),
        "ocr_ground_truth_labels": len(ocr_labels),
        "four_layer_annotations": len(annotations_rows),
        "calibration_count": splits.calibration_count,
        "heldout_count": splits.heldout_count,
        "splits_content_hash": splits.splits_content_sha256,
        "files": {
            "matches.jsonl": _sha256_file(matches_path),
            "labels.jsonl": _sha256_file(labels_path),
            "annotations.jsonl": _sha256_file(annotations_path),
            "splits.json": _sha256_file(splits_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
