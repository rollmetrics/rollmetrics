"""Schemas and text hygiene utilities for the RollMetrics versioned knowledge base."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

BELT_ORDER: dict[str, int] = {
    "all": 0,
    "white": 1,
    "blue": 2,
    "purple": 3,
    "brown": 4,
    "black": 5,
}

POSITION_ALIASES: dict[str, set[str]] = {
    "closed_guard": {
        "closed_guard",
        "closed guard",
        "closed guard bottom",
        "closed guard top",
        "high guard",
        "clamp guard",
        "overhook guard",
        "full guard",
    },
    "half_guard": {
        "half_guard",
        "half guard",
        "half guard bottom",
        "half guard top",
        "knee shield",
        "z guard",
        "deep half",
    },
    "side_control": {
        "side_control",
        "side control",
        "side control bottom",
        "side control top",
        "north south",
        "north_south",
        "knee on belly",
        "knee_on_belly",
        "kob",
    },
    "mount": {
        "mount",
        "full mount",
        "high mount",
        "low mount",
        "s mount",
        "s-mount",
        "mount bottom",
        "mount top",
        "mounted",
    },
    "turtle": {
        "turtle",
        "turtle bottom",
        "turtle top",
        "bottom turtle",
        "top turtle",
        "front headlock",
        "four point",
    },
    "back_control": {
        "back_control",
        "back control",
        "back mount",
        "rear mount",
        "back attack",
        "back escape",
        "back",
    },
    "butterfly_guard": {
        "butterfly_guard",
        "butterfly guard",
        "butterfly",
        "seated guard",
    },
    "open_guard": {
        "open_guard",
        "open guard",
        "de la riva",
        "reverse de la riva",
        "rdlr",
        "dlr",
        "spider guard",
        "collar sleeve",
        "lasso",
        "50/50",
    },
    "standing_pass": {
        "standing_pass",
        "standing pass",
        "standing passes",
        "guard pass",
        "guard passing",
        "passing",
        "double under",
        "over under",
        "toreando",
        "knee cut",
    },
    "takedowns": {
        "takedowns",
        "takedown",
        "standing",
        "standup",
        "stand-up",
        "guard pull",
        "wrestle up",
        "wrestle-up",
        "single leg",
        "double leg",
    },
    "all": {"all", "any", "general", "match", "rules"},
}

_EM_DASH_RE = re.compile(r"[\u2014\u2013\u2015]+")
_NOT_X_ITS_Y_RE = re.compile(r"\bnot\s+([^,.;:!?]{1,50}),\s*it['\u2019]?s\s+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"[ \t]+")


def sanitize_text(value: str) -> str:
    """Removes forbidden punctuation and phrasing while keeping clean ASCII-friendly prose."""
    if not value:
        return ""
    cleaned = _EM_DASH_RE.sub(" - ", value)
    cleaned = _NOT_X_ITS_Y_RE.sub("instead of \\1, this uses ", cleaned)
    cleaned = cleaned.replace("\u2019", "'").replace("\u2018", "'")
    cleaned = cleaned.replace("\u201c", '"').replace("\u201d", '"')
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _sanitize_any(obj: Any) -> Any:
    if isinstance(obj, str):
        return sanitize_text(obj)
    if isinstance(obj, list):
        return [_sanitize_any(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _sanitize_any(v) for k, v in obj.items()}
    return obj


def canonicalize_position(raw: str | None) -> str:
    """Maps free-form position labels to canonical RollMetrics position slugs."""
    if not raw:
        return "all"
    norm = re.sub(r"[_/\-]+", " ", raw.strip().lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return "all"
    for canonical, aliases in POSITION_ALIASES.items():
        if norm == canonical or norm in aliases:
            return canonical
    for canonical, aliases in POSITION_ALIASES.items():
        if canonical == "all":
            continue
        for alias in aliases:
            if alias in norm or norm in alias:
                return canonical
    return norm.replace(" ", "_")


def positions_match(query_pos: str | None, doc_pos: str | None) -> bool:
    """Checks whether a document position satisfies a requested position filter."""
    if not query_pos or canonicalize_position(query_pos) == "all":
        return True
    q_can = canonicalize_position(query_pos)
    d_can = canonicalize_position(doc_pos)
    if d_can == "all" or q_can == d_can:
        return True
    # Related position transitions (for example standing guard pass <-> closed/open/butterfly guard)
    related_pairs = {
        ("standing_pass", "open_guard"),
        ("standing_pass", "butterfly_guard"),
        ("standing_pass", "closed_guard"),
        ("takedowns", "standing_pass"),
        ("open_guard", "butterfly_guard"),
    }
    return (q_can, d_can) in related_pairs


def normalize_academy_slug(raw: str | None, default: str = "cta") -> str:
    """Normalizes an academy or school identifier into a safe lowercase slug."""
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", str(raw or "").strip().lower()).strip("-_")
    return cleaned or default


class KnowledgeDocument(BaseModel):
    """Canonical record written to ibjjf_rules.jsonl and academy curriculum indexes."""

    doc_id: str
    corpus: Literal["ibjjf", "cta"]
    academy_id: str = ""
    title: str
    section_or_belt: str
    position: str = "all"
    text: str
    source_url: str
    timestamp_sec: int | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _clean_fields(self) -> KnowledgeDocument:
        self.doc_id = self.doc_id.strip()
        if not self.academy_id.strip():
            self.academy_id = "global" if self.corpus == "ibjjf" else "cta"
        else:
            self.academy_id = normalize_academy_slug(
                self.academy_id,
                default="global" if self.corpus == "ibjjf" else "cta",
            )
        self.title = sanitize_text(self.title)
        self.section_or_belt = sanitize_text(self.section_or_belt)
        self.position = canonicalize_position(self.position)
        self.text = sanitize_text(self.text)
        self.source_url = self.source_url.strip()
        self.tags = [sanitize_text(t).lower() for t in self.tags if t and t.strip()]
        self.metadata = _sanitize_any(self.metadata)
        return self


class KnowledgeHit(BaseModel):
    """Search result contract shared with video analyzer, live HUD, and FastAPI routes."""

    doc_id: str
    corpus: Literal["ibjjf", "cta"]
    academy_id: str = "global"
    title: str
    section_or_belt: str
    position: str
    text: str
    source_url: str
    timestamp_sec: int | None = None
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactEntry(BaseModel):
    """Checksum and size record for a single versioned knowledge base file."""

    filename: str
    sha256: str
    size_bytes: int
    record_count: int | None = None


class KnowledgeManifest(BaseModel):
    """Content-addressed manifest stored at knowledge_base/versions/<version>/manifest.json."""

    version: str
    created_at: str
    embedding_backend: str
    embedding_model: str
    embedding_dim: int = 768
    counts: dict[str, int]
    artifacts: dict[str, ArtifactEntry]
    build_metadata: dict[str, Any] = Field(default_factory=dict)
