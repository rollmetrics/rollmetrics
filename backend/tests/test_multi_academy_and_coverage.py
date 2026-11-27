"""Tests for Multi-Academy Private Reference Library isolation and comprehensive backend coverage."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from rollmetrics.auth import (
    authenticate_token_or_bypass,
    load_user_profiles,
    upsert_user_profile,
    verify_google_token_and_allowlist,
)
from rollmetrics.cli import main as cli_main
from rollmetrics.config import REPO_ROOT, Settings
from rollmetrics.knowledge.academies import (
    add_academy_reference,
    default_academy_for_email,
    get_academy,
    list_academy_references,
    load_academies,
    search_academy_references,
    upsert_academy,
)
from rollmetrics.knowledge.private_importer import import_private_curriculum
from rollmetrics.knowledge.retriever import KnowledgeRetriever
from rollmetrics.knowledge.schema import (
    KnowledgeDocument,
    canonicalize_position,
    normalize_academy_slug,
    positions_match,
    sanitize_text,
)
from rollmetrics.main import create_app
from rollmetrics.providers.base import (
    FrameObservation,
    VideoVisionResult,
    coerce_frame_observation,
    coerce_raw_event,
    parse_json_lenient,
)
from rollmetrics.providers.ollama_local import (
    OllamaLocalProvider,
    select_best_ollama_vision_model,
)
from rollmetrics.rules.schema import (
    AgeDivision,
    AthleteId,
    BeltRank,
    EventKind,
    MatchDivisionConfig,
    RawObservedEvent,
    UniformType,
)
from rollmetrics.video.ingest import resolve_binary


@pytest.fixture(scope="module")
def synth_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out_dir = tmp_path_factory.mktemp("acad_synth")
    vid = out_dir / "sample_bout.mp4"
    ffmpeg_bin = resolve_binary("ffmpeg")
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=24:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(vid),
        ],
        check=True,
        capture_output=True,
    )
    return vid


def _make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    kb_dir = REPO_ROOT / "knowledge_base"
    defaults: dict[str, Any] = {
        "environment": "test",
        "allow_local_dev_bypass": True,
        "allow_open_signup": False,
        "runtime_dir": runtime_dir,
        "knowledge_dir": kb_dir,
        "profile": "local",
        "gemini_api_key": "",
        "google_client_id": "test-client-id.apps.googleusercontent.com",
        "allowlist_emails": "coach@example.com,alice@northstarbjj.com,bob@marceloacademy.com",
        "owner_email": "coach@example.com",
        "default_academy_id": "cta",
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# 1. Multi-Academy Store, Tenant Isolation, REST API, and CLI --academy
# ---------------------------------------------------------------------------


def test_multi_academy_tenant_isolation_store_and_retriever(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)

    # Seeded CTA academy exists automatically
    academies = load_academies(settings)
    assert "cta" in academies
    assert academies["cta"]["academy_name"] == "Caio Terra Association"
    assert default_academy_for_email("coach@example.com", settings=settings) == ("cta", "Caio Terra Association")
    oss_settings = _make_settings(tmp_path, owner_email="", default_academy_id="osjj-roadmap")
    assert default_academy_for_email("newuser@example.com", settings=oss_settings) == (
        "osjj-roadmap",
        "Open Source Jiu-Jitsu & Roadmap",
    )
    non_owner_slug, non_owner_name = default_academy_for_email("alice@northstarbjj.com", settings=settings)
    assert non_owner_slug != "cta"
    assert non_owner_name == "Academy Library"

    # Create two isolated academies
    rec_a = upsert_academy(settings, "Northstar Jiu-Jitsu", academy_id="northstar-bjj")
    rec_b = upsert_academy(settings, "Marcelo Academy", academy_id="marcelo-academy")
    assert rec_a["academy_id"] == "northstar-bjj"
    assert rec_b["academy_id"] == "marcelo-academy"
    assert get_academy(settings, "northstar-bjj")["academy_name"] == "Northstar Jiu-Jitsu"

    # Add private notes/videos into Academy A and Academy B
    doc_a = add_academy_reference(
        settings,
        "northstar-bjj",
        title="Northstar Quantum Lapel Sweep",
        text="Feed far lapel under shin and elevate hip into mount.",
        position="open_guard",
        belt="blue",
        source_url="https://example.com/northstar-lapel",
        reference_type="video",
        tags=["lapel", "quantum"],
    )
    doc_b = add_academy_reference(
        settings,
        "marcelo-academy",
        title="Marcelo High-Elbow Guillotine System",
        text="Chin strap control, lift elbow high, crunch ribs to finish.",
        position="butterfly_guard",
        belt="white",
        reference_type="note",
        tags=["guillotine"],
    )
    assert doc_a.academy_id == "northstar-bjj"
    assert doc_b.academy_id == "marcelo-academy"
    assert len(list_academy_references(settings, "northstar-bjj")) == 1
    assert len(list_academy_references(settings, "marcelo-academy")) == 1

    # Direct academy reference search is strictly isolated
    hits_a = search_academy_references(settings, "northstar-bjj", "Quantum Lapel Sweep", top_k=5)
    assert len(hits_a) == 1 and hits_a[0].doc_id == doc_a.doc_id
    hits_b_cross = search_academy_references(settings, "marcelo-academy", "Quantum Lapel Sweep", top_k=5)
    assert all(h.academy_id == "marcelo-academy" for h in hits_b_cross)
    assert all(doc_a.doc_id != h.doc_id for h in hits_b_cross)

    # KnowledgeRetriever enforces strict tenant isolation (global IBJJF + own academy ONLY)
    retriever = KnowledgeRetriever(knowledge_dir=settings.knowledge_dir, version="v0.1.0", settings=settings)

    # Academy B querying for Academy A's secret technique NEVER receives Academy A or CTA private docs
    b_search = retriever.search("Northstar Quantum Lapel Sweep", corpus="all", top_k=10, academy_id="marcelo-academy")
    assert all(h.academy_id in ("global", "marcelo-academy") for h in b_search)
    assert all("Northstar Quantum" not in h.title for h in b_search)

    # Academy B querying for IBJJF global rules ALWAYS receives global IBJJF rules
    b_ibjjf = retriever.search("IBJJF Art. 6.2.3 knee reaping", corpus="ibjjf", top_k=5, academy_id="marcelo-academy")
    assert len(b_ibjjf) >= 1
    assert all(h.corpus == "ibjjf" and h.academy_id == "global" for h in b_ibjjf)

    # Academy B querying its own note receives its own reference
    b_own = retriever.search("High-Elbow Guillotine", corpus="cta", top_k=5, academy_id="marcelo-academy")
    assert any(h.doc_id == doc_b.doc_id and h.academy_id == "marcelo-academy" for h in b_own)

    # Kory's seeded CTA academy still receives CTA corpus seamlessly
    cta_recs = retriever.recommend_cta_for_position("closed_guard", "overhook sweep", belt="white", top_k=3, academy_id="cta")
    assert len(cta_recs) >= 1


def test_multi_academy_rest_endpoints_and_user_isolation(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, allow_local_dev_bypass=False)
    app = create_app(settings=settings)
    client = TestClient(app)

    def _fake_verify(token: str, _req: Any, audience: str | None = None) -> dict[str, Any]:
        del audience
        if token == "tok-kory":
            return {"email": "coach@example.com", "name": "Head Coach", "email_verified": True}
        if token == "tok-alice":
            return {"email": "alice@northstarbjj.com", "name": "Alice Coach", "email_verified": True}
        if token == "tok-bob":
            return {"email": "bob@marceloacademy.com", "name": "Bob Coach", "email_verified": True}
        raise ValueError("bad token")

    with patch("rollmetrics.auth.google_id_token.verify_oauth2_token", side_effect=_fake_verify):
        # 1. Kory defaults to seeded CTA academy
        r_kory_me = client.get("/api/auth/me", headers={"Authorization": "Bearer tok-kory"})
        assert r_kory_me.status_code == 200
        assert r_kory_me.json()["academy_id"] == "cta"
        assert r_kory_me.json()["academy_name"] == "Caio Terra Association"

        r_kory_acad = client.get("/api/academies/me", headers={"Authorization": "Bearer tok-kory"})
        assert r_kory_acad.status_code == 200
        assert r_kory_acad.json()["academy_id"] == "cta"
        assert r_kory_acad.json()["seeded_corpus_count"] > 0

        # 2. Alice sets her own academy and adds a private reference video
        r_alice_put = client.put(
            "/api/academies/me",
            headers={"Authorization": "Bearer tok-alice"},
            json={"academy_name": "Northstar BJJ Montreal", "academy_id": "northstar-mtl"},
        )
        assert r_alice_put.status_code == 200
        assert r_alice_put.json()["academy_id"] == "northstar-mtl"

        r_alice_post = client.post(
            "/api/academies/me/references",
            headers={"Authorization": "Bearer tok-alice"},
            json={
                "title": "Northstar Secret Berimbolo Counter",
                "text": "Sit on far hip, clear crab ride hook, and backstep into leg drag.",
                "position": "open_guard",
                "belt": "purple",
                "source_url": "https://youtu.be/northstar123",
                "reference_type": "video",
                "tags": ["berimbolo", "counter"],
            },
        )
        assert r_alice_post.status_code == 201
        created_ref = r_alice_post.json()["reference"]
        assert created_ref["academy_id"] == "northstar-mtl"

        r_alice_refs = client.get(
            "/api/academies/me/references?q=Berimbolo",
            headers={"Authorization": "Bearer tok-alice"},
        )
        assert r_alice_refs.status_code == 200
        assert r_alice_refs.json()["count"] == 1

        # 3. Bob sets a different academy and MUST NOT see Alice's private reference
        r_bob_put = client.put(
            "/api/academies/me",
            headers={"Authorization": "Bearer tok-bob"},
            json={"academy_name": "Marcelo NYC", "academy_id": "marcelo-nyc"},
        )
        assert r_bob_put.status_code == 200

        r_bob_refs = client.get("/api/academies/me/references", headers={"Authorization": "Bearer tok-bob"})
        assert r_bob_refs.status_code == 200
        assert r_bob_refs.json()["count"] == 0

        r_bob_search = client.get(
            "/api/knowledge/search?q=Northstar+Secret+Berimbolo+Counter&corpus=all",
            headers={"Authorization": "Bearer tok-bob"},
        )
        assert r_bob_search.status_code == 200
        hits = r_bob_search.json()["hits"]
        assert all(h["academy_id"] in ("global", "marcelo-nyc") for h in hits)
        assert all("Northstar Secret Berimbolo" not in h["title"] for h in hits)

        # Persisted profiles contain academy_id and academy_name
        profiles = load_user_profiles(settings)
        assert profiles["alice@northstarbjj.com"]["academy_id"] == "northstar-mtl"
        assert profiles["bob@marceloacademy.com"]["academy_id"] == "marcelo-nyc"


def test_private_importer_and_cli_academy_flag(tmp_path: Path) -> None:
    drop_dir = tmp_path / "academy_drop"
    drop_dir.mkdir()
    (drop_dir / "plain_notes.md").write_text("Keep elbows tight in bottom side control.", encoding="utf-8")
    (drop_dir / "structured.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": "Apex Takedown Entry",
                        "text": "Level change and snap down to single leg.",
                        "position": "standing",
                        "belt": "white",
                        "tags": ["wrestling"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (drop_dir / "drill.jsonl").write_text(
        json.dumps({"title": "Apex Mount Escape", "text": "Bridge and elbow escape.", "position": "mount"}) + "\n",
        encoding="utf-8",
    )
    (drop_dir / "clip.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\nPin the hip first.\n", encoding="utf-8")
    (drop_dir / "standalone_lesson.mp4").write_bytes(b"fake-mp4-stub")

    kb_copy = tmp_path / "kb"
    # Run via CLI --academy
    code = cli_main(
        [
            "index",
            "import-private",
            str(drop_dir),
            "--target-version",
            "v0.2.0-apex",
            "--academy",
            "apex-bjj",
            "--knowledge-dir",
            str(kb_copy),
        ]
    )
    assert code == 0

    # Verify imported docs are isolated to apex-bjj in SQLite
    settings = _make_settings(tmp_path, knowledge_dir=kb_copy)
    retriever = KnowledgeRetriever(knowledge_dir=kb_copy, version="v0.2.0-apex", settings=settings)
    apex_hits = retriever.search("Apex Takedown Entry", corpus="cta", top_k=5, academy_id="apex-bjj")
    assert any("Apex Takedown Entry" in h.title and h.academy_id == "apex-bjj" for h in apex_hits)

    other_hits = retriever.search("Apex Takedown Entry", corpus="cta", top_k=5, academy_id="other-gym")
    assert all(h.academy_id in ("global", "other-gym") for h in other_hits)
    assert all("Apex Takedown Entry" not in h.title for h in other_hits)

    # Error guard on missing directory
    with pytest.raises(FileNotFoundError):
        import_private_curriculum(tmp_path / "does_not_exist", knowledge_dir=kb_copy)


# ---------------------------------------------------------------------------
# 2. Comprehensive Backend Route, Auth, Provider, Schema, and CLI Coverage
# ---------------------------------------------------------------------------


def test_auth_error_branches_and_open_signup(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, environment="cloud", allow_local_dev_bypass=False)

    # Empty token in cloud -> 401
    with pytest.raises(Exception) as exc_info:
        authenticate_token_or_bypass("", settings)
    assert getattr(exc_info.value, "status_code", None) == 401

    # Unverified email -> 401
    with (
        patch(
            "rollmetrics.auth.google_id_token.verify_oauth2_token",
            return_value={"email": "coach@example.com", "email_verified": False},
        ),
        pytest.raises(Exception) as exc_unv,
    ):
        verify_google_token_and_allowlist("tok", settings)
    assert getattr(exc_unv.value, "status_code", None) == 401

    # Missing email claim -> 401
    with (
        patch(
            "rollmetrics.auth.google_id_token.verify_oauth2_token",
            return_value={"email_verified": True},
        ),
        pytest.raises(Exception) as exc_no_email,
    ):
        verify_google_token_and_allowlist("tok", settings)
    assert getattr(exc_no_email.value, "status_code", None) == 401

    # Non-allowlisted -> 403 when allow_open_signup=False
    with (
        patch(
            "rollmetrics.auth.google_id_token.verify_oauth2_token",
            return_value={"email": "outsider@example.com", "email_verified": True},
        ),
        pytest.raises(Exception) as exc_403,
    ):
        verify_google_token_and_allowlist("tok", settings)
    assert getattr(exc_403.value, "status_code", None) == 403

    # Non-allowlisted -> 200 when allow_open_signup=True
    open_settings = _make_settings(tmp_path, allow_open_signup=True, allow_local_dev_bypass=False)
    with patch(
        "rollmetrics.auth.google_id_token.verify_oauth2_token",
        return_value={"email": "newcoach@example.com", "name": "New Coach", "email_verified": True},
    ):
        user = authenticate_token_or_bypass("tok-new", open_settings)
    assert user["email"] == "newcoach@example.com"
    assert user["academy_name"] == "Academy Library"
    assert upsert_user_profile(open_settings, {"email": ""}) == {"email": ""}


def test_main_api_endpoints_websocket_and_pages(tmp_path: Path, synth_mp4: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings=settings)
    client = TestClient(app)

    # Public health & legal pages
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/health").json()["app"] == "RollMetrics"
    assert client.get("/api/auth/config").status_code == 200
    expected_spa = (200,) if (REPO_ROOT / "frontend" / "dist" / "index.html").exists() else (200, 404)
    assert client.get("/privacy").status_code in expected_spa
    assert client.get("/terms").status_code in expected_spa

    # System status, profile switch, config update, test-connection
    assert client.get("/api/system/status").status_code == 200
    assert client.post("/api/system/profile", json={"profile": "local"}).status_code == 200
    assert client.post("/api/system/config", json={"profile": "local", "ollama_vision_model": "gemma4:e2b-it-q4_K_M"}).status_code == 200
    assert client.post("/api/system/test-connection", json={"target": "cloud"}).status_code == 200
    assert client.post("/api/system/test-connection", json={"target": "local", "ollama_base_url": "http://127.0.0.1:1"}).status_code == 200

    # Matches, bucket toggle, summaries, admin overview
    r_matches = client.get("/api/matches")
    assert r_matches.status_code == 200
    assert len(r_matches.json()["matches"]) >= 6
    assert client.post("/api/matches/seb_gi_sf/bucket", json={"bucket": "film_study"}).status_code == 200
    assert client.post("/api/matches/bad%20id!/bucket", json={"bucket": "film_study"}).status_code in (400, 404, 422)
    assert client.get("/api/me/coaching-summary").status_code == 200
    assert client.get("/api/film-study/summary?athlete=Sebastian+Bussieres").status_code == 200
    assert client.post(
        "/api/collections/summary",
        json={"match_ids": ["seb_nogi_qf", "seb_gi_sf"], "focus_athlete": "Sebastian Bussieres"},
    ).status_code == 200
    assert client.get("/api/admin/overview").status_code == 200

    # Presets, events analysis, legality check, manifest, evals
    assert client.get("/api/analyze/presets").status_code == 200
    r_ev = client.post(
        "/api/analyze/events",
        json={
            "division": {"uniform": "gi", "belt": "white", "age_division": "adult"},
            "events": [
                {"timestamp_sec": 30.0, "actor": "A", "kind": "sweep", "hold_duration_sec": 3.5},
                {"timestamp_sec": 70.0, "actor": "A", "kind": "mount", "hold_duration_sec": 4.0},
            ],
        },
    )
    assert r_ev.status_code == 200
    assert r_ev.json()["athlete_a"]["points"] == 6

    r_leg = client.post(
        "/api/knowledge/legality-check",
        json={"technique_slug": "heel_hook", "belt": "white", "uniform": "nogi", "age_division": "adult", "include_matrix": True},
    )
    assert r_leg.status_code == 200
    assert r_leg.json()["verdict"]["severity"] == "serious_foul_dq"
    assert client.get("/api/knowledge/manifest").status_code == 200
    assert client.get("/api/evals/latest").status_code == 200
    assert client.post("/api/evals/run", json={"version": "v0.1.0"}).status_code == 200
    assert client.get("/api/evals/models").status_code == 200
    assert client.post("/api/evals/models/run", json={"live_api": False, "version": "v0.1.0"}).status_code == 200
    assert client.get("/api/evals/scoreboard").status_code == 200
    assert client.post("/api/evals/scoreboard/run", json={"split": "calibration", "live_video": False}).status_code == 200

    # Video Range / HEAD / poster + upload analysis
    matches_dir = settings.runtime_dir / "matches"
    matches_dir.mkdir(parents=True, exist_ok=True)
    (matches_dir / "seb_gi_sf.mp4").write_bytes(synth_mp4.read_bytes())
    r_head = client.head("/api/matches/seb_gi_sf/video")
    assert r_head.status_code == 200
    assert r_head.headers.get("accept-ranges") == "bytes"
    r_range = client.get("/api/matches/seb_gi_sf/video", headers={"Range": "bytes=0-1"})
    assert r_range.status_code == 206
    assert len(r_range.content) == 2
    r_poster = client.get("/api/matches/seb_gi_sf/poster")
    assert r_poster.status_code == 200
    assert r_poster.headers.get("content-type", "").startswith("image/jpeg")

    # Media board & slowmo on synthetic mp4 + file download
    r_board = client.post(
        "/api/media/board",
        json={"video_path": str(synth_mp4), "center_timestamp_sec": 2.0, "title": "Sweep Board"},
    )
    assert r_board.status_code == 200
    board_url = r_board.json()["url"]
    assert client.get(board_url).status_code == 200

    r_slow = client.post(
        "/api/media/slowmo",
        json={"video_path": str(synth_mp4), "start_sec": 0.5, "duration_sec": 1.5, "speed": 0.5, "caption": "Slowmo"},
    )
    assert r_slow.status_code == 200
    assert client.get(r_slow.json()["url"]).status_code == 200
    assert client.get("/api/media/files/missing_file.mp4").status_code == 404

    # Live WebSocket (/ws/live): init -> event -> status -> end
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"type": "init", "division": {"uniform": "gi", "belt": "white", "age_division": "adult"}})
        init_msg = ws.receive_json()
        assert init_msg["type"] == "hud"

        ws.send_json(
            {
                "type": "event",
                "event": {"timestamp_sec": 12.0, "actor": "A", "kind": "takedown", "hold_duration_sec": 3.2},
            }
        )
        ev_msg = ws.receive_json()
        assert ev_msg["type"] == "hud"

        ws.send_json({"type": "end"})
        end_msg = ws.receive_json()
        assert end_msg["type"] in ("final", "hud", "state")


def test_provider_schema_and_cli_media_edge_cases(tmp_path: Path, synth_mp4: Path) -> None:
    # parse_json_lenient & coerce helpers
    assert parse_json_lenient("") == {}
    assert parse_json_lenient("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert parse_json_lenient("prefix {\"b\": 2} suffix") == {"b": 2}
    assert parse_json_lenient("not json at all") == {}
    assert coerce_raw_event("not-a-dict") is None  # type: ignore[arg-type]
    ev = coerce_raw_event({"actor": "B", "kind": "unknown_kind", "timestamp_sec": 5.0})
    assert ev is not None and ev.kind == EventKind.POSITION_CHANGE
    obs = coerce_frame_observation({"stabilizing_scoring_kind": "invalid", "top_or_dominant_actor": "A"}, 10.0, "m1")
    assert isinstance(obs, FrameObservation) and obs.stabilizing_scoring_kind is None
    assert VideoVisionResult(model_used="m1").model_id == "m1"

    # Knowledge schema helpers
    assert sanitize_text("") == ""
    assert canonicalize_position("") == "all"
    assert positions_match("all", "mount") is True
    assert normalize_academy_slug("  Gracie Barra #1! ") == "gracie-barra-1"
    doc = KnowledgeDocument(
        doc_id="d1",
        corpus="ibjjf",
        title="Test",
        section_or_belt="Art. 1",
        text="Clean text",
        source_url="https://ibjjf.com",
    )
    assert doc.academy_id == "global"

    # Ollama model selector edge cases
    assert select_best_ollama_vision_model([], "gemma4:e2b-it-q4_K_M") == "gemma4:e2b-it-q4_K_M"
    assert select_best_ollama_vision_model(["nomic-embed-text", "qwen2.5vl:7b"], "gemma4:e2b-it-q4_K_M") == "qwen2.5vl:7b"
    assert select_best_ollama_vision_model(["nomic-embed-text"], "gemma4:e2b-it-q4_K_M") == "gemma4:e2b-it-q4_K_M"

    # Ollama agentic fallback when model proposes 0 inspect_windows
    def _empty_windows_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma4:e2b-it-q4_K_M"}]})
        return httpx.Response(
            200,
            json={
                "model": "gemma4:e2b-it-q4_K_M",
                "message": {"role": "assistant", "content": json.dumps({"summary": "ok", "events": []})},
                "prompt_eval_count": 50,
                "eval_count": 10,
            },
        )

    import asyncio

    prov = OllamaLocalProvider(
        settings=_make_settings(tmp_path),
        transport=httpx.MockTransport(_empty_windows_handler),
    )
    div = MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.WHITE, age_division=AgeDivision.ADULT)
    res = asyncio.run(prov.analyze_video_agentic(synth_mp4, div, coarse_frames=4, max_windows=2))
    assert res.telemetry["agentic_windows_count"] >= 1

    # CLI media board & slowmo + adjudicate_dispute_or_synthesize
    adj = asyncio.run(
        prov.adjudicate_dispute_or_synthesize(
            [RawObservedEvent(timestamp_sec=10.0, actor=AthleteId.ATHLETE_A, kind=EventKind.SWEEP)],
            div,
            ambiguous_indices=[0],
        )
    )
    assert "rulings" in adj and "cta_synthesis" in adj

    board_out = tmp_path / "cli_board.png"
    slow_out = tmp_path / "cli_slow.mp4"
    assert cli_main(["media", "board", "--video", str(synth_mp4), "--timestamp", "2.0", "--output", str(board_out)]) == 0
    assert cli_main(["media", "slowmo", "--video", str(synth_mp4), "--start", "0.5", "--duration", "1.0", "--output", str(slow_out)]) == 0
    assert board_out.is_file() and slow_out.is_file()

    # Model arena offline deterministic run + mocked cloud embeddings
    from rollmetrics.evals.model_bench import run_model_arena
    from rollmetrics.knowledge.indexer import compute_cloud_embeddings

    arena = run_model_arena(live_api=False, version="v0.1.0", knowledge_dir=REPO_ROOT / "knowledge_base")
    assert arena.get("models")

    class _FakeEmb:
        def __init__(self, n: int) -> None:
            self.values = [0.1] * n

    class _FakeModels:
        def embed_content(self, model: str, contents: Any, config: Any) -> Any:
            del model, config
            count = len(contents) if isinstance(contents, list) else 1
            return type("Resp", (), {"embeddings": [_FakeEmb(768) for _ in range(count)]})()

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            del api_key
            self.models = _FakeModels()

    with patch("google.genai.Client", _FakeClient):
        vecs = compute_cloud_embeddings(["closed guard sweep", "knee reaping"], api_key="fake-key")
    assert len(vecs) == 2 and vecs[0].shape == (768,)


def test_scoreboard_bench_runner_and_model_bench_mocked_coverage(tmp_path: Path, synth_mp4: Path) -> None:
    """Covers scoreboard_bench runner, labeler helpers, and model_bench live adapter paths with mocks."""
    from rollmetrics.evals.model_bench import (
        GOLDEN_ARENA_FIXTURES,
        _aggregate_model_row,
        _build_fixture_prompt,
        _call_gemini_fixture,
        _evaluate_fixture_prediction,
        _render_fixture_jpeg,
    )
    from rollmetrics.evals.scoreboard_bench.labeler import (
        _clock_to_seconds,
        _parse_ocr_tokens_to_reading,
    )
    from rollmetrics.evals.scoreboard_bench.runner import (
        adjudicate_raw_events_to_prediction,
        score_masked_video_agentic,
    )
    from rollmetrics.evals.scoreboard_bench.schema import (
        CompactScore,
        GroundTruthMatchLabel,
        MatchManifestEntry,
    )

    # 1. Labeler helper coverage
    assert _clock_to_seconds("04:30") == 270
    assert _clock_to_seconds("invalid") is None
    reading = _parse_ocr_tokens_to_reading(
        tokens=[
            {"text": "2", "x": 0.25, "y": 0.5, "h": 0.4},
            {"text": "04:10", "x": 0.55, "y": 0.5, "h": 0.4},
            {"text": "0", "x": 0.80, "y": 0.5, "h": 0.4},
        ],
        preset_name="smoothcomp_lower_bar",
    )
    assert reading is not None and reading["clock_str"] == "04:10"

    # 2. adjudicate_raw_events_to_prediction
    div = MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.WHITE, age_division=AgeDivision.ADULT)
    adj_pred = adjudicate_raw_events_to_prediction(
        match_id="test-adj-01",
        model_id="gemini-4-hydrogen-agentic",
        division=div,
        raw_events=[
            RawObservedEvent(timestamp_sec=10.0, actor=AthleteId.ATHLETE_A, kind=EventKind.SWEEP, hold_duration_sec=3.5),
            RawObservedEvent(timestamp_sec=20.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MOUNT, hold_duration_sec=4.0),
        ],
        leakage_verified=True,
        latency_ms=1200.0,
        input_tokens=1000,
        output_tokens=150,
        bout_duration_sec=60.0,
    )
    assert adj_pred.winner == "A"
    assert adj_pred.final_score_a.points == 6

    # 3. score_masked_video_agentic with mocked Gemini Files + Interactions API
    entry = MatchManifestEntry(
        match_id="test-agentic-01",
        video_id="test123",
        url="https://www.youtube.com/watch?v=test123",
        start_sec=0.0,
        end_sec=4.0,
        athlete_a="Athlete A",
        athlete_b="Athlete B",
        division="Adult White Gi",
        belt="white",
        uniform="gi",
        age_division="adult",
        overlay_preset="smoothcomp_lower_bar",
    )
    gt = GroundTruthMatchLabel(
        match_id="test-agentic-01",
        video_id="test123",
        active_bout_start_sec=0.0,
        active_bout_end_sec=4.0,
        final_score_a=CompactScore(points=6),
        final_score_b=CompactScore(points=0),
        winner="A",
        method="points",
    )

    class _FakeFile:
        name = "files/test123"
        uri = "https://generativelanguage.googleapis.com/v1beta/files/test123"
        state = "ACTIVE"

    class _FakeFiles:
        def upload(self, file: str) -> Any:
            del file
            return _FakeFile()

        def get(self, name: str) -> Any:
            del name
            return _FakeFile()

        def delete(self, name: str) -> None:
            del name

    class _FakeInteractions:
        def create(self, **kwargs: Any) -> Any:
            del kwargs
            txt = json.dumps(
                {
                    "summary": "Sweep to mount",
                    "events": [
                        {
                            "timestamp_sec": 1.5,
                            "actor": "A",
                            "kind": "sweep",
                            "hold_duration_sec": 3.5,
                            "in_bounds": True,
                            "confidence": 0.9,
                        },
                        {
                            "timestamp_sec": 3.0,
                            "actor": "A",
                            "kind": "mount",
                            "hold_duration_sec": 3.5,
                            "in_bounds": True,
                            "confidence": 0.9,
                        },
                    ],
                }
            )
            usage = type("U", (), {"total_input_tokens": 1200, "total_output_tokens": 120})()
            out = type("O", (), {"type": "text", "text": txt})()
            return type("Resp", (), {"outputs": [out], "output_text": txt, "text": txt, "usage": usage})()

    fake_client = type("C", (), {"files": _FakeFiles(), "interactions": _FakeInteractions()})()
    settings = _make_settings(tmp_path, gemini_api_key="fake-gemini-key")
    pred = score_masked_video_agentic(
        entry=entry,
        gt=gt,
        video_path=synth_mp4,
        model_id="gemini-4-hydrogen-agentic",
        settings=settings,
        genai_client=fake_client,
    )
    assert pred.winner == "A"
    assert pred.final_score_a.points == 6
    assert pred.leakage_verified is True

    # 3b. build_masked_storyboard_sheets + score_masked_match_live (cloud & local mocked)
    from rollmetrics.evals.scoreboard_bench.runner import (
        build_masked_storyboard_sheets,
        score_masked_match_live,
    )

    sheets, frame_cnt = build_masked_storyboard_sheets(
        video_path=synth_mp4,
        bout_start_sec=0.0,
        bout_end_sec=4.0,
        preset_name="smoothcomp_lower_bar",
        num_frames=4,
    )
    assert len(sheets) >= 1 and frame_cnt >= 1
    live_cloud_pred = score_masked_match_live(
        entry=entry,
        gt=gt,
        video_path=synth_mp4,
        model_id="gemini-4-hydrogen",
        settings=settings,
        genai_client=fake_client,
    )
    assert live_cloud_pred.winner == "A"
    assert live_cloud_pred.leakage_verified is True

    # 4. model_bench fixture rendering, Gemini & Ollama calls, and aggregation
    fixture = GOLDEN_ARENA_FIXTURES[0]
    jpeg = _render_fixture_jpeg(fixture)
    assert len(jpeg) > 100
    prompt = _build_fixture_prompt(fixture)
    parsed_fx, lat_ms, in_tok, out_tok = _call_gemini_fixture(fake_client, "gemini-4-hydrogen", jpeg, prompt)
    assert isinstance(parsed_fx, dict) and lat_ms >= 0.0
    case_res = _evaluate_fixture_prediction(
        fixture,
        {
            "actor": fixture.get("expected_actor", "A"),
            "detected_kind": fixture.get("expected_kind", "guard_pass"),
            "hold_duration_sec": 3.5,
            "in_bounds": True,
            "technique_slug": fixture.get("expected_slug", ""),
        },
        latency_ms=lat_ms,
        in_tok=in_tok,
        out_tok=out_tok,
    )
    agg = _aggregate_model_row("gemini-4-hydrogen", [case_res])
    assert agg["model_id"] == "gemini-4-hydrogen"

    from rollmetrics.evals.model_bench import _call_ollama_fixture

    class _FakeHttpxResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "message": {"content": json.dumps({"actor": "A", "detected_kind": "guard_pass"})},
                "prompt_eval_count": 200,
                "eval_count": 40,
            }

    with patch("rollmetrics.evals.model_bench.httpx.post", return_value=_FakeHttpxResp()):
        ol_parsed, ol_lat, ol_in, ol_out = _call_ollama_fixture(
            "ollama/gemma4:e2b-it-q4_K_M",
            "http://127.0.0.1:11434",
            jpeg,
            prompt,
        )
    assert ol_parsed["detected_kind"] == "guard_pass" and ol_lat >= 0.0 and ol_in == 200 and ol_out == 40

    # 5. Live-mode run_model_arena with mocked Gemini client + labeler crop helper
    from PIL import Image
    from rollmetrics.evals.model_bench import run_model_arena
    from rollmetrics.evals.scoreboard_bench.labeler import (
        _crop_digits_strip,
        _detect_flograppling_cap_colors,
        extract_scoreboard_strip_sheets,
    )

    class _FakeGenAIForArena:
        def __init__(self, api_key: str, **kwargs: Any) -> None:
            del api_key, kwargs
            self.interactions = _FakeInteractions()

    with patch("google.genai.Client", _FakeGenAIForArena):
        live_arena = run_model_arena(
            models=["gemini-4-hydrogen"],
            live_api=True,
            version="v0.1.0",
            knowledge_dir=REPO_ROOT / "knowledge_base",
            settings=settings,
        )
    assert live_arena["models"] and live_arena["models"][0]["model_id"] == "gemini-4-hydrogen"

    # Labeler image crop & strip sheet helpers on real synth_mp4
    sample_im = Image.new("RGB", (640, 120), (30, 40, 50))
    assert _crop_digits_strip(sample_im, "smoothcomp_lower_bar").size[0] > 0
    assert _crop_digits_strip(sample_im, "flograppling_lower_bar").size[0] > 0
    cap_a, cap_b = _detect_flograppling_cap_colors(sample_im, "gi")
    assert cap_a and cap_b
    strip_sheets = extract_scoreboard_strip_sheets(
        video_path=synth_mp4,
        start_sec=0.0,
        end_sec=3.0,
        preset_name="smoothcomp_lower_bar",
        sample_every_sec=1.0,
    )
    assert len(strip_sheets) >= 1


def test_benchmark_accuracy_upgrades_anchors_cooldown_reconcile_and_local_agentic(
    tmp_path: Path,
    synth_mp4: Path,
) -> None:
    """Verifies Workstreams 1.2, 1.3, and 1.4: A/B anchor, 6s cooldown, hand-raise reconcile, local 3-stage eval."""
    from rollmetrics.evals.scoreboard_bench.masking import extract_masked_anchor_frame
    from rollmetrics.evals.scoreboard_bench.runner import (
        _reconcile_observed_winner,
        score_masked_match_live,
    )
    from rollmetrics.evals.scoreboard_bench.schema import (
        CompactScore,
        GroundTruthMatchLabel,
        MatchManifestEntry,
        MatchPredictionRecord,
    )
    from rollmetrics.rules.state_machine import IBJJFRefereeEngine

    # 1.2 Masked t=2s A/B visual anchor frame
    anchor_bytes = extract_masked_anchor_frame(
        video_path=synth_mp4,
        anchor_sec=1.5,
        preset_name="smoothcomp_lower_bar",
        athlete_a_appearance="Black Rashguard (Red Side)",
        athlete_b_appearance="Black Rashguard (Blue Side)",
    )
    assert anchor_bytes is not None and len(anchor_bytes) > 1000

    # 1.3 Position-hold 6.0s cooldown deduplication in IBJJFRefereeEngine
    div = MatchDivisionConfig(uniform=UniformType.GI, belt=BeltRank.WHITE, age_division=AgeDivision.ADULT)
    eng = IBJJFRefereeEngine(division=div)
    a1 = eng.ingest_event(RawObservedEvent(timestamp_sec=20.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MOUNT, hold_duration_sec=3.5))
    a2 = eng.ingest_event(RawObservedEvent(timestamp_sec=24.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MOUNT, hold_duration_sec=3.5))
    assert a1.awarded_points == 4
    assert a2.awarded_points == 0
    assert "Deduplicated" in a2.ruling_reason
    # After position reset, re-scoring mount is allowed
    eng.ingest_event(RawObservedEvent(timestamp_sec=25.0, actor=AthleteId.ATHLETE_B, kind=EventKind.POSITION_CHANGE, position_after="Half Guard"))
    a3 = eng.ingest_event(RawObservedEvent(timestamp_sec=30.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MOUNT, hold_duration_sec=3.5))
    assert a3.awarded_points == 4
    assert eng.scores[AthleteId.ATHLETE_A].points == 8

    # 1.3 Deterministic hand-raise reconciliation on low-margin (<=2 pts) discrepancy vs decisive (>2 pts)
    low_margin = MatchPredictionRecord(
        match_id="rec-1",
        model_id="gemini-4-hydrogen-agentic",
        final_score_a=CompactScore(points=2),
        final_score_b=CompactScore(points=0),
        winner="A",
        method="points",
    )
    _reconcile_observed_winner(low_margin, "B", "points")
    assert low_margin.winner == "B"

    decisive = MatchPredictionRecord(
        match_id="rec-2",
        model_id="gemini-4-hydrogen-agentic",
        final_score_a=CompactScore(points=8),
        final_score_b=CompactScore(points=0),
        winner="A",
        method="points",
    )
    _reconcile_observed_winner(decisive, "B", "points")
    assert decisive.winner == "A"
    _reconcile_observed_winner(decisive, "B", "submission")
    assert decisive.winner == "B" and decisive.method == "submission"

    # 1.4 Local ollama/gemma4 3-stage agentic wiring in score_masked_match_live
    entry = MatchManifestEntry(
        match_id="local-agentic-01",
        video_id="synth1",
        url="https://www.youtube.com/watch?v=synth1",
        start_sec=0.0,
        end_sec=4.0,
        athlete_a="Athlete A",
        athlete_b="Athlete B",
        division="Adult White Gi",
        belt="white",
        uniform="gi",
        age_division="adult",
        overlay_preset="smoothcomp_lower_bar",
    )
    gt = GroundTruthMatchLabel(
        match_id="local-agentic-01",
        video_id="synth1",
        active_bout_start_sec=0.0,
        active_bout_end_sec=4.0,
        final_score_a=CompactScore(points=2),
        final_score_b=CompactScore(points=0),
        winner="A",
        method="points",
    )

    async def _fake_chat_json(self: Any, prompt: str, images_jpeg: Any = None, system: str = "", model: Any = None) -> tuple[dict[str, Any], str]:
        del images_jpeg, system, model
        self.last_telemetry = {"model_id": "gemma4:e2b-it-q4_K_M", "latency_ms": 12.0, "input_tokens": 100, "output_tokens": 30}
        if "STAGE 1 COARSE SCAN" in prompt:
            return ({"summary": "Coarse", "inspect_windows": [{"start_sec": 0.5, "end_sec": 3.5, "reason": "sweep"}]}, "gemma4:e2b-it-q4_K_M")
        return (
            {
                "window_summary": "Verified sweep",
                "observed_winner": "A",
                "observed_win_method": "points",
                "events": [{"timestamp_sec": 2.0, "actor": "A", "kind": "sweep", "hold_duration_sec": 3.4, "in_bounds": True, "confidence": 0.9}],
            },
            "gemma4:e2b-it-q4_K_M",
        )

    settings = _make_settings(tmp_path)
    with patch.object(OllamaLocalProvider, "chat_json", _fake_chat_json):
        local_pred = score_masked_match_live(
            entry=entry,
            gt=gt,
            video_path=synth_mp4,
            model_id="ollama/gemma4:e2b-it-q4_K_M",
            settings=settings,
        )
    assert local_pred.winner == "A"
    assert local_pred.final_score_a.points == 2
    assert local_pred.leakage_verified is True

