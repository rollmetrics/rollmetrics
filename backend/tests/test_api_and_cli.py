"""Comprehensive API, Cloud OAuth allowlist, WebSocket HUD lifecycle, Media, Model Arena, and CLI tests."""

from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import rollmetrics.auth as auth_mod
import rollmetrics.cli as cli_mod
from fastapi.testclient import TestClient
from PIL import Image
from rollmetrics.config import Settings
from rollmetrics.main import create_app
from rollmetrics.providers.base import FrameObservation, VideoVisionResult
from rollmetrics.rules.schema import AthleteId, EventKind, RawObservedEvent
from rollmetrics.video.ingest import resolve_binary
from starlette.websockets import WebSocketDisconnect


def _make_sample_mp4(path: Path) -> Path:
    ffmpeg_bin = resolve_binary("ffmpeg")
    if not Path(ffmpeg_bin).is_file() and shutil.which(ffmpeg_bin) is None:
        pytest.skip("ffmpeg binary not installed in current test environment")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _make_jpeg_base64() -> str:
    buf = io.BytesIO()
    img = Image.new("RGB", (160, 90), (16, 185, 129))
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class _ScriptedLiveVisionProvider:
    """Deterministic vision stub driving stalling, 3.0s stabilization, and advantage downgrades."""

    async def analyze_live_frame(
        self,
        elapsed_sec: float = 0.0,
        **_kwargs: Any,
    ) -> FrameObservation:
        # Phase 1 (0s - 25s): Athlete B stalls non-combatively past 20.0s threshold
        if elapsed_sec <= 26.0:
            return FrameObservation(
                current_position="Closed Guard",
                active_combativeness_a=True,
                active_combativeness_b=False,
                in_bounds=True,
                confidence=0.93,
                occluded=False,
                corner_cue="Break posture and climb cross-lapel overhook.",
                model_used="scripted-vision",
            )
        # Phase 2 (30s - 34s): Athlete A holds Mount past 3.0s stabilization threshold (+4 pts)
        if 30.0 <= elapsed_sec <= 34.0:
            return FrameObservation(
                current_position="Mount",
                top_or_dominant_actor="A",
                stabilizing_scoring_kind=EventKind.MOUNT,
                active_combativeness_a=True,
                active_combativeness_b=True,
                in_bounds=True,
                confidence=0.96,
                occluded=False,
                corner_cue="Walk knees high into armpits and isolate elbow.",
                model_used="scripted-vision",
            )
        # Phase 3 (40s - 41.4s): Athlete B initiates Guard Pass for 1.4s (<3.0s)
        if 40.0 <= elapsed_sec <= 41.5:
            return FrameObservation(
                current_position="Side Control Scramble",
                top_or_dominant_actor="B",
                stabilizing_scoring_kind=EventKind.GUARD_PASS,
                active_combativeness_a=True,
                active_combativeness_b=True,
                in_bounds=True,
                confidence=0.91,
                occluded=False,
                corner_cue="Frame hip and neck immediately before 3-second count.",
                model_used="scripted-vision",
            )
        # Phase 4 (>41.5s): Guard recovered before 3.0s -> Athlete B pass downgrades to +1 Advantage
        return FrameObservation(
            current_position="Closed Guard",
            top_or_dominant_actor="none",
            stabilizing_scoring_kind=None,
            active_combativeness_a=True,
            active_combativeness_b=True,
            in_bounds=True,
            confidence=0.94,
            occluded=False,
            corner_cue="Closed guard recovered cleanly.",
            model_used="scripted-vision",
        )

    async def analyze_video(self, **_kwargs: Any) -> VideoVisionResult:
        return VideoVisionResult(
            events=[
                RawObservedEvent(
                    timestamp_sec=24.0,
                    actor=AthleteId.ATHLETE_A,
                    kind=EventKind.SWEEP,
                    hold_duration_sec=3.4,
                    in_bounds=True,
                    confidence=0.95,
                    position_after="Mount",
                    summary="Scissor sweep stabilized on top.",
                ),
                RawObservedEvent(
                    timestamp_sec=48.0,
                    actor=AthleteId.ATHLETE_A,
                    kind=EventKind.MOUNT,
                    hold_duration_sec=3.8,
                    in_bounds=True,
                    confidence=0.97,
                    position_after="Mount",
                    summary="Full mount stabilized 3.8s.",
                ),
            ],
            summary="Scissor sweep directly into mount (+6 pts).",
            model_used="scripted-vision",
        )


def test_local_bypass_and_disabled_bypass_modes(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        allow_local_dev_bypass=True,
        allowlist_emails="coach@example.com",
        runtime_dir=tmp_path / "runtime",
    )
    client = TestClient(create_app(settings))

    r_health = client.get("/healthz")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "ok"
    assert r_health.json()["build_sha"]

    r_cfg = client.get("/api/auth/config")
    assert r_cfg.status_code == 200
    assert r_cfg.json()["allow_local_dev_bypass"] is True
    assert r_cfg.json()["auth_required"] is False
    assert r_cfg.json()["build_sha"]

    r_me = client.get("/api/auth/me")
    assert r_me.status_code == 200
    assert r_me.json()["email"] == "coach@example.com"
    assert r_me.json()["auth_mode"] == "local_bypass"

    # Disabling allow_local_dev_bypass even in local mode enforces 401
    strict_local = Settings(
        environment="local",
        allow_local_dev_bypass=False,
        allowlist_emails="coach@example.com",
        runtime_dir=tmp_path / "runtime_strict",
    )
    strict_client = TestClient(create_app(strict_local))
    assert strict_client.get("/api/auth/config").json()["auth_required"] is True
    assert strict_client.get("/api/auth/me").status_code == 401


