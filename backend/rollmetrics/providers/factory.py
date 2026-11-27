"""Provider factory and live health inspection for Cloud Gemini 4 and Local Ollama Gemma."""

from __future__ import annotations

import httpx

from rollmetrics.config import Settings, get_settings
from rollmetrics.providers.base import VisionProvider
from rollmetrics.providers.gemini_cloud import GeminiCloudProvider
from rollmetrics.providers.ollama_local import (
    OllamaLocalProvider,
    select_best_ollama_vision_model,
)


def get_vision_provider(
    profile: str | None = None,
    settings: Settings | None = None,
    model_override: str | None = None,
) -> VisionProvider:
    cfg = settings or get_settings()
    override = (model_override or "").strip() or None
    if override and override.lower().startswith("ollama/"):
        return OllamaLocalProvider(settings=cfg, model_override=override.split("/", 1)[1])
    resolved = (profile or cfg.profile or "local").strip().lower()
    if resolved == "local":
        return OllamaLocalProvider(settings=cfg, model_override=override)
    if resolved in ("gemma", "gemma_cloud", "gemma-api"):
        return GeminiCloudProvider(settings=cfg, use_gemma_api=True, model_override=override)
    return GeminiCloudProvider(settings=cfg, model_override=override)


async def check_providers_health(settings: Settings | None = None) -> dict:
    """Checks Cloud Gemini key configuration and local Ollama availability + vision models."""
    cfg = settings or get_settings()
    ollama_reachable = False
    ollama_models: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(f"{cfg.ollama_base_url.rstrip('/')}/api/tags")
            if resp.status_code == 200:
                ollama_reachable = True
                data = resp.json()
                ollama_models = [
                    str(m.get("name", ""))
                    for m in (data.get("models") or [])
                    if isinstance(m, dict) and m.get("name")
                ]
    except (httpx.HTTPError, OSError, ValueError):
        ollama_reachable = False
        ollama_models = []

    recommended_local = select_best_ollama_vision_model(
        ollama_models,
        cfg.ollama_vision_model or "gemma3:4b",
    )

    return {
        "active_profile": cfg.profile,
        "cloud_gemini_configured": bool(cfg.gemini_api_key),
        "ollama_reachable": ollama_reachable,
        "ollama_models": ollama_models,
        "recommended_local_model": recommended_local,
    }
