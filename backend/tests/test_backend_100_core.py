"""Wave 3: 100.0% line coverage across all non-main.py backend modules (zero pragma: no cover)."""

from __future__ import annotations

import io
import json
import runpy
import shutil
import sqlite3
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image, ImageFont
from rollmetrics import auth as auth_mod
from rollmetrics import cli as cli_mod
from rollmetrics import desktop as desktop_mod
from rollmetrics.config import Settings
from rollmetrics.evals import datasets as ds_mod
from rollmetrics.evals import model_bench as mb_mod
from rollmetrics.evals import next_wave as nw_mod
from rollmetrics.evals import pricing as pr_mod
from rollmetrics.evals import runner as ev_runner_mod
from rollmetrics.evals import vicos_adapter as vicos_mod
from rollmetrics.evals.scoreboard_bench import masking as mask_mod
from rollmetrics.evals.scoreboard_bench import metrics as sb_metrics_mod
from rollmetrics.evals.scoreboard_bench import runner as sb_runner_mod
from rollmetrics.evals.scoreboard_bench import schema as sb_schema_mod
from rollmetrics.evals.scoreboard_bench import splits as sb_splits_mod
from rollmetrics.knowledge import academies as acad_mod
from rollmetrics.knowledge import cta_sources as cta_mod
from rollmetrics.knowledge import ibjjf_sources as ibjjf_mod
from rollmetrics.knowledge import indexer as idx_mod
from rollmetrics.knowledge import private_importer as priv_mod
from rollmetrics.knowledge import retriever as ret_mod
from rollmetrics.knowledge import schema as kb_schema_mod
from rollmetrics.providers import base as prov_base
from rollmetrics.providers import factory as prov_factory
from rollmetrics.providers import gemini_cloud as gc_mod
from rollmetrics.providers import ollama_local as ol_mod
from rollmetrics.rules import legality_matrix as leg_mod
from rollmetrics.rules import schema as rules_schema
from rollmetrics.rules import state_machine as sm_mod
from rollmetrics.rules import temporal_smoother as ts_mod
from rollmetrics.video import analyzer as an_mod
from rollmetrics.video import ingest as ing_mod
from rollmetrics.video import live_session as ls_mod
from rollmetrics.video import media_studio as ms_mod
from rollmetrics.video import tracking as trk_mod


def _make_jpeg_bytes(color: tuple[int, int, int] = (20, 120, 220), size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _make_tiny_mp4(dest: Path, duration: float = 2.5, size: str = "320x180") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = ing_mod.resolve_binary("ffmpeg")
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate=24:duration={duration}",
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


# ---------------------------------------------------------------------------
# 1. auth.py, cli.py, desktop.py, rules/*
# ---------------------------------------------------------------------------


def test_auth_cli_desktop_and_rules_100(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        environment="cloud",
        allow_local_dev_bypass=False,
        allowlist_emails="admin@example.com",
        runtime_dir=tmp_path / "rt",
    )
    # auth.py:46 malformed non-dict claims
    monkeypatch.setattr(auth_mod.google_id_token, "verify_oauth2_token", lambda *_a, **_kw: ["not-a-dict"])
    with pytest.raises(auth_mod.HTTPException) as exc:
        auth_mod.verify_google_token_and_allowlist("tok", settings)
    assert exc.value.status_code == 401

    # auth.py:91 non-dict users.json -> {}
    users_path = settings.runtime_dir / "users.json"
    users_path.parent.mkdir(parents=True, exist_ok=True)
    users_path.write_text('["not-a-dict"]', encoding="utf-8")
    assert auth_mod.load_user_profiles(settings) == {}

    # auth.py:155 coach role preserved in upsert_user_profile
    coach_prof = auth_mod.upsert_user_profile(
        settings, {"email": "coach@example.com", "name": "Coach", "role": "coach"}
    )
    assert coach_prof["role"] == "coach"

    # auth.py:304 get_app_settings fallback when request.app.state.settings is unset
    fake_req = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace()))
    assert isinstance(auth_mod.get_app_settings(fake_req), Settings)

    # auth.py:346-347 invalid ROLLMETRICS_MAX_DAILY_CLOUD_USD fallback
    monkeypatch.setenv("ROLLMETRICS_MAX_DAILY_CLOUD_USD", "not-a-float")
    budget_info = auth_mod.get_daily_cloud_budget_status(settings)
    assert budget_info["max_daily_cloud_usd"] == 10.0

    # cli.py:170-171 _cmd_analyze retriever init failure fallback
    ev_json = tmp_path / "ev.json"
    ev_json.write_text(json.dumps({"events": []}), encoding="utf-8")
    monkeypatch.setattr(ret_mod, "KnowledgeRetriever", lambda **_kw: (_ for _ in ()).throw(RuntimeError("no kb")))
    assert cli_mod.main(["analyze", str(ev_json)]) == 0
    monkeypatch.undo()

    # cli.py:272-274 scoreboard HoldoutContaminationError -> exit 2
    import rollmetrics.evals.scoreboard_bench as sb_pkg

    monkeypatch.setattr(
        sb_pkg,
        "run_scoreboard_bench",
        lambda **_kw: (_ for _ in ()).throw(sb_splits_mod.HoldoutContaminationError("leaked")),
    )
    assert cli_mod.main(["eval", "scoreboard", "--split", "heldout"]) == 2
    monkeypatch.undo()

    # cli.py:300-305 --compare-models ImportError fallback (with and without scorecard file)
    orig_import = __import__

    def _block_model_bench(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "rollmetrics.evals.model_bench":
            raise ImportError("blocked")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _block_model_bench)
    kb_empty = tmp_path / "kb_empty"
    assert cli_mod.main(["eval", "--compare-models", "--knowledge-dir", str(kb_empty)]) == 0
    sc_path = kb_empty / "evals" / "model_scorecard_v0.1.0.json"
    sc_path.parent.mkdir(parents=True, exist_ok=True)
    sc_path.write_text(json.dumps({"version": "v0.1.0", "overall_pass": True}), encoding="utf-8")
    assert cli_mod.main(["eval", "--compare-models", "--knowledge-dir", str(kb_empty)]) == 0
    monkeypatch.undo()

    # cli.py:337-338, 364-365 missing --video for media board/slowmo
    assert cli_mod.main(["media", "board"]) == 1
    assert cli_mod.main(["media", "slowmo"]) == 1

    # cli.py:534 main(argv=None) nonzero raises SystemExit + cli.py:539 __main__ block
    monkeypatch.setattr(sys, "argv", ["rollmetrics", "media", "board"])
    with pytest.raises(SystemExit) as sysexc:
        cli_mod.main(None)
    assert sysexc.value.code == 1
    with pytest.raises(SystemExit) as sysexc2:
        runpy.run_module("rollmetrics.cli", run_name="__main__")
    assert sysexc2.value.code == 1
    monkeypatch.undo()

    # desktop.py:40-42, 57-59, 135, 137, 229, 231, 250, 259-260
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no sysctl")))
    monkeypatch.setattr(desktop_mod.os, "sysconf", lambda *_a: (_ for _ in ()).throw(OSError("no sysconf")), raising=False)
    assert desktop_mod._detect_total_ram_gb() is None
    assert isinstance(desktop_mod._detect_cpu_label(), str)
    monkeypatch.undo()

    vid_dir = tmp_path / "vids"
    (vid_dir / "00_nested_sub").mkdir(parents=True)
    (vid_dir / "a.mp4").write_bytes(b"123")
    (vid_dir / "b.mp4").write_bytes(b"456")
    scanned = desktop_mod.scan_local_video_folder(vid_dir, max_files=1, settings=settings)
    assert len(scanned["videos"]) == 1

    acad_dir = tmp_path / "acad_import"
    (acad_dir / "00_sub_folder").mkdir(parents=True)
    (acad_dir / "01_list.json").write_text('["item1", "item2"]', encoding="utf-8")
    (acad_dir / "02_empty.json3").write_text('{"events": []}', encoding="utf-8")
    (acad_dir / "03_note.md").write_text("Keep frames tight.", encoding="utf-8")
    imp_res = desktop_mod.import_local_academy_folder(acad_dir, academy_id="test-gym", max_files=1, settings=settings)
    assert imp_res["imported_count"] == 1
    imp_all = desktop_mod.import_local_academy_folder(acad_dir, academy_id="test-gym", max_files=10, settings=settings)
    assert imp_all["skipped_count"] >= 1

    # rules/legality_matrix.py:230 fallback return
    monkeypatch.setitem(leg_mod.FOUL_CATALOG, "_test_custom_legal", {"label": "Custom", "article": "Art. 1", "rule": "other"})
    assert (
        leg_mod.check_technique_legality(
            "_test_custom_legal",
            belt=rules_schema.BeltRank.WHITE,
            uniform=rules_schema.UniformType.GI,
            age_division=rules_schema.AgeDivision.ADULT,
        ).severity
        == "legal"
    )
    monkeypatch.undo()

    # rules/state_machine.py:134 technique_slug resolving to minor_foul overrides non-foul kind
    minor_slug = next(k for k, v in leg_mod.FOUL_CATALOG.items() if v["rule"] == "minor_foul")
    eng = sm_mod.IBJJFRefereeEngine(rules_schema.MatchDivisionConfig(uniform=rules_schema.UniformType.NOGI))
    adj = eng.ingest_event(
        rules_schema.RawObservedEvent(
            timestamp_sec=10.0,
            actor=rules_schema.AthleteId.ATHLETE_A,
            kind=rules_schema.EventKind.TAKEDOWN,
            technique_slug=minor_slug,
        )
    )
    assert adj.awarded_penalties == 1

    # rules/temporal_smoother.py:99 dropout between two different states is not bridged
    smoother = ts_mod.TemporalPositionSmoother(window_size=1, max_dropout_sec=2.0)
    samples = [
        ts_mod.FramePositionSample(timestamp_sec=0.0, state="standing", top_athlete="neutral"),
        ts_mod.FramePositionSample(timestamp_sec=2.5, state="standing", top_athlete="neutral"),
        ts_mod.FramePositionSample(timestamp_sec=3.0, state="scramble", top_athlete="neutral"),
        ts_mod.FramePositionSample(timestamp_sec=3.5, state="mount", top_athlete="A"),
        ts_mod.FramePositionSample(timestamp_sec=6.0, state="mount", top_athlete="A"),
    ]
    assert len(smoother.smooth_samples(samples)) >= 2