def test_cloud_auth_strictly_enforces_google_token_and_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="cloud",
        allow_local_dev_bypass=True,  # Must be ignored in cloud mode
        google_client_id="test-client-id.apps.googleusercontent.com",
        allowlist_emails="coach@example.com",
        runtime_dir=tmp_path / "runtime",
    )
    client = TestClient(create_app(settings))

    # Public routes remain accessible (both /api/health for Cloud Run GFE, /healthz, and public /pitch deck)
    assert client.get("/api/health").status_code == 200
    assert client.get("/healthz").status_code == 200
    r_pitch = client.get("/pitch")
    assert r_pitch.status_code == 200
    assert "text/html" in r_pitch.headers.get("content-type", "")
    r_pitch_html = client.get("/pitch-deck.html")
    assert r_pitch_html.status_code == 200
    assert "text/html" in r_pitch_html.headers.get("content-type", "")
    cfg = client.get("/api/auth/config").json()
    assert cfg["environment"] == "cloud"
    assert cfg["auth_required"] is True
    assert cfg["allow_local_dev_bypass"] is False

    # Missing token -> 401 across protected endpoints
    for route in ("/api/auth/me", "/api/system/status", "/api/analyze/presets", "/api/evals/models"):
        assert client.get(route).status_code == 401

    def fake_verify(token: str, _req: Any, audience: str | None = None) -> dict[str, Any]:
        assert audience == "test-client-id.apps.googleusercontent.com"
        if token == "valid-kory":
            return {"email": "Coach@example.com", "email_verified": True, "name": "Head Coach"}
        if token == "unverified-kory":
            return {"email": "coach@example.com", "email_verified": False}
        if token == "no-email":
            return {"sub": "12345", "email_verified": True}
        if token == "valid-outsider":
            return {"email": "intruder@example.com", "email_verified": True, "name": "Intruder"}
        msg = "Token signature invalid"
        raise ValueError(msg)

    monkeypatch.setattr(auth_mod.google_id_token, "verify_oauth2_token", fake_verify)

    # Invalid, unverified, and claim-missing tokens -> 401
    assert client.get("/api/system/status", headers={"Authorization": "Bearer broken"}).status_code == 401
    assert (
        client.get("/api/system/status", headers={"Authorization": "Bearer unverified-kory"}).status_code
        == 401
    )
    assert client.get("/api/system/status", headers={"Authorization": "Bearer no-email"}).status_code == 401

    # Valid Google account outside allowlist -> 403
    assert (
        client.get("/api/system/status", headers={"Authorization": "Bearer valid-outsider"}).status_code
        == 403
    )

    # Allowlisted account -> 200
    r_ok = client.get("/api/auth/me", headers={"Authorization": "Bearer valid-kory"})
    assert r_ok.status_code == 200
    assert r_ok.json()["email"] == "coach@example.com"
    assert r_ok.json()["auth_mode"] == "google_id_token"

    # WebSocket without token in cloud mode -> closed with policy violation 1008
    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect("/ws/live") as ws:
        ws.receive_json()
    assert exc_info.value.code == 1008

    # WebSocket with non-allowlisted token -> closed with 1008
    with (
        pytest.raises(WebSocketDisconnect) as exc_forbid,
        client.websocket_connect("/ws/live?token=valid-outsider") as ws_bad,
    ):
        ws_bad.receive_json()
    assert exc_forbid.value.code == 1008

    # WebSocket with ?token=valid-kory succeeds
    with client.websocket_connect("/ws/live?token=valid-kory") as ws_ok:
        first_msg = ws_ok.receive_json()
        assert first_msg["type"] == "hud"
        assert first_msg["user"]["email"] == "coach@example.com"


def test_presets_event_analysis_legality_knowledge_and_evals(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        allow_local_dev_bypass=True,
        runtime_dir=tmp_path / "runtime",
    )
    client = TestClient(create_app(settings))

    # System status and profile switcher
    r_status = client.get("/api/system/status")
    assert r_status.status_code == 200
    assert "providers" in r_status.json()

    r_prof = client.post("/api/system/profile", json={"profile": "local"})
    assert r_prof.status_code == 200
    assert r_prof.json()["active_profile"] == "local"

    r_cfg_update = client.post(
        "/api/system/config",
        json={"profile": "local", "ollama_vision_model": "gemma4:e2b-it-q4_K_M"},
    )
    assert r_cfg_update.status_code == 200
    assert r_cfg_update.json()["ollama_vision_model"] == "gemma4:e2b-it-q4_K_M"

    # Connection test returns structured Missing Key when gemini_api_key is unset
    no_key_client = TestClient(
        create_app(
            Settings(
                environment="test",
                allow_local_dev_bypass=True,
                gemini_api_key="",
                runtime_dir=tmp_path / "runtime_nokey",
            )
        )
    )
    r_conn_cloud = no_key_client.post("/api/system/test-connection", json={"target": "cloud"})
    assert r_conn_cloud.status_code == 200
    assert r_conn_cloud.json()["ok"] is False
    assert r_conn_cloud.json()["status"] == "Missing Key"

    # Presets endpoint includes Mastermind Cup 9 matches
    r_presets = client.get("/api/analyze/presets")
    assert r_presets.status_code == 200
    presets = r_presets.json()["presets"]
    assert len(presets) >= 2

    # Replay Semifinal and Final presets deterministically
    for preset in presets[:2]:
        r_replay = client.post("/api/analyze/events", json={"preset_id": preset["id"]})
        assert r_replay.status_code == 200
        snap = r_replay.json()["snapshot"]
        assert "athlete_a" in snap
        assert len(snap["timeline"]) >= 1

    # Legality matrix checks across divisions
    r_nogi = client.post(
        "/api/knowledge/legality-check",
        json={
            "technique_slug": "heel_hook",
            "belt": "brown",
            "uniform": "nogi",
            "age_division": "adult",
            "include_matrix": True,
        },
    )
    assert r_nogi.status_code == 200
    assert r_nogi.json()["verdict"]["severity"] == "legal"
    assert len(r_nogi.json()["catalog"]) >= 15
    assert "across_belts" in r_nogi.json()["catalog"][0]

    r_white_jump = client.post(
        "/api/knowledge/legality-check",
        json={
            "technique_slug": "jumping_closed_guard",
            "belt": "white",
            "uniform": "gi",
            "age_division": "master",
        },
    )
    assert r_white_jump.status_code == 200
    assert r_white_jump.json()["verdict"]["severity"] == "serious_foul_dq"

    # Root serves compiled SPA
    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "RollMetrics" in r_root.text

    # Knowledge manifest, hybrid search, benchmark runner, and model arena endpoints
    r_man = client.get("/api/knowledge/manifest")
    assert r_man.status_code == 200
    assert r_man.json()["version"] == "v0.1.0"

    r_search = client.get("/api/knowledge/search?q=closed+guard+overhook&corpus=all&top_k=5")
    assert r_search.status_code == 200
    assert len(r_search.json()["hits"]) >= 1

    r_evals = client.post("/api/evals/run", json={"version": "v0.1.0"})
    assert r_evals.status_code == 200
    assert r_evals.json()["overall_pass"] is True

    r_models = client.get("/api/evals/models")
    assert r_models.status_code == 200
    assert "models" in r_models.json() or "results" in r_models.json()

    r_models_run = client.post(
        "/api/evals/models/run",
        json={"models": ["gemini-4-hydrogen", "gemini-4-neon"], "live_api": False},
    )
    assert r_models_run.status_code == 200

    # Showcase match events include IBJJF rule_info + CTA cta_info with embedded video metadata (#146, #215)
    r_matches = client.get("/api/matches")
    assert r_matches.status_code == 200
    sf_events = r_matches.json()["matches"][0]["events"]
    assert sf_events[0]["rule_info"]["rulebook_url"].startswith("https://")
    assert len(sf_events[0]["rule_info"]["excerpt"]) > 10
    assert "cta_info" in sf_events[0]
    cta_info = sf_events[0]["cta_info"]
    assert cta_info["title"]
    assert cta_info["video_url"].startswith("https://")
    assert isinstance(cta_info["start_sec"], int)
    assert cta_info["source_kind"] in ("youtube", "cta", "external")


