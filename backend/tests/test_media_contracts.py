"""Unit tests for scripts/audit_media_contracts.py (Layer 2 Media & Streaming Contracts)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_media_contracts.py"


def _load_audit_module() -> Any:
    spec = importlib.util.spec_from_file_location("audit_media_contracts", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_media_contracts"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_mp4(dest: Path, *, faststart: bool = True, pix_fmt: str = "yuv420p") -> Path:
    env_path = f"/opt/homebrew/bin:/usr/local/bin:{os.environ.get('PATH', '')}"
    ffmpeg = shutil.which("ffmpeg", path=env_path)
    assert ffmpeg is not None, "ffmpeg must be installed for media contract tests"
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1.0:size=320x180:rate=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        pix_fmt,
    ]
    if faststart:
        cmd.extend(["-movflags", "+faststart"])
    cmd.extend([str(dest), "-loglevel", "error"])
    subprocess.run(cmd, check=True)
    return dest


def test_audit_local_mp4_passes_for_clean_cfr_faststart_and_landing_preview(tmp_path: Path) -> None:
    mod = _load_audit_module()
    clean_mp4 = _make_mp4(tmp_path / "clean.mp4", faststart=True)
    res = mod.audit_local_mp4(clean_mp4)
    assert res["ok"] is True
    assert res["codec_name"] == "h264"
    assert res["pix_fmt"] == "yuv420p"
    assert res["faststart"] is True
    assert res["cfr_drift"] < 0.05

    landing = REPO_ROOT / "frontend" / "public" / "landing-preview.mp4"
    if landing.is_file():
        landing_res = mod.audit_local_mp4(landing)
        assert landing_res["ok"] is True, landing_res


def test_audit_local_mp4_rejects_missing_non_faststart_vfr_and_bad_codec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_audit_module()
    missing = mod.audit_local_mp4(tmp_path / "does_not_exist.mp4")
    assert missing["ok"] is False
    assert "File not found" in missing["errors"][0]

    non_faststart = _make_mp4(tmp_path / "no_faststart.mp4", faststart=False)
    nf_res = mod.audit_local_mp4(non_faststart)
    assert nf_res["ok"] is False
    assert any("not faststart" in e for e in nf_res["errors"])

    clean = _make_mp4(tmp_path / "vfr_check.mp4", faststart=True)
    monkeypatch.setattr(
        mod,
        "probe_video_stream",
        lambda _p: {
            "stream": {
                "codec_name": "vp9",
                "pix_fmt": "yuv444p",
                "width": 320,
                "height": 180,
                "r_frame_rate": "60/1",
                "avg_frame_rate": "30/1",
            },
            "format": {"duration": "1.0", "size": "1024"},
        },
    )
    bad_res = mod.audit_local_mp4(clean)
    assert bad_res["ok"] is False
    assert any("h264" in e for e in bad_res["errors"])
    assert any("yuv420p" in e for e in bad_res["errors"])
    assert any("Variable frame rate drift" in e for e in bad_res["errors"])


def test_probe_base_url_with_demo_auth_and_cli_exit_codes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _load_audit_module()
    assert mod.parse_rate("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert mod.parse_rate("0/0") == 0.0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/landing-preview.mp4":
            assert request.headers.get("range") == "bytes=0-65535"
            return httpx.Response(
                206,
                content=b"\x00\x00\x00\x20ftypisom",
                headers={"content-type": "video/mp4", "content-range": "bytes 0-11/1000"},
            )
        if request.url.path == "/api/auth/demo-login":
            return httpx.Response(200, json={"access_token": "demo-tok"})
        if request.url.path == "/api/matches/malfacine_terra_2019/video":
            assert request.headers.get("authorization") == "Bearer demo-tok"
            return httpx.Response(
                206,
                content=b"\x00\x00\x00\x20ftypisom",
                headers={"content-type": "video/mp4", "content-range": "bytes 0-11/5000"},
            )
        return httpx.Response(404, json={"detail": "not found"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ok_results = mod.probe_base_url(
            "https://rollmetrics.example",
            probe_demo_matches=True,
            match_ids=["malfacine_terra_2019"],
            client=client,
        )
        assert len(ok_results) == 2
        assert all(r["ok"] for r in ok_results)

        fail_results = mod.probe_base_url(
            "https://rollmetrics.example",
            probe_demo_matches=True,
            match_ids=["missing_match"],
            client=client,
        )
        assert fail_results[0]["ok"] is True
        assert fail_results[1]["ok"] is False

    clean_mp4 = _make_mp4(tmp_path / "cli_ok.mp4", faststart=True)
    assert mod.main(["--file", str(clean_mp4), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True

    assert mod.main(["--file", str(tmp_path / "missing.mp4")]) == 1
    assert mod.main([]) == 2