# ---------------------------------------------------------------------------
# 2. providers/base.py, factory.py, gemini_cloud.py, ollama_local.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_providers_100(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # providers/base.py:174, 203, 234-235, 304-306
    fo = prov_base.FrameObservation(model_id="gemma4:e2b")
    assert fo.model_used == "gemma4:e2b"
    vr = prov_base.VideoVisionResult(model_id="gemini-4-hydrogen")
    assert vr.model_used == "gemini-4-hydrogen"
    assert prov_base.parse_json_lenient("prefix {bad: json} suffix") == {}
    coerced = prov_base.coerce_frame_observation(
        {"detected_events": [{"actor": "A", "kind": "sweep", "hold_duration_sec": 3.5}]},
        elapsed_sec=12.0,
        model_used="test-model",
    )
    assert len(coerced.detected_events) == 1

    # providers/factory.py:28-30
    cfg = Settings(environment="test", runtime_dir=tmp_path / "rt", gemini_api_key="AIzaTest")
    assert isinstance(prov_factory.get_vision_provider("gemma_cloud", cfg), gc_mod.GeminiCloudProvider)
    assert isinstance(prov_factory.get_vision_provider("cloud", cfg), gc_mod.GeminiCloudProvider)

    # providers/gemini_cloud.py:32, 42, 81, 105, 146, 159, 164, 208-212, 346, 354-355, 361, 438
    assert gc_mod.extract_interaction_text(None) == ""
    assert gc_mod.extract_interaction_text({"text": "hello"}) == "hello"
    assert gc_mod.extract_interaction_usage({"no_usage": True}) == (0, 0, 0)
    assert gc_mod._file_state_str(types.SimpleNamespace(state=None)) == "ACTIVE"

    no_key_prov = gc_mod.GeminiCloudProvider(Settings(environment="test", gemini_api_key="", runtime_dir=tmp_path / "rt2"))
    with pytest.raises(RuntimeError):
        _ = no_key_prov.client

    ov_prov = gc_mod.GeminiCloudProvider(cfg, model_override="custom-model")
    assert ov_prov._tracker_model_id() == "custom-model"
    assert ov_prov._adjudicator_model_id() == "custom-model"

    with pytest.raises(RuntimeError):
        await ov_prov._wait_for_file_active(types.SimpleNamespace(state="FAILED", name="vid1"))

    ov_prov.max_upload_wait_sec = 0.0
    with pytest.raises(TimeoutError):
        await ov_prov._wait_for_file_active(types.SimpleNamespace(state="PROCESSING", name="vid2"))

    # analyze_video adjudicator dispute loop (346, 354-355, 361)
    sample_mp4 = _make_tiny_mp4(tmp_path / "gc.mp4")

    class _FakeFiles:
        def upload(self, **_kw: Any) -> Any:
            return types.SimpleNamespace(uri="gs://x/y.mp4", name="f1", state="ACTIVE")

    class _FakeInterApi:
        def __init__(self, include_non_dict: bool = False) -> None:
            self.calls = 0
            self.include_non_dict = include_non_dict

        def create(self, **_kw: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                body = json.dumps(
                    {
                        "events": [
                            {
                                "timestamp_sec": 10.0,
                                "actor": "A",
                                "kind": "takedown",
                                "hold_duration_sec": 2.8,
                                "confidence": 0.65,
                            }
                        ],
                        "summary": "close call",
                    }
                )
            else:
                rulings: list[Any] = [
                    {
                        "event_index": 0,
                        "confirmed_kind": "not_a_real_kind",
                        "technique_slug": "clean_takedown",
                        "hold_duration_sec": 3.2,
                        "in_bounds": True,
                        "confidence": 0.91,
                        "ruling_note": "Confirmed",
                    }
                ]
                if self.include_non_dict:
                    rulings.insert(0, "skip-non-dict")
                body = json.dumps({"rulings": rulings})
            return types.SimpleNamespace(outputs=[types.SimpleNamespace(type="text", text=body)])

    ov_prov._client = types.SimpleNamespace(files=_FakeFiles(), interactions=_FakeInterApi(include_non_dict=False))
    v_res = await ov_prov.analyze_video(sample_mp4, rules_schema.MatchDivisionConfig())
    assert v_res.events[0].technique_slug == "clean_takedown"

    ov_prov._client = types.SimpleNamespace(files=_FakeFiles(), interactions=_FakeInterApi(include_non_dict=True))
    with pytest.raises(ValueError):
        await ov_prov.analyze_video(sample_mp4, rules_schema.MatchDivisionConfig())

    # query_gemma_api with image_jpeg (438)
    g_res = await ov_prov.query_gemma_api("describe", image_jpeg=_make_jpeg_bytes())
    assert isinstance(g_res, dict)

    # providers/ollama_local.py:61, 63, 75, 127-128, 172-173, 248, 284, 328-336, 383, 390, 397, 469-470, 520, 641, 644-645, 687-688
    assert ol_mod.select_best_ollama_vision_model(["gemma4:e2b-custom"], "gemma4:e2b") == "gemma4:e2b-custom"
    assert ol_mod.select_best_ollama_vision_model(["gemma4:e4b"], "gemma4:e2b") == "gemma4:e4b"
    assert ol_mod.select_best_ollama_vision_model(["custom-vision:7b"], "gemma4:e2b") == "custom-vision:7b"

    ol = ol_mod.OllamaLocalProvider(cfg)
    monkeypatch.setattr(ol, "list_installed_models", lambda: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert await ol.resolve_model() == ol.configured_model
    monkeypatch.undo()

    # chat_json with "response" key (172-173)
    class _FakeOlResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"response": '{"ok": true}', "prompt_eval_count": 10, "eval_count": 5}

    async def _fake_post(_self: Any, *_a: Any, **_kw: Any) -> _FakeOlResp:
        return _FakeOlResp()

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    ol._resolved_model = "gemma4:e2b-it-q4_K_M"
    assert (await ol.chat_json("hi"))[0] == {"ok": True}
    monkeypatch.undo()

    # _extract_single_frame_jpeg failure (248)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: types.SimpleNamespace(returncode=1, stdout=b""))
    assert ol._extract_single_frame_jpeg(sample_mp4, 0.5, "ffmpeg") is None
    monkeypatch.undo()

    # _normalize_inspect_windows non-dict (284) & _deduplicate_events duplicate replacement (328-336)
    assert ol._normalize_inspect_windows(["not-a-dict", {"start_sec": 1.0, "end_sec": 5.0}], 20.0)
    ev1 = rules_schema.RawObservedEvent(timestamp_sec=10.0, actor=rules_schema.AthleteId.ATHLETE_A, kind=rules_schema.EventKind.SWEEP, confidence=0.7)
    ev2 = rules_schema.RawObservedEvent(timestamp_sec=11.0, actor=rules_schema.AthleteId.ATHLETE_A, kind=rules_schema.EventKind.SWEEP, confidence=0.9)
    deduped = ol._deduplicate_events([ev1, ev2])
    assert len(deduped) == 1 and deduped[0].confidence == 0.9

    # analyze_video_agentic: empty coarse_list (390, 397)
    monkeypatch.setattr(ol, "_extract_single_frame_jpeg", lambda *_a, **_kw: None)
    empty_ag = await ol.analyze_video_agentic(sample_mp4, rules_schema.MatchDivisionConfig())
    assert empty_ag.events == []
    monkeypatch.undo()

    # analyze_video_agentic: tail offsets (383), coaching_notes (469-470), empty zoom_frames (520)
    long_mp4 = _make_tiny_mp4(tmp_path / "long_ol.mp4", duration=10.0)
    async def _fake_chat_ag(*_a: Any, **_kw: Any) -> tuple[dict[str, Any], str]:
        return (
            {
                "events": [],
                "coaching_notes": [{"headline": "Keep elbows tight", "detail": "Frame early."}],
                "candidate_windows": [{"start_sec": 2.0, "end_sec": 5.0, "reason": "scramble"}],
                "summary": "ok",
            },
            "gemma4:e2b-it-q4_K_M",
        )

    monkeypatch.setattr(ol, "chat_json", _fake_chat_ag)
    monkeypatch.setattr(ing_mod, "extract_frame_burst", lambda *_a, **_kw: [])
    monkeypatch.setattr(
        ing_mod,
        "validate_video_file",
        lambda p: ing_mod.VideoMetadata(path=Path(p), duration_sec=40.0, width=320, height=180, fps=24.0, codec="h264", size_bytes=100),
    )
    monkeypatch.setattr(ol, "_extract_single_frame_jpeg", lambda *_a, **_kw: _make_jpeg_bytes())
    ag_res = await ol.analyze_video_agentic(long_mp4, rules_schema.MatchDivisionConfig())
    assert len(ag_res.coaching_notes) >= 1
    monkeypatch.undo()

    # analyze_video: empty burst_frames (644-645), burst_frames + frame_mask_fn (641), coaching_notes (687-688)
    ol._resolved_model = "gemma4:e2b-it-q4_K_M"
    empty_av = await ol.analyze_video(sample_mp4, rules_schema.MatchDivisionConfig(), burst_frames=[])
    assert empty_av.events == []

    async def _fake_chat_burst(*_a: Any, **_kw: Any) -> tuple[dict[str, Any], str]:
        return (
            {"events": [], "coaching_notes": [{"headline": "Grip fight", "detail": "Break collar."}]},
            "gemma4:e2b-it-q4_K_M",
        )

    monkeypatch.setattr(ol, "chat_json", _fake_chat_burst)
    masked_av = await ol.analyze_video(
        sample_mp4,
        rules_schema.MatchDivisionConfig(),
        burst_frames=[(1.0, _make_jpeg_bytes())],
        frame_mask_fn=lambda b: b,
    )
    assert len(masked_av.coaching_notes) == 1


# ---------------------------------------------------------------------------
# 3. evals/* (datasets, model_bench, next_wave, pricing, runner, scoreboard_bench, vicos_adapter)
# ---------------------------------------------------------------------------


def test_evals_modules_100(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # evals/datasets.py:12, 428, 728, 912, 917, 922
    ds_paths = ds_mod.ensure_eval_datasets(tmp_path / "ds", overwrite=True)
    assert all(p.is_file() for p in ds_paths.values())

    # evals/model_bench.py:362-363, 606-607, 626, 673-674
    fix0 = mb_mod.GOLDEN_ARENA_FIXTURES[0]
    ev_res = mb_mod._evaluate_fixture_prediction(fix0, {"detected_kind": "invalid_kind"}, 10.0, 5, 5)
    assert ev_res["fixture_id"] == fix0["id"]

    # missing GEMINI_API_KEY in live_api=True raises RuntimeError (626)
    with pytest.raises(RuntimeError):
        mb_mod.run_model_arena(
            models=["gemini-4-hydrogen"],
            live_api=True,
            settings=Settings(environment="test", gemini_api_key="", runtime_dir=tmp_path / "rt_mb"),
        )

    # Ollama warmup ConnectError (606-607) + persist=True (673-674)
    call_count = {"n": 0}

    def _fake_ollama_fix(*_a: Any, **_kw: Any) -> tuple[dict[str, Any], float, int, int]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("warmup refused")
        return ({"detected_kind": "guard_pass", "actor": "A", "foul_severity": "legal", "points": 3}, 100.0, 20, 10)

    monkeypatch.setattr(mb_mod, "_call_ollama_fixture", _fake_ollama_fix)
    mb_mod.run_model_arena(
        models=["ollama/gemma4:e2b-it-q4_K_M"],
        live_api=True,
        persist=True,
        knowledge_dir=tmp_path / "kb_mb",
        settings=Settings(environment="test", gemini_api_key="", runtime_dir=tmp_path / "rt_mb2"),
    )
    assert (tmp_path / "kb_mb" / "evals" / "model_scorecard_v0.1.0.json").is_file()
    monkeypatch.undo()

    # evals/next_wave.py:283
    assert nw_mod._state_at_time([], 5.0) == ("standing", "neutral")

    # evals/pricing.py:92, 115
    assert pr_mod.get_model_pricing("gemma3:4b").model_id == "ollama/gemma4:e2b-it-q4_K_M"
    assert pr_mod.get_model_pricing("totally-unknown-model").model_id == "totally-unknown-model"

    # evals/runner.py:35, 39-40, 44, 115, 177, 255
    vdir = tmp_path / "ver"
    vdir.mkdir()
    assert ev_runner_mod._verify_manifest_checksums(vdir, {"artifacts": {}})[0] is False
    assert ev_runner_mod._verify_manifest_checksums(vdir, {"artifacts": {"a.txt": {"sha256": "abc"}}})[0] is False
    (vdir / "a.txt").write_text("hello", encoding="utf-8")
    assert ev_runner_mod._verify_manifest_checksums(vdir, {"artifacts": {"a.txt": {"sha256": "wrong"}}})[0] is False

    class _EmptyRet:
        def recommend_cta_for_position(self, **_kw: Any) -> list[Any]:
            return []

        def cite_ibjjf_rule(self, *_a: Any, **_kw: Any) -> list[Any]:
            return []

        def search(self, *_a: Any, **_kw: Any) -> list[Any]:
            return []

    s1_path = tmp_path / "s1_bad.json"
    s1_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "bad1",
                        "type": "legality",
                        "technique_slug": "heel_hook",
                        "belt": "white",
                        "uniform": "gi",
                        "age_division": "adult",
                        "expected_severity": "legal",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    bad_rule = ev_runner_mod._run_suite_1_ibjjf_rules(s1_path, _EmptyRet())  # type: ignore[arg-type]
    assert len(bad_rule["failures"]) == 1

    s2_path = tmp_path / "s2_bad.json"
    s2_path.write_text(
        json.dumps(
            {
                "timelines": [
                    {
                        "id": "bad_tl",
                        "division": {"uniform": "gi", "belt": "white", "age_division": "adult"},
                        "events": [],
                        "expected": {
                            "athlete_a": {"points": 99, "advantages": 0, "penalties": 0},
                            "athlete_b": {"points": 0, "advantages": 0, "penalties": 0},
                            "winner": "A",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    bad_tl = ev_runner_mod._run_suite_2_match_timelines(s2_path, "v0.1.0")
    assert len(bad_tl["failures"]) == 1

    s3_path = tmp_path / "s3_bad.json"
    s3_path.write_text(
        json.dumps(
            {
                "queries": [
                    {"id": "q1", "position": "mount", "query": "escape", "belt": "white", "expected_doc_ids": ["doc-1"]}
                ]
            }
        ),
        encoding="utf-8",
    )
    bad_cta = ev_runner_mod._run_suite_3_cta_coaching(s3_path, _EmptyRet())  # type: ignore[arg-type]
    assert len(bad_cta["failures"]) == 1

    # scoreboard_bench/schema.py:31, 117
    with pytest.raises(ValueError):
        sb_schema_mod.NormalizedBox(x0=0.5, y0=0.1, x1=0.2, y1=0.5)
    with pytest.raises(ValueError):
        sb_schema_mod.MatchManifestEntry(
            match_id="m1",
            url="https://youtu.be/abc12345678",
            video_id="abc12345678",
            event_name="Test",
            athlete_a="A",
            athlete_b="B",
            division="Adult Black Gi",
            uniform="gi",
            belt="black",
            start_sec=100.0,
            end_sec=50.0,
            overlay_preset="flograppling_lower_bar",
        )

    # scoreboard_bench/splits.py:43, 80-83, 92, 119, 126, 132
    with pytest.raises(ValueError):
        sb_splits_mod.build_deterministic_splits([], "sha")

    e1 = sb_schema_mod.MatchManifestEntry(
        match_id="m1", url="https://youtu.be/vid1", video_id="vid1", event_name="Ev1",
        athlete_a="A", athlete_b="B", division="Adult Black Gi", uniform="gi", belt="black",
        start_sec=0.0, end_sec=60.0, overlay_preset="flograppling_lower_bar",
    )
    e2 = sb_schema_mod.MatchManifestEntry(
        match_id="m2", url="https://youtu.be/vid2", video_id="vid2", event_name="Ev2",
        athlete_a="A", athlete_b="B", division="Adult Black Gi", uniform="gi", belt="black",
        start_sec=0.0, end_sec=60.0, overlay_preset="flograppling_lower_bar",
    )
    sp_forced = sb_splits_mod.build_deterministic_splits([e1, e2], "sha", calibration_ratio=1.0)
    assert len(sp_forced.heldout_match_ids) == 1

    # Cover defensive non-empty heldout guard (splits.py:80-83)
    e3 = e2.model_copy(update={"match_id": "m3", "video_id": "vid3", "event_name": "Ev3"})
    calls_sgk = {"n": 0}

    def _dynamic_sgk(self: Any) -> str:
        calls_sgk["n"] += 1
        if calls_sgk["n"] <= 3:
            return self.event_name
        return "Ev1"

    monkeypatch.setattr(sb_schema_mod.MatchManifestEntry, "split_group_key", _dynamic_sgk)
    sp_guard = sb_splits_mod.build_deterministic_splits([e1, e2, e3], "sha", calibration_ratio=1.0)
    assert len(sp_guard.heldout_match_ids) >= 1
    monkeypatch.undo()

    # Overlap error in build_deterministic_splits (92)
    e1_dup = e1.model_copy(update={"match_id": "m1_dup", "event_name": "Ev2"})
    monkeypatch.setattr(sb_schema_mod.MatchManifestEntry, "split_group_key", lambda self: self.event_name)
    with pytest.raises(RuntimeError):
        sb_splits_mod.build_deterministic_splits([e1, e1_dup], "sha", calibration_ratio=0.5)
    monkeypatch.undo()

    # load_and_verify_splits: missing (119), hash mismatch (126), video overlap (132)
    with pytest.raises(FileNotFoundError):
        sb_splits_mod.load_and_verify_splits(tmp_path / "missing_splits.json")

    bad_hash_path = tmp_path / "bad_hash.json"
    bad_hash_path.write_text(sp_forced.model_copy(update={"splits_content_sha256": "bad"}).model_dump_json(), encoding="utf-8")
    with pytest.raises(sb_splits_mod.HoldoutContaminationError):
        sb_splits_mod.load_and_verify_splits(bad_hash_path)

    bad_ov_path = tmp_path / "bad_ov.json"
    bad_ov_path.write_text(
        sp_forced.model_copy(update={"calibration_video_ids": ["v1"], "heldout_video_ids": ["v1"]}).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(sb_splits_mod.HoldoutContaminationError):
        sb_splits_mod.load_and_verify_splits(bad_ov_path)

    # scoreboard_bench/masking.py:30, 34, 85-96, 133, 258, 266, 323
    assert mask_mod._find_binary("python3") is not None
    monkeypatch.setattr(mask_mod.shutil, "which", lambda *_a, **_kw: None)
    assert mask_mod._find_binary("sh") == "/usr/bin/sh" or mask_mod._find_binary("env") is not None
    monkeypatch.undo()
    monkeypatch.setattr(mask_mod, "_find_binary", lambda _n: "/usr/bin/fake_tess")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: types.SimpleNamespace(returncode=0, stdout="SCORE 4"))
    assert mask_mod._optional_tesseract_ocr(Image.new("RGB", (40, 20), (255, 255, 255))) == "SCORE 4"
    monkeypatch.undo()

    with pytest.raises(mask_mod.ScoreboardLeakageError):
        mask_mod.assert_frame_leakage_free(Image.new("RGB", (320, 180), (255, 255, 255)), "flograppling_lower_bar")

    # Mask box #idx leakage (133) when scoreboard_box is black but another mask_box is white
    im_box_leak = Image.new("RGB", (320, 180), (0, 0, 0))
    preset_fg = mask_mod.OVERLAY_PRESETS["flograppling_lower_bar"]
    if len(preset_fg.mask_boxes) > 1:
        bx0, by0, bx1, by1 = preset_fg.mask_boxes[-1].to_pixel_rect(320, 180)
        im_box_leak.paste((255, 255, 255), (bx0, by0, bx1, by1))
        with pytest.raises(mask_mod.ScoreboardLeakageError):
            mask_mod.assert_frame_leakage_free(im_box_leak, "flograppling_lower_bar")

    sample_mp4 = _make_tiny_mp4(tmp_path / "mask_in.mp4")
    # Post-crop proj is None (258) when crop_box is top-left away from bottom bar
    cropped_out = mask_mod.render_masked_video(
        sample_mp4,
        0.0,
        2.0,
        tmp_path / "mask_crop_ok.mp4",
        preset_name="flograppling_lower_bar",
        crop_box=sb_schema_mod.NormalizedBox(x0=0.0, y0=0.0, x1=0.6, y1=0.6),
    )
    assert isinstance(cropped_out, dict)

    # Post-crop leakage error (266)
    monkeypatch.setattr(mask_mod, "_project_box_into_crop", lambda _b, _c: sb_schema_mod.NormalizedBox(x0=0.0, y0=0.0, x1=0.5, y1=0.5))
    with pytest.raises(mask_mod.ScoreboardLeakageError):
        mask_mod.render_masked_video(
            sample_mp4,
            0.0,
            2.0,
            tmp_path / "mask_crop_leak.mp4",
            preset_name="flograppling_lower_bar",
            crop_box=sb_schema_mod.NormalizedBox(x0=0.0, y0=0.0, x1=0.6, y1=0.6),
        )
    monkeypatch.undo()

    # extract_masked_anchor_frame failure (323)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: types.SimpleNamespace(returncode=1, stdout=b""))
    assert mask_mod.extract_masked_anchor_frame(sample_mp4, 0.5, "flograppling_lower_bar") is None
    monkeypatch.undo()

    # scoreboard_bench/metrics.py:27, 148
    assert sb_metrics_mod._event_signature(0, 0, 0) == ("none", 0)
    empty_agg = sb_metrics_mod.aggregate_split_metrics("m", "calibration", [])
    assert empty_agg.match_count == 0

    # scoreboard_bench/runner.py:102, 195, 375-378, 480-483, 567-570, 649-650, 700, 732, 739, 797-805, 864-865
    monkeypatch.setattr(sb_runner_mod, "_find_binary", lambda _n: None)
    with pytest.raises(RuntimeError):
        sb_runner_mod.build_masked_storyboard_sheets(sample_mp4, 0.0, 2.0, "flograppling_lower_bar")
    monkeypatch.undo()

    # adjudicate_raw_events_to_prediction opponent bonus (195)
    div = rules_schema.MatchDivisionConfig(uniform=rules_schema.UniformType.GI, belt=rules_schema.BeltRank.BLACK)
    pred_rec_bonus = sb_runner_mod.adjudicate_raw_events_to_prediction(
        match_id="m_pen",
        model_id="gemini-4-hydrogen",
        division=div,
        raw_events=[
            rules_schema.RawObservedEvent(timestamp_sec=10.0, actor=rules_schema.AthleteId.ATHLETE_A, kind=rules_schema.EventKind.MINOR_FOUL),
            rules_schema.RawObservedEvent(timestamp_sec=20.0, actor=rules_schema.AthleteId.ATHLETE_A, kind=rules_schema.EventKind.MINOR_FOUL),
            rules_schema.RawObservedEvent(timestamp_sec=30.0, actor=rules_schema.AthleteId.ATHLETE_A, kind=rules_schema.EventKind.MINOR_FOUL),
        ],
        leakage_verified=True,
        latency_ms=10.0,
        input_tokens=10,
        output_tokens=5,
        bout_duration_sec=60.0,
    )
    assert any(p.athlete == "B" and (p.adv_delta > 0 or p.pts_delta > 0) for p in pred_rec_bonus.events)

    # score_masked_match_live + score_masked_video_agentic with genai_client=None (375-378, 480-483, 567-570, 649-650)
    import google.genai as genai_mod

    gt_sample = sb_schema_mod.GroundTruthMatchLabel(
        match_id="m1",
        video_id="vid1",
        active_bout_start_sec=0.0,
        active_bout_end_sec=2.0,
        final_a_points=2,
        final_b_points=0,
        winner="A",
        method="points",
        events=[],
    )

    class _FakeGenaiClient:
        def __init__(self, **_kw: Any) -> None:
            self.poll_step = 0
            self.inter_calls = 0
            self.files = self
            self.interactions = self

        def upload(self, **_kw: Any) -> Any:
            return types.SimpleNamespace(uri="gs://b/v.mp4", name="f_poll", state="PROCESSING")

        def get(self, **_kw: Any) -> Any:
            self.poll_step += 1
            return types.SimpleNamespace(uri="gs://b/v.mp4", name="f_poll", state="ACTIVE")

        def delete(self, **_kw: Any) -> None:
            return None

        def create(self, **_kw: Any) -> Any:
            self.inter_calls += 1
            if self.inter_calls == 1:
                txt = json.dumps({"events": [], "observed_winner": "A", "summary": "a won"})
            else:
                txt = json.dumps({"events": [], "observed_winner": "", "summary": "retry empty"})
            return types.SimpleNamespace(outputs=[types.SimpleNamespace(type="text", text=txt)])

    monkeypatch.setattr(genai_mod, "Client", _FakeGenaiClient)
    monkeypatch.setattr(sb_runner_mod.time, "sleep", lambda _s: None)
    s_key = Settings(environment="test", gemini_api_key="AIzaKey", runtime_dir=tmp_path / "rt_sb")
    live_res = sb_runner_mod.score_masked_match_live(
        entry=e1, gt=gt_sample, video_path=sample_mp4, model_id="gemini-4-hydrogen",
        cached_sheets=([_make_jpeg_bytes()], 1), genai_client=None, settings=s_key,
    )
    assert live_res.model_id == "gemini-4-hydrogen"

    ag_sb_res = sb_runner_mod.score_masked_video_agentic(
        entry=e1, gt=gt_sample, video_path=sample_mp4, model_id="gemini-4-hydrogen-agentic",
        genai_client=None, settings=s_key,
    )
    assert ag_sb_res.model_id == "gemini-4-hydrogen-agentic"

    # Retry branch preserving obs_win_first (649-650) on >60s bout with fresh client
    gt_long = gt_sample.model_copy(update={"active_bout_start_sec": 0.0, "active_bout_end_sec": 90.0})

    def _fake_render_masked(*args: Any, **kw: Any) -> dict[str, Any]:
        dst = kw.get("dst_video") or args[3]
        shutil.copyfile(sample_mp4, dst)
        return {"ok": True, "masked_path": str(dst)}

    monkeypatch.setattr(sb_runner_mod, "render_masked_video", _fake_render_masked)
    class _RetryPreserveClient(_FakeGenaiClient):
        def create(self, **_kw: Any) -> Any:
            self.inter_calls += 1
            if self.inter_calls == 1:
                txt = json.dumps({"events": [], "observed_winner": "A", "observed_win_method": "decision"})
            else:
                txt = json.dumps({"events": [], "observed_winner": ""})
            return types.SimpleNamespace(output_text=txt)

    ag_retry_res = sb_runner_mod.score_masked_video_agentic(
        entry=e1, gt=gt_long, video_path=sample_mp4, model_id="gemini-4-hydrogen-agentic",
        genai_client=_RetryPreserveClient(), settings=s_key,
    )
    assert ag_retry_res.model_id == "gemini-4-hydrogen-agentic"

    # FAILED file state (567-568)
    class _FailFilesClient(_FakeGenaiClient):
        def upload(self, **_kw: Any) -> Any:
            return types.SimpleNamespace(uri="gs://b/v.mp4", name="f_fail", state="FAILED")

    with pytest.raises(RuntimeError):
        sb_runner_mod.score_masked_video_agentic(
            entry=e1, gt=gt_sample, video_path=sample_mp4, model_id="gemini-4-hydrogen-agentic",
            genai_client=_FailFilesClient(), settings=s_key,
        )
    monkeypatch.undo()

    # run_scoreboard_bench: empty matches (700), split="all" (739), default models (732), storyboard branch (797-805), dated merge (864-865)
    kb_sb = tmp_path / "kb_sb"
    bdir = kb_sb / "evals" / "scoreboard_v1"
    bdir.mkdir(parents=True)
    (bdir / "matches.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        sb_runner_mod.run_scoreboard_bench(split="calibration", knowledge_dir=kb_sb)

    (bdir / "matches.jsonl").write_text(e1.model_dump_json() + "\n" + e2.model_dump_json() + "\n", encoding="utf-8")
    (bdir / "labels.jsonl").write_text(gt_sample.model_dump_json() + "\n", encoding="utf-8")
    raw_dir = s_key.runtime_dir / "eval_media"
    raw_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sample_mp4, raw_dir / f"{e1.video_id}.mp4")

    today_str = sb_runner_mod.time.strftime("%Y-%m-%d", sb_runner_mod.time.gmtime())
    (bdir / f"results_{today_str}.json").write_text(json.dumps({"splits_summary": {"calibration": {}}}), encoding="utf-8")

    monkeypatch.setattr(
        sb_runner_mod,
        "score_masked_match_live",
        lambda **_kw: sb_schema_mod.MatchPredictionRecord(
            match_id="m1", model_id="gemini-4-hydrogen", final_a_points=2, final_b_points=0, winner="A"
        ),
    )
    sb_all = sb_runner_mod.run_scoreboard_bench(
        split="pilot",
        models=None,
        live_video=True,
        agentic_video=False,
        knowledge_dir=kb_sb,
        persist=True,
        settings=s_key,
    )
    assert sb_all["split"] == "pilot"
    monkeypatch.undo()

    # evals/vicos_adapter.py:65, 90, 105, 115-116
    first_vicos_key = next(iter(vicos_mod.VICOS_CLASS_TO_ROLLMETRICS))
    assert vicos_mod.map_vicos_label(f"prefix_{first_vicos_key}_suffix") == vicos_mod.VICOS_CLASS_TO_ROLLMETRICS[first_vicos_key]

    v_list = tmp_path / "v_list"
    v_list.mkdir()
    (v_list / "annotations.json").write_text(json.dumps([{"image": "a.jpg", "label": "mount"}]), encoding="utf-8")
    assert len(vicos_mod.load_vicos_directory(v_list)) == 1

    v_coco = tmp_path / "v_coco"
    v_coco.mkdir()
    (v_coco / "annotations.json").write_text(
        json.dumps({"images": [{"id": 1, "file_name": "a.jpg"}], "categories": [{"id": 1, "name": "mount"}], "annotations": ["skip", {"image_id": 1, "category_id": 1}]}),
        encoding="utf-8",
    )
    assert len(vicos_mod.load_vicos_directory(v_coco)) == 1

    v_samp = tmp_path / "v_samp"
    v_samp.mkdir()
    (v_samp / "annotations.json").write_text(json.dumps({"samples": [{"image": "b.jpg", "label": "guard"}]}), encoding="utf-8")
    assert len(vicos_mod.load_vicos_directory(v_samp)) == 1


# ---------------------------------------------------------------------------
# 4. knowledge/* & video/*
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_knowledge_and_video_100(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(environment="test", runtime_dir=tmp_path / "rt_kv")

    # knowledge/academies.py:98, 156, 181, 186, 188, 196, 253
    assert acad_mod.upsert_academy(settings, "Global", academy_id="global")["academy_id"] == "academy-global"
    doc_g = acad_mod.add_academy_reference(settings, "global", title="Test", text="Details", belt="invalid_belt", position="mount")
    assert doc_g.academy_id == "academy-global" and doc_g.section_or_belt == "white"
    refs_path = acad_mod._academy_refs_path(settings, "academy-global")
    refs_path.write_text("\n" + refs_path.read_text(encoding="utf-8"), encoding="utf-8")
    assert len(acad_mod.list_academy_references(settings, "academy-global")) == 1
    assert acad_mod.search_academy_references(settings, "academy-global", "Test", position="standing") == []
    with pytest.raises(ValueError):
        acad_mod.add_academy_reference(settings, "cta", title="", text="Details")
    with pytest.raises(ValueError):
        acad_mod.add_academy_reference(settings, "cta", title="Title", text="")

    # knowledge/cta_sources.py:656, 662, 667, 670, 710, 724, 802
    assert cta_mod._infer_sterling_position("Mount Escape Fundamentals") == "mount"
    monkeypatch.setattr(cta_mod, "SCRATCH_STERLING_TXT", tmp_path / "missing_sterling.txt")
    assert cta_mod._parse_sterling_curriculum_videos(set()) == []
    sterl = tmp_path / "sterling.txt"
    sterl.write_text("badline\nvid1 | 3:00 | Mount drill\nvid1 | 3:00 | Dup\n", encoding="utf-8")
    monkeypatch.setattr(cta_mod, "SCRATCH_STERLING_TXT", sterl)
    assert len(cta_mod._parse_sterling_curriculum_videos(set())) == 1

    monkeypatch.setattr(cta_mod, "SCRATCH_SUBS_DIR", tmp_path / "no_subs_dir")
    assert cta_mod._parse_json3_transcript_chapters() == ([], 0)
    empty_subs = tmp_path / "empty_subs"
    empty_subs.mkdir()
    monkeypatch.setattr(cta_mod, "SCRATCH_SUBS_DIR", empty_subs)
    assert cta_mod._parse_json3_transcript_chapters() == ([], 0)

    monkeypatch.setattr(cta_mod, "SCRATCH_CAIO_REEL_JSON", tmp_path / "no_reel.json")
    assert cta_mod._parse_caio_study_reel_chapters() == []
    monkeypatch.undo()

    # knowledge/ibjjf_sources.py:695, 700, 704-706
    monkeypatch.setattr(ibjjf_mod, "SCRATCH_IBJJF_VIDEOS_TXT", tmp_path / "no_ibjjf.txt")
    assert ibjjf_mod._parse_extra_scratch_ibjjf_videos(set()) == []
    ib_txt = tmp_path / "ib_extra.txt"
    ib_txt.write_text("short|line\nNEWVID123 | 4:00 | IBJJF | Scoring Breakdown\n", encoding="utf-8")
    monkeypatch.setattr(ibjjf_mod, "SCRATCH_IBJJF_VIDEOS_TXT", ib_txt)
    assert len(ibjjf_mod._parse_extra_scratch_ibjjf_videos(set())) == 1
    monkeypatch.undo()

    # knowledge/indexer.py:66-67, 99, 146, 171
    assert idx_mod.compute_local_embedding("")[0] == 1.0
    monkeypatch.setattr(idx_mod.np.linalg, "norm", lambda _v: 0.0)
    assert idx_mod.compute_local_embedding("armbar")[0] == 1.0
    monkeypatch.undo()

    assert idx_mod.load_jsonl_documents(tmp_path / "missing.jsonl") == []

    import google.genai as genai_mod

    class _EmptyEmbClient:
        def __init__(self, **_kw: Any) -> None:
            self.models = self

        def embed_content(self, **_kw: Any) -> Any:
            return types.SimpleNamespace(embeddings=[])

    monkeypatch.setattr(genai_mod, "Client", _EmptyEmbClient)
    with pytest.raises(RuntimeError):
        idx_mod.compute_cloud_embeddings(["hello"], api_key="AIzaKey")
    monkeypatch.undo()

    # knowledge/private_importer.py:61, 102, 116-119, 167, 193, 247
    md_empty = tmp_path / "empty_sec.md"
    md_empty.write_text("# Heading Only\n\n# Real\nKeep posture.", encoding="utf-8")
    assert len(priv_mod._parse_markdown_drop(md_empty)) == 1
    j_empty = tmp_path / "empty.json"
    j_empty.write_text("   ", encoding="utf-8")
    assert priv_mod._parse_json_or_jsonl_drop(j_empty) == []
    j_ch = tmp_path / "ch.json"
    j_ch.write_text(json.dumps({"chapters": [{"title": "C1", "text": "Grip"}]}), encoding="utf-8")
    assert len(priv_mod._parse_json_or_jsonl_drop(j_ch)) == 1
    j_one = tmp_path / "one.json"
    j_one.write_text(json.dumps({"title": "Single", "text": "Sweep"}), encoding="utf-8")
    assert len(priv_mod._parse_json_or_jsonl_drop(j_one)) == 1
    tr_empty = tmp_path / "empty.srt"
    tr_empty.write_text("1\n00:00:01,000 --> 00:00:02,000\n\n", encoding="utf-8")
    assert priv_mod._parse_subtitle_or_mp4_drop(tr_empty) == []
    mp4_sc = tmp_path / "clip_with_sidecar.mp4"
    mp4_sc.write_bytes(b"mp4")
    (tmp_path / "clip_with_sidecar.txt").write_text("transcript", encoding="utf-8")
    assert priv_mod._parse_subtitle_or_mp4_drop(mp4_sc) == []
    drop_sub = tmp_path / "drop_sub"
    (drop_sub / "nested").mkdir(parents=True)
    (drop_sub / "nested" / "note.md").write_text("Bridge hard.", encoding="utf-8")
    assert isinstance(priv_mod.import_private_curriculum(drop_sub, target_version="v0.9.9", knowledge_dir=tmp_path / "kb_p"), dict)

    # knowledge/retriever.py:81, 154-155, 160, 167, 185-186, 191, 195, 232, 238, 376
    kb_auto = tmp_path / "kb_auto"
    r_auto = ret_mod.KnowledgeRetriever(knowledge_dir=kb_auto, version="v0.1.0", settings=settings)
    assert r_auto.search("guard", top_k=0) == []
    assert isinstance(r_auto.search("", position="closed_guard", top_k=2), list)
    assert r_auto._fts_bm25_scores("!") == {}
    assert r_auto._fts_bm25_scores("zzzznonexistenttoken999") == {}

    # Empty SQLite rows (154-155) + missing manifest (160)
    r_empty_dir = tmp_path / "kb_zero"
    v_zero = r_empty_dir / "versions" / "v0.0.0"
    v_zero.mkdir(parents=True)
    conn = sqlite3.connect(v_zero / "index.sqlite")
    conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    conn.execute("CREATE TABLE documents (doc_id TEXT, corpus TEXT, academy_id TEXT, title TEXT, section_or_belt TEXT, position TEXT, text TEXT, source_url TEXT, timestamp_sec REAL, tags_json TEXT, metadata_json TEXT, embedding BLOB, local_embedding BLOB)")
    conn.commit()
    conn.close()
    (v_zero / "manifest.json").write_text(json.dumps({"version": "v0.0.0"}), encoding="utf-8")
    r_zero = ret_mod.KnowledgeRetriever(knowledge_dir=r_empty_dir, version="v0.0.0", settings=settings)
    assert r_zero.search("guard") == []
    assert r_zero._primary_matrix is not None and r_zero._primary_matrix.shape[0] == 0
    (v_zero / "manifest.json").unlink()
    assert r_zero.manifest()["counts"]["total"] == 0

    # FTS5 sqlite3.Error (185-186) and max_sc == 0 (195)
    assert r_zero._fts_bm25_scores("guard") == {}
    class _ZeroScoreConn:
        def execute(self, *_a: Any, **_kw: Any) -> Any:
            return types.SimpleNamespace(fetchall=lambda: [{"doc_id": "d1", "lex_score": 0.0}])
        def close(self) -> None:
            return None
    monkeypatch.setattr(r_auto, "_connect_ro", lambda: _ZeroScoreConn())
    assert r_auto._fts_bm25_scores("guard") == {"d1": 0.0}
    monkeypatch.undo()

    # duplicate doc_id in recommend_cta_for_position (376)
    dup_hit = kb_schema_mod.KnowledgeHit(
        doc_id="d_dup", corpus="cta", title="T", section_or_belt="white", position="mount",
        source_url="https://example.com", text="Mount escape cue.", score=0.9, metadata={"min_belt": "white"},
    )
    monkeypatch.setattr(r_auto, "search", lambda *_a, **_kw: [dup_hit, dup_hit])
    assert len(r_auto.recommend_cta_for_position("mount", "escape", top_k=3)) == 1
    monkeypatch.undo()

    # knowledge/schema.py:158
    assert kb_schema_mod.canonicalize_position("   ") == "all"

    # video/analyzer.py:125-126, 147-151, 167, 451
    monkeypatch.setattr(ret_mod, "KnowledgeRetriever", lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert an_mod.load_optional_retriever(settings) is None
    monkeypatch.undo()

    assert an_mod.lookup_cta_recommendations(None, "closed_guard", "sweep", belt="white")
    assert an_mod.lookup_cta_recommendations(None, "totally_unknown_pos_xyz", "sweep", belt="white")
    assert an_mod.lookup_ibjjf_citations(None, "sweep", "IBJJF Art. 4.6", "2 pts sweep")

    sample_mp4 = _make_tiny_mp4(tmp_path / "an.mp4")

    class _LocalAgenticStub:
        async def analyze_video_agentic(self, **_kw: Any) -> prov_base.VideoVisionResult:
            return prov_base.VideoVisionResult(events=[], model_id="gemma4:e2b")

        async def analyze_video(self, **_kw: Any) -> prov_base.VideoVisionResult:
            return prov_base.VideoVisionResult(events=[], model_id="gemma4:e2b")

    monkeypatch.setattr(an_mod, "get_vision_provider", lambda **_kw: _LocalAgenticStub())
    analyzer = an_mod.MatchAnalyzer(settings=settings, retriever=None)
    res_loc = await analyzer.analyze_video(sample_mp4, rules_schema.MatchDivisionConfig(), profile="local")
    assert "division" in res_loc
    monkeypatch.undo()

    # video/ingest.py:24, 35-36, 75-76, 168, 185, 249, 302
    assert ing_mod.resolve_binary("python3")
    monkeypatch.setattr(ing_mod.shutil, "which", lambda *_a, **_kw: None)
    assert ing_mod.resolve_binary("env")
    monkeypatch.undo()
    with pytest.raises(FileNotFoundError):
        ing_mod.resolve_binary("definitely_nonexistent_binary_xyz_999")
    assert ing_mod._parse_fps("abc/def") == 30.0

    orig_run_ing = subprocess.run

    def _fail_ffmpeg_only(cmd: list[str], **kw: Any) -> Any:
        if "ffprobe" in str(cmd[0]):
            return orig_run_ing(cmd, **kw)
        return types.SimpleNamespace(returncode=1, stderr="err", stdout="")

    monkeypatch.setattr(subprocess, "run", _fail_ffmpeg_only)
    with pytest.raises(RuntimeError):
        ing_mod.download_video_url("https://youtu.be/abc123", output_dir=tmp_path / "dl1")
    with pytest.raises(RuntimeError):
        ing_mod.transcode_to_720p_h264(sample_mp4, tmp_path / "fail_t.mp4")
    with pytest.raises(RuntimeError):
        ing_mod.extract_frame_burst(sample_mp4, tmp_path / "fail_f")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: types.SimpleNamespace(returncode=0, stderr="", stdout=""))
    with pytest.raises(RuntimeError):
        ing_mod.download_video_url("https://youtu.be/abc123", output_dir=tmp_path / "dl2")
    monkeypatch.undo()

    # video/live_session.py:46, 48, 143-144, 177-178, 252, 271, 273, 309, 385
    ls_none = ls_mod.LiveRefereeSession(division=None, settings=settings)
    ls_dict = ls_mod.LiveRefereeSession(division={"belt": "blue"}, settings=settings)
    assert ls_dict.division.belt == rules_schema.BeltRank.BLUE
    ls_none._tick_ongoing_stabilization_without_vision(1.0)
    ls_none._process_audio_transcript("tap! lute!", 5.0)
    assert len(ls_none._audio_alerts) >= 2
    ls_none._combative_state = {"A": False, "B": True}
    ls_none._advance_stalling_clocks(25.0, 25.0)
    assert ls_none.engine.scores[rules_schema.AthleteId.ATHLETE_A].penalties == 1
    ls_none._apply_observation(
        prov_base.FrameObservation(
            detected_events=[
                rules_schema.RawObservedEvent(
                    timestamp_sec=30.0, actor=rules_schema.AthleteId.ATHLETE_A, kind=rules_schema.EventKind.TAKEDOWN
                )
            ]
        ),
        30.0,
    )
    assert ls_none.engine.scores[rules_schema.AthleteId.ATHLETE_A].points == 2
    ls_none.set_locked_athlete(None)
    assert ls_none.division.locked_athlete is None
    ls_none.engine.ended = True
    ls_none._update_stabilization(rules_schema.AthleteId.ATHLETE_A, rules_schema.EventKind.MOUNT, 35.0, True)

    # video/media_studio.py:46-49, 307, 379, 437, 447, 487, 519, 543
    monkeypatch.setattr(Path, "is_file", lambda _self: False)
    assert ms_mod._load_font(14) is not None
    orig_load_def = ImageFont.load_default
    monkeypatch.setattr(ImageFont, "load_default", lambda *a, **kw: (_ for _ in ()).throw(TypeError("old pillow")) if kw else orig_load_def())
    assert ms_mod._load_font(14) is not None
    monkeypatch.undo()

    studio = ms_mod.MediaStudio()
    # Odd width (307) + ffmpeg slowmo failure (379)
    odd_meta = ing_mod.VideoMetadata(path=sample_mp4, duration_sec=2.5, width=321, height=180, fps=24.0, codec="h264", size_bytes=100)
    monkeypatch.setattr(ms_mod, "validate_video_file", lambda _p: odd_meta)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: types.SimpleNamespace(returncode=1, stderr="ff err"))
    with pytest.raises(RuntimeError):
        studio.generate_slowmo_clip(sample_mp4, 0.0, 1.0, tmp_path / "fail_slow.mp4")
    monkeypatch.undo()

    # Study reel error branches (437, 447, 487, 519, 543)
    with pytest.raises(ValueError):
        studio.generate_uncut_study_reel([{"title": "no path"}], tmp_path / "r0.mp4")

    # Default dur_sec branch (447) + title card fail (487), clip fail (519), concat fail (543)
    orig_sub_run = subprocess.run

    def _fail_on_ffmpeg_n(target_n: int) -> Any:
        state = {"n": 0}

        def _wrapped(cmd: list[str], **kw: Any) -> Any:
            if "ffprobe" in str(cmd[0]):
                return orig_sub_run(cmd, **kw)
            state["n"] += 1
            if state["n"] == target_n:
                return types.SimpleNamespace(returncode=1, stderr=f"ffmpeg fail #{target_n}")
            return orig_sub_run(cmd, **kw)

        return _wrapped

    monkeypatch.setattr(subprocess, "run", _fail_on_ffmpeg_n(1))
    with pytest.raises(RuntimeError):
        studio.generate_uncut_study_reel([{"video_path": str(sample_mp4), "start_sec": 0.0}], tmp_path / "r1.mp4")

    monkeypatch.setattr(subprocess, "run", _fail_on_ffmpeg_n(2))
    with pytest.raises(RuntimeError):
        studio.generate_uncut_study_reel([{"video_path": str(sample_mp4), "start_sec": 0.0, "duration_sec": 0.8}], tmp_path / "r2.mp4")

    monkeypatch.setattr(subprocess, "run", _fail_on_ffmpeg_n(3))
    with pytest.raises(RuntimeError):
        studio.generate_uncut_study_reel([{"video_path": str(sample_mp4), "start_sec": 0.0, "duration_sec": 0.8}], tmp_path / "r3.mp4")
    monkeypatch.undo()

    # video/tracking.py:60, 65-66, 158, 164, 175, 230, 295-296, 338-339, 348
    step_trk = {"n": 0}

    def _fake_trk_sub(*_a: Any, **_kw: Any) -> Any:
        step_trk["n"] += 1
        if step_trk["n"] == 1:
            return types.SimpleNamespace(returncode=1, stdout=b"")
        return types.SimpleNamespace(returncode=0, stdout=b"corrupt-jpeg-bytes")

    monkeypatch.setattr(subprocess, "run", _fake_trk_sub)
    assert trk_mod._sample_masked_frames(sample_mp4, 0.0, 2.0, "flograppling_lower_bar", num_samples=2) == []
    monkeypatch.undo()

    f_small = np.zeros((20, 20, 3), dtype=np.float32)
    f_big = np.zeros((30, 30, 3), dtype=np.float32)
    assert trk_mod.compute_mat_crop_box_from_arrays([f_small, f_big]) is None
    assert trk_mod.compute_mat_crop_box_from_arrays([f_small, f_small]) is None
    f_tiny_diff = f_small.copy()
    f_tiny_diff[0, :5] = 255.0
    assert trk_mod.compute_mat_crop_box_from_arrays([f_small, f_tiny_diff]) is None

    # extract_hsv_signature zero-norm (230)
    monkeypatch.setattr(trk_mod.np.linalg, "norm", lambda _v: 0.0)
    assert trk_mod.extract_hsv_signature(np.zeros((16, 16, 3), dtype=np.uint8))[0] == 1.0
    monkeypatch.undo()

    # lock_athlete_identities: c0[0] <= c1[0] (295-296), swapped pair (338-339), risky swap (348)
    fr0 = np.zeros((60, 100, 3), dtype=np.uint8)
    fr0[:, :50] = (240, 20, 20)
    fr0[:, 50:] = (20, 20, 240)
    fr1 = np.zeros((60, 100, 3), dtype=np.uint8)
    fr1[:, :50] = (20, 20, 240)
    fr1[:, 50:] = (240, 20, 20)
    fr_same = np.full((60, 100, 3), 120, dtype=np.uint8)
    locked_res = trk_mod.lock_athlete_identities(
        [fr0, fr1, fr_same],
        [
            [(0.0, 0.0, 0.45, 1.0), (0.55, 0.0, 1.0, 1.0)],
            [(0.0, 0.0, 0.45, 1.0), (0.55, 0.0, 1.0, 1.0)],
            [(0.2, 0.0, 0.7, 1.0), (0.2, 0.0, 0.7, 1.0)],
        ],
    )
    assert len(locked_res["tracks"]) == 3
    assert len(locked_res["swap_risk_frames"]) >= 1


# ---------------------------------------------------------------------------
# 5. encyclopedia/* (store, gcs_vault, ingestor, harvester) & cli.py 100%
# ---------------------------------------------------------------------------


def test_encyclopedia_and_cli_100(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from rollmetrics.encyclopedia import gcs_vault as vault_mod
    from rollmetrics.encyclopedia import harvester as harv_mod
    from rollmetrics.encyclopedia import ingestor as ing_enc_mod
    from rollmetrics.encyclopedia import schema as enc_schema
    from rollmetrics.encyclopedia import store as enc_store_mod

    # 5A. store.py remaining lines (115, 126, 333-354, 391, 400-410, 433-458, 493-499)
    db_path = tmp_path / "enc" / "catalog.sqlite"
    with enc_store_mod.EncyclopediaStore(db_path) as store:
        vid = enc_schema.VideoRecord(
            video_id="vid_enc_1",
            channel="FloGrappling",
            url="https://www.youtube.com/watch?v=vid_enc_1",
            title="Alpha vs Beta | 2025 IBJJF Worlds",
            duration_sec=360.0,
        )
        store.upsert_video(vid)
        m1 = enc_schema.MatchRecord(
            match_id="flo-vid_enc_1",
            video_id="vid_enc_1",
            url="https://www.youtube.com/watch?v=vid_enc_1",
            start_sec=0.0,
            end_sec=300.0,
            duration_sec=300.0,
            athlete_a="Alpha",
            athlete_b="Beta",
            event_name="IBJJF Worlds",
            event_year=2025,
            belt="black",
            uniform="gi",
            source="FloGrappling",
            footage_type="broadcast_scoreboard",
            overlay_preset="flograppling_lower_bar",
            tier=1,
            in_scoreboard_v1=False,
        )
        store.upsert_match(m1, commit=False)
        store.commit()

        bundle = enc_schema.MatchAnnotationBundle(
            match_id=m1.match_id,
            annotator_id="tester",
            tier=1,
            scoring_events=[
                enc_schema.ScoringRuleEvent(
                    video_sec=120.0,
                    athlete="A",
                    event_type="submission",
                    submission_type="armbar",
                )
            ],
            technique_events=[
                enc_schema.TechniqueEvent(
                    video_sec=100.0,
                    athlete="A",
                    category="guard_state",
                    technique_name="closed_guard",
                    guard_family="closed",
                )
            ],
            rule_edge_cases=[
                enc_schema.RuleEdgeCaseTag(
                    video_sec=80.0,
                    category="out_of_bounds_reset",
                    ibjjf_rule_ref="Art. 6.2",
                )
            ],
            summary="Clean armbar finish from closed guard.",
        )
        store.upsert_annotation(bundle)

        assert len(
            store.search(
                "",
                belt="black",
                uniform="gi",
                footage_type="broadcast_scoreboard",
                source="FloGrappling",
                event_year=2025,
                min_tier=1,
            )
        ) == 1
        assert len(
            store.select_cohort(
                limit=5,
                footage_type="broadcast_scoreboard",
                belt="black",
                uniform="gi",
                source="FloGrappling",
                exclude_ingested=True,
                exclude_v1=True,
            )
        ) == 1

        store.record_gcs_asset(m1.match_id, "raw_video", "gs://b/raw.mp4", 100, "sha1")
        store.record_gcs_asset(m1.match_id, "masked_video", "gs://b/masked.mp4", 90, "sha2")
        store.record_gcs_asset(m1.match_id, "annotation_json", "gs://b/ann.json", 50, "sha3")
        store.record_gcs_asset(m1.match_id, "metadata_json", "gs://b/meta.json", 40, "sha4")
        assert store.export_matches_jsonl(tmp_path / "enc" / "export.jsonl") == 1

    # 5B. gcs_vault.py (100%)
    assert "PATH" in vault_mod._gcloud_env()
    monkeypatch.setattr(vault_mod.shutil, "which", lambda *_a, **_kw: None)
    with pytest.raises(FileNotFoundError):
        vault_mod._resolve_gcloud()
    monkeypatch.setattr(vault_mod.shutil, "which", lambda *_a, **_kw: "/usr/bin/gcloud")
    assert vault_mod._resolve_gcloud() == "/usr/bin/gcloud"

    v_file = tmp_path / "sample.bin"
    v_file.write_bytes(b"hello-vault")
    assert len(vault_mod.file_sha256(v_file)) == 64

    vault = vault_mod.GCSVault(bucket="example-vault-bucket", project="example-proj")
    assert vault.bucket == "gs://example-vault-bucket"
    assert vault.uri("/clips/a.mp4") == "gs://example-vault-bucket/clips/a.mp4"

    call_seq = {"n": 0}

    def _fake_gcloud_run(cmd: list[str], **_kw: Any) -> Any:
        call_seq["n"] += 1
        if "describe" in cmd and call_seq["n"] == 1:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="not found")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vault_mod.subprocess, "run", _fake_gcloud_run)
    assert vault.ensure_bucket() == "gs://example-vault-bucket"
    assert vault.ensure_bucket() == "gs://example-vault-bucket"
    assert vault.exists("clips/a.mp4") is True
    u_uri, u_sz, u_sha = vault.upload_file(v_file, "clips/sample.bin")
    assert u_uri == "gs://example-vault-bucket/clips/sample.bin" and u_sz == 11 and len(u_sha) == 64
    dl_out = vault.download_file("clips/sample.bin", tmp_path / "dl" / "sample.bin")
    assert dl_out == tmp_path / "dl" / "sample.bin"
    b_uri, _b_sz, _b_sha = vault.backup_catalog(db_path)
    assert b_uri.endswith("catalog/catalog.sqlite.gz")
    monkeypatch.undo()

    # 5C. ingestor.py (100%)
    sample_mp4 = _make_tiny_mp4(tmp_path / "enc_src.mp4")
    assert "PATH" in ing_enc_mod._media_env()
    monkeypatch.setattr(ing_enc_mod.shutil, "which", lambda *_a, **_kw: None)
    with pytest.raises(FileNotFoundError):
        ing_enc_mod._resolve_tool("yt-dlp")
    monkeypatch.setattr(ing_enc_mod.shutil, "which", lambda *_a, **_kw: "/usr/bin/yt-dlp")
    assert ing_enc_mod._resolve_tool("yt-dlp") == "/usr/bin/yt-dlp"

    monkeypatch.setattr(ing_enc_mod.subprocess, "run", lambda *_a, **_kw: types.SimpleNamespace(returncode=0))
    with pytest.raises(FileNotFoundError):
        ing_enc_mod.download_match_video("https://youtu.be/abc", tmp_path / "missing_out.mp4")

    def _fake_ytdlp_dl(cmd: list[str], **_kw: Any) -> Any:
        out_p = Path(cmd[cmd.index("-o") + 1])
        shutil.copyfile(sample_mp4, out_p)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(ing_enc_mod.subprocess, "run", _fake_ytdlp_dl)
    monkeypatch.setattr(
        ing_enc_mod,
        "create_masked_video_clip",
        lambda **kw: (shutil.copyfile(sample_mp4, kw["dst_video"]), {"ok": True})[1],
    )
    monkeypatch.setattr(vault, "upload_file", lambda p, rel: (f"gs://example-vault/{rel}", Path(p).stat().st_size, "sha"))
    with enc_store_mod.EncyclopediaStore(db_path) as store:
        ing_res = ing_enc_mod.ingest_match_to_gcs(
            m1,
            store=store,
            vault=vault,
            scratch_root=tmp_path / "enc_scratch",
            render_masked=True,
        )
        assert ing_res["status"] == "ok" and "masked_video" in ing_res["assets"]
    monkeypatch.undo()

    # 5D. harvester.py (100%)
    assert "PATH" in harv_mod._ytdlp_env()
    monkeypatch.setattr(harv_mod.shutil, "which", lambda *_a, **_kw: None)
    with pytest.raises(FileNotFoundError):
        harv_mod._resolve_ytdlp()
    monkeypatch.setattr(harv_mod.shutil, "which", lambda *_a, **_kw: "/usr/bin/yt-dlp")
    assert harv_mod._resolve_ytdlp() == "/usr/bin/yt-dlp"

    assert harv_mod.load_scoreboard_v1_video_ids(tmp_path / "no_kb_dir") == set()
    kb_v1 = tmp_path / "kb_v1" / "evals" / "scoreboard_v1"
    kb_v1.mkdir(parents=True)
    (kb_v1 / "matches.jsonl").write_text('\n{"video_id": "v1"}\n{bad-json\n', encoding="utf-8")
    assert harv_mod.load_scoreboard_v1_video_ids(tmp_path / "kb_v1") == {"v1"}

    assert harv_mod.parse_belt("White Belt Final") == "white"
    assert harv_mod.parse_belt("Brown Belt Match") == "brown"
    assert harv_mod.parse_belt("Black Belt Match") == "black"
    assert harv_mod.parse_belt("Random Match") == "unknown"
    assert harv_mod.parse_uniform("IBJJF Worlds", source_label="IBJJF") == "gi"
    assert harv_mod.parse_uniform("Random Clip") == "unknown"
    assert harv_mod.parse_age_division("Kids Final") == "kids"
    assert harv_mod.parse_age_division("Juvenile Final") == "juvenile"
    assert harv_mod.parse_age_division("Master 2 Final") == "master"
    assert harv_mod.parse_age_division("Adult Final") == "adult"
    assert harv_mod.parse_age_division("Open Final") == "unknown"
    assert harv_mod.parse_athletes("No Versus Here") == ("Unknown", "Unknown")
    assert harv_mod.parse_event_name("Alpha vs Beta | Custom Cup") == "Custom Cup"
    assert harv_mod.parse_event_name("Alpha vs Beta") == "IBJJF Tournament"

    assert harv_mod.classify_video("ADCC Highlight", 300, "IBJJF") == "non_match"
    assert harv_mod.classify_video("Alpha vs Beta", 300, "FloGrappling") == "non_match"
    assert harv_mod.classify_video("Alpha vs Beta", 300, "YouTube_ColoredBelt") == "non_match"
    assert harv_mod.classify_video("Alpha vs Beta | IBJJF Worlds", 30, "IBJJF") == "non_match"
    assert harv_mod.classify_video("Mat 1 Livestream | IBJJF Worlds", 4000, "IBJJF") == "full_mat_stream"
    assert harv_mod.classify_video("Every Match | IBJJF Worlds", 900, "IBJJF") == "multi_match_compilation"
    assert harv_mod.classify_video("IBJJF Worlds Final 2025", 200, "IBJJF") == "standalone_match"
    assert harv_mod.classify_video("IBJJF Worlds Clip", 0, "IBJJF") == "non_match"
    assert harv_mod.infer_footage_and_preset("IBJJF", "IBJJF") == ("broadcast_scoreboard", "ibjjf_top_left_bug")
    assert harv_mod.infer_footage_and_preset("YouTube_ColoredBelt", "Gym") == ("matside_raw", "")

    assert harv_mod.video_entry_to_records({}, source_label="IBJJF", harvest_source="ibjjf", v1_video_ids=set()) == (None, [])
    vid_non, ms_non = harv_mod.video_entry_to_records(
        {"id": "v_non", "title": "Podcast Episode 1", "url": "v_non"},
        source_label="IBJJF",
        harvest_source="ibjjf",
        v1_video_ids=set(),
    )
    assert vid_non is not None and ms_non == []

    vid_comp, ms_comp = harv_mod.video_entry_to_records(
        {
            "id": "v_comp",
            "title": "Every Match | IBJJF Worlds 2025",
            "duration": 600,
            "chapters": [
                {"title": "Intro", "start_time": 0, "end_time": 10},
                {"title": "Alpha vs Beta", "start_time": 10, "end_time": 200},
            ],
        },
        source_label="IBJJF",
        harvest_source="ibjjf",
        v1_video_ids=set(),
    )
    assert vid_comp is not None and len(ms_comp) == 1

    fake_stdout = "\nnot-json\n{bad-json\n" + json.dumps({"id": "v_ok", "title": "Alpha vs Beta | IBJJF Worlds 2025", "duration": 300}) + "\n" + json.dumps({"id": "", "title": ""}) + "\n"
    monkeypatch.setattr(harv_mod.subprocess, "run", lambda *_a, **_kw: types.SimpleNamespace(returncode=0, stdout=fake_stdout))
    entries = harv_mod.fetch_flat_playlist("https://www.youtube.com/@ibjjf/videos", max_items=2)
    assert len(entries) == 2

    db_harv = tmp_path / "enc" / "harv.sqlite"
    with enc_store_mod.EncyclopediaStore(db_harv) as store_h:
        h_rep = harv_mod.harvest_sources(store_h, sources=("ibjjf",), knowledge_dir=tmp_path / "kb_v1", max_per_feed=2)
        assert h_rep["harvested_matches"] >= 1
    monkeypatch.undo()

    # 5E. cli.py encyclopedia subcommands (384-387, 391-404, 408-413, 417-430, 434-483)
    db_cli = tmp_path / "enc" / "cli_cat.sqlite"
    monkeypatch.setattr(harv_mod, "harvest_sources", lambda store, **_kw: {"harvested_matches": 1})
    assert cli_mod.main(["encyclopedia", "harvest", "--sources", "ibjjf", "--max-per-feed", "1", "--db", str(db_cli)]) == 0
    monkeypatch.setenv("ROLLMETRICS_RUNTIME_DIR", str(tmp_path / "rt_cli_enc"))
    assert cli_mod.main(["encyclopedia", "stats"]) == 0
    assert cli_mod.main(["encyclopedia", "stats", "--db", str(db_cli)]) == 0
    assert cli_mod.main(["encyclopedia", "search", "Alpha", "--db", str(db_cli)]) == 0

    with enc_store_mod.EncyclopediaStore(db_cli) as store_c:
        store_c.upsert_video(vid)
        store_c.upsert_match(m1)
        m2 = m1.model_copy(update={"match_id": "flo-vid_enc_2"})
        store_c.upsert_match(m2)

    ing_calls = {"n": 0}

    def _fake_ingest_cli(match: Any, **_kw: Any) -> dict[str, Any]:
        ing_calls["n"] += 1
        if ing_calls["n"] == 2:
            raise RuntimeError("transient download error")
        return {"match_id": match.match_id, "status": "ok"}

    monkeypatch.setattr(vault_mod.GCSVault, "ensure_bucket", lambda self: self.bucket)
    monkeypatch.setattr(vault_mod.GCSVault, "backup_catalog", lambda self, _p: ("gs://b/cat.gz", 10, "sha"))
    monkeypatch.setattr(ing_enc_mod, "ingest_match_to_gcs", _fake_ingest_cli)
    assert cli_mod.main(["encyclopedia", "ingest", "--db", str(db_cli), "--bucket", "example-b", "--limit", "2", "--backup-catalog"]) == 0

