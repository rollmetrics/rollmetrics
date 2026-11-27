"""Private CTA / academy curriculum drop importer creating a new content-addressed KB version."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from rollmetrics.config import get_settings
from rollmetrics.knowledge.indexer import build_knowledge_index, load_jsonl_documents, write_jsonl_documents
from rollmetrics.knowledge.schema import (
    KnowledgeDocument,
    canonicalize_position,
    normalize_academy_slug,
    sanitize_text,
)


def _slug_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _parse_markdown_drop(md_path: Path) -> list[KnowledgeDocument]:
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    sections = re.split(r"(?m)^#{1,3}\s+", text)
    docs: list[KnowledgeDocument] = []
    if len(sections) <= 1:
        cleaned = sanitize_text(text)
        if cleaned:
            docs.append(
                KnowledgeDocument(
                    doc_id=f"cta-private-md-{_slug_hash(md_path.name + cleaned[:120])}",
                    corpus="cta",
                    title=f"Private Academy Note: {md_path.stem.replace('_', ' ').title()}",
                    section_or_belt="white",
                    position=canonicalize_position(md_path.stem),
                    text=cleaned[:2400],
                    source_url=f"local://private_curriculum/{md_path.name}",
                    timestamp_sec=None,
                    tags=["cta", "private curriculum", md_path.stem.lower()],
                    metadata={
                        "instructor": "Caio Terra Online / Academy Notes",
                        "min_belt": "white",
                        "coaching_cue": cleaned[:220],
                        "source_kind": "private_markdown",
                        "filename": md_path.name,
                    },
                )
            )
        return docs

    lines = text.splitlines()
    current_heading = md_path.stem.replace("_", " ").title()
    buf: list[str] = []

    def _flush(heading: str, body_lines: list[str]) -> None:
        body = sanitize_text(" ".join(body_lines))
        if not body:
            return
        pos = canonicalize_position(f"{md_path.stem} {heading}")
        docs.append(
            KnowledgeDocument(
                doc_id=f"cta-private-md-{_slug_hash(md_path.name + heading + body[:80])}",
                corpus="cta",
                title=f"CTA Private Note: {sanitize_text(heading)}",
                section_or_belt="white",
                position=pos if pos != "all" else "closed_guard",
                text=body[:2400],
                source_url=f"local://private_curriculum/{md_path.name}",
                timestamp_sec=None,
                tags=["cta", "private curriculum", heading.lower()],
                metadata={
                    "instructor": "Caio Terra Online / Academy Notes",
                    "min_belt": "white",
                    "coaching_cue": body[:220],
                    "source_kind": "private_markdown",
                    "filename": md_path.name,
                },
            )
        )

    for ln in lines:
        m = re.match(r"^#{1,3}\s+(.+)$", ln.strip())
        if m:
            if buf:
                _flush(current_heading, buf)
                buf = []
            current_heading = m.group(1).strip()
        else:
            buf.append(ln.strip())
    if buf:
        _flush(current_heading, buf)
    return docs


def _parse_json_or_jsonl_drop(path: Path) -> list[KnowledgeDocument]:
    docs: list[KnowledgeDocument] = []
    raw_text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not raw_text:
        return docs

    records: list[dict[str, Any]] = []
    if path.suffix.lower() == ".jsonl":
        for ln in raw_text.splitlines():
            if ln.strip():
                records.append(json.loads(ln))
    else:
        parsed = json.loads(raw_text)
        if isinstance(parsed, list):
            records = [r for r in parsed if isinstance(r, dict)]
        elif isinstance(parsed, dict):
            if "items" in parsed and isinstance(parsed["items"], list):
                records = [r for r in parsed["items"] if isinstance(r, dict)]
            elif "chapters" in parsed and isinstance(parsed["chapters"], list):
                records = [r for r in parsed["chapters"] if isinstance(r, dict)]
            else:
                records = [parsed]

    for idx, rec in enumerate(records):
        title = str(rec.get("title") or rec.get("label") or f"{path.stem} #{idx + 1}")
        body = str(rec.get("text") or rec.get("transcript") or rec.get("cue") or rec.get("summary") or title)
        pos = canonicalize_position(str(rec.get("position") or title))
        belt = str(rec.get("belt") or rec.get("section_or_belt") or "white").lower()
        t_sec_raw = rec.get("timestamp_sec", rec.get("sec"))
        t_sec = int(t_sec_raw) if t_sec_raw is not None else None
        url = str(rec.get("source_url") or rec.get("url") or f"local://private_curriculum/{path.name}")
        doc_id = str(rec.get("doc_id") or f"cta-private-json-{_slug_hash(path.name + title + str(idx))}")
        docs.append(
            KnowledgeDocument(
                doc_id=doc_id,
                corpus="cta",
                title=title,
                section_or_belt=belt,
                position=pos if pos != "all" else "closed_guard",
                text=body,
                source_url=url,
                timestamp_sec=t_sec,
                tags=["cta", "private curriculum", *[str(t) for t in rec.get("tags", [])]],
                metadata={
                    "instructor": str(rec.get("instructor") or "Caio Terra Online"),
                    "min_belt": belt if belt in ("white", "blue", "purple", "brown", "black") else "white",
                    "coaching_cue": str(rec.get("cue") or sanitize_text(body)[:220]),
                    "source_kind": "private_json",
                    "filename": path.name,
                },
            )
        )
    return docs


def _parse_subtitle_or_mp4_drop(path: Path) -> list[KnowledgeDocument]:
    if path.suffix.lower() in (".srt", ".vtt", ".txt"):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        # Strip SRT/VTT cue numbers and timestamp lines
        lines = [
            ln.strip()
            for ln in raw.splitlines()
            if ln.strip()
            and not ln.strip().isdigit()
            and "-->" not in ln
            and not ln.strip().upper().startswith("WEBVTT")
        ]
        cleaned = sanitize_text(" ".join(lines))
        if not cleaned:
            return []
        pos = canonicalize_position(path.stem)
        return [
            KnowledgeDocument(
                doc_id=f"cta-private-transcript-{_slug_hash(path.name + cleaned[:80])}",
                corpus="cta",
                title=f"CTA Private Video Transcript: {path.stem.replace('_', ' ').title()}",
                section_or_belt="white",
                position=pos if pos != "all" else "closed_guard",
                text=cleaned[:2400],
                source_url=f"local://private_curriculum/{path.name}",
                timestamp_sec=0,
                tags=["cta", "private curriculum", "video transcript"],
                metadata={
                    "instructor": "Caio Terra Online",
                    "min_belt": "white",
                    "coaching_cue": cleaned[:220],
                    "source_kind": "private_transcript",
                    "filename": path.name,
                },
            )
        ]

    # Standalone MP4 without sidecar transcript: index filename/metadata stub if no matching .txt/.srt/.md exists
    for ext in (".txt", ".srt", ".vtt", ".json", ".md"):
        if path.with_suffix(ext).exists():
            return []
    title_clean = sanitize_text(path.stem.replace("_", " ").replace("-", " ").title())
    pos = canonicalize_position(title_clean)
    return [
        KnowledgeDocument(
            doc_id=f"cta-private-mp4-{_slug_hash(path.name)}",
            corpus="cta",
            title=f"CTA Private Lesson Video: {title_clean}",
            section_or_belt="white",
            position=pos if pos != "all" else "closed_guard",
            text=f"Local private CTA video lesson ({path.name}): {title_clean}.",
            source_url=f"local://private_curriculum/{path.name}",
            timestamp_sec=0,
            tags=["cta", "private curriculum", "mp4"],
            metadata={
                "instructor": "Caio Terra Online",
                "min_belt": "white",
                "coaching_cue": title_clean,
                "source_kind": "private_mp4",
                "filename": path.name,
            },
        )
    ]


def import_private_curriculum(
    source_dir: Path | str,
    target_version: str = "v0.2.0",
    knowledge_dir: Path | str | None = None,
    base_version: str = "v0.1.0",
    embed_cloud: bool = False,
    academy_id: str = "cta",
) -> dict[str, Any]:
    """Ingests local Markdown/JSON/MP4 transcript drops into a tenant-scoped KB version."""
    src = Path(source_dir).expanduser().resolve()
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(f"Private curriculum directory does not exist: {src}")

    norm_academy = normalize_academy_slug(academy_id, default="cta")
    kb_root = Path(knowledge_dir).expanduser().resolve() if knowledge_dir else get_settings().knowledge_dir
    base_dir = kb_root / "versions" / base_version
    target_dir = kb_root / "versions" / target_version
    target_dir.mkdir(parents=True, exist_ok=True)

    # Seed from base version corpora if available; otherwise build fresh v0.1.0 corpora first
    if not (base_dir / "ibjjf_rules.jsonl").exists() or not (base_dir / "cta_curriculum.jsonl").exists():
        build_knowledge_index(version=base_version, knowledge_dir=kb_root, embed_cloud=False)

    ibjjf_docs = load_jsonl_documents(base_dir / "ibjjf_rules.jsonl")
    cta_docs = load_jsonl_documents(base_dir / "cta_curriculum.jsonl")

    imported_docs: list[KnowledgeDocument] = []
    for file_path in sorted(src.rglob("*")):
        if not file_path.is_file():
            continue
        suf = file_path.suffix.lower()
        if suf == ".md":
            imported_docs.extend(_parse_markdown_drop(file_path))
        elif suf in (".json", ".jsonl"):
            imported_docs.extend(_parse_json_or_jsonl_drop(file_path))
        elif suf in (".txt", ".srt", ".vtt", ".mp4"):
            imported_docs.extend(_parse_subtitle_or_mp4_drop(file_path))

    scoped_imported: list[KnowledgeDocument] = []
    for doc in imported_docs:
        meta = dict(doc.metadata)
        meta["academy_id"] = norm_academy
        tags = list(dict.fromkeys([norm_academy, *doc.tags]))
        scoped_imported.append(
            doc.model_copy(update={"academy_id": norm_academy, "metadata": meta, "tags": tags})
        )

    by_id: dict[str, KnowledgeDocument] = {d.doc_id: d for d in cta_docs}
    for d in scoped_imported:
        by_id[d.doc_id] = d
    merged_cta_docs = list(by_id.values())

    write_jsonl_documents(target_dir / "ibjjf_rules.jsonl", ibjjf_docs)
    write_jsonl_documents(target_dir / "cta_curriculum.jsonl", merged_cta_docs)

    manifest = build_knowledge_index(
        version=target_version,
        knowledge_dir=kb_root,
        embed_cloud=embed_cloud,
        reuse_existing_jsonl=True,
        extra_build_metadata={
            "imported_from": str(src),
            "base_version": base_version,
            "academy_id": norm_academy,
            "imported_private_docs": len(scoped_imported),
        },
    )
    return {
        "target_version": target_version,
        "academy_id": norm_academy,
        "imported_count": len(scoped_imported),
        "total_cta_count": len(merged_cta_docs),
        "manifest": manifest,
    }
