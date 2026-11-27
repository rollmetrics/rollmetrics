#!/usr/bin/env python3
"""Export a publishable Markdown leaderboard for rollmetrics-scoreboard-v1."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCOREBOARD_DIR = REPO_ROOT / "knowledge_base" / "evals" / "scoreboard_v1"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "benchmarks" / "scoreboard_v1" / "LEADERBOARD.md"
DEFAULT_CATALOG_PATH = REPO_ROOT / "benchmarks" / "scoreboard_v1" / "DATASET_CATALOG.md"


def load_scoreboard_artifacts(scoreboard_dir: Path) -> dict[str, Any]:
    """Load latest_results.json, splits.json, labels.jsonl, and matches.jsonl from scoreboard_dir."""
    base = Path(scoreboard_dir).expanduser().resolve()
    results_path = base / "latest_results.json"
    splits_path = base / "splits.json"
    labels_path = base / "labels.jsonl"
    matches_path = base / "matches.jsonl"

    if not results_path.is_file():
        raise FileNotFoundError(f"Missing scoreboard results file: {results_path}")
    if not splits_path.is_file():
        raise FileNotFoundError(f"Missing scoreboard splits file: {splits_path}")

    results = json.loads(results_path.read_text(encoding="utf-8"))
    splits = json.loads(splits_path.read_text(encoding="utf-8"))

    labels: list[dict[str, Any]] = []
    if labels_path.is_file():
        for raw_line in labels_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line:
                labels.append(json.loads(line))

    matches: list[dict[str, Any]] = []
    if matches_path.is_file():
        for raw_line in matches_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line:
                matches.append(json.loads(line))

    return {"results": results, "splits": splits, "labels": labels, "matches": matches}


def summarize_corpus(labels: list[dict[str, Any]], splits: dict[str, Any]) -> dict[str, Any]:
    """Compute corpus counts, split isolation stats, and finish-method breakdown."""
    cal_ids = set(splits.get("calibration_match_ids", []))
    held_ids = set(splits.get("heldout_match_ids", []))
    cal_videos = set(splits.get("calibration_video_ids", []))
    held_videos = set(splits.get("heldout_video_ids", []))

    method_counts: Counter[str] = Counter()
    human_audited = 0
    total_events = 0
    unique_videos_in_labels: set[str] = set()

    for row in labels:
        method = str(row.get("method") or "unknown").strip().lower()
        method_counts[method] += 1
        if row.get("human_spot_checked"):
            human_audited += 1
        events = row.get("events")
        if isinstance(events, list):
            total_events += len(events)
        vid = row.get("video_id")
        if vid:
            unique_videos_in_labels.add(str(vid))

    return {
        "labeled_matches": len(labels),
        "calibration_matches": int(splits.get("calibration_count", len(cal_ids))),
        "heldout_matches": int(splits.get("heldout_count", len(held_ids))),
        "calibration_videos": len(cal_videos),
        "heldout_videos": len(held_videos),
        "shared_video_ids": len(cal_videos & held_videos),
        "shared_match_ids": len(cal_ids & held_ids),
        "unique_videos_in_labels": len(unique_videos_in_labels),
        "human_audited_matches": human_audited,
        "total_labeled_events": total_events,
        "method_counts": dict(sorted(method_counts.items())),
    }


def rank_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort evaluated models by accuracy first, then lower point MAE and cost."""

    def _sort_key(item: dict[str, Any]) -> tuple[float, float, float, float, str]:
        return (
            -float(item.get("winner_accuracy", 0.0)),
            -float(item.get("final_score_exact_match", 0.0)),
            float(item.get("mae_points", 999.0)),
            float(item.get("mean_cost_per_match_usd", 999.0)),
            str(item.get("model_id", "")),
        )

    return sorted(models, key=_sort_key)


def _fmt_pct(value: Any) -> str:
    return f"{float(value or 0.0) * 100.0:.1f}%"


def _fmt_latency(ms: Any) -> str:
    val = float(ms or 0.0)
    if val >= 1000.0:
        return f"{val / 1000.0:.1f}s"
    return f"{val:.0f}ms"


def _fmt_cost(usd: Any) -> str:
    val = float(usd or 0.0)
    if val == 0.0:
        return "$0.0000"
    return f"${val:.5f}"


