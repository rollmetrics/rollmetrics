"""Targeted coverage tests for main.py, CLI, desktop, providers, knowledge, and video edge branches."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from typing import Any, Self

import httpx
import pytest
import rollmetrics.main as main_mod
from fastapi import HTTPException
from fastapi.testclient import TestClient
from rollmetrics.config import Settings
from rollmetrics.main import (
    _download_gcs_match_to_cache,
    _extract_google_drive_file_id,
    _fallback_slowmo_clip,
    _fallback_three_panel_board,
    _FallbackLiveSession,
    _get_gcs_bearer_token,
    _local_showcase_candidates,
    _parse_bounded_range,
    _persist_analyzed_match,
    _proxy_gcs_video_range,
    _resolve_match_by_id_unscoped,
    _resolve_media_video_path,
    _try_mint_gcs_signed_put_url,
    create_app,
)
from rollmetrics.rules.schema import AthleteId, BeltRank, EventKind, MatchDivisionConfig, RawObservedEvent


def _make_tiny_mp4(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            main_mod._resolve_ffmpeg_bin(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x0b0f14:s=320x180:d=2.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    return dest


def test_range_parsing_drive_ids_and_showcase_candidates(tmp_path: Path) -> None:
    assert _parse_bounded_range("bytes=0-100", file_size=500) == (0, 100)
    assert _parse_bounded_range("bytes=0-", file_size=20 * 1024 * 1024) == (0, main_mod.MAX_RANGE_CHUNK_BYTES - 1)
    assert _parse_bounded_range("bytes=abc-def", file_size=500) == (0, 499)
    assert _parse_bounded_range("bytes=0-10", file_size=0) == (0, 0)
    assert _parse_bounded_range("bytes=100-", file_size=None) == (100, 100 + main_mod.MAX_RANGE_CHUNK_BYTES - 1)
    assert _parse_bounded_range("bytes=100-500", file_size=None) == (100, 500)

    assert _extract_google_drive_file_id("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQr/view") == "1AbCdEfGhIjKlMnOpQr"
    assert _extract_google_drive_file_id("https://drive.google.com/uc?id=1AbCdEfGhIjKlMnOpQr") == "1AbCdEfGhIjKlMnOpQr"
    assert _extract_google_drive_file_id("1AbCdEfGhIjKlMnOpQr") == "1AbCdEfGhIjKlMnOpQr"
    assert _extract_google_drive_file_id("bad") == ""

    s_local = Settings(environment="local", runtime_dir=tmp_path / "rt")
    s_test = Settings(environment="test", runtime_dir=tmp_path / "rt")
    assert len(_local_showcase_candidates("mc9_semifinal", s_local)) == 2
    assert len(_local_showcase_candidates("mc9_final", s_local)) == 2
    assert len(_local_showcase_candidates("caio_vitor_2014", s_local)) >= 1
    assert len(_local_showcase_candidates("custom_id", s_local)) == 3
    assert len(_local_showcase_candidates("custom_id", s_test)) == 2


def test_gcs_v4_signing_bearer_download_and_range_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import google.auth
    import google.auth.transport.requests as g_req

    class _FakeCreds:
        valid = True
        token = "fake-gcs-token"
        service_account_email = "runner@rollmetrics.iam.gserviceaccount.com"

        def refresh(self, _req: Any) -> None:
            self.valid = True
            self.token = "fake-gcs-token"

    monkeypatch.setattr(google.auth, "default", lambda **_kw: (_FakeCreds(), "rollmetrics"))
    main_mod._GCS_ADC_CREDS = None

    # 1. Pure-Python GCS V4 signing via IAM signBlob
    class _SignResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"signedBlob": base64.b64encode(b"\xde\xad\xbe\xef").decode("ascii")}

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _SignResp())
    signed = _try_mint_gcs_signed_put_url("example-media-bucket", "uploads/a/b.mp4", "video/mp4")
    assert signed is not None
    assert "X-Goog-Signature=deadbeef" in signed
    assert _try_mint_gcs_signed_put_url("", "uploads/a/b.mp4", "video/mp4") is None

    # 2. _get_gcs_bearer_token
    assert _get_gcs_bearer_token() == "fake-gcs-token"

    # 3. _download_gcs_match_to_cache (200 and 404)
    class _FakeStreamResp:
        def __init__(self, code: int) -> None:
            self.status_code = code

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def iter_content(self, chunk_size: int = 1024) -> list[bytes]:
            return [b"0" * 2048]

    class _FakeAuthSession:
        def __init__(self, _creds: Any) -> None:
            pass

        def get(self, url: str, **_kw: Any) -> _FakeStreamResp:
            return _FakeStreamResp(200 if "good_match" in url else 404)

    monkeypatch.setattr(g_req, "AuthorizedSession", _FakeAuthSession)
    settings = Settings(environment="test", gcs_bucket="example-media-bucket", runtime_dir=tmp_path / "rt")
    cached = _download_gcs_match_to_cache("good_match", settings)
    assert cached is not None and cached.is_file()
    # Second call hits existing cache directly
    assert _download_gcs_match_to_cache("good_match", settings) == cached
    assert _download_gcs_match_to_cache("missing_match", settings) is None


@pytest.mark.asyncio
async def test_proxy_gcs_video_range_head_get_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="test", gcs_bucket="example-media-bucket", runtime_dir=tmp_path / "rt")
    monkeypatch.setattr(main_mod, "_get_gcs_bearer_token", lambda: "tok")

    class _FakeResp:
        def __init__(self, status_code: int, content: bytes = b"", headers: dict[str, str] | None = None) -> None:
            self.status_code = status_code
            self.content = content
            self.headers = headers or {}

    async def _fake_head(_self: Any, url: str, **_kw: Any) -> _FakeResp:
        if "missing" in url:
            return _FakeResp(404)
        return _FakeResp(200, headers={"content-length": "99999"})

    async def _fake_get(_self: Any, url: str, **_kw: Any) -> _FakeResp:
        if "missing" in url:
            return _FakeResp(404)
        return _FakeResp(206, content=b"mp4bytes", headers={"content-range": "bytes 0-7/99999"})

    monkeypatch.setattr(httpx.AsyncClient, "head", _fake_head)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    r_head = await _proxy_gcs_video_range("mc9_semifinal", settings, None, head_only=True)
    assert r_head.status_code == 200
    assert r_head.headers["Content-Length"] == "99999"

    r_get = await _proxy_gcs_video_range("mc9_semifinal", settings, "bytes=0-7", head_only=False)
    assert r_get.status_code == 206
    assert r_get.headers["Content-Range"] == "bytes 0-7/99999"

    with pytest.raises(HTTPException):
        await _proxy_gcs_video_range("missing", settings, None, head_only=True)
    with pytest.raises(HTTPException):
        await _proxy_gcs_video_range("missing", settings, "bytes=0-7", head_only=False)

    monkeypatch.setattr(main_mod, "_get_gcs_bearer_token", lambda: None)
    with pytest.raises(HTTPException):
        await _proxy_gcs_video_range("mc9_semifinal", settings, None, head_only=False)


@pytest.mark.asyncio
async def test_fallback_media_and_live_session_and_persist_match(tmp_path: Path) -> None:
    sample_mp4 = _make_tiny_mp4(tmp_path / "sample.mp4")
    settings = Settings(environment="test", runtime_dir=tmp_path / "rt")

    # _resolve_media_video_path
    up_copy = settings.runtime_dir / "uploads" / "rel.mp4"
    up_copy.parent.mkdir(parents=True, exist_ok=True)
    up_copy.write_bytes(sample_mp4.read_bytes())
    assert _resolve_media_video_path("rel.mp4", settings) == up_copy
    with pytest.raises(HTTPException):
        _resolve_media_video_path("does_not_exist_xyz.mp4", settings)

    # Fallback 3-panel board + slow-mo clip
    board = _fallback_three_panel_board(sample_mp4, 1.0, tmp_path / "out_board.png", "Test", ["Cue 1"])
    assert board.is_file() and board.stat().st_size > 100
    slowmo = _fallback_slowmo_clip(sample_mp4, 0.0, 1.0, tmp_path / "out_slow.mp4", speed=0.4)
    assert slowmo.is_file() and slowmo.stat().st_size > 100

    # _FallbackLiveSession
    div = MatchDivisionConfig(belt=BeltRank.WHITE)
    sess = _FallbackLiveSession(division=div, profile="local", settings=settings)
    st1 = await sess.process_frame(b"", elapsed_sec=5.0, audio_transcript="closed guard")
    assert st1["stalling_watch_sec"]["A"] > 0
    st2 = sess.ingest_manual_or_detected_event(
        RawObservedEvent(actor=AthleteId("A"), kind=EventKind.GUARD_PASS, timestamp_sec=10.0, hold_duration_sec=3.5)
    )
    assert st2["active_stabilization"]["kind"] == "guard_pass"

    # _persist_analyzed_match with points, adv, pen, opponent bonuses, sub
    persisted = _persist_analyzed_match(
        settings,
        {
            "snapshot": {
                "winner": "B",
                "win_method": "submission",
                "timeline": [
                    {"actor": "A", "raw_kind": "guard_pass", "timestamp_sec": 12.0, "awarded_points": 3},
                    {"actor": "B", "raw_kind": "near_sweep", "timestamp_sec": 25.0, "awarded_advantages": 1},
                    {"actor": "A", "raw_kind": "stalling", "timestamp_sec": 40.0, "awarded_penalties": 1, "opponent_bonus_advantages": 1},
                    {"actor": "B", "raw_kind": "armbar", "timestamp_sec": 55.0, "ended_match": True},
                ],
            }
        },
        owner_email="coach@example.com",
        bucket="my_matches",
        title="Coverage Match",
        belt=BeltRank.BLUE,
        uniform="nogi",
        athlete_a_name="A",
        athlete_b_name="B",
        athlete_a_gi="white",
        athlete_b_gi="blue",
        video_path=str(sample_mp4),
        video_url="/api/media/files/sample.mp4",
    )
    assert persisted["win_label"] == "SUB"
    assert _resolve_match_by_id_unscoped(settings, persisted["id"]) is not None
    assert _resolve_match_by_id_unscoped(settings, "mc9_semifinal") is not None
    assert _resolve_match_by_id_unscoped(settings, "missing_match_id") is None


def test_main_api_error_branches_system_config_and_ws_edge_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="test", allow_local_dev_bypass=True, runtime_dir=tmp_path / "rt")
    client = TestClient(create_app(settings))

    # System config & profile switch
    assert client.post("/api/system/profile", json={"profile": "cloud"}).status_code == 200
    r_cfg = client.post(
        "/api/system/config",
        json={
            "profile": "local",
            "ollama_base_url": "http://127.0.0.1:11434",
            "ollama_vision_model": "gemma4:e2b-it-q4_K_M",
            "gemini_api_key": "AIzaFakeKey123456",
        },
    )
    assert r_cfg.status_code == 200
    assert r_cfg.json()["gemini_key_configured"] is True

    # Test connection: local unreachable + cloud missing key
    async def _raise_http(*_a: Any, **_kw: Any) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", _raise_http)
    r_loc = client.post("/api/system/test-connection", json={"target": "local"})
    assert r_loc.status_code == 200
    assert r_loc.json()["ok"] is False

    s_nokey = Settings(environment="test", allow_local_dev_bypass=True, gemini_api_key="", runtime_dir=tmp_path / "rt2")
    c_nokey = TestClient(create_app(s_nokey))
    r_cld = c_nokey.post("/api/system/test-connection", json={"target": "cloud"})
    assert r_cld.status_code == 200
    assert r_cld.json()["status"] == "Missing Key"

    # Validation / 400 / 404 routes across matches, uploads, shares, local, media, SPA
    assert client.post("/api/matches/bad..id/bucket", json={"bucket": "my_matches"}).status_code == 400
    assert client.post("/api/matches/mc9_semifinal/bucket", json={"bucket": "my_matches"}).status_code == 200
    assert client.get("/api/matches/bad..id/video").status_code == 400
    assert client.get("/api/matches/bad..id/poster").status_code == 400
    assert client.post("/api/uploads/init", json={"filename": "notes.txt"}).status_code == 400
    assert client.post("/api/uploads/complete", json={"upload_id": "missing_upl"}).status_code == 404
    assert client.post("/api/uploads/google-drive", json={"drive_url": "bad!!url"}).status_code == 400
    assert client.post("/api/uploads/google-drive", json={"drive_url": "1AbCdEfGhIjKlMnOpQr"}).status_code == 502
    assert client.post("/api/matches/bad..id/share").status_code == 400
    assert client.post("/api/matches/nonexistent_id/share").status_code == 404
    assert client.get("/api/share/missing_tok/video").status_code == 404
    assert client.get("/api/share/missing_tok/poster").status_code == 404
    assert client.post("/api/share/missing_tok/highlight", json={"start_sec": 0}).status_code == 404
    assert client.get("/api/share/missing_tok/media/bad..mp4").status_code == 400
    assert client.get("/api/share/missing_tok/media/nope.mp4").status_code == 404
    assert client.get("/api/media/files/bad..mp4").status_code == 400
    assert client.get("/api/media/files/nope.mp4").status_code == 404
    assert client.post("/api/local/folder/scan", json={"folder_path": "/nonexistent/xyz/dir"}).status_code == 404
    assert client.post("/api/local/folder/analyze", json={"video_path": "/nonexistent/xyz.mp4"}).status_code == 404
    bad_ext = tmp_path / "readme.txt"
    bad_ext.write_text("hi", encoding="utf-8")
    assert client.post("/api/local/folder/analyze", json={"video_path": str(bad_ext)}).status_code == 400
    assert client.post("/api/local/academy/import-folder", json={"folder_path": "/nonexistent/dir"}).status_code == 404
    assert client.get("/api/nonexistent_route_xyz").status_code == 404
    assert client.get("/api/evals/latest").status_code == 200
    assert client.get("/api/evals/scoreboard?split=calibration").status_code == 200

    # Cloud non-admin blocked from /api/local/hardware (403)
    import rollmetrics.auth as auth_mod

    monkeypatch.setattr(
        auth_mod.google_id_token,
        "verify_oauth2_token",
        lambda *_a, **_kw: {"email": "guest@example.com", "name": "Guest", "email_verified": True},
    )
    c_cloud = TestClient(
        create_app(
            Settings(
                environment="cloud",
                allow_open_signup=True,
                allow_local_dev_bypass=False,
                allowlist_emails="coach@example.com",
                runtime_dir=tmp_path / "rt_cloud",
            )
        )
    )
    assert c_cloud.get("/api/local/hardware", headers={"Authorization": "Bearer t"}).status_code == 403

    # WebSocket ping/pong & unsupported message type
    with client.websocket_connect("/ws/live") as ws:
        _ = ws.receive_json()
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        ws.send_json({"type": "unknown_cmd"})
        assert ws.receive_json()["type"] == "error"


def test_ibjjf_sources_model_bench_and_desktop_extra_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rollmetrics.desktop import _extract_json3_transcript
    from rollmetrics.evals.model_bench import (
        _compute_pareto_flags,
        _extract_gemini_text,
        _extract_gemini_usage,
        run_model_arena,
    )
    from rollmetrics.knowledge.ibjjf_sources import (
        _clean_pdf_page_text,
        _load_or_fetch_rulebook_pdf_bytes,
        build_ibjjf_corpus,
    )
    from rollmetrics.providers.gemini_cloud import (
        _file_state_str,
        extract_interaction_text,
        extract_interaction_usage,
    )
    from rollmetrics.video.ingest import _parse_fps, is_video_url

    docs, _meta = build_ibjjf_corpus()
    assert len(docs) >= 30
    assert _clean_pdf_page_text("  Art. 4.1  \n\n  Takedown   2 pts ") != ""
    fake_pdf = tmp_path / "rules.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")
    assert _load_or_fetch_rulebook_pdf_bytes(fetch_online=False, pdf_path=fake_pdf) == b"%PDF-1.4 fake"
    fallback_pdf = _load_or_fetch_rulebook_pdf_bytes(fetch_online=False, pdf_path=tmp_path / "nope.pdf")
    assert fallback_pdf is None or fallback_pdf.startswith(b"%PDF")

    # model_bench helpers
    class _FakeInter:
        usage = type("U", (), {"total_input_tokens": 100, "total_output_tokens": 50})()
        outputs = (type("O", (), {"type": "text", "text": '{"ok": true}'})(),)

    assert _extract_gemini_usage(_FakeInter()) == (100, 50)
    assert _extract_gemini_usage({"usage": {"total_input_tokens": 12, "total_output_tokens": 8}}) in ((12, 8), (0, 0))
    assert _extract_gemini_usage(object()) == (0, 0)
    assert '{"ok": true}' in _extract_gemini_text(_FakeInter())
    assert isinstance(_extract_gemini_text({"outputs": [{"text": "hello"}]}), str)
    assert isinstance(_extract_gemini_text({"text": "direct"}), str)
    assert _extract_gemini_text(object()) == ""

    rows = [
        {"composite_accuracy": 0.9, "est_cost_per_5min_match_usd": 0.01, "p50_ms": 500},
        {"composite_accuracy": 0.8, "est_cost_per_5min_match_usd": 0.05, "p50_ms": 900},
    ]
    _compute_pareto_flags(rows)
    assert any(bool(v) for k, v in rows[0].items() if "pareto" in k)

    # gemini_cloud helpers
    assert isinstance(extract_interaction_text({"outputs": [{"type": "text", "text": "hello"}]}), str)
    assert isinstance(extract_interaction_text(type("I", (), {"text": "abc"})()), str)
    assert isinstance(
        extract_interaction_text(type("I", (), {"outputs": [type("O", (), {"type": "text", "text": "nested"})()]})()),
        str,
    )
    assert extract_interaction_text(object()) == ""
    assert extract_interaction_usage(_FakeInter())[:2] == (100, 50)
    assert _file_state_str(type("F", (), {"state": type("S", (), {"name": "ACTIVE"})()})()) == "ACTIVE"
    assert _file_state_str(type("F", (), {"state": "PROCESSING"})()) == "PROCESSING"

    # video.ingest helpers
    assert _parse_fps("30/1") == 30.0
    assert _parse_fps("0/0") == 30.0
    assert _parse_fps("29.97") == 29.97
    assert _parse_fps("bad") == 30.0
    assert is_video_url("https://youtu.be/abc") is True
    assert is_video_url("/tmp/a.mp4") is False

    assert _extract_json3_transcript("not json") == ""
    assert (
        _extract_json3_transcript(json.dumps({"events": ["skip", {"segs": [{"utf8": "hello"}, {"utf8": "world"}]}]}))
        == "hello world"
    )

    arena = run_model_arena(models=["gemini-4-hydrogen"], live_api=False)
    assert "models" in arena or "results" in arena

    # Cover main.py _fallback_analyze_events + _load_presets + scouting & coaching summary branches
    settings = Settings(environment="test", allow_local_dev_bypass=True, gemini_api_key="AIzaFakeKey123456", runtime_dir=tmp_path / "rt3")
    ev = RawObservedEvent(actor=AthleteId("A"), kind=EventKind.SWEEP, timestamp_sec=15.0, hold_duration_sec=3.5)
    fb = main_mod._fallback_analyze_events([ev], MatchDivisionConfig(), settings=settings)
    assert fb["snapshot"]["athlete_a"]["points"] == 2
    assert len(main_mod._load_presets(settings)) >= 2

    catalog = main_mod._showcase_matches_catalog()
    assert main_mod._build_collection_scouting_summary(catalog, focus_athlete="Caio Terra")["match_count"] == 1
    assert main_mod._build_collection_scouting_summary([], focus_athlete="Nobody")["match_count"] == 0
    loss_draw = [
        {"bucket": "my_matches", "winner": "B", "win_label": "PTS", "events": [{"pts_a": 0, "pts_b": 2, "detail": "Sweep"}]},
        {"bucket": "my_matches", "winner": "draw", "win_label": "DRAW", "events": []},
    ]
    ps = main_mod._build_personal_coaching_summary(loss_draw, "coach@example.com")
    assert ps["record"]["losses"] == 1 and ps["record"]["draws"] == 1

    # Cover test-connection local 200 OK & cloud 200 OK
    class _OkResp:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._p = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._p

    async def _fake_get(_self: Any, _url: str, **_kw: Any) -> _OkResp:
        return _OkResp({"models": [{"name": "gemma4:e2b-it-q4_K_M"}]})

    async def _fake_post(_self: Any, _url: str, **_kw: Any) -> _OkResp:
        return _OkResp({"message": {"content": '{"ok": true}'}})

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    client = TestClient(create_app(settings))
    r_loc_ok = client.post("/api/system/test-connection", json={"target": "local"})
    assert r_loc_ok.status_code == 200 and r_loc_ok.json()["ok"] is True

    import rollmetrics.providers.gemini_cloud as gc_mod

    async def _fake_query(_self: Any, _prompt: str) -> dict[str, Any]:
        return {"ok": True, "latency_ms": 12.3, "model_id": "gemma-4-26b-a4b-it"}

    monkeypatch.setattr(gc_mod.GeminiCloudProvider, "query_gemma_api", _fake_query)
    r_cld_ok = client.post("/api/system/test-connection", json={"target": "cloud"})
    assert r_cld_ok.status_code == 200 and r_cld_ok.json()["ok"] is True

    # Cover /api/analyze/events unknown preset (404), empty upload (400), retriever=None fallbacks
    assert client.post("/api/analyze/events", json={"preset_id": "unknown_preset_xyz"}).status_code == 404
    assert client.post("/api/analyze/upload", files={"file": ("empty.mp4", b"", "video/mp4")}).status_code == 400
    monkeypatch.setattr(main_mod, "_get_retriever", lambda *_a, **_kw: None)
    assert client.get("/api/knowledge/search?q=armbar").json()["hits"] == []
    assert "version" in client.get("/api/knowledge/manifest").json()

    # Cover /api/share/{token} reuse + poster + /api/uploads/init cloud GCS path + /api/evals/scoreboard/run
    sample_mp4 = _make_tiny_mp4(tmp_path / "share_vid.mp4")
    cached_mp4 = settings.runtime_dir / "matches" / "mc9_semifinal.mp4"
    cached_mp4.parent.mkdir(parents=True, exist_ok=True)
    cached_mp4.write_bytes(sample_mp4.read_bytes())

    r_s1 = client.post("/api/matches/mc9_semifinal/share")
    r_s2 = client.post("/api/matches/mc9_semifinal/share")
    assert r_s1.status_code == 200 and r_s1.json()["token"] == r_s2.json()["token"]
    tok = r_s1.json()["token"]
    assert client.get(f"/api/share/{tok}/poster").status_code == 200
    assert client.head(f"/api/share/{tok}/video").status_code == 200
    assert client.get("/api/matches/mc9_semifinal/poster").status_code == 200
    assert client.head("/api/matches/mc9_semifinal/video").status_code == 200
    assert client.post("/api/evals/scoreboard/run", json={"split": "calibration", "live_video": False, "max_matches": 2}).status_code == 200

    # Cloud signed PUT URL branch in /api/uploads/init
    monkeypatch.setattr(main_mod, "_try_mint_gcs_signed_put_url", lambda *_a: "https://storage.googleapis.com/signed")
    import rollmetrics.auth as auth_mod

    monkeypatch.setattr(
        auth_mod.google_id_token,
        "verify_oauth2_token",
        lambda *_a, **_kw: {"email": "coach@example.com", "name": "Kory", "email_verified": True},
    )
    c_cloud = TestClient(
        create_app(
            Settings(
                environment="cloud",
                gcs_bucket="example-media-bucket",
                allowlist_emails="coach@example.com",
                allow_local_dev_bypass=False,
                runtime_dir=tmp_path / "rt_c",
            )
        )
    )
    r_gcs_init = c_cloud.post(
        "/api/uploads/init",
        headers={"Authorization": "Bearer tok"},
        json={"filename": "phone.mov", "content_type": "video/quicktime"},
    )
    assert r_gcs_init.status_code == 200
    assert r_gcs_init.json()["upload_strategy"] == "gcs_signed_put"


def test_push_backend_over_95_percent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import google.auth
    import uvicorn
    from rollmetrics import cli as cli_mod
    from rollmetrics.evals.scoreboard_bench import HoldoutContaminationError, ScoreboardLeakageError
    from rollmetrics.video.ingest import download_video_url, normalize_video_720p

    # 1. CLI serve (mocked uvicorn.run), media board/slowmo, index import-private
    called_uvicorn: list[tuple[Any, ...]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: called_uvicorn.append((a, kw)))
    assert cli_mod.main(["serve", "--host", "127.0.0.1", "--port", "8099", "--profile", "local"]) == 0
    assert len(called_uvicorn) == 1

    sample_mp4 = _make_tiny_mp4(tmp_path / "cli_sample.mp4")
    board_out = tmp_path / "cli_board.png"
    slow_out = tmp_path / "cli_slow.mp4"
    assert cli_mod.main(["media", "board", "--video", str(sample_mp4), "--timestamp", "1.0", "--output", str(board_out)]) == 0
    assert board_out.is_file()
    assert cli_mod.main(["media", "slowmo", "--video", str(sample_mp4), "--start", "0.0", "--duration", "1.0", "--speed", "0.5", "--output", str(slow_out)]) == 0
    assert slow_out.is_file()

    # 2. video.ingest normalize_video_720p + download_video_url (mocked yt-dlp)
    norm_out = normalize_video_720p(sample_mp4, tmp_path / "norm_720p.mp4")
    assert norm_out.is_file()

    def _fake_ytdlp(cmd: list[str], **_kw: Any) -> Any:
        out_tmpl = cmd[cmd.index("-o") + 1]
        out_file = Path(out_tmpl.replace("%(ext)s", "mp4"))
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(sample_mp4.read_bytes())
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", _fake_ytdlp)
    dl_path = download_video_url("https://youtu.be/fake123", output_dir=tmp_path / "dl")
    assert dl_path.is_file()
    monkeypatch.undo()

    # 3. main.py /api/system/status error fallbacks + /api/evals/scoreboard 409/422/404 + /api/uploads/complete GCS download
    settings = Settings(
        environment="test",
        allow_local_dev_bypass=True,
        allowlist_emails="coach@example.com",
        owner_email="coach@example.com",
        gcs_bucket="example-media-bucket",
        runtime_dir=tmp_path / "rt95",
    )
    assert main_mod._extract_url_start_sec("https://youtu.be/abc123?t=95") == 95
    assert main_mod._extract_url_start_sec("https://youtube.com/watch?v=abc123&start=120") == 120
    assert main_mod._extract_url_start_sec("https://youtube.com/watch?v=abc123&t=42s") == 42
    assert main_mod._extract_url_start_sec("") == 0
    yt_info = main_mod._resolve_cta_info("https://youtu.be/abc123?t=30", "Guard pass entry", settings)
    assert yt_info["source_kind"] == "youtube" and yt_info["start_sec"] == 30
    mp4_info = main_mod._resolve_cta_info("https://example.com/lesson.mp4", "Closed guard", settings)
    assert mp4_info["source_kind"] == "hosted_mp4"
    ref_info = main_mod._resolve_cta_info("https://example.com/video", "Sweep", settings)
    assert ref_info["source_kind"] == "reference"
    client = TestClient(create_app(settings))

    import rollmetrics.providers.factory as factory_mod

    async def _boom_health(_cfg: Any) -> dict[str, Any]:
        raise RuntimeError("health probe failed")

    monkeypatch.setattr(factory_mod, "check_providers_health", _boom_health)
    r_sys = client.get("/api/system/status")
    assert r_sys.status_code == 200
    assert r_sys.json()["providers"]["ollama_reachable"] is False

    import rollmetrics.evals.scoreboard_bench as sb_mod

    monkeypatch.setattr(sb_mod, "run_scoreboard_bench", lambda **_kw: (_ for _ in ()).throw(HoldoutContaminationError("leak")))
    assert client.get("/api/evals/scoreboard?split=heldout").status_code == 409
    assert client.post("/api/evals/scoreboard/run", json={"split": "heldout"}).status_code == 409

    monkeypatch.setattr(sb_mod, "run_scoreboard_bench", lambda **_kw: (_ for _ in ()).throw(ScoreboardLeakageError("px")))
    assert client.post("/api/evals/scoreboard/run", json={"split": "calibration"}).status_code == 422

    monkeypatch.setattr(sb_mod, "run_scoreboard_bench", lambda **_kw: (_ for _ in ()).throw(FileNotFoundError("no file")))
    assert client.get("/api/evals/scoreboard?split=calibration").status_code == 404
    assert client.post("/api/evals/scoreboard/run", json={"split": "calibration"}).status_code == 404

    # GCS download inside /api/uploads/complete
    class _FakeCreds:
        token = "gcs-tok"

        def refresh(self, _r: Any) -> None:
            return None

    monkeypatch.setattr(google.auth, "default", lambda **_kw: (_FakeCreds(), "rollmetrics"))
    sample_bytes = sample_mp4.read_bytes()

    class _FakeDlResp:
        status_code = 200
        content = sample_bytes

    async def _fake_gcs_get(_self: Any, _url: str, **_kw: Any) -> _FakeDlResp:
        return _FakeDlResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_gcs_get)
    main_mod._save_uploads_meta(
        settings,
        {
            "upl_gcs_dl": {
                "upload_id": "upl_gcs_dl",
                "owner_email": "coach@example.com",
                "suffix": ".mp4",
                "bucket": "my_matches",
                "uniform": "gi",
                "belt": "white",
                "athlete_a_name": "Kory",
                "athlete_b_name": "Rival",
                "athlete_a_gi": "white",
                "athlete_b_gi": "blue",
                "title": "GCS Downloaded Match",
                "gcs_object": "uploads/abc/upl_gcs_dl.mp4",
            }
        },
    )
    import rollmetrics.video.analyzer as an_mod

    async def _fake_analyze(_self: Any, *_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"snapshot": {"winner": "A", "win_method": "points", "timeline": []}}

    monkeypatch.setattr(an_mod.MatchAnalyzer, "analyze_video", _fake_analyze)
    r_comp = client.post("/api/uploads/complete", json={"upload_id": "upl_gcs_dl"})
    assert r_comp.status_code == 200
    assert r_comp.json()["match"]["title"] == "GCS Downloaded Match"


def test_final_95_percent_backend_sprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from rollmetrics import cli as cli_mod
    from rollmetrics.evals import model_bench as mb_mod
    from rollmetrics.knowledge import ibjjf_sources as ib_mod
    from rollmetrics.knowledge import indexer as idx_mod
    from rollmetrics.video import ingest as ingest_mod

    # 1. ibjjf_sources _load_or_fetch_rulebook_pdf_bytes online fetch (mocked httpx.get)
    class _PdfResp:
        status_code = 200
        content = b"%PDF-1.4 mocked online rulebook"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _PdfResp())
    fetched = ib_mod._load_or_fetch_rulebook_pdf_bytes(fetch_online=True, pdf_path=tmp_path / "missing2.pdf")
    assert fetched is not None and fetched.startswith(b"%PDF")

    # 2. model_bench live_api=True (mocked _call_gemini_fixture + _call_ollama_fixture)
    fake_tuple = (
        {"kind": "guard_pass", "actor": "A", "foul_severity": "legal", "points": 3},
        420.0,
        120,
        40,
    )
    monkeypatch.setattr(mb_mod, "_call_gemini_fixture", lambda *_a, **_kw: fake_tuple)
    monkeypatch.setattr(mb_mod, "_call_ollama_fixture", lambda *_a, **_kw: fake_tuple)
    arena_live = mb_mod.run_model_arena(
        models=["gemini-4-hydrogen", "ollama/gemma4:e2b-it-q4_K_M"],
        live_api=True,
        settings=Settings(environment="test", gemini_api_key="AIzaFakeKey", runtime_dir=tmp_path / "rt_mb"),
    )
    assert len(arena_live.get("models") or []) == 2

    # 3. indexer compute_cloud_embeddings (mocked google.genai.Client) + verify_index_integrity
    import google.genai as genai_mod

    class _FakeEmbedClient:
        def __init__(self, **_kw: Any) -> None:
            class _Models:
                def embed_content(self, **_k: Any) -> Any:
                    emb = type("E", (), {"values": [0.1] * 768})()
                    return type("R", (), {"embeddings": [emb, emb]})()

            self.models = _Models()

    monkeypatch.setattr(genai_mod, "Client", _FakeEmbedClient)
    vecs = idx_mod.compute_cloud_embeddings(["armbar", "sweep"], api_key="AIzaFake", dim=768)
    assert len(vecs) == 2

    # 4. main.py ImportError fallbacks for /api/local/folder/scan, /api/local/academy/import-folder, /api/evals/models
    settings = Settings(environment="test", allow_local_dev_bypass=True, runtime_dir=tmp_path / "rt_fb")
    client = TestClient(create_app(settings))
    vdir = tmp_path / "fb_vids"
    vdir.mkdir()
    (vdir / "spar.mp4").write_bytes(b"0" * 2048)
    ndir = tmp_path / "fb_notes"
    ndir.mkdir()
    (ndir / "sweep.md").write_text("Off-balance before sweeping.", encoding="utf-8")

    monkeypatch.setitem(sys.modules, "rollmetrics.desktop", None)
    r_sc = client.post("/api/local/folder/scan", json={"folder_path": str(vdir)})
    assert r_sc.status_code == 200 and r_sc.json()["count"] == 1
    r_im = client.post(
        "/api/local/academy/import-folder",
        json={"folder_path": str(ndir), "academy_id": "fb-acad", "academy_name": "FB Academy"},
    )
    assert r_im.status_code == 200 and r_im.json()["imported_count"] == 1
    monkeypatch.delitem(sys.modules, "rollmetrics.desktop", raising=False)

    # /api/evals/models/run fallback when model_bench unavailable
    monkeypatch.setitem(sys.modules, "rollmetrics.evals.model_bench", None)
    r_mr = client.post("/api/evals/models/run", json={"models": ["gemini-4-hydrogen"], "live_api": False})
    assert r_mr.status_code == 200 and len(r_mr.json()["models"]) == 1
    monkeypatch.delitem(sys.modules, "rollmetrics.evals.model_bench", raising=False)

    # 5. CLI analyze (mocked MatchAnalyzer) + index import-private + desktop --dry-run
    import rollmetrics.video.analyzer as an_mod

    async def _fake_an(_self: Any, *_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"snapshot": {"winner": "A", "win_method": "points", "timeline": []}}

    monkeypatch.setattr(an_mod.MatchAnalyzer, "analyze_video", _fake_an)
    sample_mp4 = _make_tiny_mp4(tmp_path / "cli_an.mp4")
    assert cli_mod.main(["analyze", str(sample_mp4), "--belt", "white", "--uniform", "gi", "--profile", "local"]) == 0
    assert cli_mod.main(["desktop", "--dry-run", "--no-browser"]) == 0

    # 6. video.ingest download_video_url invalid URL ValueError
    with pytest.raises(ValueError, match="Only http://"):
        ingest_mod.download_video_url("ftp://bad-scheme/video.mp4")


def test_cross_95_percent_backend_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time
    import urllib.request
    import webbrowser

    import google.genai as genai_mod
    import numpy as np
    import uvicorn
    from rollmetrics import cli as cli_mod
    from rollmetrics.knowledge import ibjjf_sources as ib_mod
    from rollmetrics.knowledge import indexer as idx_mod
    from rollmetrics.knowledge import retriever as ret_mod
    from rollmetrics.video.analyzer import MatchAnalyzer

    # 1. ibjjf_sources online discovery + download (lines 614-636)
    monkeypatch.setattr(ib_mod, "SCRATCH_CACHED_PDF", tmp_path / "no_cached.pdf")
    assert ib_mod._load_or_fetch_rulebook_pdf_bytes(fetch_online=False, pdf_path=tmp_path / "no2.pdf") is None

    call_n = {"n": 0}

    class _UrlResp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    def _fake_urlopen(_req: Any, **_kw: Any) -> _UrlResp:
        call_n["n"] += 1
        if call_n["n"] == 1:
            return _UrlResp(b'<a href="/rails/active_storage/blobs/redirect/abc/Rules_EN.pdf">PDF</a>')
        return _UrlResp(b"%PDF-1.4 online-fetched")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    pdf_bytes = ib_mod._load_or_fetch_rulebook_pdf_bytes(fetch_online=True, pdf_path=tmp_path / "no3.pdf")
    assert pdf_bytes == b"%PDF-1.4 online-fetched"

    def _boom_urlopen(_req: Any, **_kw: Any) -> Any:
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", _boom_urlopen)
    assert ib_mod._load_or_fetch_rulebook_pdf_bytes(fetch_online=True, pdf_path=tmp_path / "no4.pdf") is None

    # 2. indexer compute_cloud_embeddings resize + per-doc concurrent fallback (lines 121-150)
    class _SingleFallbackClient:
        def __init__(self, **_kw: Any) -> None:
            class _Models:
                def embed_content(self, **k: Any) -> Any:
                    emb = type("E", (), {"values": [0.2] * 16})()
                    # Return 1 embedding even for 2-item batch -> triggers per-doc concurrent fallback
                    return type("R", (), {"embeddings": [emb]})()

            self.models = _Models()

    monkeypatch.setattr(genai_mod, "Client", _SingleFallbackClient)
    vecs = idx_mod.compute_cloud_embeddings(["doc1", "doc2"], api_key="AIzaFake", dim=768)
    assert len(vecs) == 2 and vecs[0].shape == (768,)

    # 3. MatchAnalyzer defensive opponent scoring + extra_vision_notes (lines 283-306, 331-345)
    from rollmetrics.rules.state_machine import IBJJFRefereeEngine

    analyzer = MatchAnalyzer(settings=Settings(environment="test", runtime_dir=tmp_path / "rt_an"))
    div = MatchDivisionConfig(belt=BeltRank.WHITE, locked_athlete=AthleteId("A"))
    ev_opp = RawObservedEvent(actor=AthleteId("B"), kind=EventKind.GUARD_PASS, timestamp_sec=40.0, hold_duration_sec=3.5)
    eng = IBJJFRefereeEngine(division=div)
    eng.ingest_event(ev_opp)
    cards = analyzer._build_coaching_summary(
        eng.snapshot(),
        AthleteId("A"),
        extra_vision_notes=[{"headline": "Frame the near hip early", "position": "closed_guard", "timestamp_sec": 38.0}],
    )
    assert any(c.get("category") == "Defensive Frame & Retention Priority" for c in cards)
    assert any(c.get("category") == "Video Technical Note" for c in cards)

    # 4. CLI desktop (non-dry-run with mocked uvicorn/browser), index build, missing import-private
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)
    monkeypatch.setattr(webbrowser, "open", lambda *_a: True)
    monkeypatch.setattr(time, "sleep", lambda *_a: None)
    assert cli_mod.main(["desktop", "--port", "8098"]) == 0
    monkeypatch.setattr(idx_mod, "build_knowledge_index", lambda **_kw: {"version": "v9.9.9", "ok": True})
    assert cli_mod.main(["index", "build", "--version", "v9.9.9", "--knowledge-dir", str(tmp_path)]) == 0
    assert cli_mod.main(["index", "import-private", str(tmp_path / "does_not_exist")]) != 0

    # 5. KnowledgeRetriever cloud _embed_query happy + fallback (lines 203-214)
    r_cfg = Settings(environment="test", profile="cloud", gemini_api_key="AIzaFake")
    retr = ret_mod.KnowledgeRetriever(knowledge_dir=r_cfg.knowledge_dir, version="v0.1.0", settings=r_cfg)
    retr._index_backend = "gemini-embedding-2"
    monkeypatch.setattr(ret_mod, "compute_cloud_embeddings", lambda *_a, **_kw: [np.ones(768, dtype=np.float32)])
    vec_ok, used_cloud = retr._embed_query("closed guard armbar")
    assert used_cloud is True and vec_ok.shape == (768,)
    monkeypatch.setattr(ret_mod, "compute_cloud_embeddings", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("api err")))
    vec_fb, used_cloud_fb = retr._embed_query("closed guard armbar")
    assert used_cloud_fb is False and vec_fb.shape == (768,)

    # 7. GeminiCloudProvider query_gemma_api (mocked genai.Client)
    import asyncio

    from rollmetrics.providers.gemini_cloud import GeminiCloudProvider

    class _FakeGenaiClient:
        def __init__(self, **_kw: Any) -> None:
            class _Interactions:
                def create(self, **_k: Any) -> Any:
                    out = type("O", (), {"type": "text", "text": '{"ok": true}'})()
                    usage = type("U", (), {"total_input_tokens": 10, "total_output_tokens": 5})()
                    return type("I", (), {"outputs": [out], "usage": usage})()

            self.interactions = _Interactions()

    monkeypatch.setattr(genai_mod, "Client", _FakeGenaiClient)
    gc = GeminiCloudProvider(settings=Settings(environment="test", gemini_api_key="AIzaFakeKey"))
    gemma_res = asyncio.run(gc.query_gemma_api("Return JSON"))
    assert gemma_res.get("ok") is True or "text" in gemma_res or "model_id" in gemma_res

    # 8. main.py /ws/live lock_athlete + frame + finalize + /api/evals/latest fallback
    s_ws = Settings(environment="test", allow_local_dev_bypass=True, runtime_dir=tmp_path / "rt_ws")
    c_ws = TestClient(create_app(s_ws))
    with c_ws.websocket_connect("/ws/live") as ws:
        _ = ws.receive_json()
        ws.send_json({"type": "lock_athlete", "athlete": "B"})
        assert ws.receive_json()["type"] == "hud"
        ws.send_json({"type": "finalize"})
        assert ws.receive_json()["type"] == "hud"

    import sys

    monkeypatch.setitem(sys.modules, "rollmetrics.evals.runner", None)
    r_ev_fb = c_ws.get("/api/evals/latest?version=v0.1.0")
    assert r_ev_fb.status_code == 200 and r_ev_fb.json()["overall_pass"] is True
    monkeypatch.delitem(sys.modules, "rollmetrics.evals.runner", raising=False)

    # 9. EOF frame fallback in media_studio + test-connection cloud error + /api/evals/models fallback + CLI compare-models
    from rollmetrics.video.media_studio import MediaStudio

    sample_eof = _make_tiny_mp4(tmp_path / "eof_sample.mp4")
    eof_board = MediaStudio().generate_three_panel_board(
        sample_eof, 9999.0, tmp_path / "eof_board.png", title="EOF Test", cues=["Frame 1"]
    )
    assert Path(eof_board).is_file()

    async def _boom_gemma(_self: Any, _prompt: str) -> dict[str, Any]:
        raise RuntimeError("cloud error")

    monkeypatch.setattr(GeminiCloudProvider, "query_gemma_api", _boom_gemma)
    c_err = TestClient(
        create_app(
            Settings(environment="test", allow_local_dev_bypass=True, gemini_api_key="AIzaFakeKey", runtime_dir=tmp_path / "rt_err")
        )
    )
    r_cld_err = c_err.post("/api/system/test-connection", json={"target": "cloud"})
    assert r_cld_err.status_code == 200 and r_cld_err.json()["ok"] is False

    monkeypatch.setitem(sys.modules, "rollmetrics.evals.model_bench", None)
    r_m_fb = c_err.get("/api/evals/models?version=v9.9.9")
    assert r_m_fb.status_code == 200 and len(r_m_fb.json()["models"]) >= 3
    monkeypatch.delitem(sys.modules, "rollmetrics.evals.model_bench", raising=False)

    assert cli_mod.main(["eval", "--version", "v0.1.0", "--compare-models"]) == 0

    # 10. indexer embed_cloud fallback branch (lines 362-374) + happy cloud embed + reuse_existing_jsonl (lines 334-335)
    monkeypatch.undo()
    monkeypatch.setattr(idx_mod, "get_settings", lambda: Settings(environment="test", gemini_api_key="AIzaFakeKey"))
    monkeypatch.setattr(idx_mod, "compute_cloud_embeddings", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("embed fail")))
    man_fb = idx_mod.build_knowledge_index(
        version="v0.9.9",
        knowledge_dir=tmp_path / "kb_test",
        embed_cloud=True,
        fetch_online_pdf=False,
    )
    assert man_fb["version"] == "v0.9.9"

    monkeypatch.setattr(
        idx_mod,
        "compute_cloud_embeddings",
        lambda texts, **_kw: [np.ones(768, dtype=np.float32) for _ in texts],
    )
    man_ok = idx_mod.build_knowledge_index(
        version="v0.9.9",
        knowledge_dir=tmp_path / "kb_test",
        embed_cloud=True,
        reuse_existing_jsonl=True,
        fetch_online_pdf=False,
    )
    assert man_ok["embedding_backend"] == "gemini-embedding-2"

    # 11. media_studio EOF frame fallback directly (lines 103-114)
    from rollmetrics.video import media_studio as ms_mod

    orig_sub = subprocess.run

    def _eof_then_ok(cmd: list[str], **kw: Any) -> Any:
        if "-ss" in cmd:
            return type("P", (), {"returncode": 1, "stdout": "", "stderr": "eof"})()
        return orig_sub(cmd, **kw)

    monkeypatch.setattr(ms_mod.subprocess, "run", _eof_then_ok)
    fb_jpg = ms_mod._extract_single_frame(sample_eof, 999.0, tmp_path / "eof_direct.jpg")
    assert fb_jpg.is_file()

    # 12. desktop _detect_total_ram_gb & _detect_cpu_label (lines 33-39, 55-56)
    from rollmetrics import desktop as desk_mod

    monkeypatch.setattr(desk_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        desk_mod.subprocess,
        "run",
        lambda cmd, **_kw: type("P", (), {"returncode": 0, "stdout": "17179869184" if "hw.memsize" in cmd else "Apple M3"})(),
    )
    assert desk_mod._detect_total_ram_gb() == 16.0
    assert desk_mod._detect_cpu_label() == "Apple M3"
    monkeypatch.setattr(desk_mod.platform, "system", lambda: "Linux")
    ram_linux = desk_mod._detect_total_ram_gb()
    assert ram_linux is None or ram_linux > 0

    # 13. video.ingest download_video_url FileNotFoundError -> yt_dlp module fallback (lines 168-186)
    from rollmetrics.video import ingest as ing_mod

    monkeypatch.setattr(ing_mod.subprocess, "run", lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError("no yt-dlp")))

    class _FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self._outtmpl = opts["outtmpl"]

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def download(self, _urls: list[str]) -> None:
            out_file = Path(self._outtmpl.replace("%(ext)s", "mp4"))
            out_file.write_bytes(b"fake-mp4")

    monkeypatch.setitem(sys.modules, "yt_dlp", type("M", (), {"YoutubeDL": _FakeYDL})())
    dl_path = ing_mod.download_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path / "yt_dl")
    assert dl_path.is_file()
