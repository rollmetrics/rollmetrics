"""Optional research-only evaluation adapter for the ViCoS BJJ Positions Dataset (CC BY-NC-SA 4.0).

IMPORTANT LICENSE NOTICE:
The ViCoS Brazilian Jiu-Jitsu Positions Dataset (Hudovernik & Skocaj, MMSports '22) is licensed
under CC BY-NC-SA 4.0 (NonCommercial + ShareAlike). RollMetrics NEVER bundles ViCoS images or
trains commercial weights on ViCoS. This adapter strictly reads an external user-supplied local
directory for non-commercial research benchmarking only.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from rollmetrics.evals.next_wave import CANONICAL_GRAPPLING_STATES

VICOS_LICENSE_NOTICE = (
    "Research-only evaluation adapter for ViCoS BJJ Positions Dataset (Hudovernik & Skocaj, "
    "MMSports 2022), licensed CC BY-NC-SA 4.0 (NonCommercial-ShareAlike). External local data "
    "only; never bundled or used for commercial model training."
)

# Maps all 18 ViCoS BJJ position classes to (RollMetrics canonical state, top_athlete perspective)
VICOS_CLASS_TO_ROLLMETRICS: dict[str, tuple[str, Literal["A", "B", "neutral"]]] = {
    "standing": ("standing", "neutral"),
    "takedown": ("standing", "neutral"),
    "closed_guard_top": ("closed_guard", "A"),
    "closed_guard_bottom": ("closed_guard", "B"),
    "open_guard_top": ("open_guard", "A"),
    "open_guard_bottom": ("open_guard", "B"),
    "half_guard_top": ("half_guard", "A"),
    "half_guard_bottom": ("half_guard", "B"),
    "side_control_top": ("side_control", "A"),
    "side_control_bottom": ("side_control", "B"),
    "knee_on_belly_top": ("side_control", "A"),
    "knee_on_belly_bottom": ("side_control", "B"),
    "mount_top": ("mount", "A"),
    "mount_bottom": ("mount", "B"),
    "back_control_top": ("back_control", "A"),
    "back_control_bottom": ("back_control", "B"),
    "turtle_top": ("turtle", "A"),
    "turtle_bottom": ("turtle", "B"),
}


class ViCoSSample(BaseModel):
    sample_id: str
    image_path: str
    vicos_label: str
    canonical_state: str
    top_athlete: Literal["A", "B", "neutral"] = "neutral"


def map_vicos_label(raw_label: str) -> tuple[str, Literal["A", "B", "neutral"]]:
    """Maps a ViCoS 18-class label to RollMetrics' canonical 8-state grappling taxonomy + top/bottom."""
    norm = str(raw_label or "").strip().lower().replace("-", "_").replace(" ", "_")
    if norm in VICOS_CLASS_TO_ROLLMETRICS:
        return VICOS_CLASS_TO_ROLLMETRICS[norm]
    for v_key, mapped in VICOS_CLASS_TO_ROLLMETRICS.items():
        if v_key in norm or norm in v_key:
            return mapped
    return ("standing", "neutral")


def load_vicos_directory(dataset_dir: Path | str) -> list[ViCoSSample]:
    """Loads frame annotations from a user-supplied external ViCoS directory (annotations.json or .jsonl)."""
    root = Path(dataset_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(
            f"External ViCoS dataset directory not found: {root}. "
            "ViCoS (CC BY-NC-SA 4.0) is not bundled with RollMetrics."
        )

    samples: list[ViCoSSample] = []
    json_path = root / "annotations.json"
    jsonl_path = root / "annotations.jsonl"

    records: list[dict[str, Any]] = []
    if jsonl_path.is_file():
        for ln in jsonl_path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                records.append(json.loads(ln))
    elif json_path.is_file():
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            records = [r for r in raw if isinstance(r, dict)]
        elif isinstance(raw, dict):
            cat_map = {
                int(c["id"]): str(c["name"])
                for c in (raw.get("categories") or [])
                if isinstance(c, dict) and "id" in c and "name" in c
            }
            img_map = {
                int(im["id"]): str(im.get("file_name", ""))
                for im in (raw.get("images") or [])
                if isinstance(im, dict) and "id" in im
            }
            if raw.get("annotations") and img_map:
                for ann in raw["annotations"]:
                    if not isinstance(ann, dict):
                        continue
                    img_id = int(ann.get("image_id", -1))
                    cat_id = int(ann.get("category_id", -1))
                    records.append(
                        {
                            "sample_id": str(ann.get("id", img_id)),
                            "image_path": str(root / img_map.get(img_id, "")),
                            "label": cat_map.get(cat_id, str(ann.get("label", "standing"))),
                        }
                    )
            elif isinstance(raw.get("samples"), list):
                records = [r for r in raw["samples"] if isinstance(r, dict)]

    for idx, rec in enumerate(records):
        raw_lbl = str(rec.get("label") or rec.get("vicos_label") or rec.get("position") or "standing")
        can_state, top_ath = map_vicos_label(raw_lbl)
        img_rel = str(rec.get("image_path") or rec.get("file_name") or f"frame_{idx:05d}.jpg")
        samples.append(
            ViCoSSample(
                sample_id=str(rec.get("sample_id") or rec.get("id") or f"vicos-{idx:05d}"),
                image_path=img_rel,
                vicos_label=raw_lbl,
                canonical_state=can_state,
                top_athlete=top_ath,
            )
        )
    return samples


def evaluate_vicos_predictions(
    samples: list[ViCoSSample],
    predictions: dict[str, str],
) -> dict[str, Any]:
    """Evaluates predicted canonical states (or 18-class ViCoS labels) against ground-truth ViCoS samples."""
    if not samples:
        return {
            "suite": "vicos_research_eval",
            "license_notice": VICOS_LICENSE_NOTICE,
            "non_commercial_only": True,
            "num_samples": 0,
            "canonical_accuracy": 0.0,
            "canonical_macro_f1": 0.0,
            "top_bottom_accuracy": 0.0,
            "per_state_f1": {},
        }

    tp: Counter[str] = Counter()
    fp: Counter[str] = Counter()
    fn: Counter[str] = Counter()
    exact_can = 0
    exact_top = 0

    for item in samples:
        pred_raw = predictions.get(item.sample_id, "standing")
        pred_state, pred_top = map_vicos_label(pred_raw)
        if pred_state == item.canonical_state:
            exact_can += 1
            tp[item.canonical_state] += 1
        else:
            fp[pred_state] += 1
            fn[item.canonical_state] += 1
        if pred_top == item.top_athlete:
            exact_top += 1

    per_state_f1: dict[str, float] = {}
    active: list[str] = []
    for st in CANONICAL_GRAPPLING_STATES:
        denom = 2 * tp[st] + fp[st] + fn[st]
        if denom > 0:
            active.append(st)
            per_state_f1[st] = round((2 * tp[st]) / float(denom), 4)

    macro_f1 = round(sum(per_state_f1[s] for s in active) / len(active), 4) if active else 0.0
    n = len(samples)
    return {
        "suite": "vicos_research_eval",
        "license_notice": VICOS_LICENSE_NOTICE,
        "non_commercial_only": True,
        "num_samples": n,
        "canonical_accuracy": round(exact_can / float(n), 4),
        "canonical_macro_f1": macro_f1,
        "top_bottom_accuracy": round(exact_top / float(n), 4),
        "per_state_f1": per_state_f1,
    }
