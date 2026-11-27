#!/usr/bin/env python3
"""Media & streaming contract auditor for local MP4 assets and live Range endpoints."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

DEFAULT_DEMO_MATCH_IDS: tuple[str, ...] = ("malfacine_terra_2019", "caio_vitor_2014")


def _resolve_ffprobe() -> str:
    env_path = f"/opt/homebrew/bin:/usr/local/bin:{os.environ.get('PATH', '')}"
    binary = shutil.which("ffprobe", path=env_path)
    if not binary:
        raise FileNotFoundError("ffprobe not found in PATH")
    return binary


def parse_rate(rate_str: str) -> float:
    """Parses ffprobe rational frame rate strings like '30/1' or '30000/1001'."""
    raw = (rate_str or "").strip()
    if not raw or raw in {"0/0", "0"}:
        return 0.0
    if "/" in raw:
        num_s, den_s = raw.split("/", 1)
        num, den = float(num_s), float(den_s)
        return (num / den) if den > 0 else 0.0
    return float(raw)


def parse_mp4_top_level_boxes(path: Path) -> list[tuple[str, int, int]]:
    """Scans top-level ISO BMFF / MP4 boxes as (box_type, offset, size) without loading full file."""
    boxes: list[tuple[str, int, int]] = []
    file_size = path.stat().st_size
    with path.open("rb") as fh:
        pos = 0
        while pos + 8 <= file_size:
            fh.seek(pos)
            header = fh.read(8)
            if len(header) < 8:
                break
            size, raw_type = struct.unpack(">I4s", header)
            box_type = raw_type.decode("latin1", errors="replace")
            if size == 1:
                ext = fh.read(8)
                if len(ext) < 8:
                    break
                size = struct.unpack(">Q", ext)[0]
            elif size == 0:
                size = file_size - pos
            boxes.append((box_type, pos, size))
            if size < 8:
                break
            pos += size
    return boxes


def probe_video_stream(path: Path) -> dict[str, Any]:
    """Runs ffprobe and returns the primary video stream metadata dictionary."""
    ffprobe = _resolve_ffprobe()
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,width,height,r_frame_rate,avg_frame_rate",
            "-show_entries",
            "format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    payload = json.loads(proc.stdout or "{}")
    streams = payload.get("streams") or []
    fmt = payload.get("format") or {}
    stream = streams[0] if streams else {}
    return {"stream": stream, "format": fmt}


def audit_local_mp4(path: Path, *, max_cfr_drift: float = 0.05) -> dict[str, Any]:
    """Verifies codec=h264, pix_fmt=yuv420p, constant frame rate, and faststart moov-before-mdat."""
    errors: list[str] = []
    if not path.is_file():
        return {
            "target": str(path),
            "kind": "local_mp4",
            "ok": False,
            "errors": [f"File not found: {path}"],
        }

    boxes = parse_mp4_top_level_boxes(path)
    box_offsets = {kind: offset for kind, offset, _ in boxes}
    moov_off = box_offsets.get("moov")
    mdat_off = box_offsets.get("mdat")
    faststart_ok = moov_off is not None and mdat_off is not None and moov_off < mdat_off
    if not faststart_ok:
        errors.append(
            f"MP4 is not faststart (moov_offset={moov_off}, mdat_offset={mdat_off})"
        )

    probed = probe_video_stream(path)
    stream = probed["stream"]
    fmt = probed["format"]
    codec = str(stream.get("codec_name") or "").lower()
    pix_fmt = str(stream.get("pix_fmt") or "").lower()
    r_fps = parse_rate(str(stream.get("r_frame_rate") or ""))
    avg_fps = parse_rate(str(stream.get("avg_frame_rate") or ""))

    if codec != "h264":
        errors.append(f"Expected video codec 'h264', got '{codec or 'missing'}'")
    if pix_fmt != "yuv420p":
        errors.append(f"Expected pix_fmt 'yuv420p', got '{pix_fmt or 'missing'}'")

    cfr_drift = 0.0
    if avg_fps <= 0.0 or r_fps <= 0.0:
        errors.append(f"Invalid frame rate metadata (r_frame_rate={r_fps}, avg_frame_rate={avg_fps})")
    else:
        cfr_drift = abs(r_fps - avg_fps) / avg_fps
        if cfr_drift >= max_cfr_drift:
            errors.append(
                f"Variable frame rate drift {cfr_drift:.4f} >= {max_cfr_drift:.2f} "
                f"(r_fps={r_fps:.3f}, avg_fps={avg_fps:.3f})"
            )

    return {
        "target": str(path),
        "kind": "local_mp4",
        "ok": len(errors) == 0,
        "codec_name": codec,
        "pix_fmt": pix_fmt,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "r_fps": round(r_fps, 4),
        "avg_fps": round(avg_fps, 4),
        "cfr_drift": round(cfr_drift, 5),
        "moov_offset": moov_off,
        "mdat_offset": mdat_off,
        "faststart": faststart_ok,
        "duration_sec": round(float(fmt.get("duration") or 0.0), 3),
        "size_bytes": int(fmt.get("size") or path.stat().st_size),
        "errors": errors,
    }


def probe_remote_range(
    url: str,
    *,
    client: httpx.Client,
    headers: dict[str, str] | None = None,
    range_spec: str = "bytes=0-65535",
) -> dict[str, Any]:
    """Probes a remote video endpoint with HTTP Range and verifies 200/206 + video/mp4."""
    req_headers = {"Range": range_spec, **(headers or {})}
    errors: list[str] = []
    resp = client.get(url, headers=req_headers)
    ctype = str(resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    content_range = str(resp.headers.get("content-range") or "")
    body_len = len(resp.content)

    if resp.status_code not in (200, 206):
        errors.append(f"Expected HTTP 200 or 206, got {resp.status_code}")
    if ctype != "video/mp4":
        errors.append(f"Expected content-type 'video/mp4', got '{ctype or 'missing'}'")
    if body_len <= 0:
        errors.append("Empty response body for video Range request")

    return {
        "target": url,
        "kind": "remote_range",
        "ok": len(errors) == 0,
        "status_code": resp.status_code,
        "content_type": ctype,
        "content_range": content_range,
        "bytes_received": body_len,
        "errors": errors,
    }


def probe_base_url(
    base_url: str,
    *,
    probe_demo_matches: bool = False,
    match_ids: list[str] | None = None,
    timeout: float = 20.0,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Probes /landing-preview.mp4 and optionally authenticated demo match video streams."""
    root = base_url.rstrip("/")
    results: list[dict[str, Any]] = []

    owns_client = client is None
    active_client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        results.append(
            probe_remote_range(f"{root}/landing-preview.mp4", client=active_client)
        )
        if probe_demo_matches:
            demo_resp = active_client.post(f"{root}/api/auth/demo-login")
            if demo_resp.status_code != 200:
                results.append(
                    {
                        "target": f"{root}/api/auth/demo-login",
                        "kind": "demo_auth",
                        "ok": False,
                        "status_code": demo_resp.status_code,
                        "errors": [f"Demo auth failed with HTTP {demo_resp.status_code}"],
                    }
                )
            else:
                demo_data = demo_resp.json() if demo_resp.content else {}
                token = str(demo_data.get("access_token") or demo_data.get("token") or "")
                auth_headers = {"Authorization": f"Bearer {token}"} if token else {}
                targets = match_ids or list(DEFAULT_DEMO_MATCH_IDS)
                for mid in targets:
                    results.append(
                        probe_remote_range(
                            f"{root}/api/matches/{mid}/video",
                            client=active_client,
                            headers=auth_headers,
                        )
                    )
    finally:
        if owns_client:
            active_client.close()
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_media_contracts",
        description="Audit local MP4 streaming contracts (H.264, yuv420p, CFR, faststart) and remote HTTP Range endpoints.",
    )
    parser.add_argument(
        "--file",
        dest="files",
        action="append",
        default=[],
        help="Local MP4 file path to audit (repeatable).",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="Optional deployed base URL to probe /landing-preview.mp4 with HTTP Range.",
    )
    parser.add_argument(
        "--probe-demo-matches",
        action="store_true",
        help="When --base-url is set, authenticate via /api/auth/demo and probe demo match /video streams.",
    )
    parser.add_argument(
        "--match-id",
        dest="match_ids",
        action="append",
        default=[],
        help="Optional match ID(s) to probe with --probe-demo-matches.",
    )
    parser.add_argument(
        "--max-cfr-drift",
        type=float,
        default=0.05,
        help="Maximum allowed relative drift between r_frame_rate and avg_frame_rate (default 0.05).",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit full JSON report to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.files and not args.base_url:
        print("Error: specify at least one --file or --base-url target.", file=sys.stderr)
        return 2

    checks: list[dict[str, Any]] = []
    for raw_file in args.files:
        checks.append(audit_local_mp4(Path(raw_file), max_cfr_drift=args.max_cfr_drift))
    if args.base_url:
        checks.extend(
            probe_base_url(
                args.base_url,
                probe_demo_matches=bool(args.probe_demo_matches),
                match_ids=args.match_ids or None,
            )
        )

    all_ok = all(bool(item.get("ok")) for item in checks)
    report = {"ok": all_ok, "checked": len(checks), "results": checks}
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        for item in checks:
            status_tag = "PASS" if item["ok"] else "FAIL"
            detail = (
                f"{item.get('codec_name')}/{item.get('pix_fmt')} "
                f"{item.get('width')}x{item.get('height')} "
                f"fps={item.get('avg_fps')} faststart={item.get('faststart')}"
                if item["kind"] == "local_mp4" and item["ok"]
                else (
                    f"HTTP {item.get('status_code')} {item.get('content_type')} ({item.get('bytes_received')} B)"
                    if item["kind"] == "remote_range" and item["ok"]
                    else "; ".join(item.get("errors") or ["failed"])
                )
            )
            print(f"[{status_tag}] {item['target']} ({detail})")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
