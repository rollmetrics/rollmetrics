"""Unit tests for Offline Gemma 4 Desktop Mode and Next-Wave Evaluation Suites."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image
from rollmetrics.cli import main as cli_main
from rollmetrics.config import REPO_ROOT, Settings
from rollmetrics.desktop import (
    get_local_hardware_status,
    import_local_academy_folder,
    scan_local_video_folder,
)
from rollmetrics.evals.next_wave import (
    CANONICAL_GRAPPLING_STATES,
    STABILIZATION_EDGE_CASES,
    PositionWindow,
    evaluate_position_segmentation,
    evaluate_stabilization_edge_cases,
    mask_referee_region,
)
from rollmetrics.knowledge.academies import list_academy_references
from rollmetrics.video.ingest import resolve_binary


@pytest.fixture(scope="module")
def desktop_synth_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out_dir = tmp_path_factory.mktemp("desktop_vid")
    vid = out_dir / "open_mat_roll.mp4"
    ffmpeg_bin = resolve_binary("ffmpeg")
    subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=24:duration=3",
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


def test_offline_desktop_hardware_folder_scan_and_academy_import(
    tmp_path: Path,
    desktop_synth_mp4: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        environment="local",
        profile="local",
        runtime_dir=runtime_dir,
        knowledge_dir=REPO_ROOT / "knowledge_base",
        gemini_api_key="",
        ollama_vision_model="gemma4:e2b-it-q4_K_M",
    )

    # 1. Hardware readiness with mocked Ollama returning Gemma 4
    def _ok_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma4:e2b-it-q4_K_M"}]})
        return httpx.Response(404)

    hw = get_local_hardware_status(settings=settings, transport=httpx.MockTransport(_ok_handler))
    assert hw["offline_ready"] is True
    assert hw["ollama_reachable"] is True
    assert "gemma4:e2b-it-q4_K_M" in hw["gemma_models"]
    assert hw["recommended_model"] == "gemma4:e2b-it-q4_K_M"
    assert hw["zero_cloud_keys_required"] is True
    assert hw["cpu_cores"] >= 1

    # Unreachable Ollama degrades gracefully
    def _fail_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    hw_down = get_local_hardware_status(settings=settings, transport=httpx.MockTransport(_fail_handler))
    assert hw_down["offline_ready"] is False
    assert hw_down["ollama_reachable"] is False

    # 2. Local video folder scanner
    vid_folder = tmp_path / "bjj_videos"
    vid_folder.mkdir()
    (vid_folder / "open_mat_roll.mp4").write_bytes(desktop_synth_mp4.read_bytes())
    (vid_folder / "corrupt.mp4").write_bytes(b"not-a-valid-mp4")
    (vid_folder / "notes.txt").write_text("not a video", encoding="utf-8")
    scan = scan_local_video_folder(vid_folder, settings=settings)
    assert scan["count"] == 2
    valid_vids = [v for v in scan["videos"] if v["valid"]]
    invalid_vids = [v for v in scan["videos"] if not v["valid"]]
    assert len(valid_vids) == 1 and valid_vids[0]["filename"] == "open_mat_roll.mp4"
    assert valid_vids[0]["duration_sec"] == pytest.approx(3.0, abs=0.4)
    assert valid_vids[0]["analyzed"] is False
    assert len(invalid_vids) == 1 and invalid_vids[0]["filename"] == "corrupt.mp4"

    with pytest.raises(FileNotFoundError):
        scan_local_video_folder(tmp_path / "missing_folder", settings=settings)

    # 3. Local academy folder importer (.md, .txt, .json3, .json, .mp4) with zero cloud API keys
    acad_folder = tmp_path / "local_academy_notes"
    acad_folder.mkdir()
    (acad_folder / "half_guard_underhook.md").write_text(
        "Frame neck, shoot deep far underhook, and come up to dogfight.",
        encoding="utf-8",
    )
    (acad_folder / "mount_escape.txt").write_text(
        "Keep elbows glued to ribs, trap ankle, bridge and shrimp to half guard.",
        encoding="utf-8",
    )
    (acad_folder / "back_control.json3").write_text(
        json.dumps({"events": [{"segs": [{"utf8": "Seatbelt grip and ear to ear pressure."}]}]}),
        encoding="utf-8",
    )
    (acad_folder / "custom_drill.json").write_text(
        json.dumps({"title": "Knee Cut Pin", "text": "Pin far hip and win underhook before sliding knee."}),
        encoding="utf-8",
    )
    (acad_folder / "demo_sweep.mp4").write_bytes(desktop_synth_mp4.read_bytes())
    (acad_folder / "empty.md").write_text("   ", encoding="utf-8")
    (acad_folder / "broken.json").write_text("{not valid json", encoding="utf-8")
    (acad_folder / "bad_video.mp4").write_bytes(b"bad")

    imp = import_local_academy_folder(
        acad_folder,
        academy_id="local-garage-bjj",
        academy_name="Local Garage BJJ",
        settings=settings,
    )
    assert imp["academy_id"] == "local-garage-bjj"
    assert imp["imported_count"] == 7
    assert imp["skipped_count"] == 1
    assert imp["zero_cloud_keys_required"] is True
    stored = list_academy_references(settings, "local-garage-bjj")
    assert len(stored) == 7
    assert all(d.academy_id == "local-garage-bjj" for d in stored)

    with pytest.raises(FileNotFoundError):
        import_local_academy_folder(tmp_path / "missing_acad", academy_id="x", settings=settings)

    # 4. CLI `rollmetrics desktop --dry-run` + executable launcher script exists
    assert cli_main(["desktop", "--folder", str(vid_folder), "--port", "8099", "--no-browser", "--dry-run"]) == 0
    launcher = REPO_ROOT / "scripts" / "run_local.sh"
    assert launcher.is_file()


def test_next_wave_stabilization_edge_cases_referee_mask_and_position_segmentation() -> None:
    # 1. Stabilization edge-case suite (13 razor-thin IBJJF boundary cases)
    assert len(STABILIZATION_EDGE_CASES) >= 12
    res = evaluate_stabilization_edge_cases()
    assert res["overall_pass"] is True
    assert res["passed"] == res["total"]
    assert res["pass_rate"] == 1.0

    # 2. Mat-only blind ablation: mask_referee_region zeroes out referee bounding box
    raw = Image.new("RGB", (640, 360), (200, 180, 160))
    masked = mask_referee_region(raw, referee_boxes=[(0.0, 0.0, 0.25, 0.80)])
    arr = np.asarray(masked)
    assert int(arr[100, 40].max()) == 0
    assert int(arr[100, 320].min()) == 160

    # 3. Positional state segmentation (5s window macro-F1, top/bottom acc, transition mIoU)
    assert len(CANONICAL_GRAPPLING_STATES) == 8
    gt = [
        PositionWindow(start_sec=0.0, end_sec=15.0, state="standing", top_athlete="neutral"),
        PositionWindow(start_sec=15.0, end_sec=35.0, state="closed_guard", top_athlete="A"),
        PositionWindow(start_sec=35.0, end_sec=50.0, state="mount", top_athlete="A"),
    ]
    perfect = evaluate_position_segmentation(gt, gt, step_sec=5.0)
    assert perfect["num_windows"] == 10
    assert perfect["macro_f1"] == 1.0
    assert perfect["top_bottom_accuracy"] == 1.0
    assert perfect["transition_miou"] == 1.0

    shifted_pred = [
        PositionWindow(start_sec=0.0, end_sec=20.0, state="standing", top_athlete="neutral"),
        PositionWindow(start_sec=20.0, end_sec=35.0, state="closed_guard", top_athlete="A"),
        PositionWindow(start_sec=35.0, end_sec=50.0, state="side_control", top_athlete="A"),
    ]
    partial = evaluate_position_segmentation(gt, shifted_pred, step_sec=5.0)
    assert 0.0 < partial["macro_f1"] < 1.0
    assert 0.0 < partial["transition_miou"] < 1.0
    assert partial["top_bottom_accuracy"] == pytest.approx(0.9, abs=0.01)

    empty = evaluate_position_segmentation([], [])
    assert empty["num_windows"] == 0
