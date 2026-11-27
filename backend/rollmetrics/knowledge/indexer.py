"""Versioned SQLite FTS5 + 768-d hybrid vector index builder for RollMetrics."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from rollmetrics.config import get_settings
from rollmetrics.knowledge.cta_sources import build_cta_corpus
from rollmetrics.knowledge.ibjjf_sources import build_ibjjf_corpus
from rollmetrics.knowledge.osjj_roadmap import build_osjj_roadmap_corpus
from rollmetrics.knowledge.schema import (
    ArtifactEntry,
    KnowledgeDocument,
    KnowledgeManifest,
)

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")

# Domain synonym expansions so lexical and hashed n-gram vectors bridge BJJ terminology seamlessly
_BJJ_SYNONYMS: dict[str, list[str]] = {
    "nogi": ["no gi", "rashguard", "heel hook"],
    "reap": ["knee reaping", "reaping", "art 6.2.3"],
    "reaping": ["knee reap", "art 6.2.3"],
    "heelhook": ["heel hook", "art 6.2.3"],
    "wristlock": ["wrist lock", "goose neck", "art 6.2.2"],
    "kob": ["knee on belly", "2 points", "art 4.3"],
    "rnc": ["rear naked choke", "back control", "mata leao"],
    "upa": ["trap and roll", "mount escape", "bridge"],
    "shrimp": ["hip escape", "elbow knee"],
    "dummy": ["double ankle sweep", "standing sweep"],
    "catucada": ["hip bump sweep", "sit up sweep"],
    "clamp": ["shoulder clamp", "90 degree", "anti stack"],
    "stack": ["double under", "tripod stack", "shoulder walk"],
    "stalling": ["lack of combativeness", "20 seconds", "penalty"],
    "dq": ["disqualification", "serious foul", "severe foul"],
    "cuff": ["sleeve", "pant grip", "four finger"],
}


def _hash_feature(feature: str, dim: int = EMBEDDING_DIM) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    val = int.from_bytes(digest, "little")
    idx = val % dim
    sign = 1.0 if ((val >> 32) & 1) == 0 else -1.0
    return idx, sign


def compute_local_embedding(text: str, dim: int = EMBEDDING_DIM) -> np.ndarray:
    """Computes a deterministic L2-normalized 768-d hashed word/bigram/char-ngram vector."""
    vec = np.zeros(dim, dtype=np.float32)
    norm_text = (text or "").lower().replace("-", " ").replace("_", " ")
    tokens = _TOKEN_RE.findall(norm_text)
    if not tokens:
        vec[0] = 1.0
        return vec

    expanded_tokens = list(tokens)
    for tok in tokens:
        if tok in _BJJ_SYNONYMS:
            for syn in _BJJ_SYNONYMS[tok]:
                expanded_tokens.extend(_TOKEN_RE.findall(syn))

    # 1. Word unigrams
    for tok in expanded_tokens:
        idx, sign = _hash_feature(f"w1:{tok}", dim)
        vec[idx] += 1.5 * sign

    # 2. Word bigrams
    for i in range(len(tokens) - 1):
        bg = f"w2:{tokens[i]}_{tokens[i + 1]}"
        idx, sign = _hash_feature(bg, dim)
        vec[idx] += 1.9 * sign

    # 3. Character 4-grams within words >= 4 chars
    for tok in set(tokens):
        if len(tok) >= 4:
            padded = f"<{tok}>"
            for j in range(len(padded) - 3):
                cg = f"c4:{padded[j : j + 4]}"
                idx, sign = _hash_feature(cg, dim)
                vec[idx] += 0.45 * sign

    norm = float(np.linalg.norm(vec))
    if norm > 1e-8:
        vec /= norm
    else:
        vec[0] = 1.0
    return vec


def compute_cloud_embeddings(
    texts: list[str],
    api_key: str,
    model_name: str = "gemini-embedding-2",
    dim: int = EMBEDDING_DIM,
) -> list[np.ndarray]:
    """Computes L2-normalized 768-d vectors via google-genai gemini-embedding-2."""
    from concurrent.futures import ThreadPoolExecutor

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    cfg = types.EmbedContentConfig(output_dimensionality=dim)

    def _normalize_vals(vals: list[float] | Any) -> np.ndarray:
        arr = np.asarray(vals, dtype=np.float32)
        if arr.shape[0] != dim:
            resized = np.zeros(dim, dtype=np.float32)
            copy_len = min(dim, arr.shape[0])
            resized[:copy_len] = arr[:copy_len]
            arr = resized
        norm = float(np.linalg.norm(arr))
        if norm > 1e-8:
            arr = arr / norm
        return arr

    # Multimodal gemini-embedding-2 treats a list[str] as parts of 1 Content unless wrapped as Content objects
    results: list[np.ndarray] = []
    batch_size = 32
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        content_objs = [types.Content(parts=[types.Part.from_text(text=t)]) for t in batch]
        resp = client.models.embed_content(model=model_name, contents=content_objs, config=cfg)
        embeddings = resp.embeddings or []
        if len(embeddings) == len(batch):
            for emb in embeddings:
                results.append(_normalize_vals(emb.values))
        else:
            # Per-document concurrent fallback if endpoint collapses batch into single multimodal item
            def _embed_one(item: str) -> np.ndarray:
                single = client.models.embed_content(model=model_name, contents=item, config=cfg)
                if not single.embeddings:
                    raise RuntimeError("Empty embedding response")
                return _normalize_vals(single.embeddings[0].values)

            with ThreadPoolExecutor(max_workers=8) as pool:
                results.extend(list(pool.map(_embed_one, batch)))
    return results


def _doc_embedding_input(doc: KnowledgeDocument) -> str:
    tag_str = " ".join(doc.tags)
    cue = str(doc.metadata.get("coaching_cue", ""))
    return (
        f"{doc.title} | {doc.section_or_belt} | {doc.position.replace('_', ' ')} | "
        f"{tag_str} | {doc.text} | {cue}"
    )


def write_jsonl_documents(path: Path, docs: list[KnowledgeDocument]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(d.model_dump(mode="json"), ensure_ascii=True, sort_keys=True) for d in docs]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_jsonl_documents(path: Path) -> list[KnowledgeDocument]:
    if not path.exists():
        return []
    docs: list[KnowledgeDocument] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            docs.append(KnowledgeDocument.model_validate_json(ln))
    return docs


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _build_sqlite_db(
    db_path: Path,
    all_docs: list[KnowledgeDocument],
    primary_vecs: list[np.ndarray],
    local_vecs: list[np.ndarray],
    embedding_backend: str,
    embedding_model: str,
    version: str,
) -> None:
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=DELETE;")
        conn.execute("PRAGMA synchronous=FULL;")
        conn.execute(
            """
            CREATE TABLE meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY,
                corpus TEXT NOT NULL,
                academy_id TEXT NOT NULL DEFAULT 'global',
                title TEXT NOT NULL,
                section_or_belt TEXT NOT NULL,
                position TEXT NOT NULL,
                text TEXT NOT NULL,
                source_url TEXT NOT NULL,
                timestamp_sec INTEGER,
                tags_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                embedding_backend TEXT NOT NULL,
                embedding BLOB NOT NULL,
                local_embedding BLOB NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX idx_documents_corpus ON documents(corpus);")
        conn.execute("CREATE INDEX idx_documents_academy ON documents(academy_id);")
        conn.execute("CREATE INDEX idx_documents_position ON documents(position);")
        conn.execute(
            """
            CREATE VIRTUAL TABLE documents_fts USING fts5(
                doc_id UNINDEXED,
                corpus,
                title,
                section_or_belt,
                position,
                text,
                tags,
                tokenize='porter unicode61'
            );
            """
        )

        meta_rows = [
            ("version", version),
            ("embedding_backend", embedding_backend),
            ("embedding_model", embedding_model),
            ("embedding_dim", str(EMBEDDING_DIM)),
            ("doc_count", str(len(all_docs))),
        ]
        conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?);", meta_rows)

        for doc, p_vec, l_vec in zip(all_docs, primary_vecs, local_vecs, strict=True):
            tags_json = json.dumps(doc.tags, ensure_ascii=True)
            meta_json = json.dumps(doc.metadata, ensure_ascii=True, sort_keys=True)
            conn.execute(
                """
                INSERT INTO documents(
                    doc_id, corpus, academy_id, title, section_or_belt, position, text,
                    source_url, timestamp_sec, tags_json, metadata_json,
                    embedding_backend, embedding, local_embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    doc.doc_id,
                    doc.corpus,
                    doc.academy_id,
                    doc.title,
                    doc.section_or_belt,
                    doc.position,
                    doc.text,
                    doc.source_url,
                    doc.timestamp_sec,
                    tags_json,
                    meta_json,
                    embedding_backend,
                    np.asarray(p_vec, dtype=np.float32).tobytes(),
                    np.asarray(l_vec, dtype=np.float32).tobytes(),
                ),
            )
            conn.execute(
                """
                INSERT INTO documents_fts(
                    doc_id, corpus, title, section_or_belt, position, text, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    doc.doc_id,
                    doc.corpus,
                    doc.title,
                    doc.section_or_belt,
                    doc.position.replace("_", " "),
                    doc.text,
                    " ".join(doc.tags),
                ),
            )
        conn.commit()
        conn.execute("VACUUM;")
        conn.commit()
    finally:
        conn.close()


