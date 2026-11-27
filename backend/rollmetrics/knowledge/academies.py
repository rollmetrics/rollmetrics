"""Tenant-scoped Academy / School Reference Library storage and search."""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from rollmetrics.config import Settings
from rollmetrics.knowledge.indexer import EMBEDDING_DIM, compute_local_embedding
from rollmetrics.knowledge.schema import (
    KnowledgeDocument,
    KnowledgeHit,
    canonicalize_position,
    normalize_academy_slug,
    positions_match,
    sanitize_text,
)

DEFAULT_SEEDED_ACADEMY_ID = "cta"
DEFAULT_SEEDED_ACADEMY_NAME = "Caio Terra Association"
DEFAULT_PUBLIC_ACADEMY_ID = "osjj-roadmap"
DEFAULT_PUBLIC_ACADEMY_NAME = "Open Source Jiu-Jitsu & Roadmap"
DEFAULT_FALLBACK_ACADEMY_NAME = "Academy Library"


def _academies_path(settings: Settings) -> Path:
    return settings.runtime_dir / "academies.json"


def _academy_refs_path(settings: Settings, academy_id: str) -> Path:
    slug = normalize_academy_slug(academy_id, default=DEFAULT_SEEDED_ACADEMY_ID)
    return settings.runtime_dir / "academy_references" / f"{slug}.jsonl"


def _seed_defaults(data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if DEFAULT_SEEDED_ACADEMY_ID not in data:
        data[DEFAULT_SEEDED_ACADEMY_ID] = {
            "academy_id": DEFAULT_SEEDED_ACADEMY_ID,
            "academy_name": DEFAULT_SEEDED_ACADEMY_NAME,
            "is_seeded": True,
            "created_at": "2026-01-01 00:00 UTC",
            "updated_at": "2026-01-01 00:00 UTC",
        }
    if DEFAULT_PUBLIC_ACADEMY_ID not in data:
        data[DEFAULT_PUBLIC_ACADEMY_ID] = {
            "academy_id": DEFAULT_PUBLIC_ACADEMY_ID,
            "academy_name": DEFAULT_PUBLIC_ACADEMY_NAME,
            "is_seeded": True,
            "created_at": "2026-01-01 00:00 UTC",
            "updated_at": "2026-01-01 00:00 UTC",
        }
    return data


def load_academies(settings: Settings) -> dict[str, dict[str, Any]]:
    """Loads persisted academies from data/runtime/academies.json with CTA seeded."""
    path = _academies_path(settings)
    academies: dict[str, dict[str, Any]] = {}
    if path.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, val in raw.items():
                    if isinstance(val, dict):
                        slug = normalize_academy_slug(key, default=DEFAULT_SEEDED_ACADEMY_ID)
                        academies[slug] = {
                            "academy_id": slug,
                            "academy_name": sanitize_text(
                                str(val.get("academy_name") or DEFAULT_FALLBACK_ACADEMY_NAME)
                            )
                            or DEFAULT_FALLBACK_ACADEMY_NAME,
                            "is_seeded": bool(val.get("is_seeded", slug == DEFAULT_SEEDED_ACADEMY_ID)),
                            "created_at": str(val.get("created_at") or ""),
                            "updated_at": str(val.get("updated_at") or ""),
                        }
    return _seed_defaults(academies)


def save_academies(settings: Settings, academies: dict[str, dict[str, Any]]) -> None:
    path = _academies_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_seed_defaults(dict(academies)), indent=2, sort_keys=True), encoding="utf-8")


def upsert_academy(
    settings: Settings,
    academy_name: str,
    academy_id: str | None = None,
) -> dict[str, Any]:
    """Creates or updates an academy record in data/runtime/academies.json."""
    clean_name = sanitize_text(academy_name) or DEFAULT_FALLBACK_ACADEMY_NAME
    slug = normalize_academy_slug(academy_id or clean_name, default="academy")
    if slug == "global":
        slug = "academy-global"
    now_str = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    academies = load_academies(settings)
    existing = academies.get(slug, {})
    record = {
        "academy_id": slug,
        "academy_name": clean_name,
        "is_seeded": bool(existing.get("is_seeded", slug == DEFAULT_SEEDED_ACADEMY_ID)),
        "created_at": str(existing.get("created_at") or now_str),
        "updated_at": now_str,
    }
    academies[slug] = record
    save_academies(settings, academies)
    return record


def get_academy(settings: Settings, academy_id: str) -> dict[str, Any]:
    slug = normalize_academy_slug(academy_id, default=DEFAULT_SEEDED_ACADEMY_ID)
    academies = load_academies(settings)
    if slug in academies:
        return academies[slug]
    return {
        "academy_id": slug,
        "academy_name": DEFAULT_SEEDED_ACADEMY_NAME
        if slug == DEFAULT_SEEDED_ACADEMY_ID
        else DEFAULT_FALLBACK_ACADEMY_NAME,
        "is_seeded": slug == DEFAULT_SEEDED_ACADEMY_ID,
        "created_at": "",
        "updated_at": "",
    }


def default_academy_for_email(email: str, settings: Settings | None = None) -> tuple[str, str]:
    clean_email = (email or "").strip().lower()
    if settings is not None:
        owner = settings.owner_email.strip().lower()
        def_acad = normalize_academy_slug(settings.default_academy_id, default=DEFAULT_PUBLIC_ACADEMY_ID)
        if owner and clean_email == owner:
            if def_acad == DEFAULT_SEEDED_ACADEMY_ID:
                return DEFAULT_SEEDED_ACADEMY_ID, DEFAULT_SEEDED_ACADEMY_NAME
            rec = get_academy(settings, def_acad)
            return rec["academy_id"], rec["academy_name"]
        if def_acad == DEFAULT_PUBLIC_ACADEMY_ID and clean_email:
            return DEFAULT_PUBLIC_ACADEMY_ID, DEFAULT_PUBLIC_ACADEMY_NAME
    prefix = clean_email.split("@")[0] if "@" in clean_email else clean_email
    slug = normalize_academy_slug(f"academy-{prefix}" if prefix else "academy-personal", default="academy-personal")
    return slug, DEFAULT_FALLBACK_ACADEMY_NAME