def test_open_signup_persists_isolated_user_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rollmetrics.auth as auth_mod

    monkeypatch.setattr(
        auth_mod.google_id_token,
        "verify_oauth2_token",
        lambda token, _req, audience=None: (
            {
                "email": "coach@example.com",
                "name": "Kory Mathewson",
                "picture": "",
                "email_verified": True,
            }
            if token == "valid-admin-token"
            else {
                "email": "newgrappler@example.com",
                "name": "New Grappler",
                "picture": "https://example.com/pic.png",
                "email_verified": True,
            }
        ),
    )
    settings = Settings(
        environment="cloud",
        google_client_id="test-client-id",
        allowlist_emails="coach@example.com",
        allow_open_signup=True,
        allow_local_dev_bypass=False,
        runtime_dir=tmp_path / "runtime",
    )
    client = TestClient(create_app(settings))
    headers = {"Authorization": "Bearer valid-open-signup-token"}
    admin_headers = {"Authorization": "Bearer valid-admin-token"}

    r_me = client.get("/api/auth/me", headers=headers)
    assert r_me.status_code == 200
    assert r_me.json()["email"] == "newgrappler@example.com"
    assert r_me.json()["is_admin"] is False
    assert (tmp_path / "runtime" / "users.json").is_file()

    # Isolated personal matches (0 my_matches for new user) + /admin gated to allowlist_emails (#170)
    r_gp = client.get("/api/me/coaching-summary", headers=headers)
    assert r_gp.status_code == 200
    assert r_gp.json()["match_count"] == 0

    r_admin_denied = client.get("/api/admin/overview", headers=headers)
    assert r_admin_denied.status_code == 403

    r_admin = client.get("/api/admin/overview", headers=admin_headers)
    assert r_admin.status_code == 200
    admin_body = r_admin.json()
    admin_emails = {u["email"] for u in admin_body["users_and_usage"]}
    assert "newgrappler@example.com" in admin_emails
    assert len(admin_body["teams"]) >= 2
    assert len(admin_body["curricula"]) >= 2
    assert len(admin_body["rulesets"]) >= 2

    # ML Admin endpoint (/api/ml-admin/overview) + Sandboxed Sebastian Demo Login
    r_ml = client.get("/api/ml-admin/overview", headers=admin_headers)
    assert r_ml.status_code == 200
    assert len(r_ml.json()["engine_versions"]) >= 3
    assert len(r_ml.json()["experiments"]) >= 3

    r_demo = client.post("/api/auth/demo-login")
    assert r_demo.status_code == 200
    demo_tok = r_demo.json()["token"]
    assert r_demo.json()["user"]["role"] == "demo"
    assert r_demo.json()["user"]["is_admin"] is False
    demo_headers = {"Authorization": f"Bearer {demo_tok}"}
    assert client.get("/api/admin/overview", headers=demo_headers).status_code == 403
    r_demo_matches = client.get("/api/matches", headers=demo_headers)
    assert r_demo_matches.status_code == 200
    demo_my = [m for m in r_demo_matches.json()["matches"] if m["bucket"] == "my_matches"]
    assert len(demo_my) == 5


