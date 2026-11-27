"""Exhaustive 100% line coverage tests for backend/rollmetrics/main.py (Wave 3)."""

from __future__ import annotations

import builtins
import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from rollmetrics import main as main_mod
from rollmetrics.config import Settings
from rollmetrics.main import create_app


def _make_tiny_mp4(dest: Path) -> Path:
    ffmpeg = main_mod._resolve_ffmpeg_bin()
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=0.4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        capture_output=True,
        check=True,
    )
    return dest


def test_main_100_helpers_and_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. _resolve_build_sha env override (L75) and subprocess error fallback (L88-90)
    monkeypatch.setenv("ROLLMETRICS_BUILD_SHA", "abcdef123456")
    assert main_mod._resolve_build_sha() == "abcdef1"
    monkeypatch.delenv("ROLLMETRICS_BUILD_SHA", raising=False)
    monkeypatch.setattr(main_mod.subprocess, "run", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no git")))
    assert main_mod._resolve_build_sha() == "local"
    monkeypatch.undo()

    # 2. _load_presets invalid item continue (L501) + builtin merge (L528) + missing file (L530)
    kb_empty = tmp_path / "kb_empty"
    s_empty = Settings(environment="test", knowledge_dir=kb_empty, runtime_dir=tmp_path / "rt_p")
    assert len(main_mod._load_presets(s_empty)) >= 2

    eval_dir = kb_empty / "evals"
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "match_timelines.json").write_text(
        json.dumps([{"id": "non_dict_skip"}, "bad_string", {"match_id": "custom_only_1", "events": []}]),
        encoding="utf-8",
    )
    presets = main_mod._load_presets(s_empty)
    assert any(p.get("id") == "custom_only_1" for p in presets)

    # 3. _get_retriever TypeError fallback (L540-541) and recoverable error -> None (L542-543)
    import rollmetrics.knowledge.retriever as ret_mod

    class _NoSettingsRet:
        def __init__(self, knowledge_dir: Any = None, version: str = "v0.1.0") -> None:
            self.version = version

    monkeypatch.setattr(ret_mod, "KnowledgeRetriever", _NoSettingsRet)
    assert main_mod._get_retriever(s_empty) is not None
    monkeypatch.setattr(ret_mod, "KnowledgeRetriever", lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert main_mod._get_retriever(s_empty) is None
    monkeypatch.undo()

    # 4. _resolve_ffmpeg_bin fallback loop (L588-592)
    import rollmetrics.video.ingest as ing_mod

    monkeypatch.setattr(ing_mod, "resolve_binary", lambda *_a: (_ for _ in ()).throw(FileNotFoundError("no bin")))
    res_ff = main_mod._resolve_ffmpeg_bin()
    assert "ffmpeg" in res_ff
    monkeypatch.setattr(main_mod.Path, "is_file", lambda _self: False)
    assert main_mod._resolve_ffmpeg_bin() == "ffmpeg"
    monkeypatch.undo()

    # 5. _load_user_bucket_overrides bad JSON (L1703), _load_usage_telemetry bad JSON (L2076),
    # _record_usage_event empty email (L2082), _load_uploads_meta & _load_shares bad JSON (L2414, L2431)
    s_bad = Settings(environment="test", runtime_dir=tmp_path / "rt_bad")
    s_bad.runtime_dir.mkdir(parents=True, exist_ok=True)
    for fn in ("user_match_buckets.json", "usage_telemetry.json", "uploads_meta.json", "shares.json"):
        (s_bad.runtime_dir / fn).write_text("[]", encoding="utf-8")
    assert main_mod._load_user_bucket_overrides(s_bad) == {}
    assert main_mod._load_usage_telemetry(s_bad) == {}
    main_mod._record_usage_event(s_bad, "")
    assert main_mod._load_uploads_meta(s_bad) == {}
    assert main_mod._load_shares(s_bad) == {}

    # 6. _resolve_rule_info with non-youtube source_url (L1748)
    main_mod._RULE_INFO_CACHE.clear()

    class _FakeRet:
        def cite_ibjjf_rule(self, *_a: Any, **_kw: Any) -> list[dict[str, Any]]:
            return [{"title": "Art 4.1", "text": "Takedown.", "source_url": "https://ibjjf.com/rules.pdf"}]

    monkeypatch.setattr(main_mod, "_get_retriever", lambda *_a, **_kw: _FakeRet())
    rinfo = main_mod._resolve_rule_info("Art. 4.1", "Takedown", s_bad)
    assert rinfo["rulebook_url"] == "https://ibjjf.com/rules.pdf"
    monkeypatch.undo()

    # 7. _build_collection_scouting_summary athlete B match + draw (L1967, L2002, L2013-2014)
    sc = main_mod._build_collection_scouting_summary(
        [
            {
                "id": "m_b",
                "athlete_a": {"name": "Alpha"},
                "athlete_b": {"name": "Beta Athlete"},
                "winner": "draw",
                "events": [{"pts_a": 2, "pts_b": 4, "detail": "Sweep"}],
            }
        ],
        focus_athlete="Beta Athlete",
    )
    assert sc["match_count"] == 1
    assert sc["combined_points_for"] == 4 and sc["combined_points_against"] == 2

    # 8. _load_admin_overview_payload: blank jsonl line (L2130) + custom private academy refs (L2259-2262)
    from rollmetrics.knowledge.academies import add_academy_reference, upsert_academy

    s_adm = Settings(environment="test", runtime_dir=tmp_path / "rt_adm")
    upsert_academy(s_adm, "my-gym", "My Gym")
    add_academy_reference(
        s_adm,
        "my-gym",
        title="Private Pass",
        text="Hip switch pass",
        source_url="https://youtu.be/dQw4w9WgXcQ",
    )
    adm_payload = main_mod._load_admin_overview_payload(s_adm, "coach@example.com")
    assert any(c["id"] == "my-gym" and c["visibility"] == "private" for c in adm_payload["curricula"])

    # 9. _try_mint_gcs_signed_put_url edge cases (L2473, L2509)
    assert main_mod._try_mint_gcs_signed_put_url("", "obj", "video/mp4") is None
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_kw: type("R", (), {"status_code": 500, "json": lambda self: {}})(),
    )
    import google.auth as gauth_mod

    class _NoSaCreds:
        token = ""
        service_account_email = ""

        def refresh(self, *_a: Any) -> None:
            return None

    monkeypatch.setattr(gauth_mod, "default", lambda **_kw: (_NoSaCreds(), "proj"))
    assert main_mod._try_mint_gcs_signed_put_url("bkt", "obj", "video/mp4") is None

    class _SaCreds:
        token = "tok"
        service_account_email = "sa@proj.iam.gserviceaccount.com"

        def refresh(self, *_a: Any) -> None:
            return None

    monkeypatch.setattr(gauth_mod, "default", lambda **_kw: (_SaCreds(), "proj"))
    assert main_mod._try_mint_gcs_signed_put_url("bkt", "obj", "video/mp4") is None

    # 10. _get_gcs_bearer_token refresh (L2695) + error (L2697-2698)
    main_mod._GCS_ADC_CREDS = type("C", (), {"valid": False, "token": "t2", "refresh": lambda self, _r: None})()
    assert main_mod._get_gcs_bearer_token() == "t2"
    main_mod._GCS_ADC_CREDS = type(
        "C", (), {"valid": False, "token": "", "refresh": lambda self, _r: (_ for _ in ()).throw(RuntimeError("err"))}
    )()
    assert main_mod._get_gcs_bearer_token() is None
    main_mod._GCS_ADC_CREDS = None

    # 11. _download_gcs_match_to_cache (L2736, L2758-2759), _find_local_showcase_video (L2766),
    # _resolve_showcase_video_path (L2773-2776), _stream_file_with_range early EOF (L2809),
    # _resolve_media_video_path fallback to showcase match_id (L2901-2902)
    s_no_gcs = Settings(environment="test", gcs_bucket="", runtime_dir=tmp_path / "rt_nogcs")
    assert main_mod._download_gcs_match_to_cache("mc9_semifinal", s_no_gcs) is None
    s_gcs = Settings(environment="test", gcs_bucket="bkt", runtime_dir=tmp_path / "rt_gcs")
    monkeypatch.setattr(gauth_mod, "default", lambda **_kw: (_ for _ in ()).throw(OSError("no adc")))
    assert main_mod._download_gcs_match_to_cache("mc9_semifinal", s_gcs) is None
    assert main_mod._find_local_showcase_video("nonexistent_match_xyz_99", s_gcs) is None
    monkeypatch.setattr(main_mod, "_find_local_showcase_video", lambda *_a: None)
    monkeypatch.setattr(main_mod, "_download_gcs_match_to_cache", lambda *_a: None)
    with pytest.raises(HTTPException) as exc_404:
        main_mod._resolve_showcase_video_path("mc9_semifinal", s_gcs)
    assert exc_404.value.status_code == 404
    fake_cached = tmp_path / "cached_m.mp4"
    fake_cached.write_bytes(b"mp4")
    monkeypatch.setattr(main_mod, "_download_gcs_match_to_cache", lambda *_a: fake_cached)
    assert main_mod._resolve_showcase_video_path("mc9_semifinal", s_gcs) == fake_cached
    assert main_mod._resolve_media_video_path(None, s_gcs) == fake_cached

    # _stream_file_with_range early EOF break (L2809)
    class _EarlyEofPath:
        def stat(self) -> Any:
            return type("St", (), {"st_size": 100})()

        def open(self, _mode: str) -> Any:
            import io

            return io.BytesIO(b"")

    resp_eof = main_mod._stream_file_with_range(_EarlyEofPath(), "bytes=0-50")  # type: ignore[arg-type]
    assert resp_eof.status_code == 206
    import asyncio

    async def _drain() -> list[bytes]:
        return [c async for c in resp_eof.body_iterator]  # type: ignore[attr-defined]

    assert asyncio.run(_drain()) == []

    # _persist_analyzed_match skips non-dict timeline items (L2536)
    persisted = main_mod._persist_analyzed_match(
        s_no_gcs,
        {"timeline": ["not-a-dict"]},
        owner_email="coach@example.com",
        bucket="my_matches",
        title="Skip Test",
        belt=main_mod.BeltRank.BLUE,
        uniform="gi",
        athlete_a_name="A",
        athlete_b_name="B",
        athlete_a_gi="white",
        athlete_b_gi="blue",
        video_path="",
        video_url="",
    )
    assert persisted["events"] == []

    # _load_admin_overview_payload skips non-dict model rows (L2130)
    kb_ml = tmp_path / "kb_ml"
    (kb_ml / "evals" / "scoreboard_v1").mkdir(parents=True, exist_ok=True)
    (kb_ml / "evals" / "scoreboard_v1" / "latest_results.json").write_text(
        json.dumps({"models": ["bad-row", {"model_id": "m1"}]}),
        encoding="utf-8",
    )
    s_ml = Settings(environment="test", knowledge_dir=kb_ml, runtime_dir=tmp_path / "rt_ml")
    ml_out = main_mod._load_admin_overview_payload(s_ml, "coach@example.com")
    assert isinstance(ml_out, dict)
    monkeypatch.undo()


