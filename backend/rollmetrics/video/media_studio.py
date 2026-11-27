"""On-Demand Visual Media Studio: 3-panel pointer boards, 0.4x slow-mo clips, and uncut study reels."""

from __future__ import annotations

import contextlib
import math
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from rollmetrics.config import Settings, get_settings
from rollmetrics.video.ingest import resolve_binary, validate_video_file

BG_DARK = (11, 15, 20)
PANEL_BORDER = (36, 48, 64)
HEADER_BG = (17, 24, 39)
EMERALD = (16, 185, 129)
AMBER = (245, 158, 11)
WHITE = (248, 250, 252)
SLATE_TEXT = (203, 213, 225)
MUTED_TEXT = (148, 163, 184)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        if bold
        else [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for fp in candidates:
        if Path(fp).is_file():
            with contextlib.suppress(OSError, ValueError):
                return ImageFont.truetype(fp, size=size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _fmt_ts(seconds: float) -> str:
    sec = max(0.0, seconds)
    mins = int(sec // 60)
    rem = sec - (mins * 60)
    return f"{mins:02d}:{rem:04.1f}"


def _draw_pointer_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: tuple[int, int, int] = EMERALD,
    width: int = 5,
) -> None:
    """Draws a crisp dark-outlined high-contrast tactical pointer arrow."""
    outline = (10, 14, 18)
    draw.line([start, end], fill=outline, width=width + 4)
    draw.line([start, end], fill=color, width=width)

    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    head_len = 16
    spread = 0.48
    p1 = (
        int(end[0] - head_len * math.cos(angle - spread)),
        int(end[1] - head_len * math.sin(angle - spread)),
    )
    p2 = (
        int(end[0] - head_len * math.cos(angle + spread)),
        int(end[1] - head_len * math.sin(angle + spread)),
    )
    draw.polygon([end, p1, p2], fill=color, outline=outline)


def _extract_single_frame(video_path: Path, timestamp_sec: float, out_jpg: Path) -> Path:
    ffmpeg_bin = resolve_binary("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss",
        f"{max(0.0, timestamp_sec):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_jpg),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out_jpg.is_file():
        # Fallback near 0.0s if timestamp sits on exact EOF boundary
        fallback_cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(out_jpg),
        ]
        subprocess.run(fallback_cmd, capture_output=True, text=True, check=True)
    return out_jpg


class MediaStudio:
    """Renders on-demand 3-panel textbook coaching boards, 0.4x slow-mo MP4s, and study reels."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def generate_three_panel_board(
        self,
        video_path: Path | str,
        center_timestamp_sec: float,
        output_path: Path | str,
        title: str,
        cues: list[str],
        actor_label: str = "YOU",
    ) -> Path:
        """Extracts t-1.5s, t, t+1.5s frames and renders a 3-panel CTA textbook board."""
        src = Path(video_path).expanduser().resolve()
        meta = validate_video_file(src)
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        max_ts = max(0.0, meta.duration_sec - 0.08)
        raw_offsets = [center_timestamp_sec - 1.5, center_timestamp_sec, center_timestamp_sec + 1.5]
        timestamps = [min(max_ts, max(0.0, ts)) for ts in raw_offsets]

        panel_w = 580
        panel_h = 326
        gap = 24
        margin_x = 36
        header_h = 96
        footer_h = 168
        board_w = margin_x * 2 + panel_w * 3 + gap * 2  # 1860 px
        board_h = header_h + panel_h + footer_h + 24  # 614 px

        canvas = Image.new("RGB", (board_w, board_h), BG_DARK)
        draw = ImageDraw.Draw(canvas)

        # Header strip
        draw.rectangle([(0, 0), (board_w, header_h - 12)], fill=HEADER_BG)
        draw.rectangle([(0, 0), (8, header_h - 12)], fill=EMERALD)

        eyebrow_font = _load_font(14, bold=True)
        title_font = _load_font(26, bold=True)
        badge_font = _load_font(20, bold=True)
        pill_font = _load_font(14, bold=True)
        cue_head_font = _load_font(15, bold=True)
        cue_body_font = _load_font(15, bold=False)

        draw.text(
            (margin_x, 16),
            "ROLLMETRICS // CTA TECHNICAL SEQUENCE BOARD",
            fill=EMERALD,
            font=eyebrow_font,
        )
        draw.text((margin_x, 40), title or "3-Step Positional Sequence", fill=WHITE, font=title_font)

        window_label = f"{_fmt_ts(timestamps[0])} -> {_fmt_ts(timestamps[1])} -> {_fmt_ts(timestamps[2])}"
        draw.text((board_w - 310, 36), window_label, fill=MUTED_TEXT, font=pill_font)

        phase_labels = ["ENTRY / SETUP", "KEY LEVERAGE", "FINISH / STABILIZE"]
        pointer_vectors = [
            ((115, 86), (255, 162)),
            ((450, 82), (305, 168)),
            ((125, 250), (268, 178)),
        ]

        with tempfile.TemporaryDirectory(prefix="rollmetrics_board_") as tmp_s:
            tmp_dir = Path(tmp_s)
            for idx in range(3):
                frame_jpg = _extract_single_frame(src, timestamps[idx], tmp_dir / f"p_{idx+1}.jpg")
                with Image.open(frame_jpg) as raw_img:
                    rgb = raw_img.convert("RGB").resize((panel_w, panel_h), Image.Resampling.LANCZOS)

                px = margin_x + idx * (panel_w + gap)
                py = header_h

                draw.rectangle(
                    [(px - 2, py - 2), (px + panel_w + 2, py + panel_h + 2)],
                    outline=PANEL_BORDER,
                    width=2,
                )
                canvas.paste(rgb, (px, py))

                # Pointer arrow + Athlete callout pill
                start_rel, end_rel = pointer_vectors[idx]
                arrow_start = (px + start_rel[0], py + start_rel[1])
                arrow_end = (px + end_rel[0], py + end_rel[1])
                arrow_color = AMBER if idx == 1 else EMERALD
                _draw_pointer_arrow(draw, arrow_start, arrow_end, color=arrow_color, width=5)

                callout_txt = f"{actor_label} ({phase_labels[idx]})"
                pill_w = max(136, len(callout_txt) * 8 + 22)
                pill_x = max(px + 12, min(px + panel_w - pill_w - 12, arrow_start[0] - pill_w // 2))
                pill_y = max(py + 12, arrow_start[1] - 32)
                draw.rounded_rectangle(
                    [(pill_x, pill_y), (pill_x + pill_w, pill_y + 26)],
                    radius=6,
                    fill=(11, 15, 20),
                    outline=arrow_color,
                    width=2,
                )
                draw.text((pill_x + 10, pill_y + 5), callout_txt, fill=WHITE, font=pill_font)

                # Numbered badge (1, 2, 3) top-left
                bx, by = px + 14, py + 14
                draw.ellipse([(bx, by), (bx + 36, by + 36)], fill=EMERALD, outline=BG_DARK, width=2)
                draw.text((bx + 12, by + 6), str(idx + 1), fill=BG_DARK, font=badge_font)

                # Timestamp pill bottom-right
                ts_str = f"T={_fmt_ts(timestamps[idx])}"
                tx = px + panel_w - 112
                ty = py + panel_h - 34
                draw.rounded_rectangle(
                    [(tx, ty), (tx + 98, ty + 24)],
                    radius=5,
                    fill=(11, 15, 20),
                    outline=PANEL_BORDER,
                    width=1,
                )
                draw.text((tx + 10, ty + 4), ts_str, fill=SLATE_TEXT, font=pill_font)

                # Footer cue block under each panel
                fy = py + panel_h + 16
                draw.rounded_rectangle(
                    [(px, fy), (px + panel_w, fy + footer_h - 18)],
                    radius=8,
                    fill=HEADER_BG,
                    outline=PANEL_BORDER,
                    width=1,
                )
                cue_title = f"STEP {idx + 1} // {phase_labels[idx]}"
                draw.text((px + 16, fy + 12), cue_title, fill=EMERALD, font=cue_head_font)

                cue_text = (
                    cues[idx]
                    if idx < len(cues)
                    else (cues[-1] if cues else "Maintain inside frames, hip angle, and 3.0s control.")
                )
                wrapped = textwrap.wrap(cue_text, width=62)[:4]
                for line_i, line in enumerate(wrapped):
                    draw.text(
                        (px + 16, fy + 38 + line_i * 22),
                        line,
                        fill=SLATE_TEXT,
                        font=cue_body_font,
                    )

        if out.suffix.lower() in (".jpg", ".jpeg"):
            canvas.save(out, format="JPEG", quality=94)
        else:
            canvas.save(out, format="PNG")
        return out

    def _render_caption_overlay_png(self, caption: str, width: int, out_png: Path) -> Path:
        banner_h = 68
        img = Image.new("RGBA", (width, banner_h), (11, 15, 20, 215))
        draw = ImageDraw.Draw(img)
        draw.rectangle([(0, 0), (width, 3)], fill=(16, 185, 129, 255))
        draw.rectangle([(0, 0), (8, banner_h)], fill=(16, 185, 129, 255))
        tag_font = _load_font(13, bold=True)
        cap_font = _load_font(18, bold=True)
        draw.text((20, 10), "ROLLMETRICS // 0.4X SLOW-MO STUDY", fill=(16, 185, 129, 255), font=tag_font)
        trimmed = caption if len(caption) <= 95 else f"{caption[:92]}..."
        draw.text((20, 32), trimmed, fill=(248, 250, 252, 255), font=cap_font)
        img.save(out_png, format="PNG")
        return out_png

    def generate_slowmo_clip(
        self,
        video_path: Path | str,
        start_sec: float,
        duration_sec: float,
        output_path: Path | str,
        speed: float = 0.4,
        caption: str = "",
    ) -> Path:
        """Generates a smooth slow-motion H.264 yuv420p MP4 clip with optional caption banner."""
        src = Path(video_path).expanduser().resolve()
        meta = validate_video_file(src)
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        clamped_start = max(0.0, min(float(start_sec), max(0.0, meta.duration_sec - 0.1)))
        clamped_dur = max(0.2, float(duration_sec))
        eff_speed = max(0.1, min(2.0, float(speed)))
        pts_mult = 1.0 / eff_speed

        target_w = min(1280, meta.width if meta.width > 0 else 1280)
        if target_w % 2 != 0:
            target_w -= 1

        ffmpeg_bin = resolve_binary("ffmpeg")
        with tempfile.TemporaryDirectory(prefix="rollmetrics_slowmo_") as tmp_s:
            tmp_dir = Path(tmp_s)
            if caption.strip():
                banner_png = self._render_caption_overlay_png(
                    caption=caption.strip(),
                    width=target_w,
                    out_png=tmp_dir / "caption.png",
                )
                filter_complex = (
                    f"[0:v]scale={target_w}:-2,setsar=1,setpts={pts_mult:.4f}*PTS,fps=30[base];"
                    f"[base][1:v]overlay=0:H-h[vout]"
                )
                cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-ss",
                    f"{clamped_start:.3f}",
                    "-t",
                    f"{clamped_dur:.3f}",
                    "-i",
                    str(src),
                    "-i",
                    str(banner_png),
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[vout]",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(out),
                ]
            else:
                vf = f"scale={target_w}:-2,setsar=1,setpts={pts_mult:.4f}*PTS,fps=30"
                cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-ss",
                    f"{clamped_start:.3f}",
                    "-t",
                    f"{clamped_dur:.3f}",
                    "-i",
                    str(src),
                    "-vf",
                    vf,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(out),
                ]

            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0 or not out.is_file():
                raise RuntimeError(f"ffmpeg slow-mo generation failed: {proc.stderr.strip()}")

        return out

    def _render_title_card_png(
        self,
        index: int,
        total: int,
        title: str,
        cue: str,
        window_str: str,
        out_png: Path,
    ) -> Path:
        w, h = 1280, 720
        img = Image.new("RGB", (w, h), BG_DARK)
        draw = ImageDraw.Draw(img)
        draw.rectangle([(72, 140), (82, 580)], fill=EMERALD)

        eyebrow_font = _load_font(18, bold=True)
        title_font = _load_font(42, bold=True)
        sub_font = _load_font(24, bold=False)

        draw.text(
            (116, 152),
            f"ROLLMETRICS FILM ROOM // STUDY {index} OF {total} ({window_str})",
            fill=EMERALD,
            font=eyebrow_font,
        )
        for idx, line in enumerate(textwrap.wrap(title or f"Study Segment {index}", width=38)[:2]):
            draw.text((116, 206 + idx * 54), line, fill=WHITE, font=title_font)

        if cue:
            for idx, line in enumerate(textwrap.wrap(cue, width=62)[:4]):
                draw.text((116, 350 + idx * 36), line, fill=SLATE_TEXT, font=sub_font)

        img.save(out_png, format="PNG")
        return out_png

    def generate_uncut_study_reel(
        self,
        segments: list[dict[str, Any]],
        output_path: Path | str,
    ) -> Path:
        """Builds an uncut coaching study reel interleaving title cards and 720p match segments."""
        if not segments:
            raise ValueError("segments must contain at least one study segment dictionary.")

        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_bin = resolve_binary("ffmpeg")

        with tempfile.TemporaryDirectory(prefix="rollmetrics_reel_") as tmp_s:
            tmp_dir = Path(tmp_s)
            part_paths: list[Path] = []

            for i, seg in enumerate(segments, start=1):
                raw_vp = seg.get("video_path") or seg.get("path") or seg.get("source")
                if not raw_vp:
                    raise ValueError(f"Segment #{i} is missing 'video_path'.")
                src = Path(raw_vp).expanduser().resolve()
                meta = validate_video_file(src)

                start_sec = max(0.0, float(seg.get("start_sec", 0.0)))
                if "duration_sec" in seg and seg["duration_sec"] is not None:
                    dur_sec = max(0.3, float(seg["duration_sec"]))
                elif "end_sec" in seg and seg["end_sec"] is not None:
                    dur_sec = max(0.3, float(seg["end_sec"]) - start_sec)
                else:
                    dur_sec = max(0.5, min(8.0, meta.duration_sec - start_sec))

                title = str(seg.get("title") or f"Coaching Study #{i}")
                cue = str(seg.get("cue") or seg.get("subtitle") or seg.get("notes") or "")
                card_dur = max(1.0, float(seg.get("title_duration_sec", 1.8)))
                win_str = f"{_fmt_ts(start_sec)} - {_fmt_ts(start_sec + dur_sec)}"

                card_png = self._render_title_card_png(
                    index=i,
                    total=len(segments),
                    title=title,
                    cue=cue,
                    window_str=win_str,
                    out_png=tmp_dir / f"card_{i:02d}.png",
                )
                card_mp4 = tmp_dir / f"part_{i:02d}_a_card.mp4"
                card_cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(card_png),
                    "-t",
                    f"{card_dur:.2f}",
                    "-r",
                    "30",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    str(card_mp4),
                ]
                proc_card = subprocess.run(card_cmd, capture_output=True, text=True, check=False)
                if proc_card.returncode != 0 or not card_mp4.is_file():
                    raise RuntimeError(f"Title card encode failed: {proc_card.stderr.strip()}")
                part_paths.append(card_mp4)

                clip_mp4 = tmp_dir / f"part_{i:02d}_b_clip.mp4"
                vf = (
                    "scale=1280:720:force_original_aspect_ratio=decrease,"
                    "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=#0b0f14,setsar=1,fps=30"
                )
                clip_cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-ss",
                    f"{start_sec:.3f}",
                    "-t",
                    f"{dur_sec:.3f}",
                    "-i",
                    str(src),
                    "-vf",
                    vf,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    str(clip_mp4),
                ]
                proc_clip = subprocess.run(clip_cmd, capture_output=True, text=True, check=False)
                if proc_clip.returncode != 0 or not clip_mp4.is_file():
                    raise RuntimeError(f"Segment clip encode failed: {proc_clip.stderr.strip()}")
                part_paths.append(clip_mp4)

            concat_txt = tmp_dir / "concat.txt"
            concat_lines = [f"file '{p.as_posix()}'" for p in part_paths]
            concat_txt.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

            concat_cmd = [
                ffmpeg_bin,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_txt),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out),
            ]
            proc_concat = subprocess.run(concat_cmd, capture_output=True, text=True, check=False)
            if proc_concat.returncode != 0 or not out.is_file():
                raise RuntimeError(f"Study reel concat failed: {proc_concat.stderr.strip()}")

        return out
