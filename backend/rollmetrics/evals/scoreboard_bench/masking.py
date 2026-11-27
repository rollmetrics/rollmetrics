"""Scoreboard crop isolation, frame/video blackout masking, and automated leakage verification."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from rollmetrics.evals.scoreboard_bench.schema import (
    OVERLAY_PRESETS,
    NormalizedBox,
    OverlayPreset,
    ScoreboardLeakageError,
)


def resolve_preset(preset_name: str) -> OverlayPreset:
    if preset_name not in OVERLAY_PRESETS:
        raise KeyError(f"Unknown overlay preset '{preset_name}'. Available: {sorted(OVERLAY_PRESETS)}")
    return OVERLAY_PRESETS[preset_name]


def _find_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        cand = Path(prefix) / name
        if cand.exists():
            return str(cand)
    return None


def crop_scoreboard_only(
    img: Image.Image,
    preset_name: str = "flograppling_lower_bar",
) -> Image.Image:
    """Crops strictly to the scoreboard strip, discarding all mat and athlete pixels."""
    preset = resolve_preset(preset_name)
    w, h = img.size
    px0, py0, px1, py1 = preset.scoreboard_crop.to_pixel_rect(w, h)
    return img.convert("RGB").crop((px0, py0, px1, py1))


def mask_frame_scoreboard(
    img: Image.Image,
    preset_name: str = "flograppling_lower_bar",
    extra_boxes: list[NormalizedBox] | None = None,
) -> Image.Image:
    """Applies solid #000000 blackout masks over all scoreboard, name, and overlay regions."""
    preset = resolve_preset(preset_name)
    masked = img.convert("RGB").copy()
    draw = ImageDraw.Draw(masked)
    w, h = masked.size
    for box in [*preset.mask_boxes, *(extra_boxes or [])]:
        px0, py0, px1, py1 = box.to_pixel_rect(w, h)
        draw.rectangle((px0, py0, px1, py1), fill=(0, 0, 0))
    return masked


def _glyph_edge_density(rgb_arr: np.ndarray) -> float:
    """Measures high-contrast glyph stroke density inside a cropped scoreboard array."""
    if rgb_arr.size == 0 or rgb_arr.shape[0] < 3 or rgb_arr.shape[1] < 3:
        return 0.0
    luma = (
        0.299 * rgb_arr[:, :, 0].astype(np.float32)
        + 0.587 * rgb_arr[:, :, 1].astype(np.float32)
        + 0.114 * rgb_arr[:, :, 2].astype(np.float32)
    )
    dx = np.abs(np.diff(luma, axis=1))
    dy = np.abs(np.diff(luma, axis=0))
    strong_x = float(np.mean(dx > 18.0))
    strong_y = float(np.mean(dy > 18.0))
    return round(0.5 * (strong_x + strong_y), 6)