def render_leaderboard_markdown(
    results: dict[str, Any],
    splits: dict[str, Any],
    labels: list[dict[str, Any]],
) -> str:
    """Render rollmetrics-scoreboard-v1 artifacts as a clean GitHub Markdown leaderboard."""
    benchmark_id = str(results.get("benchmark_id") or splits.get("benchmark_id") or "rollmetrics-scoreboard-v1")
    evaluated_at = str(results.get("evaluated_at") or "unknown")
    split_name = str(results.get("split") or "heldout")
    splits_sha = str(results.get("splits_content_sha256") or "unverified")
    leakage_control = str(results.get("leakage_control") or "strict_blackout_drawbox_plus_pixel_edge_ocr_gate")
    corpus_summary = summarize_corpus(labels, splits)
    ranked = rank_models(list(results.get("models") or []))

    lines: list[str] = [
        f"# {benchmark_id} Leaderboard",
        "",
        "> Auto-generated from `knowledge_base/evals/scoreboard_v1/{latest_results.json,splits.json,labels.jsonl}`.",
        "> Refresh after any new evaluation run with `python3 scripts/export_benchmark_leaderboard.py`.",
        "> See also: [122-Bout Dataset Catalog](DATASET_CATALOG.md) | [Metrics & Harness Specifications](METRICS_AND_HARNESSES.md).",
        "",
        "## Run Metadata",
        "",
        f"- **Benchmark ID**: `{benchmark_id}`",
        f"- **Evaluated At (UTC)**: `{evaluated_at}`",
        f"- **Reported Split**: `{split_name}` (`{results.get('evaluated_matches_in_split', corpus_summary['heldout_matches'])}` bouts in split)",
        f"- **Corpus Size**: `{results.get('corpus_total_matches', corpus_summary['labeled_matches'])}` labeled bouts (`{corpus_summary['calibration_matches']}` calibration / `{corpus_summary['heldout_matches']}` held-out)",
        f"- **Splits SHA-256**: `{splits_sha}`",
        f"- **Leakage Control**: `{leakage_control}` (cross-split shared `video_id` count: `{corpus_summary['shared_video_ids']}`)",
        "",
        "## Model Leaderboard",
        "",
        "| Rank | Model | Split | N | Winner Acc | Exact Score | MAE Pts | MAE Adv | MAE Pen | Event F1 (@5s) | P50 Latency | Cost / Match | Leakage Gate |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    if not ranked:
        lines.append("| - | _No model evaluations recorded yet_ | - | 0 | 0.0% | 0.0% | 0.00 | 0.00 | 0.00 | 0.0% | 0ms | $0.0000 | 0/0 |")
    else:
        for idx, row in enumerate(ranked, start=1):
            n = int(row.get("match_count", 0))
            leak_ok = int(row.get("leakage_checks_passed", 0))
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(idx),
                        f"`{row.get('model_id', 'unknown')}`",
                        str(row.get("split", split_name)),
                        str(n),
                        _fmt_pct(row.get("winner_accuracy")),
                        _fmt_pct(row.get("final_score_exact_match")),
                        f"{float(row.get('mae_points', 0.0)):.2f}",
                        f"{float(row.get('mae_advantages', 0.0)):.2f}",
                        f"{float(row.get('mae_penalties', 0.0)):.2f}",
                        _fmt_pct(row.get("score_event_f1")),
                        _fmt_latency(row.get("p50_latency_ms")),
                        _fmt_cost(row.get("mean_cost_per_match_usd")),
                        f"{leak_ok}/{n}",
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Corpus & Split Summary",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Labeled Bouts (`labels.jsonl`) | {corpus_summary['labeled_matches']} |",
            f"| Calibration Bouts / Videos | {corpus_summary['calibration_matches']} / {corpus_summary['calibration_videos']} |",
            f"| Held-Out Bouts / Videos | {corpus_summary['heldout_matches']} / {corpus_summary['heldout_videos']} |",
            f"| Cross-Split Video Overlap | {corpus_summary['shared_video_ids']} |",
            f"| Human Frame-Audited Bouts | {corpus_summary['human_audited_matches']} |",
            f"| Total Ground-Truth Score Events | {corpus_summary['total_labeled_events']} |",
        ]
    )

    method_counts = corpus_summary["method_counts"]
    if method_counts:
        lines.extend(
            [
                "",
                "### Finish Method Distribution",
                "",
                "| Finish Method | Bouts |",
                "| --- | ---: |",
            ]
        )
        for method, count in method_counts.items():
            lines.append(f"| `{method}` | {count} |")

    spot_check = results.get("spot_check_audit")
    if isinstance(spot_check, dict) and spot_check:
        lines.extend(
            [
                "",
                "## Ground-Truth Labeler Spot-Check Audit",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
                f"| Spot-Checked Matches | {int(spot_check.get('spot_checked_matches', 0))} |",
                f"| Labeler Winner Accuracy | {_fmt_pct(spot_check.get('labeler_winner_accuracy'))} |",
                f"| Labeler Final Score Exact Match | {_fmt_pct(spot_check.get('labeler_final_score_exact_match'))} |",
                f"| Labeler MAE Points | {float(spot_check.get('labeler_mae_points', 0.0)):.3f} |",
                f"| Labeler MAE Advantages | {float(spot_check.get('labeler_mae_advantages', 0.0)):.4f} |",
                f"| Labeler MAE Penalties | {float(spot_check.get('labeler_mae_penalties', 0.0)):.3f} |",
                f"| Labeler Score Event F1 | {_fmt_pct(spot_check.get('labeler_score_event_f1'))} |",
            ]
        )

    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            "python3 scripts/export_benchmark_leaderboard.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_dataset_catalog_markdown(
    matches: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    splits: dict[str, Any],
) -> str:
    """Render the complete 122-bout rollmetrics-scoreboard-v1 catalog with public YouTube URLs."""
    cal_ids = set(splits.get("calibration_match_ids", []))
    held_ids = set(splits.get("heldout_match_ids", []))
    labels_by_id = {str(r.get("match_id", "")): r for r in labels}

    lines: list[str] = [
        "# rollmetrics-scoreboard-v1 Dataset Catalog (122 Bouts)",
        "",
        "> Auto-generated from `knowledge_base/evals/scoreboard_v1/{matches.jsonl,labels.jsonl,splits.json}`.",
        "> Zero raw video files are committed to Git. Every bout links directly to its public YouTube broadcast URL and clip window.",
        "> See also: [Leaderboard](LEADERBOARD.md) | [Metrics & Harness Specifications](METRICS_AND_HARNESSES.md).",
        "",
        "| # | Match ID | Split | Event | Matchup | Uniform | Window (s) | GT Final Score (A-B) | Winner / Method | Audited | Source |",
        "| ---: | :--- | :--- | :--- | :--- | :--- | ---: | :--- | :--- | :---: | :--- |",
    ]

    for idx, m in enumerate(matches, start=1):
        mid = str(m.get("match_id", ""))
        split_label = "calibration" if mid in cal_ids else ("heldout" if mid in held_ids else "unassigned")
        lbl = labels_by_id.get(mid, {})
        sa = lbl.get("final_score_a") or {}
        sb = lbl.get("final_score_b") or {}
        pts_a, pts_b = int(sa.get("points", 0)), int(sb.get("points", 0))
        adv_a, adv_b = int(sa.get("advantages", 0)), int(sb.get("advantages", 0))
        pen_a, pen_b = int(sa.get("penalties", 0)), int(sb.get("penalties", 0))
        score_str = f"{pts_a}-{pts_b} ({adv_a}-{adv_b} adv, {pen_a}-{pen_b} pen)"
        winner = str(lbl.get("winner", "?"))
        method = str(lbl.get("method", "unknown"))
        audited = "yes" if lbl.get("human_spot_checked") else "no"
        start_s = int(float(lbl.get("active_bout_start_sec", m.get("start_sec", 0))))
        end_s = int(float(lbl.get("active_bout_end_sec", m.get("end_sec", 0))))
        dur_s = max(0, end_s - start_s)
        ath_a = str(m.get("athlete_a", "A")).replace("|", "/")
        ath_b = str(m.get("athlete_b", "B")).replace("|", "/")
        event_name = str(m.get("event_name", "")).replace("|", "/")
        uniform = str(m.get("uniform", "gi"))
        url = str(m.get("url", ""))
        src_link = f"[YouTube]({url})" if url else "-"

        lines.append(
            f"| {idx} | `{mid}` | `{split_label}` | {event_name} | {ath_a} vs. {ath_b} | "
            f"{uniform} | {start_s}-{end_s} ({dur_s}s) | {score_str} | `{winner}` ({method}) | {audited} | {src_link} |"
        )

    lines.append("")
    return "\n".join(lines)