def build_knowledge_index(
    version: str = "v0.1.0",
    knowledge_dir: Path | str | None = None,
    embed_cloud: bool = False,
    reuse_existing_jsonl: bool = False,
    fetch_online_pdf: bool = True,
    extra_build_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds versioned JSONL corpora, hybrid SQLite index, and SHA256 manifest.json."""
    settings = get_settings()
    kb_root = Path(knowledge_dir).expanduser().resolve() if knowledge_dir else settings.knowledge_dir
    ver_dir = kb_root / "versions" / version
    ver_dir.mkdir(parents=True, exist_ok=True)

    ibjjf_jsonl = ver_dir / "ibjjf_rules.jsonl"
    cta_jsonl = ver_dir / "cta_curriculum.jsonl"
    osjj_jsonl = ver_dir / "osjj_roadmap_curriculum.jsonl"
    sqlite_path = ver_dir / "index.sqlite"
    manifest_path = ver_dir / "manifest.json"

    sources_meta: dict[str, Any] = {}
    if reuse_existing_jsonl and ibjjf_jsonl.exists() and cta_jsonl.exists():
        ibjjf_docs = load_jsonl_documents(ibjjf_jsonl)
        cta_docs = load_jsonl_documents(cta_jsonl)
        if osjj_jsonl.exists():
            osjj_docs = load_jsonl_documents(osjj_jsonl)
            osjj_meta: dict[str, Any] = {"reused_jsonl": True, "total_osjj_roadmap_docs": len(osjj_docs)}
        else:
            osjj_docs, osjj_meta = build_osjj_roadmap_corpus()
            write_jsonl_documents(osjj_jsonl, osjj_docs)
        sources_meta = {
            "ibjjf": {"reused_jsonl": True, "total_ibjjf_docs": len(ibjjf_docs)},
            "cta": {"reused_jsonl": True, "total_cta_docs": len(cta_docs)},
            "osjj_roadmap": osjj_meta,
        }
    else:
        ibjjf_docs, ibjjf_meta = build_ibjjf_corpus(fetch_online_pdf=fetch_online_pdf)
        cta_docs, cta_meta = build_cta_corpus()
        osjj_docs, osjj_meta = build_osjj_roadmap_corpus()
        write_jsonl_documents(ibjjf_jsonl, ibjjf_docs)
        write_jsonl_documents(cta_jsonl, cta_docs)
        write_jsonl_documents(osjj_jsonl, osjj_docs)
        sources_meta = {"ibjjf": ibjjf_meta, "cta": cta_meta, "osjj_roadmap": osjj_meta}

    all_docs = [*ibjjf_docs, *cta_docs, *osjj_docs]
    embed_inputs = [_doc_embedding_input(d) for d in all_docs]
    local_vecs = [compute_local_embedding(inp, EMBEDDING_DIM) for inp in embed_inputs]

    embedding_backend = "local-ngram-768"
    embedding_model = "local-hashed-word-char-ngram-768"
    primary_vecs = local_vecs

    if embed_cloud and settings.gemini_api_key:
        from google.genai.errors import APIError

        try:
            primary_vecs = compute_cloud_embeddings(
                embed_inputs,
                api_key=settings.gemini_api_key,
                model_name=settings.embedding_model,
                dim=EMBEDDING_DIM,
            )
            embedding_backend = "gemini-embedding-2"
            embedding_model = settings.embedding_model
        except (APIError, OSError, RuntimeError, ValueError, TypeError) as exc:
            logger.warning("Cloud embedding fallback to local-ngram-768: %s", exc)

    _build_sqlite_db(
        db_path=sqlite_path,
        all_docs=all_docs,
        primary_vecs=primary_vecs,
        local_vecs=local_vecs,
        embedding_backend=embedding_backend,
        embedding_model=embedding_model,
        version=version,
    )

    artifacts = {
        "ibjjf_rules.jsonl": ArtifactEntry(
            filename="ibjjf_rules.jsonl",
            sha256=sha256_file(ibjjf_jsonl),
            size_bytes=ibjjf_jsonl.stat().st_size,
            record_count=len(ibjjf_docs),
        ),
        "cta_curriculum.jsonl": ArtifactEntry(
            filename="cta_curriculum.jsonl",
            sha256=sha256_file(cta_jsonl),
            size_bytes=cta_jsonl.stat().st_size,
            record_count=len(cta_docs),
        ),
        "osjj_roadmap_curriculum.jsonl": ArtifactEntry(
            filename="osjj_roadmap_curriculum.jsonl",
            sha256=sha256_file(osjj_jsonl),
            size_bytes=osjj_jsonl.stat().st_size,
            record_count=len(osjj_docs),
        ),
        "index.sqlite": ArtifactEntry(
            filename="index.sqlite",
            sha256=sha256_file(sqlite_path),
            size_bytes=sqlite_path.stat().st_size,
            record_count=len(all_docs),
        ),
    }

    build_meta = {"sources": sources_meta}
    if extra_build_metadata:
        build_meta.update(extra_build_metadata)

    manifest = KnowledgeManifest(
        version=version,
        created_at=dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        embedding_backend=embedding_backend,
        embedding_model=embedding_model,
        embedding_dim=EMBEDDING_DIM,
        counts={
            "ibjjf": len(ibjjf_docs),
            "cta": len(cta_docs) + len(osjj_docs),
            "cta_academy": len(cta_docs),
            "osjj_roadmap": len(osjj_docs),
            "total": len(all_docs),
        },
        artifacts=artifacts,
        build_metadata=build_meta,
    )
    manifest_dict = manifest.model_dump(mode="json")
    manifest_path.write_text(json.dumps(manifest_dict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_dict