def list_academy_references(settings: Settings, academy_id: str) -> list[KnowledgeDocument]:
    """Returns private coaching notes and reference video docs strictly for academy_id."""
    slug = normalize_academy_slug(academy_id, default=DEFAULT_SEEDED_ACADEMY_ID)
    path = _academy_refs_path(settings, slug)
    if not path.is_file():
        return []
    docs: list[KnowledgeDocument] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        with contextlib.suppress(ValueError, json.JSONDecodeError):
            doc = KnowledgeDocument.model_validate_json(line)
            if doc.academy_id == slug:
                docs.append(doc)
    return docs


def add_academy_reference(
    settings: Settings,
    academy_id: str,
    *,
    title: str,
    text: str,
    position: str = "all",
    belt: str = "white",
    source_url: str = "",
    reference_type: str = "note",
    instructor: str = "",
    tags: list[str] | None = None,
    timestamp_sec: int | None = None,
) -> KnowledgeDocument:
    """Appends a tenant-isolated reference note or video link into academy_references/<slug>.jsonl."""
    slug = normalize_academy_slug(academy_id, default=DEFAULT_SEEDED_ACADEMY_ID)
    if slug == "global":
        slug = "academy-global"
    academy = get_academy(settings, slug)
    clean_title = sanitize_text(title)
    clean_text = sanitize_text(text)
    if not clean_title:
        raise ValueError("Reference title is required.")
    if not clean_text:
        raise ValueError("Reference text or coaching detail is required.")

    ref_kind = "video" if str(reference_type).strip().lower() == "video" else "note"
    raw_seed = f"{slug}:{clean_title}:{clean_text[:120]}:{time.time_ns()}"
    digest = hashlib.sha256(raw_seed.encode("utf-8")).hexdigest()[:12]
    doc_id = f"acad-{slug}-{ref_kind}-{digest}"
    belt_norm = str(belt or "white").strip().lower()
    if belt_norm not in ("all", "white", "blue", "purple", "brown", "black"):
        belt_norm = "white"
    pos_can = canonicalize_position(position)
    clean_url = (source_url or "").strip() or f"local://academy/{slug}/{doc_id}"
    clean_instructor = sanitize_text(instructor) or academy["academy_name"]
    clean_tags = list(dict.fromkeys([slug, ref_kind, pos_can, *(tags or [])]))

    doc = KnowledgeDocument(
        doc_id=doc_id,
        corpus="cta",
        academy_id=slug,
        title=clean_title,
        section_or_belt=belt_norm,
        position=pos_can,
        text=clean_text,
        source_url=clean_url,
        timestamp_sec=timestamp_sec,
        tags=clean_tags,
        metadata={
            "academy_id": slug,
            "academy_name": academy["academy_name"],
            "instructor": clean_instructor,
            "min_belt": belt_norm,
            "coaching_cue": clean_text[:220],
            "reference_type": ref_kind,
            "source_kind": "academy_private_reference",
            "created_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        },
    )

    path = _academy_refs_path(settings, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(doc.model_dump(mode="json"), ensure_ascii=True, sort_keys=True) + "\n")
    return doc


def search_academy_references(
    settings: Settings,
    academy_id: str,
    query: str,
    top_k: int = 5,
    position: str | None = None,
) -> list[KnowledgeHit]:
    """Searches strictly within a single academy's private reference JSONL store."""
    docs = list_academy_references(settings, academy_id)
    if not docs or top_k <= 0:
        return []

    q_clean = (query or "").strip()
    q_lower = q_clean.lower()
    q_tokens = [tok for tok in q_lower.split() if len(tok) >= 2]
    q_vec = compute_local_embedding(q_clean or (position or ""), EMBEDDING_DIM)
    q_pos_can = canonicalize_position(position) if position else None

    scored: list[tuple[float, KnowledgeDocument]] = []
    for doc in docs:
        if q_pos_can and q_pos_can != "all" and not positions_match(q_pos_can, doc.position):
            continue
        doc_blob = f"{doc.title} {doc.section_or_belt} {doc.position} {' '.join(doc.tags)} {doc.text}"
        d_vec = compute_local_embedding(doc_blob, EMBEDDING_DIM)
        sem = max(0.0, float(d_vec @ q_vec))
        blob_low = doc_blob.lower()
        lex = 0.0
        if q_tokens:
            matches = sum(1 for tok in q_tokens if tok in blob_low)
            lex = matches / float(len(q_tokens))
        score = 0.56 * lex + 0.44 * sem + 0.12
        if len(q_lower) >= 4 and q_lower in doc.title.lower():
            score += 0.18
        scored.append((score, doc))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        KnowledgeHit(
            doc_id=doc.doc_id,
            corpus=doc.corpus,
            academy_id=doc.academy_id,
            title=doc.title,
            section_or_belt=doc.section_or_belt,
            position=doc.position,
            text=doc.text,
            source_url=doc.source_url,
            timestamp_sec=doc.timestamp_sec,
            score=round(float(sc), 4),
            metadata=doc.metadata,
        )
        for sc, doc in scored[:top_k]
    ]
