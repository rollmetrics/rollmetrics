"""Unit tests for the IBJJF Match Encyclopedia (schema, SQLite FTS5 store, harvester, GCS vault)."""

from __future__ import annotations

from pathlib import Path

from rollmetrics.encyclopedia.harvester import (
    classify_video,
    parse_athletes,
    parse_belt,
    parse_uniform,
    video_entry_to_records,
)
from rollmetrics.encyclopedia.schema import (
    MatchAnnotationBundle,
    RuleEdgeCaseTag,
    ScoringRuleEvent,
    TechniqueEvent,
)
from rollmetrics.encyclopedia.store import EncyclopediaStore


def test_classify_filters_non_ibjjf_and_accepts_ibjjf() -> None:
    assert classify_video("Christian Ozbek vs Fabricio Andrey | FULL MATCH | Polaris 40", 1237, "FloGrappling") == "non_match"
    assert classify_video("Roger Gracie Shows How To Dominate With Side Control", 373, "FloGrappling") == "non_match"
    assert classify_video("Davi Vetoraci vs Omar Nada | FULL MATCH | 2026 IBJJF's The Crown", 445, "FloGrappling") == "standalone_match"
    assert classify_video("Every Match From Mica Galvao At 2026 IBJJF's The Crown", 1916, "FloGrappling") == "multi_match_compilation"
    assert classify_video("New Jersey Fall IO 2026 | Mat 9 (Day 2)", 33572, "IBJJF") == "full_mat_stream"
    assert classify_video("IBJJF Worlds 2025 Purple Belt Final John Doe vs Jane Roe", 420, "YouTube_ColoredBelt") == "standalone_match"


def test_parse_metadata_fields() -> None:
    assert parse_athletes("Davi Vetoraci vs Omar Nada | FULL MATCH | 2026 IBJJF's The Crown") == ("Davi Vetoraci", "Omar Nada")
    assert parse_athletes("Mica Galvao x Tainan Dalpra | IBJJF Worlds 2024") == ("Mica Galvao", "Tainan Dalpra")
    assert parse_belt("IBJJF Pans 2025 Purple Belt Middleweight Final") == "purple"
    assert parse_belt("IBJJF No-Gi Worlds Blue Belt Match") == "blue"
    assert parse_belt("Davi Vetoraci vs Omar Nada | 2026 IBJJF's The Crown", source_label="FloGrappling") == "black"
    assert parse_uniform("2025 IBJJF No-Gi Worlds Final") == "nogi"
    assert parse_uniform("2026 IBJJF's The Crown Final") == "gi"


def test_store_fts_search_chapters_and_annotations(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.sqlite"
    with EncyclopediaStore(db_path) as store:
        raw_comp = {
            "id": "abc12345678",
            "title": "Every Match From Mica Galvao At 2026 IBJJF's The Crown",
            "duration": 1916,
            "channel": "FloGrappling",
            "upload_date": "20261101",
            "chapters": [
                {"title": "Mica Galvao vs Leonardo Souza", "start_time": 15.0, "end_time": 690.0},
                {"title": "Mica Galvao vs Matheus Luna", "start_time": 700.0, "end_time": 1088.0},
            ],
        }
        vid, matches = video_entry_to_records(
            raw_comp,
            source_label="FloGrappling",
            harvest_source="flo_videos",
            v1_video_ids={"abc12345678"},
        )
        assert vid is not None
        assert len(matches) == 2
        store.upsert_video(vid)
        for m in matches:
            assert m.in_scoreboard_v1 is True
            store.upsert_match(m)

        # Attach 4-layer annotation to match 1 and verify FTS5 finds technique & edge case tags
        bundle = MatchAnnotationBundle(
            match_id=matches[0].match_id,
            scoring_events=[ScoringRuleEvent(video_sec=120.0, athlete="A", event_type="guard_pass", points=3)],
            technique_events=[
                TechniqueEvent(
                    video_sec=110.0,
                    actor="A",
                    category="pass",
                    technique_name="knee_cut_pass",
                    guard_family="de_la_riva",
                )
            ],
            rule_edge_cases=[
                RuleEdgeCaseTag(
                    video_sec=200.0,
                    category="stalling_20s",
                    ibjjf_rule_ref="Art. 6.4.1",
                    description="20-second stalling warning on bottom player",
                )
            ],
        )
        store.upsert_annotation(bundle)

        hits = store.search("de_la_riva", belt="black", uniform="gi")
        assert len(hits) == 1
        assert hits[0].match_id == matches[0].match_id
        assert hits[0].tier == 1

        stats = store.stats()
        assert stats["total_videos"] == 1
        assert stats["total_matches"] == 2
        assert stats["scoreboard_v1_overlap"] == 2
        assert stats["annotated_matches"] == 1
