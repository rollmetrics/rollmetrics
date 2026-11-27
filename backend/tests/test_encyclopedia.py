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


def test_annotator_coercion_ocr_agreement_and_export_v2(tmp_path: Path) -> None:
    import json

    from rollmetrics.encyclopedia.annotator import coerce_annotation_bundle
    from rollmetrics.encyclopedia.exporter import export_scoreboard_v2
    from rollmetrics.encyclopedia.schema import MatchRecord
    from rollmetrics.evals.scoreboard_bench.schema import CompactScore, GroundTruthMatchLabel

    db_path = tmp_path / "enc.sqlite"
    with EncyclopediaStore(db_path) as store:
        m = MatchRecord(
            match_id="flo-test-1",
            video_id="vidtest0001",
            url="https://www.youtube.com/watch?v=vidtest0001",
            start_sec=0.0,
            end_sec=300.0,
            duration_sec=300.0,
            athlete_a="Athlete One",
            athlete_b="Athlete Two",
            event_name="2026 IBJJF Worlds",
            event_year=2026,
            event_group="2026-worlds",
            belt="black",
            uniform="gi",
            source="FloGrappling",
            footage_type="broadcast_scoreboard",
            overlay_preset="flograppling_lower_bar",
            gcs_raw_uri="gs://rollmetrics-ibjjf-vault/clips/flo-test-1/raw.mp4",
            gcs_masked_uri="gs://rollmetrics-ibjjf-vault/clips/flo-test-1/masked.mp4",
        )
        store.upsert_match(m)

        ocr = GroundTruthMatchLabel(
            match_id=m.match_id,
            video_id=m.video_id,
            active_bout_start_sec=5.0,
            active_bout_end_sec=295.0,
            final_score_a=CompactScore(points=5),
            final_score_b=CompactScore(points=2),
            winner="A",
            method="points",
        )
        store.upsert_ocr_label(
            m.match_id,
            labeler_id=ocr.labeler_id,
            winner=ocr.winner,
            method=ocr.method,
            event_count=0,
            payload_json=ocr.model_dump_json(),
        )

        bundle = coerce_annotation_bundle(
            {
                "summary": "A sweeps and passes to win 5-2.",
                "scoring_events": [
                    {"video_sec": 45.0, "athlete": "A", "event_type": "sweep", "points": 2},
                    {"video_sec": 120.0, "athlete": "A", "event_type": "guard_pass", "points": 3},
                    {"video_sec": 200.0, "athlete": "B", "event_type": "sweep", "points": 2},
                ],
                "technique_events": [
                    {"video_sec": 40.0, "actor": "A", "category": "sweep", "technique_name": "berimbolo", "guard_family": "de_la_riva"},
                ],
                "vision_metadata": {
                    "athlete_a_appearance": "Blue Gi",
                    "athlete_b_appearance": "White Gi",
                    "camera_angle": "broadcast_elevated",
                    "referee_signals": [{"video_sec": 124.0, "signal_type": "points_3", "athlete": "A"}],
                },
                "rule_edge_cases": [
                    {"video_sec": 180.0, "category": "out_of_bounds_reset", "description": "Reset to center"},
                ],
            },
            match=m,
            model_id="gemini-4-hydrogen",
            ocr_label_json=ocr.model_dump_json(),
        )
        assert bundle.ocr_agreement_score == 1.0
        store.upsert_annotation(bundle)

        out_dir = tmp_path / "scoreboard_v2"
        manifest = export_scoreboard_v2(store, target_dir=out_dir)
        assert manifest["total_matches"] == 1
        assert manifest["ocr_ground_truth_labels"] == 1
        assert manifest["four_layer_annotations"] == 1
        assert (out_dir / "matches.jsonl").exists()
        assert (out_dir / "labels.jsonl").exists()
        assert (out_dir / "annotations.jsonl").exists()
        splits = json.loads((out_dir / "splits.json").read_text(encoding="utf-8"))
        assert splits["benchmark_id"] == "rollmetrics-scoreboard-v2"