def test_direct_uploads_google_drive_and_public_share_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    import rollmetrics.video.analyzer as analyzer_mod

    monkeypatch.setattr(analyzer_mod, "get_vision_provider", lambda **_kw: _ScriptedLiveVisionProvider())

    settings = Settings(
        environment="test",
        allow_local_dev_bypass=True,
        runtime_dir=tmp_path / "runtime",
    )
    client = TestClient(create_app(settings))
    sample_mp4 = _make_sample_mp4(tmp_path / "iphone_match.mp4")

    # 1. Direct upload init -> complete (Workstream 2.2)
    r_init = client.post(
        "/api/uploads/init",
        json={
            "filename": "iphone_match.mov",
            "content_type": "video/quicktime",
            "size_bytes": sample_mp4.stat().st_size,
            "bucket": "my_matches",
            "uniform": "nogi",
            "belt": "blue",
            "athlete_a_name": "Kory Mathewson",
            "athlete_b_name": "Sparring Partner",
            "athlete_a_gi": "white",
            "athlete_b_gi": "blue",
            "title": "Open Mat No-Gi Round",
        },
    )
    assert r_init.status_code == 200
    init_data = r_init.json()
    assert init_data["upload_strategy"] == "multipart"
    upload_id = init_data["upload_id"]

    # Place uploaded file in runtime/uploads as direct upload would
    dest = settings.runtime_dir / "uploads" / f"{upload_id}.mov"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(sample_mp4.read_bytes())

    r_complete = client.post("/api/uploads/complete", json={"upload_id": upload_id})
    assert r_complete.status_code == 200
    created_match = r_complete.json()["match"]
    assert created_match["title"] == "Open Mat No-Gi Round"
    assert created_match["bucket"] == "my_matches"
    created_id = created_match["id"]

    # 2. Google Drive import (mocked httpx download)
    sample_bytes = sample_mp4.read_bytes()

    class _FakeResp:
        content = sample_bytes

        def raise_for_status(self) -> None:
            return None

    async def _fake_get(_self: Any, _url: str, **_kw: Any) -> _FakeResp:
        return _FakeResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    r_drive = client.post(
        "/api/uploads/google-drive",
        json={
            "drive_url": "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWx/view?usp=sharing",
            "title": "Drive Finals Footage",
            "bucket": "my_matches",
            "uniform": "gi",
            "belt": "white",
            "athlete_a_name": "Kory Mathewson",
            "athlete_b_name": "Drive Opponent",
        },
    )
    assert r_drive.status_code == 200
    assert r_drive.json()["match"]["title"] == "Drive Finals Footage"

    # 3. Shareable coach review link (/share/{token}) works publicly with ZERO auth (Workstream 2.3)
    r_share = client.post(f"/api/matches/{created_id}/share")
    assert r_share.status_code == 200
    token = r_share.json()["token"]
    assert r_share.json()["share_path"] == f"/share/{token}"

    # Verify public read-only access under strict cloud mode (no Authorization header)
    cloud_settings = Settings(
        environment="cloud",
        google_client_id="test-client-id",
        allow_local_dev_bypass=False,
        runtime_dir=tmp_path / "runtime",
    )
    public_client = TestClient(create_app(cloud_settings))

    r_pub = public_client.get(f"/api/share/{token}")
    assert r_pub.status_code == 200
    assert r_pub.json()["match"]["title"] == "Open Mat No-Gi Round"
    assert r_pub.json()["match"]["video_url"] == f"/api/share/{token}/video"

    r_vid = public_client.get(f"/api/share/{token}/video", headers={"Range": "bytes=0-1023"})
    assert r_vid.status_code == 206

    r_hl = public_client.post(
        f"/api/share/{token}/highlight",
        json={"start_sec": 0.0, "duration_sec": 2.0, "speed": 0.4},
    )
    assert r_hl.status_code == 200
    hl_url = r_hl.json()["url"]
    assert hl_url.startswith(f"/api/share/{token}/media/")
    assert public_client.get(hl_url).status_code == 200

    assert public_client.get("/api/share/nonexistent-token-123").status_code == 404


def test_local_desktop_hardware_scan_analyze_and_import_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rollmetrics.video.analyzer as analyzer_mod

    monkeypatch.setattr(analyzer_mod, "get_vision_provider", lambda **_kw: _ScriptedLiveVisionProvider())

    settings = Settings(
        environment="test",
        allow_local_dev_bypass=True,
        runtime_dir=tmp_path / "runtime",
    )
    client = TestClient(create_app(settings))

    # 1. Hardware status
    r_hw = client.get("/api/local/hardware")
    assert r_hw.status_code == 200
    assert "recommended_model" in r_hw.json()

    # 2. Scan local video folder + offline analyze
    vid_dir = tmp_path / "bjj_videos"
    vid_dir.mkdir()
    sample_mp4 = _make_sample_mp4(vid_dir / "open_mat_roll.mp4")

    r_scan = client.post("/api/local/folder/scan", json={"folder_path": str(vid_dir)})
    assert r_scan.status_code == 200
    assert r_scan.json()["count"] == 1
    assert r_scan.json()["videos"][0]["filename"] == "open_mat_roll.mp4"

    r_analyze = client.post(
        "/api/local/folder/analyze",
        json={"video_path": str(sample_mp4), "title": "Offline Open Mat"},
    )
    assert r_analyze.status_code == 200
    assert r_analyze.json()["match"]["title"] == "Offline Open Mat"

    # 3. Import local academy notes folder
    notes_dir = tmp_path / "academy_notes"
    notes_dir.mkdir()
    (notes_dir / "armbar_details.md").write_text("Pin the wrist to the chest and pinch knees.", encoding="utf-8")

    r_imp = client.post(
        "/api/local/academy/import-folder",
        json={"folder_path": str(notes_dir), "academy_id": "local-gym", "academy_name": "Local Gym"},
    )
    assert r_imp.status_code == 200
    assert r_imp.json()["imported_count"] >= 1


