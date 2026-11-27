"""RollMetrics 3-part IBJJF & CTA benchmark evaluation and Multi-Model Arena package."""

from __future__ import annotations

from rollmetrics.evals.datasets import (
    default_cta_coaching_queries,
    default_ibjjf_rule_cases,
    default_match_timelines,
    ensure_eval_datasets,
)
from rollmetrics.evals.model_bench import (
    DEFAULT_ARENA_MODELS,
    GOLDEN_ARENA_FIXTURES,
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
    serialize_pricing_table,
)
from rollmetrics.evals.runner import run_benchmark_suite

__all__ = [
    "DEFAULT_ARENA_MODELS",
    "GOLDEN_ARENA_FIXTURES",
    "LIVE_HUD_LATENCY_BUDGET_MS",
    "MODEL_PRICING_TABLE",
    "ModelPricing",
    "default_cta_coaching_queries",
    "default_ibjjf_rule_cases",
    "default_match_timelines",
    "ensure_eval_datasets",
    "estimate_workload_costs",
    "get_model_pricing",
    "register_model_pricing",
    "run_benchmark_suite",
    "run_model_arena",
    "scorecard_path_for_version",
    "serialize_pricing_table",
]