def _optional_tesseract_ocr(crop_img: Image.Image) -> str:
    tess = _find_binary("tesseract")
    if not tess:
        return ""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
        crop_img.save(tmp.name)
        proc = subprocess.run(
            [tess, tmp.name, "stdout", "--psm", "6"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
        return (proc.stdout or "").strip()


def assert_frame_leakage_free(
    masked_img: Image.Image,
    preset_name: str = "flograppling_lower_bar",
    allow_codec_luma_floor: float = 20.0,
) -> dict[str, Any]:
    """Verifies a masked frame contains zero scoreboard luminance, glyph edges, or OCR characters."""
    preset = resolve_preset(preset_name)
    rgb = masked_img.convert("RGB")
    w, h = rgb.size

    # 1. Check scoreboard crop specifically (must lie completely inside blackout mask)
    sb_crop = crop_scoreboard_only(rgb, preset_name=preset_name)
    sb_arr = np.asarray(sb_crop, dtype=np.uint8)
    max_val = float(np.max(sb_arr)) if sb_arr.size else 0.0
    std_val = float(np.std(sb_arr)) if sb_arr.size else 0.0
    edge_density = _glyph_edge_density(sb_arr)
    ocr_text = _optional_tesseract_ocr(sb_crop)

    if max_val > allow_codec_luma_floor or std_val > 2.0 or edge_density > 0.001 or ocr_text:
        raise ScoreboardLeakageError(
            f"Scoreboard leakage detected in '{preset_name}': max_val={max_val:.1f}, "
            f"std={std_val:.2f}, glyph_edge_density={edge_density:.5f}, ocr={ocr_text!r}"
        )

    # 2. Check all mask boxes (inset by 8px to clear H.264 8x8 yuv420p DCT boundary ringing)
    inset = 8 if allow_codec_luma_floor > 0.0 else 0
    for idx, box in enumerate(preset.mask_boxes):
        px0, py0, px1, py1 = box.to_pixel_rect(w, h)
        ix0, iy0 = min(px1 - 1, px0 + inset), min(py1 - 1, py0 + inset)
        ix1, iy1 = max(ix0 + 1, px1 - inset), max(iy0 + 1, py1 - inset)
        sub_arr = np.asarray(rgb.crop((ix0, iy0, ix1, iy1)), dtype=np.uint8)
        b_max = float(np.max(sub_arr)) if sub_arr.size else 0.0
        b_edges = _glyph_edge_density(sub_arr)
        if b_max > allow_codec_luma_floor or b_edges > 0.001:
            raise ScoreboardLeakageError(
                f"Mask box #{idx} leakage in '{preset_name}': max={b_max:.1f}, edges={b_edges:.5f}"
            )

    return {
        "leakage_free": True,
        "preset": preset_name,
        "scoreboard_max_pixel": max_val,
        "scoreboard_std_pixel": round(std_val, 4),
        "glyph_edge_density": edge_density,
        "ocr_text": ocr_text,
    }


def _project_box_into_crop(box: NormalizedBox, crop: NormalizedBox) -> NormalizedBox | None:
    """Projects a normalized mask box into post-crop coordinates; returns None if cropped away."""
    ix0 = max(box.x0, crop.x0)
    iy0 = max(box.y0, crop.y0)
    ix1 = min(box.x1, crop.x1)
    iy1 = min(box.y1, crop.y1)
    if ix1 <= ix0 + 0.005 or iy1 <= iy0 + 0.005:
        return None
    cw = crop.x1 - crop.x0
    ch = crop.y1 - crop.y0
    return NormalizedBox(
        x0=max(0.0, min(1.0, (ix0 - crop.x0) / cw)),
        y0=max(0.0, min(1.0, (iy0 - crop.y0) / ch)),
        x1=max(0.0, min(1.0, (ix1 - crop.x0) / cw)),
        y1=max(0.0, min(1.0, (iy1 - crop.y0) / ch)),
    )


def create_masked_video_clip(
    src_video: Path,
    start_sec: float,
    end_sec: float,
    dst_video: Path,
    preset_name: str = "flograppling_lower_bar",
    target_height: int = 480,
    crop_box: NormalizedBox | None = None,
) -> dict[str, Any]:
    """Renders a scoreboard-masked MP4 clip via ffmpeg drawbox (+ optional tight mat crop) and verifies zero leakage."""
    ffmpeg = _find_binary("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg binary is required to render scoreboard-masked video clips.")

    preset = resolve_preset(preset_name)
    dst_video.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1.0, end_sec - start_sec)

    # Always blackout all scoreboard boxes in full-frame coordinates BEFORE any mat crop
    vf_parts: list[str] = []
    for box in preset.mask_boxes:
        vf_parts.append(
            f"drawbox=x='iw*{box.x0:.4f}':y='ih*{box.y0:.4f}':"
            f"w='iw*{(box.x1 - box.x0):.4f}':h='ih*{(box.y1 - box.y0):.4f}':"
            "color=black@1.0:t=fill"
        )
    if crop_box is not None:
        cw = crop_box.x1 - crop_box.x0
        ch = crop_box.y1 - crop_box.y0
        vf_parts.append(
            f"crop=w='trunc(iw*{cw:.4f}/2)*2':h='trunc(ih*{ch:.4f}/2)*2':"
            f"x='trunc(iw*{crop_box.x0:.4f}/2)*2':y='trunc(ih*{crop_box.y0:.4f}/2)*2'"
        )
    vf_parts.append(f"scale=-2:{target_height}")
    vf_chain = ",".join(vf_parts)

    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{max(0.0, start_sec):.2f}",
            "-t",
            f"{duration:.2f}",
            "-i",
            str(src_video),
            "-vf",
            vf_chain,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            str(dst_video),
        ],
        check=True,
        capture_output=True,
    )

    # Extract 3 audit frames (10%, 50%, 90%) from the rendered MP4 and enforce leakage check
    probe_offsets = [0.1 * duration, 0.5 * duration, 0.9 * duration]
    audit_checks = []
    for i, off in enumerate(probe_offsets):
        tmp_jpg = dst_video.parent / f".leak_audit_{dst_video.stem}_{i}.png"
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    f"{off:.2f}",
                    "-i",
                    str(dst_video),
                    "-frames:v",
                    "1",
                    str(tmp_jpg),
                ],
                check=True,
                capture_output=True,
            )
            with Image.open(tmp_jpg) as frame_im:
                if crop_box is None:
                    audit_checks.append(assert_frame_leakage_free(frame_im, preset_name=preset_name))
                else:
                    rgb = frame_im.convert("RGB")
                    fw, fh = rgb.size
                    for b_idx, mbox in enumerate(preset.mask_boxes):
                        proj = _project_box_into_crop(mbox, crop_box)
                        if proj is None:
                            continue
                        px0, py0, px1, py1 = proj.to_pixel_rect(fw, fh)
                        ix0, iy0 = min(px1 - 1, px0 + 8), min(py1 - 1, py0 + 8)
                        ix1, iy1 = max(ix0 + 1, px1 - 8), max(iy0 + 1, py1 - 8)
                        sub_arr = np.asarray(rgb.crop((ix0, iy0, ix1, iy1)), dtype=np.uint8)
                        b_max = float(np.max(sub_arr)) if sub_arr.size else 0.0
                        b_edges = _glyph_edge_density(sub_arr)
                        if b_max > 20.0 or b_edges > 0.001:
                            raise ScoreboardLeakageError(
                                f"Post-crop mask box #{b_idx} leakage in '{preset_name}': "
                                f"max={b_max:.1f}, edges={b_edges:.5f}"
                            )
                    audit_checks.append({"leakage_free": True, "preset": preset_name, "cropped": True})
        finally:
            tmp_jpg.unlink(missing_ok=True)

    return {
        "masked_video_path": str(dst_video),
        "duration_sec": round(duration, 2),
        "audit_frames_verified": len(audit_checks),
        "leakage_free": True,
        "crop_box": crop_box.model_dump() if crop_box is not None else None,
    }