def export_leaderboard(
    scoreboard_dir: Path,
    output_path: Path | None = None,
    catalog_output_path: Path | None = None,
) -> str:
    """Load scoreboard artifacts, render Markdown, and optionally write leaderboard & dataset catalog."""
    bundle = load_scoreboard_artifacts(scoreboard_dir)
    markdown = render_leaderboard_markdown(bundle["results"], bundle["splits"], bundle["labels"])
    if output_path is not None:
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
    if catalog_output_path is not None and bundle.get("matches"):
        cat_md = render_dataset_catalog_markdown(bundle["matches"], bundle["labels"], bundle["splits"])
        cat_out = Path(catalog_output_path).expanduser().resolve()
        cat_out.parent.mkdir(parents=True, exist_ok=True)
        cat_out.write_text(cat_md, encoding="utf-8")
    return markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export benchmarks/scoreboard_v1/LEADERBOARD.md and DATASET_CATALOG.md from scoreboard_v1 artifacts."
    )
    parser.add_argument(
        "--scoreboard-dir",
        type=Path,
        default=DEFAULT_SCOREBOARD_DIR,
        help="Path to directory containing latest_results.json, splits.json, and labels.jsonl.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Destination Markdown path (default: benchmarks/scoreboard_v1/LEADERBOARD.md).",
    )
    parser.add_argument(
        "--catalog-output",
        type=Path,
        default=DEFAULT_CATALOG_PATH,
        help="Destination 122-bout catalog Markdown path (default: benchmarks/scoreboard_v1/DATASET_CATALOG.md).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Also print rendered Markdown to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    markdown = export_leaderboard(
        args.scoreboard_dir,
        output_path=args.output,
        catalog_output_path=args.catalog_output,
    )
    if args.stdout:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
