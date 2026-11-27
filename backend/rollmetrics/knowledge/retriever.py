"""Hybrid SQLite FTS5 BM25 + 768-d vector retriever for IBJJF rules and CTA curriculum."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from rollmetrics.config import Settings, get_settings
from rollmetrics.knowledge.academies import search_academy_references
from rollmetrics.knowledge.indexer import (
    EMBEDDING_DIM,
    build_knowledge_index,
    compute_cloud_embeddings,
    compute_local_embedding,
)
from rollmetrics.knowledge.schema import (
    BELT_ORDER,
    KnowledgeHit,
    canonicalize_position,
    normalize_academy_slug,
    positions_match,
)

logger = logging.getLogger(__name__)

_FTS_WORDS_RE = re.compile(r"[a-zA-Z0-9]{2,}")
_ARTICLE_NUM_RE = re.compile(r"\b([1-8]\.[0-9]+(?:\.[0-9]+)?)\b")
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "when",
        "that",
        "this",
        "how",
        "what",
        "your",
        "his",
        "her",
        "their",
        "are",
        "was",
        "were",
        "can",
        "will",
        "not",
        "all",
        "any",
    }
)


class KnowledgeRetriever:
    """Versioned hybrid retriever over IBJJF rules/videos and Caio Terra Association curriculum."""

    def __init__(
        self,
        knowledge_dir: Path | str | None = None,
        version: str = "v0.1.0",
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        base_dir = knowledge_dir if knowledge_dir is not None else self.settings.knowledge_dir
        self.knowledge_dir = Path(base_dir).expanduser().resolve()
        self.version = version or self.settings.active_index_version
        self.version_dir = self.knowledge_dir / "versions" / self.version
        self.db_path = self.version_dir / "index.sqlite"
        self.manifest_path = self.version_dir / "manifest.json"

        if not self.db_path.exists() or not self.manifest_path.exists():
            build_knowledge_index(
                version=self.version,
                knowledge_dir=self.knowledge_dir,
                embed_cloud=False,
                fetch_online_pdf=False,
            )

        self._rows_cache: list[dict[str, Any]] | None = None
        self._local_matrix: np.ndarray | None = None
        self._primary_matrix: np.ndarray | None = None
        self._index_backend: str = "local-ngram-768"

    def _connect_ro(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_loaded(self) -> None:
        if self._rows_cache is not None:
            return
        conn = self._connect_ro()
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta;").fetchall())
            self._index_backend = meta.get("embedding_backend", "local-ngram-768")
            cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(documents);").fetchall()}
            has_academy_col = "academy_id" in cols
            academy_select = "academy_id," if has_academy_col else ""
            raw_rows = conn.execute(
                f"""
                SELECT doc_id, corpus, {academy_select} title, section_or_belt, position, text,
                       source_url, timestamp_sec, tags_json, metadata_json,
                       embedding, local_embedding
                FROM documents
                ORDER BY rowid ASC;
                """
            ).fetchall()
        finally:
            conn.close()

        rows: list[dict[str, Any]] = []
        p_vecs: list[np.ndarray] = []
        l_vecs: list[np.ndarray] = []
        for r in raw_rows:
            row_corpus = str(r["corpus"])
            raw_acad = str(r["academy_id"]) if has_academy_col else ""
            resolved_acad = (
                normalize_academy_slug(raw_acad, default="global" if row_corpus == "ibjjf" else "cta")
                if raw_acad
                else ("global" if row_corpus == "ibjjf" else "cta")
            )
            rows.append(
                {
                    "doc_id": r["doc_id"],
                    "corpus": row_corpus,
                    "academy_id": resolved_acad,
                    "title": r["title"],
                    "section_or_belt": r["section_or_belt"],
                    "position": r["position"],
                    "text": r["text"],
                    "source_url": r["source_url"],
                    "timestamp_sec": r["timestamp_sec"],
                    "tags": json.loads(r["tags_json"]),
                    "metadata": json.loads(r["metadata_json"]),
                }
            )
            p_vecs.append(np.frombuffer(r["embedding"], dtype=np.float32).copy())
            l_vecs.append(np.frombuffer(r["local_embedding"], dtype=np.float32).copy())

        self._rows_cache = rows
        if rows:
            self._primary_matrix = np.vstack(p_vecs)
            self._local_matrix = np.vstack(l_vecs)
        else:
            self._primary_matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
            self._local_matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

    def manifest(self) -> dict[str, Any]:
        """Returns the versioned manifest.json dictionary."""
        if not self.manifest_path.exists():
            return {"version": self.version, "counts": {"ibjjf": 0, "cta": 0, "total": 0}, "artifacts": {}}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _fts_bm25_scores(self, query: str) -> dict[str, float]:
        raw_tokens = [t.lower() for t in _FTS_WORDS_RE.findall(query) if len(t) >= 2]
        tokens = [t for t in raw_tokens if t not in _STOPWORDS] or raw_tokens
        if not tokens:
            return {}
        fts_expr = " OR ".join(f'"{tok}"*' for tok in dict.fromkeys(tokens[:16]))
        scores: dict[str, float] = {}
        conn = self._connect_ro()
        try:
            rows = conn.execute(
                """
                SELECT doc_id,
                       -bm25(documents_fts, 0.0, 0.4, 4.0, 3.5, 2.5, 1.4, 3.0) AS lex_score
                FROM documents_fts
                WHERE documents_fts MATCH ?
                ORDER BY bm25(documents_fts, 0.0, 0.4, 4.0, 3.5, 2.5, 1.4, 3.0) ASC
                LIMIT 100;
                """,
                (fts_expr,),
            ).fetchall()
            for r in rows:
                scores[str(r["doc_id"])] = max(0.0, float(r["lex_score"]))
        except sqlite3.Error as exc:
            logger.debug("FTS5 query fallback (%s): %s", fts_expr, exc)
        finally:
            conn.close()

        if not scores:
            return {}
        max_sc = max(scores.values())
        if max_sc > 1e-8:
            return {k: v / max_sc for k, v in scores.items()}
        return scores

    def _embed_query(self, query: str) -> tuple[np.ndarray, bool]:
        if (
            self._index_backend == "gemini-embedding-2"
            and self.settings.profile == "cloud"
            and self.settings.gemini_api_key
        ):
            from google.genai.errors import APIError

            try:
                vecs = compute_cloud_embeddings(
                    [query],
                    api_key=self.settings.gemini_api_key,
                    model_name=self.settings.embedding_model,
                    dim=EMBEDDING_DIM,
                )
                return vecs[0], True
            except (APIError, OSError, RuntimeError, ValueError, TypeError) as exc:
                logger.debug("Query cloud embedding fallback to local-ngram-768: %s", exc)
        return compute_local_embedding(query, EMBEDDING_DIM), False

    def search(
        self,
        query: str,
        corpus: str = "all",
        top_k: int = 5,
        position: str | None = None,
        academy_id: str | None = None,
    ) -> list[KnowledgeHit]:
        """Hybrid BM25 + 768-d cosine search across global IBJJF and tenant-scoped academy corpora."""
        self._ensure_loaded()
        assert self._rows_cache is not None
        assert self._local_matrix is not None
        assert self._primary_matrix is not None

        if not self._rows_cache or top_k <= 0:
            return []

        norm_corpus = (corpus or "all").strip().lower()
        norm_academy = normalize_academy_slug(academy_id, default="cta") if academy_id else None
        q_clean = (query or "").strip()
        if not q_clean and position:
            q_clean = position

        bm25_map = self._fts_bm25_scores(q_clean)
        q_vec, used_primary = self._embed_query(q_clean)
        mat = self._primary_matrix if used_primary else self._local_matrix
        dense_sims = mat @ q_vec

        article_nums = _ARTICLE_NUM_RE.findall(q_clean)
        q_pos_can = canonicalize_position(position) if position else None

        def _row_allowed(row: dict[str, Any]) -> bool:
            if norm_corpus not in ("all", row["corpus"]):
                return False
            if norm_academy is not None:
                row_acad = str(row.get("academy_id") or ("global" if row["corpus"] == "ibjjf" else "cta"))
                if row_acad not in ("global", norm_academy):
                    return False
            return True

        # Pre-check if strict position filter yields candidates in requested corpus + academy
        strict_pos_candidates = 0
        if q_pos_can and q_pos_can != "all":
            for row in self._rows_cache:
                if not _row_allowed(row):
                    continue
                if positions_match(q_pos_can, row["position"]):
                    strict_pos_candidates += 1

        scored: list[tuple[float, dict[str, Any]]] = []
        q_lower = q_clean.lower()

        for idx, row in enumerate(self._rows_cache):
            if not _row_allowed(row):
                continue
            if (
                q_pos_can
                and q_pos_can != "all"
                and strict_pos_candidates > 0
                and not positions_match(q_pos_can, row["position"])
            ):
                continue

            lex = bm25_map.get(row["doc_id"], 0.0)
            sem = max(0.0, float(dense_sims[idx]))
            hybrid = 0.56 * lex + 0.44 * sem

            # Exact IBJJF article citation boost
            if article_nums and row["corpus"] == "ibjjf":
                sec_low = row["section_or_belt"].lower()
                txt_low = row["text"].lower()
                for art_n in article_nums:
                    if art_n in sec_low:
                        hybrid += 0.38
                        if row["metadata"].get("source_kind") == "structured_article":
                            hybrid += 0.10
                    elif art_n in txt_low:
                        hybrid += 0.16

            # Exact canonical position match bonus
            if q_pos_can and q_pos_can != "all" and row["position"] == q_pos_can:
                hybrid += 0.12

            # Curated lesson quality tie-breaker over unedited raw transcript chunks
            if row["corpus"] == "cta":
                sk = row["metadata"].get("source_kind", "")
                if sk in ("curated_cta_masterclass", "private_markdown", "private_json", "private_transcript"):
                    hybrid += 0.10
                elif sk in ("cta_sterling_curriculum", "caio_study_reel"):
                    hybrid += 0.05

            # Substring title match boost
            if len(q_lower) >= 4 and q_lower in row["title"].lower():
                hybrid += 0.15

            scored.append((hybrid, row))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        hits: list[KnowledgeHit] = []
        for sc, row in scored[:top_k]:
            hits.append(
                KnowledgeHit(
                    doc_id=row["doc_id"],
                    corpus=row["corpus"],
                    academy_id=str(row.get("academy_id") or ("global" if row["corpus"] == "ibjjf" else "cta")),
                    title=row["title"],
                    section_or_belt=row["section_or_belt"],
                    position=row["position"],
                    text=row["text"],
                    source_url=row["source_url"],
                    timestamp_sec=row["timestamp_sec"],
                    score=round(float(sc), 4),
                    metadata=row["metadata"],
                )
            )

        # Merge live tenant-isolated private academy references (never cross-tenant)
        if norm_academy is not None and norm_corpus in ("all", "cta"):
            priv_hits = search_academy_references(
                self.settings,
                academy_id=norm_academy,
                query=q_clean,
                top_k=top_k,
                position=position,
            )
            if priv_hits:
                merged_by_id: dict[str, KnowledgeHit] = {h.doc_id: h for h in hits}
                for ph in priv_hits:
                    merged_by_id[ph.doc_id] = ph
                hits = sorted(merged_by_id.values(), key=lambda item: item.score, reverse=True)[:top_k]

        return hits

    def recommend_cta_for_position(
        self,
        position: str,
        situation: str = "",
        belt: str = "white",
        top_k: int = 3,
        academy_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Returns belt-safe academy curriculum recommendations with timestamp links."""
        pos_can = canonicalize_position(position)
        belt_norm = (belt or "white").strip().lower()
        max_belt_rank = BELT_ORDER.get(belt_norm, 1)

        composite_query = f"{position.replace('_', ' ')} {situation}".strip()
        raw_hits = self.search(
            query=composite_query,
            corpus="cta",
            top_k=max(top_k * 3, 10),
            position=pos_can if pos_can != "all" else None,
            academy_id=academy_id,
        )

        recs: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for hit in raw_hits:
            if hit.doc_id in seen_ids:
                continue
            min_belt = str(hit.metadata.get("min_belt", "white")).lower()
            req_rank = BELT_ORDER.get(min_belt, 1)
            if req_rank > max_belt_rank:
                continue
            seen_ids.add(hit.doc_id)
            cue = str(hit.metadata.get("coaching_cue") or hit.text[:220])
            recs.append(
                {
                    "doc_id": hit.doc_id,
                    "corpus": "cta",
                    "title": hit.title,
                    "position": hit.position,
                    "belt": hit.section_or_belt,
                    "section_or_belt": hit.section_or_belt,
                    "cue": cue,
                    "text": hit.text,
                    "source_url": hit.source_url,
                    "youtube_url": hit.source_url,
                    "timestamp_sec": hit.timestamp_sec,
                    "instructor": str(hit.metadata.get("instructor", "Caio Terra")),
                    "score": hit.score,
                    "metadata": hit.metadata,
                }
            )
            if len(recs) >= top_k:
                break
        return recs

    def cite_ibjjf_rule(self, query_or_article: str, top_k: int = 2) -> list[dict[str, Any]]:
        """Returns top IBJJF rulebook articles and official rules video citations."""
        hits = self.search(query=query_or_article, corpus="ibjjf", top_k=top_k)
        citations: list[dict[str, Any]] = []
        for hit in hits:
            vid_url = str(
                hit.metadata.get("video_url")
                or (hit.source_url if "youtube.com" in hit.source_url or "youtu.be" in hit.source_url else "")
            )
            citations.append(
                {
                    "doc_id": hit.doc_id,
                    "corpus": "ibjjf",
                    "article": hit.section_or_belt,
                    "section_or_belt": hit.section_or_belt,
                    "title": hit.title,
                    "position": hit.position,
                    "text": hit.text,
                    "source_url": hit.source_url,
                    "video_url": vid_url,
                    "timestamp_sec": hit.timestamp_sec,
                    "score": hit.score,
                    "metadata": hit.metadata,
                }
            )
        return citations
