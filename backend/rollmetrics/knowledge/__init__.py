"""RollMetrics versioned IBJJF & Caio Terra Association (CTA) knowledge base package."""

from __future__ import annotations

from rollmetrics.knowledge.cta_sources import build_cta_corpus, yt_timestamp_url
from rollmetrics.knowledge.ibjjf_sources import build_ibjjf_corpus
from rollmetrics.knowledge.indexer import (
    EMBEDDING_DIM,
    build_knowledge_index,
    compute_local_embedding,
    sha256_file,
)
from rollmetrics.knowledge.private_importer import import_private_curriculum
from rollmetrics.knowledge.retriever import KnowledgeRetriever
from rollmetrics.knowledge.schema import (
    ArtifactEntry,
    KnowledgeDocument,
    KnowledgeHit,
    KnowledgeManifest,
    canonicalize_position,
    positions_match,
    sanitize_text,
)

__all__ = [
    "EMBEDDING_DIM",
    "ArtifactEntry",
    "KnowledgeDocument",
    "KnowledgeHit",
    "KnowledgeManifest",
    "KnowledgeRetriever",
    "build_cta_corpus",
    "build_ibjjf_corpus",
    "build_knowledge_index",
    "canonicalize_position",
    "compute_local_embedding",
    "import_private_curriculum",
    "positions_match",
    "sanitize_text",
    "sha256_file",
    "yt_timestamp_url",
]