def test_multipart_upload_url_and_media_studio_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rollmetrics.video.analyzer as analyzer_mod

    monkeypatch.setattr(analyzer_mod, "get_vision_provider", lambda **_kw: _ScriptedLiveVisionProvider())

    settings = Settings(
        environment="test",
        allow_local_dev_bypass=True,
        runtime_dir=tmp_path / "runtime",
    )
    client = TestClient(create_app(settings))
    sample_mp4 = _make_sample_mp4(tmp_path / "sample_match.mp4")

    # Reject unsupported extension & empty file
    r_ext = client.post(
        "/api/analyze/upload",
        files={"file": ("notes.txt", b"not video", "text/plain")},
    )
    assert r_ext.status_code == 400

    r_empty = client.post(
        "/api/analyze/upload",
        files={"file": ("empty.mp4", b"", "video/mp4")},
    )
    assert r_empty.status_code == 400

    # Valid multipart MP4 upload with custom division_json (+2 sweep +4 mount = 6 pts)
    custom_div = {
        "uniform": "gi",
        "belt": "blue",
        "age_division": "adult",
        "athlete_a_name": "Kory Mathewson",
        "athlete_b_name": "Opponent",
        "locked_athlete": "A",
    }
    with sample_mp4.open("rb") as fh:
        r_up = client.post(
            "/api/analyze/upload",
            files={"file": ("sample_match.mp4", fh, "video/mp4")},
            data={
                "division_json": json.dumps(custom_div),
                "profile": "local",
                "sample_fps": "0.5",
            },
        )
    assert r_up.status_code == 200
    up_data = r_up.json()
    assert up_data["snapshot"]["athlete_a"]["points"] == 6
    assert up_data["snapshot"]["division"]["belt"] == "blue"
    assert len(up_data["snapshot"]["coaching_summary"]) >= 1
    assert client.get(up_data["video_url"]).status_code == 200

    # URL analysis
    r_url = client.post(
        "/api/analyze/url",
        json={"url": str(sample_mp4), "profile": "local", "sample_fps": 0.5},
    )
    assert r_url.status_code == 200
    assert r_url.json()["snapshot"]["athlete_a"]["points"] == 6

    # Media Studio 3-panel board (verify 1860x614 PNG dimensions) & 0.4x slow-mo
    r_board = client.post(
        "/api/media/board",
        json={
            "video_path": str(sample_mp4),
            "center_timestamp_sec": 2.0,
            "title": "Scissor Sweep to Mount Board",
            "cues": ["Grip sleeve", "Load shin", "Stabilize mount"],
        },
    )
    assert r_board.status_code == 200
    r_board_file = client.get(r_board.json()["url"])
    assert r_board_file.status_code == 200
    with Image.open(io.BytesIO(r_board_file.content)) as board_im:
        assert board_im.size == (1860, 614)

    r_slow = client.post(
        "/api/media/slowmo",
        json={
            "video_path": str(sample_mp4),
            "start_sec": 0.5,
            "duration_sec": 1.5,
            "speed": 0.4,
        },
    )
    assert r_slow.status_code == 200
    r_slow_file = client.get(r_slow.json()["url"])
    assert r_slow_file.status_code == 200
    assert len(r_slow_file.content) > 1000

    # Path traversal and missing file checks
    assert client.get("/api/media/files/..bad").status_code == 400
    assert client.get("/api/media/files/nonexistent_file.mp4").status_code == 404

    # Showcase matches catalog + My Matches / Film Study buckets + Personal Coaching Summary (#119)
    r_matches = client.get("/api/matches")
    assert r_matches.status_code == 200
    assert r_matches.json()["default_match_id"] == "mc9_semifinal"
    matches_by_id = {m["id"]: m for m in r_matches.json()["matches"]}
    for expected_id in (
        "mc9_semifinal",
        "mc9_final",
        "seb_gi_sf",
        "seb_nogi_r16",
        "seb_nogi_qf",
        "seb_nogi_sf",
        "seb_nogi_final",
        "caio_vitor_2014",
    ):
        assert expected_id in matches_by_id
    assert matches_by_id["mc9_semifinal"]["bucket"] == "my_matches"
    assert matches_by_id["mc9_final"]["bucket"] == "my_matches"
    assert matches_by_id["seb_gi_sf"]["bucket"] == "film_study"
    assert matches_by_id["seb_nogi_r16"]["bucket"] == "film_study"
    assert matches_by_id["caio_vitor_2014"]["bucket"] == "film_study"
    assert matches_by_id["seb_nogi_qf"]["bucket"] == "film_study"

    r_gameplan = client.get("/api/me/coaching-summary")
    assert r_gameplan.status_code == 200
    gp = r_gameplan.json()
    assert gp["match_count"] == 2
    assert gp["points_allowed"] == 0
    assert gp["record"]["wins"] == 2
    assert len(gp["top_cta_priorities"]) == 3

    r_bucket = client.post("/api/matches/caio_vitor_2014/bucket", json={"bucket": "my_matches"})
    assert r_bucket.status_code == 200
    assert client.get("/api/me/coaching-summary").json()["match_count"] == 3
    client.post("/api/matches/caio_vitor_2014/bucket", json={"bucket": "film_study"})

    # Film Study athlete scouting summary + Collections summary + Admin ML Overview (#144)
    r_fs = client.get("/api/film-study/summary?athlete=Sebastian+Bussieres")
    assert r_fs.status_code == 200
    fs_data = r_fs.json()
    assert fs_data["match_count"] == 5
    assert fs_data["combined_points_for"] == 38
    assert fs_data["combined_points_against"] == 0
    assert "Double Gold" in fs_data["record_label"]

    r_coll = client.post(
        "/api/collections/summary",
        json={"match_ids": ["caio_vitor_2014"], "focus_athlete": "Caio Terra"},
    )
    assert r_coll.status_code == 200
    assert r_coll.json()["match_count"] == 1

    r_admin = client.get("/api/admin/overview")
    assert r_admin.status_code == 200
    adm = r_admin.json()
    assert adm["scoreboard_eval"]["total_matches"] == 122
    assert len(adm["scoreboard_eval"]["splits"]["heldout"]) >= 1
    assert adm["knowledge_eval"]["total_docs"] >= 200
    assert len(adm["users_and_usage"]) >= 1

    # Cache sample MP4s as mc9_semifinal and seb_nogi_final inside isolated test runtime_dir/matches
    cached_match = tmp_path / "runtime" / "matches" / "mc9_semifinal.mp4"
    cached_match.parent.mkdir(parents=True, exist_ok=True)
    cached_match.write_bytes(sample_mp4.read_bytes())
    (tmp_path / "runtime" / "matches" / "seb_nogi_final.mp4").write_bytes(sample_mp4.read_bytes())

    import rollmetrics.main as main_mod

    monkeypatch.setattr(main_mod, "LOCAL_DRIVE_BJJ_DIR", tmp_path / "no_local_drive")
    # HEAD probe + iOS WebKit 2-byte Range probe + bounded <=4 MiB open-ended Range (#108, #131)
    r_head = client.head("/api/matches/mc9_semifinal/video")
    assert r_head.status_code == 200
    assert r_head.headers["accept-ranges"] == "bytes"
    assert int(r_head.headers["content-length"]) == cached_match.stat().st_size

    r_ios_probe = client.get("/api/matches/mc9_semifinal/video", headers={"Range": "bytes=0-1"})
    assert r_ios_probe.status_code == 206
    assert len(r_ios_probe.content) == 2
    assert r_ios_probe.headers["accept-ranges"] == "bytes"
    assert r_ios_probe.headers["content-range"].startswith("bytes 0-1/")

    # Verify 6 MiB file with open-ended Range: bytes=0- is capped at exactly 4 MiB (Cloud Run <32 MiB)
    large_file = tmp_path / "runtime" / "matches" / "mc9_final.mp4"
    large_file.write_bytes(b"\x00" * (6 * 1024 * 1024))
    r_open = client.get("/api/matches/mc9_final/video", headers={"Range": "bytes=0-"})
    assert r_open.status_code == 206
    assert len(r_open.content) == 4 * 1024 * 1024
    assert r_open.headers["content-range"] == f"bytes 0-{4 * 1024 * 1024 - 1}/{6 * 1024 * 1024}"

    r_range = client.get("/api/matches/mc9_semifinal/video", headers={"Range": "bytes=0-1023"})
    assert r_range.status_code == 206
    assert len(r_range.content) == 1024
    assert r_range.headers["accept-ranges"] == "bytes"

    r_poster = client.get("/api/matches/mc9_semifinal/poster")
    assert r_poster.status_code == 200
    assert r_poster.headers["content-type"].startswith("image/jpeg")
    assert len(r_poster.content) > 1000

    # Real match_id board slicing works even when /opt/homebrew/bin is absent from PATH (including seb_nogi_*)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    r_real_board = client.post(
        "/api/media/board",
        json={"match_id": "mc9_semifinal", "center_timestamp_sec": 2.0, "title": "Real Match Strip"},
    )
    assert r_real_board.status_code == 200
    r_seb_board = client.post(
        "/api/media/board",
        json={"match_id": "seb_nogi_final", "center_timestamp_sec": 2.0, "title": "Bussieres Gold Strip"},
    )
    assert r_seb_board.status_code == 200


