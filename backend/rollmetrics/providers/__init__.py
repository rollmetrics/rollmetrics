"""Dual-model vision providers (Cloud Gemini 4 Hydrogen/Neon + Local Ollama Gemma)."""

from rollmetrics.providers.base import (
    ADJUDICATION_JSON_SCHEMA,
    FRAME_OBSERVATION_JSON_SCHEMA,
    VIDEO_ANALYSIS_JSON_SCHEMA,
    FrameObservation,
    ProviderTelemetry,
    VideoVisionResult,
    VisionProvider,
    parse_json_lenient,
)
from rollmetrics.providers.factory import check_providers_health, get_vision_provider
from rollmetrics.providers.gemini_cloud import (
    GeminiCloudProvider,
    extract_interaction_text,
    extract_interaction_usage,
)
from rollmetrics.providers.ollama_local import (
    OllamaLocalProvider,
    select_best_ollama_vision_model,
)

__all__ = [
    "ADJUDICATION_JSON_SCHEMA",
    "FRAME_OBSERVATION_JSON_SCHEMA",
    "VIDEO_ANALYSIS_JSON_SCHEMA",
    "FrameObservation",
    "GeminiCloudProvider",
    "OllamaLocalProvider",
    "ProviderTelemetry",
    "VideoVisionResult",
    "VisionProvider",
    "check_providers_health",
    "extract_interaction_text",
    "extract_interaction_usage",
    "get_vision_provider",
    "parse_json_lenient",
    "select_best_ollama_vision_model",
]
