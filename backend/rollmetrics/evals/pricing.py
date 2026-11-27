"""Transparent token and video-minute pricing table for RollMetrics vision and adjudication models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ModelPricing(BaseModel):
    """Per-model token and video pricing specification in USD."""

    model_id: str
    display_name: str
    provider: str
    tier: str
    recommended_role: str
    input_usd_per_1m_tokens: float
    output_usd_per_1m_tokens: float
    video_input_usd_per_minute: float = 0.0
    notes: str = ""


MODEL_PRICING_TABLE: dict[str, ModelPricing] = {
    "ollama/gemma4:e2b-it-q4_K_M": ModelPricing(
        model_id="ollama/gemma4:e2b-it-q4_K_M",
        display_name="Ollama Gemma 4 E2B Q4_K_M (Local M3)",
        provider="ollama_local",
        tier="local_zero_cost",
        recommended_role="Offline Privacy & Zero-Cost Local Scrimmage HUD",
        input_usd_per_1m_tokens=0.00,
        output_usd_per_1m_tokens=0.00,
        video_input_usd_per_minute=0.00,
        notes="Runs locally on Apple Silicon M3 unified memory with $0.00 marginal API cost.",
    ),
    "gemma-4-26b-a4b-it": ModelPricing(
        model_id="gemma-4-26b-a4b-it",
        display_name="Gemma 4 26B-A4B IT (Open-Weight Cloud MoE)",
        provider="google_genai",
        tier="open_weight_cloud",
        recommended_role="Low-Cost Open-Weight Cloud Batch Tagger",
        input_usd_per_1m_tokens=0.10,
        output_usd_per_1m_tokens=0.30,
        video_input_usd_per_minute=0.004,
        notes="Open-weight Gemma 4 MoE (4B active / 26B total) served via Gemini Interactions API.",
    ),
    "gemini-4-hydrogen": ModelPricing(
        model_id="gemini-4-hydrogen",
        display_name="Gemini 4 Hydrogen (Fast Multimodal Tracker)",
        provider="google_genai",
        tier="live_hud_tracker",
        recommended_role="Primary Sub-1.5s Live Mat HUD & Video Tracker",
        input_usd_per_1m_tokens=0.25,
        output_usd_per_1m_tokens=1.50,
        video_input_usd_per_minute=0.012,
        notes="Fast frontier multimodal tracker supporting agentic video ingestion and low-latency live frames.",
    ),
    "gemini-4-neon": ModelPricing(
        model_id="gemini-4-neon",
        display_name="Gemini 4 Neon (Frontier Referee Adjudicator)",
        provider="google_genai",
        tier="frontier_adjudicator",
        recommended_role="High-Reasoning Foul Dispute & CTA Film Adjudicator",
        input_usd_per_1m_tokens=1.50,
        output_usd_per_1m_tokens=10.00,
        video_input_usd_per_minute=0.072,
        notes="High-thinking frontier model reserved for 3.0s stabilization disputes and black-belt CTA study.",
    ),
}

_MODEL_ALIASES: dict[str, str] = {
    "gemma4:e2b-it-q4_k_m": "ollama/gemma4:e2b-it-q4_K_M",
    "ollama/gemma4:e2b-it-q4_k_m": "ollama/gemma4:e2b-it-q4_K_M",
    "ollama/gemma3:4b": "ollama/gemma4:e2b-it-q4_K_M",
    "gemma3:4b": "ollama/gemma4:e2b-it-q4_K_M",
}


def register_model_pricing(entry: ModelPricing) -> None:
    """Registers or overrides pricing metadata for an additional model."""
    MODEL_PRICING_TABLE[entry.model_id] = entry
    _MODEL_ALIASES[entry.model_id.lower()] = entry.model_id


def get_model_pricing(model_id: str) -> ModelPricing:
    """Resolves pricing metadata for a model identifier, falling back cleanly for custom tags."""
    raw = (model_id or "").strip()
    if raw in MODEL_PRICING_TABLE:
        return MODEL_PRICING_TABLE[raw]
    low = raw.lower()
    if low in _MODEL_ALIASES:
        return MODEL_PRICING_TABLE[_MODEL_ALIASES[low]]
    if low.endswith("-agentic"):
        base_id = raw[: -len("-agentic")]
        base_pricing = get_model_pricing(base_id)
        return base_pricing.model_copy(
            update={
                "model_id": raw,
                "display_name": f"{base_pricing.display_name} (Agentic Full Video)",
                "notes": f"Full-video agentic processing mode backed by {base_pricing.model_id}.",
            }
        )
    if low.startswith("ollama/") or ":" in raw:
        return ModelPricing(
            model_id=raw,
            display_name=f"Local Ollama ({raw})",
            provider="ollama_local",
            tier="local_zero_cost",
            recommended_role="Custom Local Vision Model",
            input_usd_per_1m_tokens=0.0,
            output_usd_per_1m_tokens=0.0,
            video_input_usd_per_minute=0.0,
            notes="Local inference on host hardware ($0.00 API cost).",
        )
    return ModelPricing(
        model_id=raw,
        display_name=raw,
        provider="google_genai",
        tier="custom_cloud",
        recommended_role="Custom Cloud Vision Model",
        input_usd_per_1m_tokens=0.25,
        output_usd_per_1m_tokens=1.50,
        video_input_usd_per_minute=0.012,
        notes="Default cloud multimodal token estimate.",
    )


def estimate_workload_costs(
    pricing: ModelPricing,
    total_input_tokens: int,
    total_output_tokens: int,
    num_calls: int,
    live_hz: float = 0.5,
) -> dict[str, float]:
    """Computes actual run cost plus projected 5-minute match and 1-hour live HUD costs."""
    calls = max(1, num_calls)
    avg_in = total_input_tokens / calls
    avg_out = total_output_tokens / calls

    in_rate = pricing.input_usd_per_1m_tokens / 1_000_000.0
    out_rate = pricing.output_usd_per_1m_tokens / 1_000_000.0

    run_cost = total_input_tokens * in_rate + total_output_tokens * out_rate

    # 5-minute pre-recorded match film study: 5 min video stream + 1 tracker pass + 1 dispute/CTA pass
    five_min_video_cost = 5.0 * pricing.video_input_usd_per_minute
    five_min_tokens_cost = (2.5 * avg_in) * in_rate + (4.0 * avg_out) * out_rate
    est_5min_match = five_min_video_cost + five_min_tokens_cost

    # 1-hour continuous live mat HUD at throttled `live_hz` (default 0.5 fps = 1,800 frames/hour)
    frames_per_hour = 3600.0 * max(0.1, live_hz)
    est_hour_live = frames_per_hour * (avg_in * in_rate + avg_out * out_rate)

    return {
        "benchmark_run_cost_usd": round(run_cost, 6),
        "est_cost_per_5min_match_usd": round(est_5min_match, 4),
        "est_cost_per_hour_live_usd": round(est_hour_live, 4),
    }


def serialize_pricing_table() -> list[dict[str, Any]]:
    return [p.model_dump(mode="json") for p in MODEL_PRICING_TABLE.values()]
