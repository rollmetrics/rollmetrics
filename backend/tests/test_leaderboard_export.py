"""Tests for scripts/export_benchmark_leaderboard.py and benchmarks/scoreboard_v1 package."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "export_benchmark_leaderboard.py"
BENCHMARK_DIR = REPO_ROOT / "benchmarks" / "scoreboard_v1"
SCOREBOARD_DIR = REPO_ROOT / "knowledge_base" / "evals" / "scoreboard_v1"


def _load_exporter() -> Any:
    spec = importlib.util.spec_from_file_location("export_benchmark_leaderboard", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_real_scoreboard_v1_artifacts(tmp_path: Path) -> None:
    exporter = _load_exporter()
    out_md = tmp_path / "LEADERBOARD.md"
    rendered = exporter.export_leaderboard(SCOREBOARD_DIR, output_path=out_md)

    assert out_md.is_file()
    assert out_md.read_text(encoding="utf-8") == rendered
    assert "# rollmetrics-scoreboard-v1 Leaderboard" in rendered
    assert "## Model Leaderboard" in rendered
    assert "## Corpus & Split Summary" in rendered
    assert "## Ground-Truth Labeler Spot-Check Audit" in rendered
    assert "Cross-Split Video Overlap | 0 |" in rendered
    assert "\u2014" not in rendered


def test_export_synthetic_ranking_and_edge_cases(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exporter = _load_exporter()

    # Missing required files raise clear FileNotFoundError
    with pytest.raises(FileNotFoundError, match="latest_results.json"):
        exporter.load_scoreboard_artifacts(tmp_path)

    (tmp_path / "latest_results.json").write_text(
        json.dumps(
            {
                "benchmark_id": "rollmetrics-scoreboard-v1",
                "evaluated_at": "2026-11-27T12:00:00+00:00",
                "split": "heldout",
                "models": [
                    {
                        "model_id": "model-b",
                        "split": "heldout",
                        "match_count": 10,
                        "winner_accuracy": 0.8,
                        "final_score_exact_match": 0.2,
                        "mae_points": 1.5,
                        "mae_advantages": 0.4,
                        "mae_penalties": 0.1,
                        "score_event_f1": 0.25,
                        "p50_latency_ms": 850.0,
                        "mean_cost_per_match_usd": 0.0,
                        "leakage_checks_passed": 10,
                    },
                    {
                        "model_id": "model-a",
                        "split": "heldout",
                        "match_count": 10,
                        "winner_accuracy": 0.8,
                        "final_score_exact_match": 0.5,
                        "mae_points": 0.9,
                        "mae_advantages": 0.2,
                        "mae_penalties": 0.0,
                        "score_event_f1": 0.5,
                        "p50_latency_ms": 2500.0,
                        "mean_cost_per_match_usd": 0.01234,
                        "leakage_checks_passed": 10,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="splits.json"):
        exporter.load_scoreboard_artifacts(tmp_path)

    (tmp_path / "splits.json").write_text(
        json.dumps(
            {
                "benchmark_id": "rollmetrics-scoreboard-v1",
                "calibration_count": 2,
                "heldout_count": 3,
                "calibration_match_ids": ["m1", "m2"],
                "heldout_match_ids": ["m3", "m4", "m5"],
                "calibration_video_ids": ["v1"],
                "heldout_video_ids": ["v2", "v3"],
            }
        ),
        encoding="utf-8",
    )

    out_path = tmp_path / "out" / "LEADERBOARD.md"
    rc = exporter.main(
        ["--scoreboard-dir", str(tmp_path), "--output", str(out_path), "--stdout"]
    )
    assert rc == 0
    captured = capsys.readouterr().out
    assert out_path.is_file()
    assert captured.strip() == out_path.read_text(encoding="utf-8").strip()

    # model-a ranks #1 ahead of model-b due to higher exact score tiebreaker
    pos_a = captured.index("`model-a`")
    pos_b = captured.index("`model-b`")
    assert pos_a < pos_b
    assert "850ms" in captured
    assert "2.5s" in captured
    assert "$0.0000" in captured
    assert "$0.01234" in captured
    assert "Ground-Truth Labeler Spot-Check Audit" not in captured

    # Empty models table renders cleanly
    empty_md = exporter.render_leaderboard_markdown(
        {"benchmark_id": "rollmetrics-scoreboard-v1", "models": []},
        {"calibration_match_ids": [], "heldout_match_ids": []},
        [],
    )
    assert "No model evaluations recorded yet" in empty_md


def test_published_benchmark_docs_present_and_clean() -> None:
    readme = BENCHMARK_DIR / "README.md"
    leaderboard = BENCHMARK_DIR / "LEADERBOARD.md"
    assert readme.is_file()
    assert leaderboard.is_file()

    readme_text = readme.read_text(encoding="utf-8")
    leaderboard_text = leaderboard.read_text(encoding="utf-8")

    assert "CC BY-NC-SA 4.0" in readme_text
    assert "ViCoS" in readme_text
    assert "export_benchmark_leaderboard.py" in readme_text
    assert "# rollmetrics-scoreboard-v1 Leaderboard" in leaderboard_text
    assert "\u2014" not in readme_text
    assert "\u2014" not in leaderboard_text