def test_websocket_live_full_stalling_stabilization_review_and_cta_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rollmetrics.video.live_session as live_mod

    monkeypatch.setattr(
        live_mod,
        "get_vision_provider",
        lambda **_kw: _ScriptedLiveVisionProvider(),
    )

    settings = Settings(
        environment="test",
        allow_local_dev_bypass=True,
        runtime_dir=tmp_path / "runtime",
    )
    client = TestClient(create_app(settings))
    frame_b64 = _make_jpeg_base64()

    with client.websocket_connect("/ws/live") as ws:
        initial = ws.receive_json()
        assert initial["type"] == "hud"

        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"

        ws.send_json(
            {
                "type": "init",
                "profile": "local",
                "division": {
                    "uniform": "gi",
                    "belt": "white",
                    "age_division": "master",
                    "athlete_a_name": "Kory Mathewson",
                    "athlete_b_name": "Opponent",
                    "locked_athlete": "A",
                },
            }
        )
        assert ws.receive_json()["scoreboard"]["athlete_a"]["points"] == 0

        # 1. Stalling lifecycle: frames at t=2s, t=14s (12s watch), t=24s (>=20s -> Penalty #1 on B!)
        ws.send_json({"type": "frame", "image_base64": frame_b64, "elapsed_sec": 2.0})
        ws.receive_json()
        ws.send_json({"type": "frame", "image_base64": frame_b64, "elapsed_sec": 14.0})
        mid_stall = ws.receive_json()
        assert mid_stall["stalling_watch_sec"]["B"] >= 10.0

        ws.send_json({"type": "frame", "image_base64": frame_b64, "elapsed_sec": 24.0})
        after_stall_pen = ws.receive_json()
        assert after_stall_pen["scoreboard"]["athlete_b"]["penalties"] == 1

        # 2. 3.0s Stabilization lifecycle: start Mount at t=30.0s, hold at t=31.6s (<3s), confirm at t=33.3s (+4 pts)
        ws.send_json({"type": "frame", "image_base64": frame_b64, "elapsed_sec": 30.0})
        stab_start = ws.receive_json()
        assert stab_start["active_stabilization"]["kind"] == "mount"
        assert stab_start["scoreboard"]["athlete_a"]["points"] == 0

        ws.send_json({"type": "frame", "image_base64": frame_b64, "elapsed_sec": 31.6})
        stab_mid = ws.receive_json()
        assert 1.0 <= stab_mid["active_stabilization"]["held_sec"] < 3.0
        assert stab_mid["scoreboard"]["athlete_a"]["points"] == 0

        ws.send_json({"type": "frame", "image_base64": frame_b64, "elapsed_sec": 33.3})
        stab_awarded = ws.receive_json()
        assert stab_awarded["scoreboard"]["athlete_a"]["points"] == 4
        assert stab_awarded["active_stabilization"] is None

        # 3. Incomplete <3.0s hold (40.0s -> 41.4s -> guard recovered at 42.6s) downgrades to +1 Advantage for B
        ws.send_json({"type": "frame", "image_base64": frame_b64, "elapsed_sec": 40.0})
        ws.receive_json()
        ws.send_json({"type": "frame", "image_base64": frame_b64, "elapsed_sec": 41.4})
        ws.receive_json()
        ws.send_json({"type": "frame", "image_base64": frame_b64, "elapsed_sec": 42.6})
        after_downgrade = ws.receive_json()
        assert after_downgrade["scoreboard"]["athlete_b"]["points"] == 0
        assert after_downgrade["scoreboard"]["athlete_b"]["advantages"] == 1

        # 4. Occluded / low-confidence manual trigger sets needs_review flag
        ws.send_json(
            {
                "type": "event",
                "event": {
                    "timestamp_sec": 50.0,
                    "actor": "A",
                    "kind": "knee_on_belly",
                    "hold_duration_sec": 3.2,
                    "confidence": 0.52,
                    "occluded": True,
                    "position_after": "Knee on Belly",
                },
            }
        )
        after_review = ws.receive_json()
        assert after_review["scoreboard"]["athlete_a"]["points"] == 6
        assert after_review["needs_review_count"] >= 1
        assert len(after_review["cta_cues"]) >= 1

        # 5. Lock athlete toggle updates CTA corner target
        ws.send_json({"type": "lock_athlete", "athlete": "B"})
        after_lock = ws.receive_json()
        assert after_lock["division"]["locked_athlete"] == "B"
        assert after_lock["cta_cues"][0]["athlete"] == "Opponent"

        # 6. Finalize regulation
        ws.send_json({"type": "finalize"})
        final_state = ws.receive_json()
        assert final_state["scoreboard"]["winner"] == "A"


