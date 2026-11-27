#!/usr/bin/env python3
"""Nightly Live-Sim and Knowledge Base v0.1.0 canary runner (Layer 3)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from rollmetrics.config import Settings, get_settings
from rollmetrics.evals.runner import run_benchmark_suite
from rollmetrics.rules.schema import BeltRank, MatchDivisionConfig, UniformType
from rollmetrics.video.live_session import LiveRefereeSession

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VIDEO = REPO_ROOT / "frontend" / "public" / "landing-preview.mp4"
DEFAULT_TIMESTAMPS: tuple[float, float, float] = (0.5, 4.0, 7.5)


def _resolve_ffmpeg() -> str:
    env_path = f"/opt/homebrew/bin:/usr/local/bin:{os.environ.get('PATH', '')}"
    binary = shutil.which("ffmpeg", path=env_path)
    if not binary:
        raise FileNotFoundError("ffmpeg not found in PATH")
    return binary


def extract_canary_frames(
    video_path: Path,
    timestamps: tuple[float, ...] = DEFAULT_TIMESTAMPS,
) -> list[tuple[float, bytes]]:
    """Extracts JPEG frames from the checked-in preview clip at the requested timestamps."""
    if not video_path.is_file():
        raise FileNotFoundError(f"Canary video not found: {video_path}")
    ffmpeg = _resolve_ffmpeg()
    frames: list[tuple[float, bytes]] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        for idx, ts in enumerate(timestamps):
            out_jpg = tmp / f"frame_{idx}.jpg"
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    f"{ts:.2f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=640:360",
                    "-q:v",
                    "4",
                    str(out_jpg),
                    "-loglevel",
                    "error",
                ],
                check=True,
                timeout=30,
            )
            frames.append((ts, out_jpg.read_bytes()))
    return frames


async def run_live_sim_canary(
    *,
    video_path: Path = DEFAULT_VIDEO,
    settings: Settings | None = None,
    provider: Any | None = None,
    require_api_key: bool = False,
    frame_timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """Runs 3 extracted frames through LiveRefereeSession(profile='cloud') and validates HUD/timeline."""
    cfg = settings or get_settings()
    api_key = (os.environ.get("GEMINI_API_KEY") or cfg.gemini_api_key or "").strip()
    if not api_key and provider is None:
        return {
            "status": "failed" if require_api_key else "skipped",
            "ok": not require_api_key,
            "reason": "GEMINI_API_KEY not set",
            "frames_processed": 0,
            "timeline_events": 0,
            "model_used": "",
        }

    frames = extract_canary_frames(video_path)
    division = MatchDivisionConfig(
        athlete_a_name="Caio Terra (White Gi)",
        athlete_b_name="Bruno Malfacine (Blue Gi)",
        belt=BeltRank.BLACK,
        uniform=UniformType.GI,
        duration_sec=600.0,
    )
    session = LiveRefereeSession(
        division=division,
        profile="cloud",
        settings=cfg.model_copy(update={"gemini_api_key": api_key}) if api_key else cfg,
        retriever=False,
        provider=provider,
        min_vision_interval_sec=0.0,
    )

    last_hud: dict[str, Any] = {}
    for ts, jpeg_bytes in frames:
        last_hud = await asyncio.wait_for(
            session.process_frame(jpeg_bytes=jpeg_bytes, elapsed_sec=ts),
            timeout=frame_timeout_sec,
        )
        if not isinstance(last_hud, dict) or "scoreboard" not in last_hud:
            return {
                "status": "failed",
                "ok": False,
                "reason": "Invalid HUD payload returned by LiveRefereeSession",
                "frames_processed": len(frames),
                "timeline_events": 0,
                "model_used": "",
            }

    timeline = list((last_hud.get("scoreboard") or {}).get("timeline") or [])
    ok = len(timeline) >= 1
    return {
        "status": "passed" if ok else "failed",
        "ok": ok,
        "reason": "" if ok else "LiveRefereeSession produced 0 timeline events across 3 frames",
        "frames_processed": len(frames),
        "timeline_events": len(timeline),
        "model_used": str(last_hud.get("model_used") or ""),
        "current_position": str((last_hud.get("scoreboard") or {}).get("current_position") or ""),
    }


def run_kb_canary(version: str = "v0.1.0") -> dict[str, Any]:
    """Runs the versioned Knowledge Base eval gate."""
    report = run_benchmark_suite(version=version, include_model_arena=False)
    ok = bool(report.get("overall_pass"))
    return {
        "status": "passed" if ok else "failed",
        "ok": ok,
        "version": version,
        "manifest_verified": bool(report.get("manifest_verified")),
    }


def write_step_summary(live_res: dict[str, Any], kb_res: dict[str, Any], summary_path: str | None = None) -> None:
    """Appends a concise Markdown canary summary table to $GITHUB_STEP_SUMMARY if set."""
    target = summary_path if summary_path is not None else os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not target:
        return
    lines = [
        "### Nightly Live-Sim & Knowledge Base Canary\n\n",
        "| Check | Status | Details |\n",
        "| :--- | :--- | :--- |\n",
        (
            f"| Live-Sim Cloud Canary | `{live_res['status'].upper()}` | "
            f"frames={live_res.get('frames_processed', 0)}, "
            f"timeline={live_res.get('timeline_events', 0)}, "
            f"model=`{live_res.get('model_used') or 'n/a'}`"
            + (f" ({live_res['reason']})" if live_res.get("reason") else "")
            + " |\n"
        ),
        (
            f"| Knowledge Base `{kb_res['version']}` Eval | `{kb_res['status'].upper()}` | "
            f"manifest_verified=`{kb_res.get('manifest_verified')}` |\n\n"
        ),
    ]
    with Path(target).open("a", encoding="utf-8") as fh:
        fh.writelines(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_nightly_canary",
        description="Run nightly Live-Sim cloud frame canary and Knowledge Base eval gate.",
    )
    parser.add_argument("--video", default=str(DEFAULT_VIDEO), help="Path to preview MP4 clip.")
    parser.add_argument("--version", default="v0.1.0", help="Knowledge Base version to verify.")
    parser.add_argument(
        "--require-api-key",
        action="store_true",
        help="Fail instead of skipping Live-Sim canary when GEMINI_API_KEY is unset.",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Print JSON summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    live_res = asyncio.run(
        run_live_sim_canary(
            video_path=Path(args.video),
            require_api_key=bool(args.require_api_key),
        )
    )
    kb_res = run_kb_canary(version=args.version)
    write_step_summary(live_res, kb_res)

    all_ok = bool(live_res["ok"] and kb_res["ok"])
    payload = {"ok": all_ok, "live_sim": live_res, "knowledge_base": kb_res}
    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"[LIVE-SIM {live_res['status'].upper()}] frames={live_res['frames_processed']} "
            f"timeline={live_res['timeline_events']} model={live_res['model_used'] or 'n/a'}"
        )
        print(
            f"[KB {kb_res['version']} {kb_res['status'].upper()}] "
            f"manifest_verified={kb_res['manifest_verified']}"
        )
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
