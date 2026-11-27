"""Exhaustive v0.3.0 tests for RuntimeStateStore, ADCC/Sparring engines, 720p trim, and CLI doctor."""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
import rollmetrics.cli as cli_mod
import rollmetrics.storage as storage_mod
from rollmetrics.config import Settings
from rollmetrics.rules import (
    RULESET_CATALOG,
    ADCCRefereeEngine,
    AthleteId,
    BeltRank,
    EventKind,
    IBJJFRefereeEngine,
    MatchDivisionConfig,
    RawObservedEvent,
    RulesetEngine,
    SparringRoundEngine,
    UniformType,
    get_ruleset_engine,
)
from rollmetrics.storage import RuntimeStateStore, _get_gcs_bearer_token, _safe_segment
from rollmetrics.video.ingest import resolve_binary, transcode_to_720p_h264, validate_video_file


def _make_synth_mp4(dest: Path, duration_sec: float = 4.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = resolve_binary("ffmpeg")
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=640x360:rate=24:duration={duration_sec}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration_sec}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    return dest


# ---------------------------------------------------------------------------
# 1A. Durable 1-File-Per-Record RuntimeStateStore (100% coverage)
# ---------------------------------------------------------------------------


def test_runtime_state_store_local_crud_and_export_zip(tmp_path: Path) -> None:
    settings = Settings(environment="test", runtime_dir=tmp_path / "rt", gcs_bucket="")
    store = RuntimeStateStore(settings)
    assert store.cloud_sync_enabled is False

    # Segment validation guards path traversal
    assert _safe_segment("match_01", "key") == "match_01"
    for bad in ("", "../etc", "a/b", "a\\b", "bad key!"):
        with pytest.raises(ValueError):
            _safe_segment(bad, "key")

    # Put, Get, List, Delete
    env1 = store.put_record("matches", "m1", {"id": "m1", "owner_email": "alice@example.com", "title": "Bout 1"})
    assert env1["schema_version"] == 1
    assert store.get_record("matches", "m1") == {"id": "m1", "owner_email": "alice@example.com", "title": "Bout 1"}
    assert store.get_record("matches", "missing") is None

    store.put_record("matches", "m2", {"id": "m2", "owner_email": "bob@example.com", "title": "Bout 2"})
    store.put_record("users", "alice@example.com", {"email": "alice@example.com", "name": "Alice"})
    # Corrupt JSON file is skipped safely during list_records & export_bundle_zip
    (store.state_root / "matches" / "corrupt.json").write_text("{not-json", encoding="utf-8")
    (store.state_root / "matches" / "nodata.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    assert store.get_record("matches", "corrupt") is None

    listed = store.list_records("matches")
    assert [r["id"] for r in listed] == ["m1", "m2"]
    assert store.list_records("empty_col") == []

    # Export full bundle ZIP
    full_zip = store.export_bundle_zip()
    with zipfile.ZipFile(io.BytesIO(full_zip), "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["record_count"] == 3
        assert "state/matches/m1.json" in zf.namelist()
        assert "state/users/alice@example.com.json" in zf.namelist()

    # Export filtered by owner_email and explicit collections (including missing collection)
    alice_zip = store.export_bundle_zip(collections=["matches", "users", "nonexistent"], owner_email="ALICE@example.com")
    with zipfile.ZipFile(io.BytesIO(alice_zip), "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["record_count"] == 2
        assert manifest["owner_email"] == "alice@example.com"
        assert "state/matches/m2.json" not in zf.namelist()

    # Delete existing and missing
    assert store.delete_record("matches", "m2") is True
    assert store.delete_record("matches", "m2") is False
    assert store.get_record("matches", "m2") is None


def test_runtime_state_store_cloud_gcs_sync_and_cold_start_hydration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import google.auth

    class _FakeCreds:
        valid = False
        token = None

        def refresh(self, _req: Any) -> None:
            self.valid = True
            self.token = "tok-gcs-123"

    monkeypatch.setattr(google.auth, "default", lambda **_kw: (_FakeCreds(), "proj"))
    assert _get_gcs_bearer_token() == "tok-gcs-123"

    # Error path in _get_gcs_bearer_token returns None
    monkeypatch.setattr(google.auth, "default", lambda **_kw: (_ for _ in ()).throw(RuntimeError("no adc")))
    assert _get_gcs_bearer_token() is None

    # Restore token provider for RuntimeStateStore cloud sync tests
    monkeypatch.setattr(storage_mod, "_get_gcs_bearer_token", lambda: "tok-gcs-123")

    cloud_bucket_objects: dict[str, bytes] = {}

    class _Resp:
        def __init__(self, status_code: int, body: bytes = b"{}") -> None:
            self.status_code = status_code
            self._body = body

        def json(self) -> Any:
            return json.loads(self._body.decode("utf-8"))

    def _fake_post(url: str, content: bytes = b"", **_kw: Any) -> _Resp:
        name = url.split("name=")[-1].replace("%2F", "/")
        cloud_bucket_objects[name] = content
        return _Resp(200)

    def _fake_get(url: str, **_kw: Any) -> _Resp:
        if "prefix=" in url:
            prefix = url.split("prefix=")[-1].replace("%2F", "/")
            items = [{"name": k} for k in sorted(cloud_bucket_objects) if k.startswith(prefix)]
            return _Resp(200, json.dumps({"items": items}).encode("utf-8"))
        obj_part = url.split("/o/")[1].split("?")[0].replace("%2F", "/")
        if obj_part in cloud_bucket_objects:
            return _Resp(200, cloud_bucket_objects[obj_part])
        return _Resp(404)

    def _fake_delete(url: str, **_kw: Any) -> _Resp:
        obj_part = url.split("/o/")[1].replace("%2F", "/")
        cloud_bucket_objects.pop(obj_part, None)
        return _Resp(204)

    monkeypatch.setattr(httpx, "post", _fake_post)
    monkeypatch.setattr(httpx, "get", _fake_get)
    monkeypatch.setattr(httpx, "delete", _fake_delete)

    s1 = Settings(environment="cloud", runtime_dir=tmp_path / "pod1", gcs_bucket="example-state-bucket")
    store1 = RuntimeStateStore(s1)
    assert store1.cloud_sync_enabled is True
    store1.put_record("matches", "cloud_m1", {"id": "cloud_m1", "title": "Cloud Bout 1"})
    store1.put_record("matches", "cloud_m2", {"id": "cloud_m2", "title": "Cloud Bout 2"})
    assert "state/matches/cloud_m1.json" in cloud_bucket_objects

    # Cold-start pod2 with empty local runtime_dir hydrates seamlessly from GCS
    s2 = Settings(environment="cloud", runtime_dir=tmp_path / "pod2", gcs_bucket="example-state-bucket")
    store2 = RuntimeStateStore(s2)
    assert store2.get_record("matches", "cloud_m1") == {"id": "cloud_m1", "title": "Cloud Bout 1"}
    assert store2.get_record("matches", "missing_remote") is None

    all_hydrated = store2.list_records("matches", hydrate_cloud=True)
    assert [r["id"] for r in all_hydrated] == ["cloud_m1", "cloud_m2"]

    # Delete removes both local and remote
    assert store2.delete_record("matches", "cloud_m1") is True
    assert "state/matches/cloud_m1.json" not in cloud_bucket_objects

    # Cover GCS HTTP error branches and missing-token branches
    monkeypatch.setattr(httpx, "post", lambda *_a, **_kw: (_ for _ in ()).throw(httpx.ConnectError("down")))
    monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: (_ for _ in ()).throw(httpx.ConnectError("down")))
    monkeypatch.setattr(httpx, "delete", lambda *_a, **_kw: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert store2._gcs_put("state/matches/x.json", b"{}") is False
    assert store2._gcs_get("state/matches/x.json") is None
    assert store2._gcs_delete("state/matches/x.json") is False
    assert store2._gcs_list_objects("matches") == []

    monkeypatch.setattr(storage_mod, "_get_gcs_bearer_token", lambda: None)
    assert store2._gcs_put("state/matches/x.json", b"{}") is False
    assert store2._gcs_get("state/matches/x.json") is None
    assert store2._gcs_delete("state/matches/x.json") is False
    assert store2._gcs_list_objects("matches") == []


# ---------------------------------------------------------------------------
# 1B. Pluggable RulesetEngine + ADCCRefereeEngine + SparringRoundEngine (100%)
# ---------------------------------------------------------------------------


def test_ruleset_factory_and_catalog() -> None:
    div = MatchDivisionConfig(uniform=UniformType.NOGI, belt=BeltRank.BLACK, duration_sec=360)
    assert len(RULESET_CATALOG) == 3

    ibjjf = get_ruleset_engine("ibjjf", div)
    adcc = get_ruleset_engine("adcc", div)
    sparring = get_ruleset_engine("sparring", div)

    assert isinstance(ibjjf, IBJJFRefereeEngine)
    assert isinstance(ibjjf, RulesetEngine)
    assert isinstance(adcc, ADCCRefereeEngine)
    assert isinstance(adcc, RulesetEngine)
    assert isinstance(sparring, SparringRoundEngine)
    assert isinstance(sparring, RulesetEngine)

    with pytest.raises(ValueError):
        get_ruleset_engine("unknown_ruleset", div)


def test_adcc_referee_engine_complete_rules() -> None:
    div = MatchDivisionConfig(uniform=UniformType.NOGI, belt=BeltRank.BLACK, duration_sec=360)
    eng = ADCCRefereeEngine(division=div)
    assert eng.no_points_sec == 180.0

    # 1. No-points first half (t < 180s): guard pull = 0 penalty, takedown = 0 pts
    e_gp_early = eng.ingest_event(
        RawObservedEvent(timestamp_sec=30.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MINOR_FOUL, technique_slug="guard_pull")
    )
    assert e_gp_early.awarded_penalties == 0 and eng.scores[AthleteId.ATHLETE_A].points == 0

    e_td_early = eng.ingest_event(
        RawObservedEvent(timestamp_sec=90.0, actor=AthleteId.ATHLETE_B, kind=EventKind.TAKEDOWN, hold_duration_sec=4.0)
    )
    assert e_td_early.awarded_points == 0

    # Stalling in first half STILL incurs -1
    e_stall = eng.ingest_event(
        RawObservedEvent(timestamp_sec=120.0, actor=AthleteId.ATHLETE_B, kind=EventKind.MINOR_FOUL, summary="stalling")
    )
    assert e_stall.awarded_points == -1 and eng.scores[AthleteId.ATHLETE_B].points == -1

    # 2. Points period (t >= 180s)
    # Guard pull during points period = -1
    e_gp_late = eng.ingest_event(
        RawObservedEvent(timestamp_sec=185.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MINOR_FOUL, technique_slug="guard_pull")
    )
    assert e_gp_late.awarded_points == -1 and eng.scores[AthleteId.ATHLETE_A].points == -1

    # Advantages are always 0 in ADCC
    e_adv = eng.ingest_event(RawObservedEvent(timestamp_sec=190.0, actor=AthleteId.ATHLETE_A, kind=EventKind.ADVANTAGE))
    assert e_adv.awarded_advantages == 0

    # Under 3.0s control or out of bounds -> 0 pts, 0 adv
    e_short = eng.ingest_event(
        RawObservedEvent(timestamp_sec=195.0, actor=AthleteId.ATHLETE_A, kind=EventKind.TAKEDOWN, hold_duration_sec=2.2)
    )
    assert e_short.awarded_points == 0

    # Regular takedown (+2), duplicate within 6s suppressed, clean sweep past guard (+4)
    e_td = eng.ingest_event(
        RawObservedEvent(timestamp_sec=205.0, actor=AthleteId.ATHLETE_A, kind=EventKind.TAKEDOWN, hold_duration_sec=3.5, position_after="Closed Guard")
    )
    assert e_td.awarded_points == 2
    e_td_dup = eng.ingest_event(
        RawObservedEvent(timestamp_sec=208.0, actor=AthleteId.ATHLETE_A, kind=EventKind.TAKEDOWN, hold_duration_sec=3.5)
    )
    assert e_td_dup.awarded_points == 0

    e_clean_sw = eng.ingest_event(
        RawObservedEvent(timestamp_sec=220.0, actor=AthleteId.ATHLETE_A, kind=EventKind.SWEEP, hold_duration_sec=3.2, position_after="Side Control")
    )
    assert e_clean_sw.awarded_points == 4

    # Guard pass (+3), Back control (+3), Mount (+2), Knee on Belly (+2)
    assert eng.ingest_event(RawObservedEvent(timestamp_sec=240.0, actor=AthleteId.ATHLETE_B, kind=EventKind.GUARD_PASS, hold_duration_sec=3.5)).awarded_points == 3
    assert eng.ingest_event(RawObservedEvent(timestamp_sec=260.0, actor=AthleteId.ATHLETE_B, kind=EventKind.BACK_CONTROL, hold_duration_sec=3.5)).awarded_points == 3
    assert eng.ingest_event(RawObservedEvent(timestamp_sec=280.0, actor=AthleteId.ATHLETE_B, kind=EventKind.MOUNT, hold_duration_sec=3.5)).awarded_points == 2
    assert eng.ingest_event(RawObservedEvent(timestamp_sec=300.0, actor=AthleteId.ATHLETE_A, kind=EventKind.KNEE_ON_BELLY, hold_duration_sec=3.5)).awarded_points == 2

    # Position change & double stalling (-1 to both)
    assert eng.ingest_event(RawObservedEvent(timestamp_sec=310.0, actor=AthleteId.ATHLETE_A, kind=EventKind.POSITION_CHANGE, position_after="Half Guard")).awarded_points == 0
    e_ds = eng.ingest_event(RawObservedEvent(timestamp_sec=320.0, actor=AthleteId.ATHLETE_A, kind=EventKind.DOUBLE_STALLING))
    assert e_ds.awarded_points == -1 and e_ds.opponent_bonus_points == -1

    # Final tally: A = -1 + 2 + 4 + 2 - 1 = 6; B = -1 + 3 + 3 + 2 - 1 = 6 (TIED!)
    snap_tie = eng.finalize_regulation()
    assert snap_tie.athlete_a.points == 6 and snap_tie.athlete_b.points == 6
    assert snap_tie.winner == "referee_decision"

    # Ignored event while ended
    e_after = eng.ingest_event(RawObservedEvent(timestamp_sec=365.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MOUNT))
    assert e_after.needs_review is True and e_after.awarded_points == 0

    # Enter ADCC Overtime -> points active immediately -> Athlete A wins by points
    eng.enter_overtime(overtime_sec=300.0)
    assert eng.overtime is True and eng.is_points_period(370.0) is True
    assert eng.ingest_event(RawObservedEvent(timestamp_sec=400.0, actor=AthleteId.ATHLETE_A, kind=EventKind.TAKEDOWN, hold_duration_sec=3.5)).awarded_points == 2
    snap_ot = eng.finalize_regulation()
    assert snap_ot.winner == "A" and snap_ot.athlete_a.points == 8

    # Separate B-win, referee-decision, submission, and serious-foul DQ branches
    eng_b = ADCCRefereeEngine(division=div, no_points_sec=0.0)
    eng_b.ingest_event(RawObservedEvent(timestamp_sec=10.0, actor=AthleteId.ATHLETE_B, kind=EventKind.GUARD_PASS, hold_duration_sec=3.5))
    assert eng_b.finalize_regulation().winner == "B"

    eng_ref = ADCCRefereeEngine(division=div)
    assert eng_ref.finalize_regulation(referee_decision=AthleteId.ATHLETE_B).winner == "B"

    eng_sub = ADCCRefereeEngine(division=div)
    e_sub = eng_sub.ingest_event(RawObservedEvent(timestamp_sec=45.0, actor=AthleteId.ATHLETE_A, kind=EventKind.SUBMISSION, summary="RNC"))
    assert e_sub.ended_match is True and eng_sub.snapshot().winner == "A"

    eng_dq = ADCCRefereeEngine(division=div)
    e_dq = eng_dq.ingest_event(RawObservedEvent(timestamp_sec=50.0, actor=AthleteId.ATHLETE_A, kind=EventKind.SERIOUS_FOUL, technique_slug="slam"))
    assert e_dq.caused_dq is True and eng_dq.snapshot().winner == "B"


def test_sparring_round_engine_complete() -> None:
    div = MatchDivisionConfig(uniform=UniformType.NOGI, belt=BeltRank.PURPLE, duration_sec=300)
    eng = SparringRoundEngine(division=div)

    # Tapout does NOT end the sparring round
    t1 = eng.ingest_event(RawObservedEvent(timestamp_sec=40.0, actor=AthleteId.ATHLETE_A, kind=EventKind.SUBMISSION, summary="Guillotine"))
    assert t1.ended_match is False and eng.ended is False
    assert eng.tapouts[AthleteId.ATHLETE_A] == 1

    # Positional control + submission threat + zero penalties on minor fouls
    assert eng.ingest_event(RawObservedEvent(timestamp_sec=90.0, actor=AthleteId.ATHLETE_B, kind=EventKind.GUARD_PASS, position_after="Side Control")).awarded_points == 1
    assert eng.ingest_event(RawObservedEvent(timestamp_sec=120.0, actor=AthleteId.ATHLETE_B, kind=EventKind.ADVANTAGE, summary="Armbar threat")).awarded_advantages == 1
    foul_ev = eng.ingest_event(RawObservedEvent(timestamp_sec=150.0, actor=AthleteId.ATHLETE_A, kind=EventKind.MINOR_FOUL, summary="stalling"))
    assert foul_ev.awarded_penalties == 0

    snap_a = eng.finalize_regulation()
    assert snap_a.winner == "A"

    eng_b = SparringRoundEngine(division=div)
    eng_b.ingest_event(RawObservedEvent(timestamp_sec=20.0, actor=AthleteId.ATHLETE_B, kind=EventKind.MOUNT))
    assert eng_b.finalize_regulation().winner == "B"

    eng_draw = SparringRoundEngine(division=div)
    assert eng_draw.finalize_regulation().winner == "draw"


# ---------------------------------------------------------------------------
# 1C. Video Normalization + Trim & CLI `rollmetrics doctor`
# ---------------------------------------------------------------------------


def test_transcode_to_720p_h264_with_trim_and_validation(tmp_path: Path) -> None:
    raw_mp4 = _make_synth_mp4(tmp_path / "raw_4s.mp4", duration_sec=4.0)

    trimmed = transcode_to_720p_h264(raw_mp4, tmp_path / "trimmed.mp4", start_sec=1.0, end_sec=3.0)
    assert trimmed.is_file()
    meta = validate_video_file(trimmed)
    assert meta.codec == "h264"
    assert 1.5 <= meta.duration_sec <= 2.5

    # Default output path (no dst) works
    auto_out = transcode_to_720p_h264(raw_mp4)
    assert auto_out.is_file()

    # Validation errors on bad trim ranges
    with pytest.raises(ValueError):
        transcode_to_720p_h264(raw_mp4, tmp_path / "bad1.mp4", start_sec=-1.0)
    with pytest.raises(ValueError):
        transcode_to_720p_h264(raw_mp4, tmp_path / "bad2.mp4", end_sec=0.0)
    with pytest.raises(ValueError):
        transcode_to_720p_h264(raw_mp4, tmp_path / "bad3.mp4", start_sec=2.0, end_sec=1.5)


def test_cli_doctor_healthy_and_missing_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_json = tmp_path / "doctor.json"
    code = cli_mod.main(["doctor", "--output", str(out_json)])
    assert code == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["healthy"] is True
    assert payload["binaries"]["ffmpeg"]
    assert payload["system"]["disk_free_gb"] > 0

    import rollmetrics.video.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "resolve_binary", lambda _name: (_ for _ in ()).throw(FileNotFoundError("no bin")))
    code_fail = cli_mod.main(["doctor"])
    assert code_fail == 1