def test_cli_subcommands_including_eval_and_model_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_mp4 = _make_sample_mp4(tmp_path / "cli_sample.mp4")

    # 1. analyze JSON event file
    events_json = tmp_path / "events.json"
    events_json.write_text(
        json.dumps(
            [
                {
                    "timestamp_sec": 30.0,
                    "actor": "A",
                    "kind": "mount",
                    "hold_duration_sec": 4.0,
                    "position_after": "Mount",
                }
            ]
        ),
        encoding="utf-8",
    )
    out_analysis = tmp_path / "analysis_out.json"
    assert (
        cli_mod.main(["analyze", str(events_json), "--belt", "white", "--output", str(out_analysis)])
        == 0
    )
    assert json.loads(out_analysis.read_text(encoding="utf-8"))["athlete_a"]["points"] == 4

    # 2. eval v0.1.0 & eval --compare-models
    eval_out = tmp_path / "eval_v010.json"
    assert cli_mod.main(["eval", "--version", "v0.1.0", "--output", str(eval_out)]) == 0
    assert json.loads(eval_out.read_text(encoding="utf-8"))["overall_pass"] is True

    arena_out = tmp_path / "arena_out.json"
    assert (
        cli_mod.main(
            [
                "eval",
                "--compare-models",
                "--models",
                "gemini-4-hydrogen,gemini-4-neon",
                "--output",
                str(arena_out),
            ]
        )
        == 0
    )
    assert arena_out.is_file()

    # 3. index import-private into isolated temp knowledge_dir
    notes_dir = tmp_path / "private_notes"
    notes_dir.mkdir()
    (notes_dir / "camp_note.md").write_text(
        "# Closed Guard Overhook Clamp\nPull elbow across centerline before hip angle.\n",
        encoding="utf-8",
    )
    import_out = tmp_path / "import_manifest.json"
    temp_kb = tmp_path / "kb_copy"
    assert (
        cli_mod.main(
            [
                "index",
                "import-private",
                str(notes_dir),
                "--target-version",
                "v0.2.0",
                "--knowledge-dir",
                str(temp_kb),
                "--output",
                str(import_out),
            ]
        )
        == 0
    )
    assert import_out.is_file()

    # 4. media board & media slowmo
    board_out = tmp_path / "cli_board.png"
    assert (
        cli_mod.main(
            [
                "media",
                "board",
                "--video",
                str(sample_mp4),
                "--timestamp",
                "2.0",
                "--output",
                str(board_out),
                "--title",
                "CLI Board",
            ]
        )
        == 0
    )
    assert board_out.is_file()

    slow_out = tmp_path / "cli_slowmo.mp4"
    assert (
        cli_mod.main(
            [
                "media",
                "slowmo",
                "--video",
                str(sample_mp4),
                "--start",
                "0.5",
                "--duration",
                "1.5",
                "--speed",
                "0.4",
                "--output",
                str(slow_out),
            ]
        )
        == 0
    )
    assert slow_out.is_file()

    # 5. serve subcommand wiring (mocking uvicorn.run)
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        cli_mod.uvicorn,
        "run",
        lambda app_str, **kw: called.update({"app": app_str, **kw}),
    )
    assert cli_mod.main(["serve", "--host", "127.0.0.1", "--port", "8199"]) == 0
    assert called["app"] == "rollmetrics.main:app"
    assert called["port"] == 8199


