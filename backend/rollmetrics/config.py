"""RollMetrics core configuration (local-first + Cloud Run parity)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROLLMETRICS_", env_file=".env", extra="ignore")

    app_name: str = "RollMetrics"
    public_url: str = "http://127.0.0.1:8090"
    environment: Literal["local", "cloud", "test"] = "local"
    host: str = "0.0.0.0"
    port: int = Field(default_factory=lambda: int(os.environ.get("PORT", "8090")))

    # Authentication: Google Sign-In ID token verification + email allowlist / open signup
    google_client_id: str = ""
    firebase_project_id: str = ""
    allowlist_emails: str = ""
    owner_email: str = ""
    default_academy_id: str = "osjj-roadmap"
    allow_open_signup: bool = False
    allow_local_dev_bypass: bool = True

    # Model profile: 'local' (Ollama Gemma 4 default) or 'cloud' (Gemini 4 Hydrogen + Neon)
    profile: Literal["cloud", "local"] = "local"
    gemini_api_key: str = Field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    tracker_model: str = "gemini-4-hydrogen"
    adjudicator_model: str = "gemini-4-neon"
    embedding_model: str = "gemini-embedding-2"
    gemma_api_model: str = "gemma-4-26b-a4b-it"

    # Local Ollama settings (Apple Silicon M3 16GB friendly defaults)
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_vision_model: str = "gemma3:4b"
    ollama_text_model: str = "gemma3:4b"

    # Storage & versioned Knowledge Index paths
    knowledge_dir: Path = REPO_ROOT / "knowledge_base"
    active_index_version: str = "v0.1.0"
    runtime_dir: Path = REPO_ROOT / "data" / "runtime"
    local_video_dir: str = ""
    gcs_bucket: str = ""

    def parsed_allowlist(self) -> set[str]:
        return {e.strip().lower() for e in self.allowlist_emails.split(",") if e.strip()}


def get_settings() -> Settings:
    return Settings()