render_masked_video = create_masked_video_clip


def extract_masked_anchor_frame(
    video_path: Path,
    anchor_sec: float,
    preset_name: str = "flograppling_lower_bar",
    athlete_a_appearance: str = "Blue Gi",
    athlete_b_appearance: str = "White Gi",
) -> bytes | None:
    """Extracts a 100% scoreboard-masked t=2s anchor frame annotated with A (LEFT) / B (RIGHT) badges."""
    import io

    ffmpeg = _find_binary("ffmpeg")
    if not ffmpeg:
        return None
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{max(0.0, anchor_sec):.2f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=640:-2",
            "-q:v",
            "3",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None

    with Image.open(io.BytesIO(proc.stdout)) as raw_im:
        masked = mask_frame_scoreboard(raw_im, preset_name=preset_name)
        assert_frame_leakage_free(masked, preset_name=preset_name, allow_codec_luma_floor=0.0)
        w, _h = masked.size
        draw = ImageDraw.Draw(masked)
        # Top identity anchor bar (never overlaps bottom scoreboard mask)
        draw.rectangle((0, 0, w, 34), fill=(9, 12, 16))
        draw.rectangle((4, 4, w // 2 - 4, 30), fill=(16, 120, 72))
        draw.rectangle((w // 2 + 4, 4, w - 4, 30), fill=(30, 64, 140))
        a_lbl = f"A (LEFT / GREEN-RED): {athlete_a_appearance}"[:42]
        b_lbl = f"B (RIGHT / YELLOW-BLUE): {athlete_b_appearance}"[:42]
        draw.text((10, 10), a_lbl, fill=(248, 250, 252))
        draw.text((w // 2 + 10, 10), b_lbl, fill=(248, 250, 252))
        buf = io.BytesIO()
        masked.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