def test_v03_pilot_features_quota_moments_roster_export_and_autotag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zipfile
    from io import BytesIO

    from fastapi import HTTPException
    from rollmetrics.auth import check_and_record_daily_quota, has_unlimited_quota

    settings = Settings(
        environment="test",
        allow_local_dev_bypass=True,
        runtime_dir=tmp_path / "rt_v03",
    )
    client = TestClient(create_app(settings))

    # 1. Quota & Cloud Spend Circuit Breaker
    open_member = {"email": "newbie@example.com", "role": "member", "teams": [], "auth_mode": "google_id_token"}
    coach_user = {"email": "coach@example.com", "role": "coach", "teams": [], "auth_mode": "google_id_token"}
    assert has_unlimited_quota(open_member, settings) is False
    assert has_unlimited_quota(coach_user, settings) is True

    for i in range(5):
        q = check_and_record_daily_quota(settings, open_member, estimated_cost_usd=0.01, max_daily_uploads=5)
        assert q["user_count"] == i + 1
    with pytest.raises(HTTPException) as exc_quota:
        check_and_record_daily_quota(settings, open_member, estimated_cost_usd=0.01, max_daily_uploads=5)
    assert exc_quota.value.status_code == 429

    monkeypatch.setenv("ROLLMETRICS_MAX_DAILY_CLOUD_USD", "0.06")
    with pytest.raises(HTTPException) as exc_budget:
        check_and_record_daily_quota(settings, coach_user, estimated_cost_usd=0.05)
    assert exc_budget.value.status_code == 429
    monkeypatch.setenv("ROLLMETRICS_MAX_DAILY_CLOUD_USD", "25.0")

    # 2. Upload init with ruleset, trim, tap-to-identify anchors -> reflected in /api/admin/overview upload_jobs
    r_init = client.post(
        "/api/uploads/init",
        json={
            "filename": "adcc_trial.mov",
            "content_type": "video/quicktime",
            "size_bytes": 1024,
            "bucket": "my_matches",
            "uniform": "nogi",
            "belt": "brown",
            "ruleset": "adcc",
            "trim_start_sec": 5.0,
            "trim_end_sec": 180.0,
            "athlete_a_anchor": {"x": 0.3, "y": 0.5},
            "athlete_b_anchor": {"x": 0.7, "y": 0.5},
        },
    )
    assert r_init.status_code == 200
    r_adm = client.get("/api/admin/overview")
    assert r_adm.status_code == 200
    adm_json = r_adm.json()
    assert any(j["filename"] == "adcc_trial.mov" and j["ruleset"] == "adcc" for j in adm_json["upload_jobs"])
    assert adm_json["id_lock_eval"]["id_lock_pct"] == 1.0

    # 3. Share create + revoke
    r_share = client.post("/api/matches/mc9_semifinal/share")
    assert r_share.status_code == 200
    tok = r_share.json()["token"]
    r_rev = client.delete(f"/api/share/{tok}")
    assert r_rev.status_code == 200 and r_rev.json()["ok"] is True
    assert client.get(f"/api/share/{tok}").status_code == 404

    # 4. Inline Moment Editing (IBJJF + ADCC + Sparring deterministic recalculation + feedback log)
    r_mom = client.put(
        "/api/matches/mc9_semifinal/moments",
        json={
            "ruleset": "adcc",
            "opt_in_feedback": True,
            "events": [
                {"id": "e1", "video_sec": 10, "actor": "A", "call": "+2 PTS", "detail": "Takedown", "coach_note": "Clean level change"},
                {"id": "e2", "video_sec": 25, "actor": "B", "call": "+1 PEN", "detail": "Guard pull"},
            ],
        },
    )
    assert r_mom.status_code == 200
    mom_json = r_mom.json()
    assert mom_json["feedback_logged"] is True
    assert mom_json["match"]["events"][0]["pts_a"] == 2
    assert mom_json["match"]["events"][1]["pts_b"] == -1
    assert (settings.runtime_dir / "moment_feedback.jsonl").is_file()

    # 5. Delete custom match
    custom_path = settings.runtime_dir / "custom_matches.json"
    custom_path.write_text(
        json.dumps([{"id": "custom_del_1", "title": "Temp", "owner_email": "coach@example.com"}]),
        encoding="utf-8",
    )
    r_del = client.delete("/api/matches/custom_del_1")
    assert r_del.status_code == 200 and r_del.json()["ok"] is True
    assert client.delete("/api/matches/custom_del_1").status_code == 404

    # 6. Team Coach Roster + Review status + Pin Drill
    r_roster = client.get("/api/team/roster")
    assert r_roster.status_code == 200 and isinstance(r_roster.json()["roster"], list)
    r_review = client.post(
        "/api/matches/mc9_semifinal/review",
        json={
            "review_status": "reviewed",
            "pin_drill": {"title": "Knee Cut Drill", "url": "https://youtu.be/dQw4w9WgXcQ", "note": "Keep underhook"},
        },
    )
    assert r_review.status_code == 200
    assert r_review.json()["review_status"] == "reviewed"
    assert len(r_review.json()["pinned_drills"]) == 1

    # 7. Auto-Tag Academy Reference
    r_tag = client.post(
        "/api/academies/me/references/auto-tag",
        json={"url": "https://youtu.be/dQw4w9WgXcQ", "title": "Knee Cut Pass Details", "notes": "Underhook pressure"},
    )
    assert r_tag.status_code == 200
    assert r_tag.json()["position"] == "guard_passing"
    assert r_tag.json()["belt"] == "blue"

    # 8. 1-Click Export Data .zip
    r_zip = client.get("/api/me/export.zip")
    assert r_zip.status_code == 200
    assert r_zip.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(r_zip.content), "r") as zf:
        assert "manifest.json" in zf.namelist()

    # 9. Editable Athlete Profile (PUT /api/me/profile)
    r_prof = client.put(
        "/api/me/profile",
        json={
            "name": "Alex Rivera",
            "belt": "blue",
            "academy": "Plateau BJJ",
            "coach": "Chris B",
            "academy_id": "osjj-roadmap",
            "academy_name": "Open Source Curriculum",
        },
    )
    assert r_prof.status_code == 200
    assert r_prof.json()["belt"] == "blue"
    assert r_prof.json()["academy"] == "Plateau BJJ"
    assert r_prof.json()["coach"] == "Chris B"

    # 10. Public On-Site Plaintext IBJJF Rulebook v6.1 (GET /api/rules/ibjjf-v6-1)
    r_rb = client.get("/api/rules/ibjjf-v6-1")
    assert r_rb.status_code == 200
    assert r_rb.json()["version"] == "v6.1"
    assert r_rb.json()["count"] > 10
