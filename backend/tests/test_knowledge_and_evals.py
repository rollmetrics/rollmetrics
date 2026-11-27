"""Comprehensive unit and benchmark tests for the RollMetrics v0.1.0 knowledge base and evals."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import rollmetrics
from rollmetrics.config import REPO_ROOT, Settings
from rollmetrics.evals.datasets import ensure_eval_datasets
from rollmetrics.evals.model_bench import (
    DEFAULT_ARENA_MODELS,
    LIVE_HUD_LATENCY_BUDGET_MS,
    run_model_arena,
    scorecard_path_for_version,
)
from rollmetrics.evals.pricing import (
    MODEL_PRICING_TABLE,
    ModelPricing,
    estimate_workload_costs,
    get_model_pricing,
    register_model_pricing,
)
from rollmetrics.evals.runner import run_benchmark_suite
from rollmetrics.knowledge.indexer import (
    EMBEDDING_DIM,
    build_knowledge_index,
    compute_local_embedding,
    sha256_file,
)
from rollmetrics.knowledge.private_importer import import_private_curriculum
from rollmetrics.knowledge.retriever import KnowledgeRetriever
from rollmetrics.knowledge.schema import KnowledgeHit, canonicalize_position, sanitize_text

YOUTUBE_TIMESTAMP_RE = re.compile(r"^https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}&t=\d+s$")


def test_package_version_and_text_hygiene() -> None:
    assert rollmetrics.__version__ == "0.1.0"
    dirty = "Guard pull \u2014 not a takedown, it's a guard entry \u2013 frame hip."
    cleaned = sanitize_text(dirty)
    assert "\u2014" not in cleaned
    assert "\u2013" not in cleaned
    assert "not a takedown, it's" not in cleaned.lower()


def test_local_embedding_768d_unit_norm_and_similarity() -> None:
    v1 = compute_local_embedding("closed guard overhook cross collar choke thumb up")
    v2 = compute_local_embedding("overhook collar choke from closed guard bottom")
    v3 = compute_local_embedding("adult black belt nogi inside heel hook reaping")
    assert v1.shape == (EMBEDDING_DIM,)
    assert v1.dtype == np.float32
    assert np.isclose(np.linalg.norm(v1), 1.0, atol=1e-5)
    assert float(v1 @ v2) > float(v1 @ v3)


def test_v0_1_0_disk_artifacts_and_manifest_sha256() -> None:
    kb_dir = REPO_ROOT / "knowledge_base"
    ver_dir = kb_dir / "versions" / "v0.1.0"
    manifest_path = ver_dir / "manifest.json"
    assert manifest_path.exists(), "v0.1.0 manifest.json must exist on disk"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == "v0.1.0"
    assert manifest["embedding_dim"] == 768
    assert manifest["counts"]["ibjjf"] >= 35
    assert manifest["counts"]["cta"] >= 60
    assert manifest["counts"]["total"] == manifest["counts"]["ibjjf"] + manifest["counts"]["cta"]

    for fname in ("ibjjf_rules.jsonl", "cta_curriculum.jsonl", "index.sqlite"):
        fpath = ver_dir / fname
        assert fpath.exists(), f"{fname} missing in v0.1.0"
        assert sha256_file(fpath) == manifest["artifacts"][fname]["sha256"]

    # Verify zero em-dashes across JSONL and eval JSON files
    for check_file in [
        ver_dir / "ibjjf_rules.jsonl",
        ver_dir / "cta_curriculum.jsonl",
        kb_dir / "evals" / "ibjjf_rule_cases.json",
        kb_dir / "evals" / "match_timelines.json",
        kb_dir / "evals" / "cta_coaching_queries.json",
    ]:
        assert check_file.exists()
        raw = check_file.read_text(encoding="utf-8")
        assert "\u2014" not in raw, f"Forbidden em-dash found in {check_file}"


def test_retriever_contracts_offline_and_position_filtering() -> None:
    offline_settings = Settings(profile="local", gemini_api_key="")
    retriever = KnowledgeRetriever(
        knowledge_dir=REPO_ROOT / "knowledge_base",
        version="v0.1.0",
        settings=offline_settings,
    )
    manifest = retriever.manifest()
    assert manifest["version"] == "v0.1.0"

    # 1. General hybrid search returns typed KnowledgeHit objects
    hits = retriever.search("closed guard overhook collar choke", corpus="all", top_k=5)
    assert len(hits) == 5
    assert all(isinstance(h, KnowledgeHit) for h in hits)
    assert hits[0].corpus == "cta"
    assert "overhook" in hits[0].title.lower() or "overhook" in hits[0].text.lower()

    # 2. Position-filtered search handles human-readable position labels cleanly
    assert canonicalize_position("Closed Guard Bottom") == "closed_guard"
    hg_hits = retriever.search("underhook dogfight back take", corpus="cta", top_k=3, position="Half Guard")
    assert hg_hits
    assert all(h.position == "half_guard" for h in hg_hits)

    # 3. CTA positional recommendation contract + White Belt legality filter + YouTube jump-links
    recs = retriever.recommend_cta_for_position(
        position="Closed Guard",
        situation="opponent stands up to break guard",
        belt="white",
        top_k=3,
    )
    assert len(recs) == 3
    for rec in recs:
        assert rec["corpus"] == "cta"
        assert YOUTUBE_TIMESTAMP_RE.match(rec["source_url"])
        assert isinstance(rec["timestamp_sec"], int)
        assert "wristlock" not in rec["doc_id"].lower()

    # Blue belt CAN receive wristlock when specifically asking for wristlock vs double biceps
    blue_recs = retriever.recommend_cta_for_position(
        position="closed_guard",
        situation="goose neck wristlock double biceps",
        belt="blue",
        top_k=3,
    )
    assert any("wristlock" in r["doc_id"] for r in blue_recs)
    white_recs = retriever.recommend_cta_for_position(
        position="closed_guard",
        situation="goose neck wristlock double biceps",
        belt="white",
        top_k=3,
    )
    assert all("wristlock" not in r["doc_id"] for r in white_recs)

    # 4. IBJJF rule citation contract
    cites = retriever.cite_ibjjf_rule("IBJJF Art. 6.2.3 heel hook knee reaping nogi", top_k=2)
    assert len(cites) == 2
    assert any("6.2.3" in c["article"] for c in cites)
    assert all(c["source_url"].startswith("https://") for c in cites)


def test_private_curriculum_importer_creates_content_addressed_v0_2_0(tmp_path: Path) -> None:
    kb_tmp = tmp_path / "kb"
    build_knowledge_index(version="v0.1.0", knowledge_dir=kb_tmp, embed_cloud=False, fetch_online_pdf=False)

    drop_dir = tmp_path / "private_cta_drop"
    drop_dir.mkdir(parents=True)
    (drop_dir / "closed_guard_worm_notes.md").write_text(
        "# Closed Guard Lapel Feed\nBreak posture first, feed far lapel under armpit, and sweep to mount.\n"
        "## Side Control Frame Drill\nKeep near forearm in hip crease and shrimp wide to re-guard.\n",
        encoding="utf-8",
    )
    (drop_dir / "caioterra_seminar.json").write_text(
        json.dumps(
            [
                {
                    "title": "Seminar: Baratoplata from Collar Sleeve",
                    "position": "open_guard",
                    "belt": "blue",
                    "timestamp_sec": 145,
                    "source_url": "https://caioterra.com/library/baratoplata",
                    "text": "Thread forearm inside bicep when opponent defends triangle and rotate hip inward.",
                }
            ]
        ),
        encoding="utf-8",
    )
    (drop_dir / "half_guard_retention.srt").write_text(
        "1\n00:00:01,000 --> 00:00:05,000\nKeep knee shield active and block the cross-face bicep early.\n",
        encoding="utf-8",
    )

    res = import_private_curriculum(
        source_dir=drop_dir,
        target_version="v0.2.0",
        knowledge_dir=kb_tmp,
        base_version="v0.1.0",
        embed_cloud=False,
    )
    assert res["target_version"] == "v0.2.0"
    assert res["imported_count"] == 4
    v2_manifest = res["manifest"]
    assert v2_manifest["version"] == "v0.2.0"

    v2_retriever = KnowledgeRetriever(
        knowledge_dir=kb_tmp,
        version="v0.2.0",
        settings=Settings(profile="local", gemini_api_key=""),
    )
    hits = v2_retriever.search("Baratoplata from Collar Sleeve", corpus="cta", top_k=3)
    assert hits and "baratoplata" in hits[0].title.lower()


def test_full_three_part_benchmark_suite_passes_100_percent() -> None:
    ensure_eval_datasets(REPO_ROOT / "knowledge_base", overwrite=False)
    report = run_benchmark_suite(version="v0.1.0", knowledge_dir=REPO_ROOT / "knowledge_base")
    assert report["overall_pass"] is True, f"Benchmark failures: {json.dumps(report, indent=2)}"
    assert report["manifest_verified"] is True

    s1 = report["suites"]["ibjjf_rules"]
    s2 = report["suites"]["match_timelines"]
    s3 = report["suites"]["cta_coaching"]

    assert s1["total"] >= 30 and s1["pass_rate"] == 1.0
    assert s2["total"] >= 8 and s2["pass_rate"] == 1.0
    assert s3["total"] >= 20 and s3["recall_at_3"] >= 0.95 and s3["valid_youtube_links_rate"] == 1.0
    assert "model_arena" in report
    assert len(report["model_arena"]["models"]) == 4


def test_pricing_table_and_extensibility() -> None:
    for m_id in DEFAULT_ARENA_MODELS:
        assert m_id in MODEL_PRICING_TABLE

    local_p = get_model_pricing("ollama/gemma4:e2b-it-q4_K_M")
    gemma_p = get_model_pricing("gemma-4-26b-a4b-it")
    hydro_p = get_model_pricing("gemini-4-hydrogen")
    neon_p = get_model_pricing("gemini-4-neon")

    local_cost = estimate_workload_costs(local_p, 2400, 600, num_calls=6)
    gemma_cost = estimate_workload_costs(gemma_p, 2400, 600, num_calls=6)
    hydro_cost = estimate_workload_costs(hydro_p, 2400, 600, num_calls=6)
    neon_cost = estimate_workload_costs(neon_p, 2400, 600, num_calls=6)

    assert local_cost["est_cost_per_5min_match_usd"] == 0.0
    assert local_cost["est_cost_per_hour_live_usd"] == 0.0
    assert (
        0.0
        < gemma_cost["est_cost_per_5min_match_usd"]
        < hydro_cost["est_cost_per_5min_match_usd"]
        < neon_cost["est_cost_per_5min_match_usd"]
    )

    register_model_pricing(
        ModelPricing(
            model_id="gemini-future-test",
            display_name="Gemini Future Test",
            provider="google_genai",
            tier="experimental",
            recommended_role="Experimental Test Tier",
            input_usd_per_1m_tokens=0.50,
            output_usd_per_1m_tokens=2.00,
            video_input_usd_per_minute=0.02,
        )
    )
    assert get_model_pricing("gemini-future-test").tier == "experimental"


def test_persisted_live_model_scorecard_and_subset_filtering(tmp_path: Path) -> None:
    kb_dir = REPO_ROOT / "knowledge_base"
    sc_path = scorecard_path_for_version(kb_dir, "v0.1.0")
    assert sc_path.exists(), "model_scorecard_v0.1.0.json must be persisted on disk"

    raw_text = sc_path.read_text(encoding="utf-8")
    assert "\u2014" not in raw_text

    arena = run_model_arena(live_api=False, knowledge_dir=kb_dir, version="v0.1.0")
    assert arena["mode"] == "live_api"
    assert arena["live_hud_budget_ms"] == LIVE_HUD_LATENCY_BUDGET_MS
    assert arena["fixture_count"] == 6
    assert len(arena["models"]) == 4
    assert len(arena["pareto_models"]) >= 1

    by_id = {m["model_id"]: m for m in arena["models"]}
    assert set(by_id.keys()) == set(DEFAULT_ARENA_MODELS)

    for m_id, row in by_id.items():
        assert 0.0 <= row["event_accuracy"] <= 1.0
        assert 0.0 <= row["foul_legality_accuracy"] <= 1.0
        assert 0.0 <= row["points_exact_match"] <= 1.0
        assert row["p50_ms"] > 0.0
        assert row["p95_ms"] >= row["p50_ms"]
        assert row["meets_live_hud_budget"] is (row["p50_ms"] < LIVE_HUD_LATENCY_BUDGET_MS)
        assert row["input_tokens"] > 0
        assert row["output_tokens"] > 0
        if m_id.startswith("ollama/"):
            assert row["est_cost_per_5min_match_usd"] == 0.0
            assert row["est_cost_per_hour_live_usd"] == 0.0
        else:
            assert row["est_cost_per_5min_match_usd"] > 0.0
            assert row["est_cost_per_hour_live_usd"] > 0.0

    # Subset query & custom uncached offline fallback check
    sub = run_model_arena(
        models=["ollama/gemma4:e2b-it-q4_K_M", "gemini-4-hydrogen"],
        live_api=False,
        knowledge_dir=kb_dir,
    )
    assert len(sub["models"]) == 2

    custom_offline = run_model_arena(
        models=["ollama/custom-bjj-vlm:latest"],
        live_api=False,
        knowledge_dir=tmp_path,
    )
    assert custom_offline["mode"] == "offline_baseline"
    assert custom_offline["models"][0]["est_cost_per_5min_match_usd"] == 0.0

