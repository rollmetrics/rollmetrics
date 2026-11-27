"""Deterministic 3-part benchmark runner for RollMetrics knowledge base and referee engine."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from rollmetrics.config import get_settings
from rollmetrics.evals.datasets import ensure_eval_datasets
from rollmetrics.evals.model_bench import run_model_arena
from rollmetrics.knowledge.indexer import sha256_file
from rollmetrics.knowledge.retriever import KnowledgeRetriever
from rollmetrics.rules.legality_matrix import check_technique_legality
from rollmetrics.rules.schema import (
    AgeDivision,
    BeltRank,
    MatchDivisionConfig,
    RawObservedEvent,
    UniformType,
)
from rollmetrics.rules.state_machine import IBJJFRefereeEngine

_YOUTUBE_JUMP_RE = re.compile(
    r"^https://(?:www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}&t=\d+s|youtu\.be/[A-Za-z0-9_-]{11}\?t=\d+)$"
)


def _verify_manifest_checksums(version_dir: Path, manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    artifacts = manifest.get("artifacts") or {}
    if not artifacts:
        return False, ["manifest.json contains no artifacts"]
    for fname, spec in artifacts.items():
        fpath = version_dir / fname
        if not fpath.exists():
            errors.append(f"Missing artifact file: {fname}")
            continue
        actual_sha = sha256_file(fpath)
        expected_sha = str(spec.get("sha256", ""))
        if actual_sha != expected_sha:
            errors.append(f"SHA256 mismatch for {fname}: expected {expected_sha}, got {actual_sha}")
    return len(errors) == 0, errors


def _run_suite_1_ibjjf_rules(
    cases_path: Path,
    retriever: KnowledgeRetriever,
) -> dict[str, Any]:
    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = payload if isinstance(payload, list) else payload.get("cases", [])

    passed = 0
    failures: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    for case in cases:
        cid = str(case["id"])
        mode = str(case.get("mode", "legality"))
        belt = BeltRank(case["belt"])
        uniform = UniformType(case["uniform"])
        age = AgeDivision(case["age_division"])
        exp_art = str(case.get("expected_article_substr", ""))

        if mode == "legality":
            slug = str(case["technique_slug"])
            verdict = check_technique_legality(slug, belt=belt, uniform=uniform, age_division=age)
            citations = retriever.cite_ibjjf_rule(f"{verdict.article} {slug}", top_k=2)
            cite_hit = bool(citations) and any(
                exp_art in c.get("article", "") or exp_art in c.get("text", "") for c in citations
            )
            ok = (
                verdict.severity == case["expected_severity"]
                and exp_art in verdict.article
                and cite_hit
            )
            rec = {
                "id": cid,
                "pass": ok,
                "actual_severity": verdict.severity,
                "expected_severity": case["expected_severity"],
                "article": verdict.article,
                "citation_retrieved": cite_hit,
            }
        else:
            div = MatchDivisionConfig(uniform=uniform, belt=belt, age_division=age)
            engine = IBJJFRefereeEngine(division=div, index_version=retriever.version)
            ev = RawObservedEvent.model_validate(case["event"])
            adj = engine.ingest_event(ev)
            citations = retriever.cite_ibjjf_rule(adj.rule_article, top_k=2)
            cite_hit = bool(citations) and any(
                exp_art in c.get("article", "") or exp_art in c.get("text", "") for c in citations
            )
            ok = (
                adj.awarded_points == int(case["expected_points"])
                and adj.awarded_advantages == int(case["expected_advantages"])
                and exp_art in adj.rule_article
                and cite_hit
            )
            rec = {
                "id": cid,
                "pass": ok,
                "awarded_points": adj.awarded_points,
                "awarded_advantages": adj.awarded_advantages,
                "article": adj.rule_article,
                "citation_retrieved": cite_hit,
            }

        details.append(rec)
        if ok:
            passed += 1
        else:
            failures.append(rec)

    total = len(cases)
    rate = round(passed / total, 4) if total else 0.0
    suite_pass = total >= 30 and passed == total
    return {
        "passed": passed,
        "total": total,
        "pass_rate": rate,
        "accuracy": rate,
        "pass": suite_pass,
        "suite_pass": suite_pass,
        "failures": failures,
        "details": details,
    }


def _run_suite_2_match_timelines(
    timelines_path: Path,
    version: str,
) -> dict[str, Any]:
    payload = json.loads(timelines_path.read_text(encoding="utf-8"))
    timelines = payload if isinstance(payload, list) else payload.get("timelines", [])

    passed = 0
    failures: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    for item in timelines:
        tid = str(item["id"])
        div = MatchDivisionConfig.model_validate(item["division"])
        engine = IBJJFRefereeEngine(division=div, index_version=version)
        for raw_ev in item["events"]:
            engine.ingest_event(RawObservedEvent.model_validate(raw_ev))
        snap = engine.finalize_regulation()

        exp = item["expected"]
        exp_a = exp["athlete_a"]
        exp_b = exp["athlete_b"]
        actual_a = snap.athlete_a.model_dump()
        actual_b = snap.athlete_b.model_dump()

        ok = (
            snap.winner == exp["winner"]
            and str(exp.get("win_method_substr", "")).lower() in snap.win_method.lower()
            and actual_a == exp_a
            and actual_b == exp_b
        )
        rec = {
            "id": tid,
            "title": item.get("title", tid),
            "pass": ok,
            "winner": snap.winner,
            "expected_winner": exp["winner"],
            "win_method": snap.win_method,
            "athlete_a": actual_a,
            "athlete_b": actual_b,
        }
        details.append(rec)
        if ok:
            passed += 1
        else:
            failures.append({**rec, "expected": exp})

    total = len(timelines)
    rate = round(passed / total, 4) if total else 0.0
    suite_pass = total >= 6 and passed == total
    return {
        "passed": passed,
        "total": total,
        "pass_rate": rate,
        "accuracy": rate,
        "pass": suite_pass,
        "suite_pass": suite_pass,
        "failures": failures,
        "details": details,
    }


def _run_suite_3_cta_coaching(
    queries_path: Path,
    retriever: KnowledgeRetriever,
) -> dict[str, Any]:
    payload = json.loads(queries_path.read_text(encoding="utf-8"))
    queries = payload if isinstance(payload, list) else payload.get("queries", [])

    recall_hits = 0
    valid_link_queries = 0
    belt_safe_queries = 0
    failures: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    for q in queries:
        qid = str(q["id"])
        belt = str(q.get("belt", "white"))
        recs = retriever.recommend_cta_for_position(
            position=str(q["position"]),
            situation=str(q.get("situation", "")),
            belt=belt,
            top_k=3,
            academy_id="cta",
        )
        got_ids = [str(r.get("doc_id", "")) for r in recs]
        expected_ids = {str(eid) for eid in q.get("expected_doc_ids", [])}
        hit_at_3 = bool(expected_ids.intersection(got_ids))

        links_ok = bool(recs) and all(
            bool(_YOUTUBE_JUMP_RE.match(str(r.get("source_url", ""))))
            and r.get("timestamp_sec") is not None
            for r in recs
        )
        # Verify white belt recommendations never include upper-belt illegal locks
        belt_ok = True
        if belt == "white":
            belt_ok = all(
                str(r.get("metadata", {}).get("min_belt", "white")) == "white"
                and "wristlock" not in str(r.get("doc_id", "")).lower()
                for r in recs
            )

        if hit_at_3:
            recall_hits += 1
        if links_ok:
            valid_link_queries += 1
        if belt_ok:
            belt_safe_queries += 1

        ok = hit_at_3 and links_ok and belt_ok
        rec = {
            "id": qid,
            "position": q["position"],
            "pass": ok,
            "hit_at_3": hit_at_3,
            "valid_youtube_links": links_ok,
            "belt_safe": belt_ok,
            "top_3_doc_ids": got_ids,
            "top_url": recs[0]["source_url"] if recs else "",
        }
        details.append(rec)
        if not ok:
            failures.append({**rec, "expected_doc_ids": sorted(expected_ids)})

    total = len(queries)
    recall_at_3 = round(recall_hits / total, 4) if total else 0.0
    yt_rate = round(valid_link_queries / total, 4) if total else 0.0
    passed = sum(1 for d in details if d["pass"])
    pass_rate = round(passed / total, 4) if total else 0.0
    suite_pass = total >= 20 and recall_at_3 >= 0.90 and yt_rate == 1.0 and belt_safe_queries == total
    return {
        "passed": passed,
        "total": total,
        "pass_rate": pass_rate,
        "accuracy": recall_at_3,
        "recall_at_3": recall_at_3,
        "valid_youtube_links_rate": yt_rate,
        "belt_safety_rate": round(belt_safe_queries / total, 4) if total else 0.0,
        "pass": suite_pass,
        "suite_pass": suite_pass,
        "failures": failures,
        "details": details,
    }


def run_benchmark_suite(
    version: str = "v0.1.0",
    knowledge_dir: Path | str | None = None,
    include_model_arena: bool = True,
    live_models: bool = False,
    models: list[str] | None = None,
) -> dict[str, Any]:
    """Executes all 3 RollMetrics verification suites + multi-model arena + SHA256 manifest check."""
    settings = get_settings()
    kb_root = Path(knowledge_dir).expanduser().resolve() if knowledge_dir else settings.knowledge_dir
    dataset_paths = ensure_eval_datasets(kb_root, overwrite=False)

    # Use deterministic offline local profile during benchmark verification so CI and local runs match 100%
    eval_settings = settings.model_copy(update={"profile": "local"})
    retriever = KnowledgeRetriever(knowledge_dir=kb_root, version=version, settings=eval_settings)
    manifest = retriever.manifest()
    manifest_ok, manifest_errors = _verify_manifest_checksums(kb_root / "versions" / version, manifest)

    s1 = _run_suite_1_ibjjf_rules(dataset_paths["ibjjf_rule_cases"], retriever)
    s2 = _run_suite_2_match_timelines(dataset_paths["match_timelines"], version)
    s3 = _run_suite_3_cta_coaching(dataset_paths["cta_coaching_queries"], retriever)

    arena_report: dict[str, Any] | None = None
    arena_ok = True
    if include_model_arena:
        arena_report = run_model_arena(
            models=models,
            live_api=live_models,
            knowledge_dir=kb_root,
            version=version,
            persist=live_models,
            settings=settings,
        )
        arena_models = arena_report.get("models", [])
        arena_ok = len(arena_models) >= 1 and bool(arena_report.get("pareto_models"))

    overall_pass = bool(manifest_ok and s1["suite_pass"] and s2["suite_pass"] and s3["suite_pass"] and arena_ok)
    result: dict[str, Any] = {
        "version": version,
        "evaluated_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        "overall_pass": overall_pass,
        "manifest_verified": manifest_ok,
        "manifest_errors": manifest_errors,
        "document_counts": manifest.get("counts", {}),
        "embedding_backend": manifest.get("embedding_backend", "unknown"),
        "suites": {
            "ibjjf_rules": s1,
            "match_timelines": s2,
            "cta_coaching": s3,
        },
    }
    if arena_report is not None:
        result["model_arena"] = arena_report
    return result