def test_main_100_endpoints_errors_and_websocket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings(environment="test", allow_local_dev_bypass=True, runtime_dir=tmp_path / "rt_ep")
    client = TestClient(create_app(s))

    # 1. /api/system/status retriever manifest error (L3208-3209) + /api/system/config overrides (L3295, L3301)
    class _BrokenManRet:
        def manifest(self) -> dict[str, Any]:
            raise RuntimeError("manifest err")

    monkeypatch.setattr(main_mod, "_get_retriever", lambda *_a, **_kw: _BrokenManRet())
    r_st = client.get("/api/system/status")
    assert r_st.status_code == 200 and r_st.json()["knowledge_manifest"]["available"] is False
    monkeypatch.undo()

    r_cfg = client.post(
        "/api/system/config",
        json={"ollama_vision_model": "gemma4:e2b-it-q4_K_M", "gemini_api_key": "AIzaTestKey123"},
    )
    assert r_cfg.status_code == 200

    # 2. /api/collections/summary empty match_ids (L3500)
    r_col = client.post("/api/collections/summary", json={"match_ids": [], "focus_athlete": ""})
    assert r_col.status_code == 200

    # 3. GCS range proxy branch on /api/matches/{id}/video & /api/share/{tok}/video (L3536, L4161)
    # and 404 poster unavailable (L3582, L4209)
    async def _fake_proxy(*_a: Any, **_kw: Any) -> Any:
        from fastapi import Response

        return Response(content=b"video", media_type="video/mp4")

    monkeypatch.setattr(main_mod, "_find_local_showcase_video", lambda *_a: None)
    monkeypatch.setattr(main_mod, "_proxy_gcs_video_range", _fake_proxy)
    s_cloud_vid = Settings(environment="test", allow_local_dev_bypass=True, gcs_bucket="bkt", runtime_dir=tmp_path / "rt_cv")
    c_cv = TestClient(create_app(s_cloud_vid))
    assert c_cv.get("/api/matches/mc9_semifinal/video").status_code == 200
    monkeypatch.setattr(main_mod, "_download_gcs_match_to_cache", lambda *_a: None)
    assert c_cv.get("/api/matches/mc9_semifinal/poster").status_code == 404

    tok_cv = c_cv.post("/api/matches/mc9_semifinal/share").json()["token"]
    assert c_cv.get(f"/api/share/{tok_cv}/video").status_code == 200
    assert c_cv.get(f"/api/share/{tok_cv}/poster").status_code == 404
    monkeypatch.undo()

    # 4. /api/uploads/google-drive empty content (L3763)
    async def _empty_get(_self: Any, _url: str, **_kw: Any) -> Any:
        return type("R", (), {"content": b"", "raise_for_status": lambda self: None})()

    monkeypatch.setattr(httpx.AsyncClient, "get", _empty_get)
    r_gd = client.post("/api/uploads/google-drive", json={"drive_url": "https://drive.google.com/file/d/12345678901234567890/view"})
    assert r_gd.status_code == 400
    monkeypatch.undo()

    # 5. Share/Match/Moments/Review validation & 403/404 branches (L3858, L3860, L3875, L3888, L3905, L3910, L4003, L4043, L4046, L4123, L4282)
    assert client.delete("/api/share/nonexistent_tok").status_code == 404
    assert client.delete("/api/matches/bad..id").status_code == 400
    assert client.put("/api/matches/bad..id/moments", json={"events": []}).status_code == 400
    assert client.put("/api/matches/nonexistent_match/moments", json={"events": []}).status_code == 404
    assert client.post("/api/matches/bad..id/review", json={"review_status": "reviewed"}).status_code == 400
    assert client.get("/api/share/nonexistent_tok/media/missing.mp4").status_code == 404

    # Non-owner / non-admin member hitting revoke, delete, roster, review
    import rollmetrics.auth as auth_mod

    monkeypatch.setattr(
        auth_mod.google_id_token,
        "verify_oauth2_token",
        lambda *_a, **_kw: {"email": "other@example.com", "name": "Other", "email_verified": True},
    )
    s_mem = Settings(
        environment="cloud",
        allow_open_signup=True,
        allow_local_dev_bypass=False,
        allowlist_emails="admin@example.com",
        runtime_dir=tmp_path / "rt_mem",
    )
    c_mem = TestClient(create_app(s_mem))
    main_mod._save_shares(s_mem, {"tok_other": {"token": "tok_other", "match_id": "mc9_semifinal", "owner_email": "admin@example.com"}})
    (s_mem.runtime_dir / "custom_matches.json").write_text(
        json.dumps([{"id": "custom_owned", "title": "Admin Match", "owner_email": "admin@example.com"}]),
        encoding="utf-8",
    )
    hdr = {"Authorization": "Bearer mem-tok"}
    assert c_mem.delete("/api/share/tok_other", headers=hdr).status_code == 403
    assert c_mem.delete("/api/matches/custom_owned", headers=hdr).status_code == 403
    assert c_mem.get("/api/team/roster", headers=hdr).json()["roster"] == []
    assert c_mem.post("/api/matches/mc9_semifinal/review", headers=hdr, json={"review_status": "reviewed"}).status_code == 403
    # Orphan share pointing to deleted match -> 404 (L4123)
    main_mod._save_shares(s_mem, {"tok_orphan": {"token": "tok_orphan", "match_id": "deleted_match_99", "owner_email": "admin@example.com"}})
    assert c_mem.get("/api/share/tok_orphan").status_code == 404
    monkeypatch.undo()

    # 6. ImportError fallback paths: MediaStudio (L4248-4249, L4612-4613, L4654-4655), desktop (L4307-4310),
    # MatchAnalyzer (L4503-4504), LiveRefereeSession (L5205-5208), legality ValueError (L4806-4807),
    # knowledge manifest unavailable (L4860), evals cache read (L4933-4934), model_bench cache write (L5094-5095)
    sample_mp4 = _make_tiny_mp4(tmp_path / "imp_fb.mp4")
    cached_m = s.runtime_dir / "matches" / "mc9_semifinal.mp4"
    cached_m.parent.mkdir(parents=True, exist_ok=True)
    cached_m.write_bytes(sample_mp4.read_bytes())
    tok_fb = client.post("/api/matches/mc9_semifinal/share").json()["token"]

    orig_import = builtins.__import__

    def _block_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name in (
            "rollmetrics.video.media_studio",
            "rollmetrics.desktop",
            "rollmetrics.video.analyzer",
            "rollmetrics.video.live_session",
        ):
            raise ImportError(f"blocked {name}")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_import)
    assert client.post(f"/api/share/{tok_fb}/highlight", json={"start_sec": 0.0, "duration_sec": 1.0, "speed": 0.4}).status_code == 200
    assert client.post("/api/media/board", json={"video_path": str(sample_mp4), "center_timestamp_sec": 0.1}).status_code == 200
    assert client.post("/api/media/slowmo", json={"video_path": str(sample_mp4), "start_sec": 0.0, "duration_sec": 1.0}).status_code == 200
    assert client.get("/api/local/hardware").status_code == 200
    assert client.post("/api/analyze/events", json={"events": [{"actor": "A", "kind": "sweep", "timestamp_sec": 10.0, "hold_duration_sec": 3.5}]}).status_code == 200

    # WS /ws/live with _FallbackLiveSession + data-URI frame + blank message + lock_athlete + finalize (L5207-5208, L5225, L5251-5254, L5270, L5302-5306)
    with client.websocket_connect("/ws/live") as ws:
        _ = ws.receive_json()
        ws.send_json(["not-a-dict"])
        ws.send_json({"type": "lock_athlete", "athlete": "B"})
        assert ws.receive_json()["type"] == "hud"
        ws.send_json({"type": "frame", "image_base64": "data:image/jpeg;base64,QUJD", "elapsed_sec": 1.0})
        assert ws.receive_json()["type"] == "hud"
        ws.send_json({"type": "finalize"})
        assert ws.receive_json()["type"] == "hud"
    monkeypatch.undo()

    # 7. Remaining endpoint branches for literal 100.0% coverage on main.py
    # test-connection overrides (L3295, L3301)
    r_tc = client.post(
        "/api/system/test-connection",
        json={"target": "local", "ollama_base_url": "http://127.0.0.1:1", "ollama_vision_model": "gemma4:e2b", "gemini_api_key": "AIzaKey"},
    )
    assert r_tc.status_code == 200

    # Poster 404 on /api/matches/{id}/poster and /api/share/{tok}/poster when ffmpeg produces no file (L3582, L4209)
    s_p404 = Settings(environment="test", allow_local_dev_bypass=True, runtime_dir=tmp_path / "rt_p404")
    c_p404 = TestClient(create_app(s_p404))
    p404_vid = s_p404.runtime_dir / "matches" / "mc9_semifinal.mp4"
    p404_vid.parent.mkdir(parents=True, exist_ok=True)
    p404_vid.write_bytes(b"0" * 2048)
    tok_p404 = c_p404.post("/api/matches/mc9_semifinal/share").json()["token"]
    monkeypatch.setattr(main_mod.subprocess, "run", lambda *_a, **_kw: None)
    assert c_p404.get("/api/matches/mc9_semifinal/poster").status_code == 404
    assert c_p404.get(f"/api/share/{tok_p404}/poster").status_code == 404
    monkeypatch.undo()

    # Shared media file missing on disk despite being in allowed_renders -> 404 (L4282)
    main_mod._save_shares(
        s,
        {"tok_miss_file": {"token": "tok_miss_file", "match_id": "mc9_semifinal", "owner_email": "coach@example.com", "allowed_renders": ["ghost.mp4"]}},
    )
    assert client.get("/api/share/tok_miss_file/media/ghost.mp4").status_code == 404

    # _require_local_or_admin in cloud mode as admin (L4288)
    s_cadm = Settings(
        environment="cloud",
        allow_local_dev_bypass=False,
        allowlist_emails="admin@example.com",
        runtime_dir=tmp_path / "rt_cadm",
    )
    monkeypatch.setattr(
        auth_mod.google_id_token,
        "verify_oauth2_token",
        lambda *_a, **_kw: {"email": "admin@example.com", "name": "Admin", "email_verified": True},
    )
    c_cadm = TestClient(create_app(s_cadm))
    assert c_cadm.get("/api/local/hardware", headers={"Authorization": "Bearer adm-tok"}).status_code == 200
    monkeypatch.undo()

    # Desktop awaitable hardware (L4305), 1-arg scan returning list (L4337, L4340), 3-arg import_local_academy_folder (L4431)
    import rollmetrics.desktop as desk_mod

    async def _async_hw(_cfg: Any) -> dict[str, Any]:
        return {"offline_ready": True}

    monkeypatch.setattr(desk_mod, "get_local_hardware_status", _async_hw)
    assert client.get("/api/local/hardware").json()["offline_ready"] is True
    monkeypatch.setattr(desk_mod, "scan_local_video_folder", lambda folder: [{"path": f"{folder}/a.mp4", "filename": "a.mp4"}])
    scan_dir = tmp_path / "scan_dir"
    scan_dir.mkdir(exist_ok=True)
    r_sc = client.post("/api/local/folder/scan", json={"folder_path": str(scan_dir)})
    assert r_sc.status_code == 200 and r_sc.json()["count"] == 1
    monkeypatch.setattr(desk_mod, "import_local_academy_folder", lambda folder, aid, aname: {"imported": 1, "academy_id": aid})
    r_imp = client.post("/api/local/academy/import-folder", json={"folder_path": str(scan_dir)})
    assert r_imp.status_code == 200 and r_imp.json()["imported"] == 1
    monkeypatch.undo()

    # /api/academies/me/references ValueError -> 422 (L4806-4807)
    import rollmetrics.knowledge.academies as acad_mod

    monkeypatch.setattr(acad_mod, "add_academy_reference", lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("bad ref")))
    assert client.post("/api/academies/me/references", json={"title": "T", "text": "Body"}).status_code == 422
    monkeypatch.undo()

    # Knowledge manifest unavailable for nonexistent version (L4860)
    monkeypatch.setattr(main_mod, "_get_retriever", lambda *_a, **_kw: None)
    assert client.get("/api/knowledge/manifest?version=v9.9.9").json()["available"] is False
    monkeypatch.undo()

    # Evals latest cached JSON read (L4933-4934)
    eval_cache = s.runtime_dir / "latest_eval_v9.9.9.json"
    eval_cache.parent.mkdir(parents=True, exist_ok=True)
    eval_cache.write_text(json.dumps({"version": "v9.9.9", "overall_pass": True}), encoding="utf-8")
    assert client.get("/api/evals/latest?version=v9.9.9").json()["version"] == "v9.9.9"

    # Async _invoke_model_arena (L5012) + /api/evals/models writes cache (L5094-5095)
    import rollmetrics.evals.model_bench as mb_mod

    async def _async_arena(**_kw: Any) -> dict[str, Any]:
        return {"version": "v9.9.9", "models": []}

    monkeypatch.setattr(mb_mod, "run_model_arena", _async_arena)
    assert client.get("/api/evals/models?version=v9.9.9").json()["version"] == "v9.9.9"
    monkeypatch.undo()

    # WS /ws/live with LiveRefereeSession rejecting retriever kwarg (L5205-5206),
    # set_locked_athlete (L5252), and finalize fallback without finalize/engine (L5306)
    import rollmetrics.video.live_session as ls_mod

    class _CompatLiveSession:
        def __init__(self, division: Any, profile: str, settings: Any) -> None:
            self.division = division
            self.profile = profile

        def current_hud_state(self) -> dict[str, Any]:
            return {"scoreboard": {}, "events": []}

        def set_locked_athlete(self, _locked: Any) -> dict[str, Any]:
            return {"scoreboard": {}, "events": []}

    monkeypatch.setattr(ls_mod, "LiveRefereeSession", _CompatLiveSession)
    with client.websocket_connect("/ws/live") as ws:
        _ = ws.receive_json()
        ws.send_json({"type": "lock_athlete", "athlete": "A"})
        assert ws.receive_json()["type"] == "hud"
        ws.send_json({"type": "finalize"})
        assert ws.receive_json()["type"] == "hud"
        ws.send_json({"type": "overtime"})
        assert ws.receive_json()["type"] == "hud"
    monkeypatch.undo()

    # Static file hit inside dist_dir (L5348) + root HTMLResponse fallback (L5326) + SPA 404 (L5352)
    fake_repo = tmp_path / "fake_repo"
    fake_dist = fake_repo / "frontend" / "dist"
    fake_dist.mkdir(parents=True, exist_ok=True)
    (fake_dist / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")
    monkeypatch.setattr(main_mod, "REPO_ROOT", fake_repo)
    c_fe = TestClient(create_app(s))
    assert c_fe.get("/robots.txt").status_code == 200
    assert c_fe.get("/").status_code == 200
    assert c_fe.get("/missing-spa-route").status_code == 404
    monkeypatch.undo()
