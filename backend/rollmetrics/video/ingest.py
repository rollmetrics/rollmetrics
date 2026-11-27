"""Video ingestion, validation, yt-dlp download, 720p normalization, and frame extraction."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def resolve_binary(name: str) -> str:
    """Resolves CLI media tools across PATH, Python virtualenv, macOS Homebrew, and Linux."""
    found = shutil.which(name)
    if found:
        return found

    venv_candidate = Path(sys.executable).resolve().parent / name
    candidates = [
        venv_candidate,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        Path("/usr/bin") / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(f"Required binary '{name}' was not found on PATH or standard install locations.")


class VideoMetadata(BaseModel):
    path: Path
    duration_sec: float
    width: int
    height: int
    fps: float
    codec: str
    size_bytes: int
    source_kind: Literal["file", "url"] = "file"
    original_source: str = ""


class ExtractedFrame(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    timestamp_sec: float
    path: Path
    jpeg_bytes: bytes


def is_video_url(source: str | Path) -> bool:
    if isinstance(source, Path):
        return False
    parsed = urlparse(str(source).strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _parse_fps(rate_str: str) -> float:
    if not rate_str or rate_str == "0/0":
        return 30.0
    if "/" in rate_str:
        num_s, den_s = rate_str.split("/", 1)
        try:
            num = float(num_s)
            den = float(den_s)
            return round(num / den, 3) if den > 0 else 30.0
        except ValueError:
            return 30.0
    try:
        return float(rate_str)
    except ValueError:
        return 30.0


def validate_video_file(path: Path | str) -> VideoMetadata:
    """Validates a local video file and extracts stream metadata via ffprobe."""
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"Video file does not exist: {target}")
    size_bytes = target.stat().st_size
    if size_bytes == 0:
        raise ValueError(f"Video file is empty: {target}")
    if target.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
        raise ValueError(
            f"Unsupported video format '{target.suffix}'. Allowed: {sorted(ALLOWED_VIDEO_EXTENSIONS)}"
        )

    ffprobe_bin = resolve_binary("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(target),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise ValueError(f"ffprobe failed on {target}: {proc.stderr.strip()}")

    probe = json.loads(proc.stdout or "{}")
    streams = probe.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise ValueError(f"No video stream found inside {target}")

    fmt = probe.get("format") or {}
    duration_raw = video_stream.get("duration") or fmt.get("duration") or 0.0
    duration_sec = max(0.0, float(duration_raw))
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    fps = _parse_fps(str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "30/1"))
    codec = str(video_stream.get("codec_name") or "unknown")

    return VideoMetadata(
        path=target,
        duration_sec=duration_sec,
        width=width,
        height=height,
        fps=fps,
        codec=codec,
        size_bytes=size_bytes,
        source_kind="file",
        original_source=str(target),
    )


def download_video_url(url: str, output_dir: Path | None = None) -> Path:
    """Downloads a YouTube or public match URL at <=720p MP4 via yt-dlp."""
    clean_url = url.strip()
    if not is_video_url(clean_url):
        raise ValueError(f"Only http:// and https:// video URLs are supported: {clean_url}")

    dest_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="rollmetrics_dl_"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.sha256(clean_url.encode("utf-8")).hexdigest()[:12]
    out_template = str(dest_dir / f"match_{url_hash}.%(ext)s")
    fmt_selector = (
        "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
        "best[height<=720][ext=mp4]/best[height<=720]/best"
    )

    try:
        ytdlp_bin = resolve_binary("yt-dlp")
        cmd = [
            ytdlp_bin,
            "--no-playlist",
            "-f",
            fmt_selector,
            "--merge-output-format",
            "mp4",
            "-o",
            out_template,
            clean_url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"yt-dlp download failed: {proc.stderr.strip()}")
    except FileNotFoundError:
        import yt_dlp

        ydl_opts = {
            "noplaylist": True,
            "format": fmt_selector,
            "merge_output_format": "mp4",
            "outtmpl": out_template,
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([clean_url])

    matches = sorted(dest_dir.glob(f"match_{url_hash}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    video_matches = [m for m in matches if m.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS]
    if not video_matches:
        raise RuntimeError(f"yt-dlp completed without producing a video file in {dest_dir}")
    return video_matches[0]


def normalize_video_720p(
    input_path: Path | str,
    output_path: Path | None = None,
    target_fps: int = 30,
) -> Path:
    """Normalizes any input MP4/MOV video into web-ready 720p H.264 MP4."""
    src = Path(input_path).expanduser().resolve()
    validate_video_file(src)

    if output_path is None:
        out_dir = Path(tempfile.mkdtemp(prefix="rollmetrics_norm_"))
        dst = out_dir / f"{src.stem}_720p.mp4"
    else:
        dst = Path(output_path).expanduser().resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = resolve_binary("ffmpeg")
    vf = "scale='min(1280,iw)':-2,setsar=1"
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-vf",
        vf,
        "-r",
        str(target_fps),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not dst.is_file():
        raise RuntimeError(f"ffmpeg 720p normalization failed: {proc.stderr.strip()}")
    return dst


def extract_frame_burst(
    video_path: Path | str,
    output_dir: Path | None = None,
    sample_fps: float = 0.5,
    start_sec: float = 0.0,
    duration_sec: float | None = None,
    max_frames: int = 60,
) -> list[ExtractedFrame]:
    """Extracts sequential 720p JPEG frames at sample_fps for local vision analysis."""
    src = Path(video_path).expanduser().resolve()
    validate_video_file(src)

    dest_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="rollmetrics_frames_"))
    dest_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = resolve_binary("ffmpeg")
    eff_fps = max(0.05, float(sample_fps))
    cmd = [ffmpeg_bin, "-y"]
    if start_sec > 0:
        cmd.extend(["-ss", f"{start_sec:.3f}"])
    cmd.extend(["-i", str(src)])
    if duration_sec is not None and duration_sec > 0:
        cmd.extend(["-t", f"{duration_sec:.3f}"])
    cmd.extend(
        [
            "-vf",
            f"fps={eff_fps:.4f},scale='min(1280,iw)':-2",
            "-frames:v",
            str(max(1, max_frames)),
            "-q:v",
            "3",
            str(dest_dir / "frame_%04d.jpg"),
        ]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame burst extraction failed: {proc.stderr.strip()}")

    jpg_files = sorted(dest_dir.glob("frame_*.jpg"))
    frames: list[ExtractedFrame] = []
    for idx, jpg in enumerate(jpg_files):
        ts = round(max(0.0, start_sec) + (idx / eff_fps), 2)
        frames.append(
            ExtractedFrame(
                timestamp_sec=ts,
                path=jpg,
                jpeg_bytes=jpg.read_bytes(),
            )
        )
    return frames


def ingest_video_source(
    source: str | Path,
    work_dir: Path | None = None,
    normalize: bool = True,
) -> VideoMetadata:
    """Ingests either a local video file or YouTube/HTTP URL and normalizes to 720p MP4."""
    base_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="rollmetrics_ingest_"))
    base_dir.mkdir(parents=True, exist_ok=True)

    if is_video_url(source):
        original = str(source).strip()
        downloaded = download_video_url(original, output_dir=base_dir / "downloads")
        meta = validate_video_file(downloaded)
        source_kind: Literal["file", "url"] = "url"
    else:
        original = str(source)
        meta = validate_video_file(Path(source))
        source_kind = "file"

    needs_norm = normalize and (
        meta.path.suffix.lower() != ".mp4"
        or meta.codec.lower() != "h264"
        or meta.height > 720
        or meta.width > 1280
    )
    if needs_norm:
        norm_out = base_dir / "normalized" / f"{meta.path.stem}_720p.mp4"
        norm_path = normalize_video_720p(meta.path, output_path=norm_out)
        meta = validate_video_file(norm_path)

    return meta.model_copy(update={"source_kind": source_kind, "original_source": original})
