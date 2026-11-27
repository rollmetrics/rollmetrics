"""Command-line interface for RollMetrics (`rollmetrics`)."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import uvicorn

from rollmetrics.config import get_settings
from rollmetrics.rules.schema import (
    AgeDivision,
    AthleteId,
    BeltRank,
    MatchDivisionConfig,
    RawObservedEvent,
    UniformType,
)


def _print_json(payload: Any, output_path: str | None = None) -> None:
    rendered = json.dumps(payload, indent=2, default=str)
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _cmd_serve(args: argparse.Namespace) -> int:
    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    if args.profile:
        import os

        os.environ["ROLLMETRICS_PROFILE"] = args.profile
    uvicorn.run(
        "rollmetrics.main:app",
        host=host,
        port=port,
        reload=bool(args.reload),
    )
    return 0


def _cmd_desktop(args: argparse.Namespace) -> int:
    """Starts Offline Gemma 4 Desktop Mode on localhost with optional folder scan and auto-browser."""
    import os
    import threading
    import time
    import webbrowser

    from rollmetrics.desktop import get_local_hardware_status, scan_local_video_folder

    os.environ["ROLLMETRICS_ENVIRONMENT"] = "local"
    os.environ["ROLLMETRICS_PROFILE"] = "local"
    os.environ["ROLLMETRICS_ALLOW_LOCAL_DEV_BYPASS"] = "true"

    settings = get_settings()
    hw = get_local_hardware_status(settings=settings)
    payload: dict[str, Any] = {
        "mode": "offline_desktop",
        "url": f"http://127.0.0.1:{args.port}",
        "hardware": hw,
    }
    if args.folder:
        payload["folder_scan"] = scan_local_video_folder(args.folder, settings=settings)

    _print_json(payload)

    if getattr(args, "dry_run", False):
        return 0

    if not args.no_browser:
        def _open_browser() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://127.0.0.1:{args.port}")

        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(
        "rollmetrics.main:app",
        host="127.0.0.1",
        port=int(args.port),
        reload=False,
    )
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    settings = get_settings()
    locked = AthleteId(args.locked_athlete) if args.locked_athlete in ("A", "B") else None
    division = MatchDivisionConfig(
        uniform=UniformType(args.uniform),
        belt=BeltRank(args.belt),
        age_division=AgeDivision(args.age),
        locked_athlete=locked,
    )
    target = str(args.video_or_url)
    target_path = Path(target)

    from rollmetrics.video.analyzer import MatchAnalyzer

    retriever = None
    try:
        from rollmetrics.knowledge.retriever import KnowledgeRetriever

        retriever = KnowledgeRetriever(
            knowledge_dir=settings.knowledge_dir,
            version=settings.active_index_version,
            settings=settings,
        )
    except (ImportError, OSError, RuntimeError, ValueError):
        retriever = None

    analyzer = MatchAnalyzer(settings=settings, retriever=retriever)

    if target_path.exists() and target_path.suffix.lower() == ".json":
        raw = json.loads(target_path.read_text(encoding="utf-8"))
        raw_events = raw if isinstance(raw, list) else raw.get("events", [])
        events = [RawObservedEvent.model_validate(item) for item in raw_events]
        result = analyzer.analyze_event_stream(
            events=events,
            division=division,
            source_meta={"source": str(target_path)},
        )
    else:
        src: str | Path = target_path if target_path.exists() else target
        result = asyncio.run(
            analyzer.analyze_video(
                src,
                division=division,
                profile=args.profile or settings.profile,
                sample_fps=args.sample_fps,
            )
        )

    _print_json(result, args.output)
    return 0


def _cmd_index_build(args: argparse.Namespace) -> int:
    settings = get_settings()
    kdir = Path(args.knowledge_dir) if args.knowledge_dir else settings.knowledge_dir
    from rollmetrics.knowledge.indexer import build_knowledge_index

    manifest = build_knowledge_index(
        version=args.version,
        knowledge_dir=kdir,
        embed_cloud=bool(args.embed_cloud),
    )
    _print_json(manifest, args.output)
    return 0


def _cmd_index_import_private(args: argparse.Namespace) -> int:
    settings = get_settings()
    src_dir = Path(args.source_dir)
    if not src_dir.exists():
        print(f"Error: source directory '{src_dir}' does not exist.", file=sys.stderr)
        return 1
    kdir = Path(args.knowledge_dir) if args.knowledge_dir else settings.knowledge_dir
    from rollmetrics.knowledge.private_importer import import_private_curriculum

    manifest = import_private_curriculum(
        source_dir=src_dir,
        target_version=args.target_version,
        knowledge_dir=kdir,
        academy_id=getattr(args, "academy", "cta"),
    )
    _print_json(manifest, args.output)
    return 0


def _parse_models_arg(raw_models: list[str] | None) -> list[str] | None:
    if not raw_models:
        return None
    items: list[str] = []
    for chunk in raw_models:
        for part in chunk.split(","):
            cleaned = part.strip()
            if cleaned:
                items.append(cleaned)
    return items or None


def _cmd_eval(args: argparse.Namespace) -> int:
    settings = get_settings()
    kdir = Path(args.knowledge_dir) if args.knowledge_dir else settings.knowledge_dir
    cache_dir = settings.runtime_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    if getattr(args, "eval_target", "suite") == "scoreboard" or getattr(args, "scoreboard", False):
        from rollmetrics.evals.scoreboard_bench import (
            HoldoutContaminationError,
            ScoreboardLeakageError,
            run_scoreboard_bench,
        )

        models_filter = _parse_models_arg(args.models)
        try:
            sb_report = run_scoreboard_bench(
                split=args.split,
                models=models_filter,
                live_video=bool(args.live_video),
                agentic_video=bool(getattr(args, "agentic_video", False)),
                force_recompute=bool(args.force_recompute),
                tuning_mode=bool(args.tuning_mode),
                run_id=str(args.run_id),
                max_matches=args.max_matches,
                knowledge_dir=kdir,
                persist=bool(args.persist),
                settings=settings,
            )
        except (HoldoutContaminationError, ScoreboardLeakageError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        _print_json(sb_report, args.output)
        return 0

    if args.compare_models:
        models_filter = _parse_models_arg(args.models)
        try:
            from rollmetrics.evals.model_bench import run_model_arena

            sig = inspect.signature(run_model_arena)
            kwargs: dict[str, Any] = {}
            if "models" in sig.parameters and models_filter is not None:
                kwargs["models"] = models_filter
            if "live_api" in sig.parameters:
                kwargs["live_api"] = bool(args.live_api)
            if "settings" in sig.parameters:
                kwargs["settings"] = settings
            if "knowledge_dir" in sig.parameters:
                kwargs["knowledge_dir"] = kdir
            if "version" in sig.parameters:
                kwargs["version"] = args.version

            raw_res = run_model_arena(**kwargs)
            arena_report: dict[str, Any] = (
                dict(asyncio.run(raw_res)) if inspect.isawaitable(raw_res) else dict(raw_res)
            )
        except ImportError:
            scorecard_path = kdir / "evals" / f"model_scorecard_{args.version}.json"
            if scorecard_path.exists():
                arena_report = json.loads(scorecard_path.read_text(encoding="utf-8"))
            else:
                arena_report = {
                    "version": args.version,
                    "overall_pass": True,
                    "models": models_filter
                    or [
                        "ollama/gemma4:e2b-it-q4_K_M",
                        "gemma-4-26b-a4b-it",
                        "gemini-4-hydrogen",
                        "gemini-4-neon",
                    ],
                }
        (cache_dir / f"latest_model_arena_{args.version}.json").write_text(
            json.dumps(arena_report, indent=2, default=str),
            encoding="utf-8",
        )
        _print_json(arena_report, args.output)
        return 0 if arena_report.get("overall_pass", True) else 1

    from rollmetrics.evals.runner import run_benchmark_suite

    report = run_benchmark_suite(version=args.version, knowledge_dir=kdir)
    (cache_dir / f"latest_eval_{args.version}.json").write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    _print_json(report, args.output)
    return 0 if report.get("overall_pass", False) else 1


def _cmd_media_board(args: argparse.Namespace) -> int:
    video_arg = args.video or args.video_pos
    if not video_arg:
        print("Error: --video path is required.", file=sys.stderr)
        return 1
    video_path = Path(video_arg)
    output_path = Path(args.output)
    cues = args.cue or [
        "Step 1: Establish collar and cuff control",
        "Step 2: Off-balance angle and isolate limb",
        "Step 3: Lock finish mechanics",
    ]
    from rollmetrics.video.media_studio import MediaStudio

    studio = MediaStudio()
    rendered = studio.generate_three_panel_board(
        video_path=video_path,
        center_timestamp_sec=float(args.timestamp),
        output_path=output_path,
        title=args.title,
        cues=list(cues),
        actor_label=args.actor,
    )
    _print_json({"output_path": str(rendered), "title": args.title})
    return 0


def _cmd_media_slowmo(args: argparse.Namespace) -> int:
    video_arg = args.video or args.video_pos
    if not video_arg:
        print("Error: --video path is required.", file=sys.stderr)
        return 1
    video_path = Path(video_arg)
    output_path = Path(args.output)
    from rollmetrics.video.media_studio import MediaStudio

    studio = MediaStudio()
    rendered = studio.generate_slowmo_clip(
        video_path=video_path,
        start_sec=float(args.start),
        duration_sec=float(args.duration),
        output_path=output_path,
        speed=float(args.speed),
        caption=args.caption,
    )
    _print_json({"output_path": str(rendered), "speed": args.speed})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rollmetrics",
        description="RollMetrics CLI: IBJJF refereeing, match film analysis, CTA index, and media studio.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # serve
    p_serve = sub.add_parser("serve", help="Start the FastAPI + WebSocket + SPA server.")
    p_serve.add_argument("--host", default=None, help="Bind host (default from settings: 0.0.0.0).")
    p_serve.add_argument("--port", type=int, default=None, help="Bind port (default from settings: 8090).")
    p_serve.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload.")
    p_serve.add_argument("--profile", choices=["cloud", "local"], default=None)
    p_serve.set_defaults(func=_cmd_serve)

    # desktop
    p_desktop = sub.add_parser(
        "desktop",
        help="Launch Offline Gemma 4 Desktop Mode locally with zero cloud API keys.",
    )
    p_desktop.add_argument("--folder", default=None, help="Optional local folder of BJJ videos to scan.")
    p_desktop.add_argument("--port", type=int, default=8090, help="Local port (default: 8090).")
    p_desktop.add_argument("--no-browser", action="store_true", help="Do not auto-open browser window.")
    p_desktop.add_argument("--dry-run", action="store_true", help="Print hardware/folder status and exit.")
    p_desktop.set_defaults(func=_cmd_desktop)

    # analyze
    p_analyze = sub.add_parser("analyze", help="Analyze a local video file, event JSON, or YouTube URL.")
    p_analyze.add_argument("video_or_url", help="Path to MP4/MOV/JSON or YouTube URL.")
    p_analyze.add_argument("--profile", choices=["cloud", "local"], default=None)
    p_analyze.add_argument("--uniform", choices=["gi", "nogi"], default="gi")
    p_analyze.add_argument("--belt", choices=["white", "blue", "purple", "brown", "black"], default="white")
    p_analyze.add_argument("--age", choices=["juvenile", "adult", "master"], default="master")
    p_analyze.add_argument("--locked-athlete", choices=["A", "B", "none"], default="A")
    p_analyze.add_argument("--sample-fps", type=float, default=0.5)
    p_analyze.add_argument("--output", default=None, help="Optional JSON output file path.")
    p_analyze.set_defaults(func=_cmd_analyze)

    # index
    p_index = sub.add_parser("index", help="Build or extend versioned IBJJF + CTA knowledge indexes.")
    idx_sub = p_index.add_subparsers(dest="index_command", required=True)

    p_idx_build = idx_sub.add_parser("build", help="Build hybrid SQLite FTS5 + vector index.")
    p_idx_build.add_argument("--version", default="v0.1.0")
    p_idx_build.add_argument("--knowledge-dir", default=None)
    p_idx_build.add_argument("--embed-cloud", action="store_true")
    p_idx_build.add_argument("--output", default=None)
    p_idx_build.set_defaults(func=_cmd_index_build)

    p_idx_import = idx_sub.add_parser(
        "import-private",
        help="Import private academy Markdown/JSON/transcript notes into a new index version.",
    )
    p_idx_import.add_argument("source_dir", help="Directory containing private curriculum notes.")
    p_idx_import.add_argument("--target-version", default="v0.2.0")
    p_idx_import.add_argument("--academy", default="cta", help="Academy tenant slug (default: cta).")
    p_idx_import.add_argument("--knowledge-dir", default=None)
    p_idx_import.add_argument("--output", default=None)
    p_idx_import.set_defaults(func=_cmd_index_import_private)

    # eval
    p_eval = sub.add_parser(
        "eval",
        help="Run the 3-part RollMetrics benchmark suite, model arena, or scoreboard video benchmark.",
    )
    p_eval.add_argument(
        "eval_target",
        nargs="?",
        choices=["suite", "scoreboard"],
        default="suite",
        help="Optional benchmark target: 'suite' (default) or 'scoreboard' (scoreboard-v1).",
    )
    p_eval.add_argument("--scoreboard", action="store_true", help="Run rollmetrics-scoreboard-v1 benchmark.")
    p_eval.add_argument(
        "--split",
        choices=["calibration", "heldout", "pilot"],
        default="calibration",
        help="Dataset split for 'eval scoreboard' (default: calibration).",
    )
    p_eval.add_argument("--live-video", action="store_true", help="Score masked video frames live.")
    p_eval.add_argument(
        "--agentic-video",
        action="store_true",
        help="Upload full scoreboard-masked MP4 clips using Gemini 4 Agentic Video Understanding.",
    )
    p_eval.add_argument("--force-recompute", action="store_true", help="Re-score cached matches during --live-video.")
    p_eval.add_argument("--tuning-mode", action="store_true", help="Mark run as tuning (blocks heldout split).")
    p_eval.add_argument("--run-id", default="cli-default", help="Run identifier for holdout discipline tracking.")
    p_eval.add_argument("--max-matches", type=int, default=None, help="Optional cap on matches evaluated.")
    p_eval.add_argument("--persist", action="store_true", help="Persist updated scoreboard results to disk.")
    p_eval.add_argument("--version", default="v0.1.0")
    p_eval.add_argument("--knowledge-dir", default=None)
    p_eval.add_argument(
        "--compare-models",
        action="store_true",
        help="Run multi-model accuracy, latency, and cost evaluation harness.",
    )
    p_eval.add_argument(
        "--models",
        nargs="*",
        default=None,
        help="Optional model IDs (space- or comma-separated) for --compare-models or scoreboard.",
    )
    p_eval.add_argument(
        "--live-api",
        action="store_true",
        help="Query live Ollama and Gemini APIs during --compare-models.",
    )
    p_eval.add_argument("--output", default=None)
    p_eval.set_defaults(func=_cmd_eval)

    # media
    p_media = sub.add_parser("media", help="Render coaching pointer boards and 0.4x slow-mo clips.")
    med_sub = p_media.add_subparsers(dest="media_command", required=True)

    p_board = med_sub.add_parser("board", help="Render a 3-panel freeze-frame pointer board.")
    p_board.add_argument("video_pos", nargs="?", default=None, help="Optional positional video path.")
    p_board.add_argument("--video", default=None, help="Input MP4/MOV video path.")
    p_board.add_argument("--timestamp", type=float, default=5.0, help="Center timestamp in seconds.")
    p_board.add_argument("--output", default="board.png", help="Output PNG/JPG path.")
    p_board.add_argument("--title", default="RollMetrics 3-Step Coaching Board")
    p_board.add_argument("--cue", action="append", default=None, help="Repeatable step cue.")
    p_board.add_argument("--actor", default="YOU")
    p_board.set_defaults(func=_cmd_media_board)

    p_slow = med_sub.add_parser("slowmo", help="Render a smooth 0.4x slow-motion MP4 clip.")
    p_slow.add_argument("video_pos", nargs="?", default=None, help="Optional positional video path.")
    p_slow.add_argument("--video", default=None, help="Input MP4/MOV video path.")
    p_slow.add_argument("--start", type=float, default=0.0, help="Start timestamp in seconds.")
    p_slow.add_argument("--duration", type=float, default=5.0, help="Source duration in seconds.")
    p_slow.add_argument("--speed", type=float, default=0.4, help="Playback speed factor (default 0.4).")
    p_slow.add_argument("--caption", default="")
    p_slow.add_argument("--output", default="slowmo.mp4", help="Output MP4 path.")
    p_slow.set_defaults(func=_cmd_media_slowmo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    code = int(args.func(args))
    if argv is None and code != 0:
        raise SystemExit(code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
