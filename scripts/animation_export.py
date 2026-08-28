#!/usr/bin/env python3
"""Encode looping sticker animations as WebP and GIF."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

GIF_ALPHA_THRESHOLD = 128
GIF_ALPHA_CANDIDATES = (96, 128, 160, 192)


def frame_duration_ms(fps: float) -> int:
    if fps < 1:
        raise ValueError("fps must be at least 1")
    return max(20, round(1000 / fps))


def frame_durations_ms(frame_count: int, fps: float) -> list[int]:
    """Return millisecond frame durations whose total tracks the source clock."""
    if frame_count < 1:
        raise ValueError("frame_count must be at least 1")
    if fps < 1:
        raise ValueError("fps must be at least 1")
    boundaries = [round(index * 1000 / fps) for index in range(frame_count + 1)]
    return [max(1, right - left) for left, right in zip(boundaries, boundaries[1:])]


def choose_gif_alpha_threshold(
    images: list[Image.Image], candidates: tuple[int, ...] = GIF_ALPHA_CANDIDATES
) -> tuple[int, dict]:
    """Choose a binary-alpha cutoff that balances fringe retention and erosion."""
    if not images:
        raise ValueError("GIF threshold selection requires at least one frame")
    scores: list[dict] = []
    if not candidates or any(not 1 <= value <= 254 for value in candidates):
        raise ValueError("GIF alpha threshold candidates must be between 1 and 254")
    for threshold in candidates:
        fringe = erosion = residual_green = 0.0
        coverages = []
        for image in images:
            rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
            alpha = rgba[:, :, 3]
            visible = alpha >= threshold
            intended = alpha >= 128
            fringe += float(np.mean(visible & (alpha < 128)))
            erosion += float(np.count_nonzero(intended & ~visible) / max(1, np.count_nonzero(intended)))
            rgb = rgba[:, :, :3].astype(np.int16)
            green = rgb[:, :, 1] - np.maximum(rgb[:, :, 0], rgb[:, :, 2])
            residual_green += float(np.mean(visible & (green > 8)))
            coverages.append(float(np.mean(visible)))
        score = (
            2.0 * fringe / len(images)
            + 1.25 * erosion / len(images)
            + 2.0 * residual_green / len(images)
            + 0.25 * float(np.std(coverages))
        )
        scores.append({"threshold": threshold, "score": round(score, 8)})
    selected = min(scores, key=lambda item: (item["score"], abs(item["threshold"] - 128)))
    return int(selected["threshold"]), {"selected": selected, "candidates": scores}


def encode_webp(frame_paths: list[Path], target: Path, fps: float) -> None:
    if not frame_paths:
        raise ValueError("animation encoding requires at least one frame")
    frames = [Image.open(path).convert("RGBA") for path in frame_paths]
    try:
        frames[0].save(
            target,
            save_all=True,
            append_images=frames[1:],
            duration=frame_durations_ms(len(frames), fps),
            loop=0,
            lossless=True,
            method=4,
        )
    finally:
        for frame in frames:
            frame.close()


def encode_webp_images(
    images: list[Image.Image], target: Path, fps: float, *, lossless: bool = True,
    quality: int = 85, method: int = 4, loop: int = 0,
) -> None:
    if not images:
        raise ValueError("animation encoding requires at least one frame")
    converted = [image.convert("RGBA") for image in images]
    converted[0].save(
        target,
        save_all=True,
        append_images=converted[1:],
        duration=frame_durations_ms(len(converted), fps),
        loop=loop,
        lossless=lossless,
        quality=quality,
        method=method,
    )


def encode_gif(
    frame_paths: list[Path], target: Path, fps: float, alpha_threshold: int = GIF_ALPHA_THRESHOLD,
    max_colors: int = 256, dither: str = "none", optimize_delta_frames: bool = True,
    loop: int = 0,
) -> None:
    if not frame_paths:
        raise ValueError("animation encoding requires at least one frame")
    if not 1 <= alpha_threshold <= 254:
        raise ValueError("alpha_threshold must be between 1 and 254")
    if not 2 <= max_colors <= 256:
        raise ValueError("max_colors must be between 2 and 256")
    if dither not in {"none", "bayer"}:
        raise ValueError("dither must be none or bayer")
    if shutil.which("ffmpeg"):
        try:
            _encode_gif_ffmpeg(
                frame_paths, target, fps, alpha_threshold, max_colors, dither,
                optimize_delta_frames, loop,
            )
            return
        except (OSError, subprocess.CalledProcessError, RuntimeError):
            pass
    _encode_gif_pillow(
        frame_paths, target, frame_durations_ms(len(frame_paths), fps), alpha_threshold, max_colors, loop
    )


def encode_gif_images(
    images: list[Image.Image], target: Path, fps: float, alpha_threshold: int = GIF_ALPHA_THRESHOLD,
    max_colors: int = 256, dither: str = "none", optimize_delta_frames: bool = True,
    loop: int = 0,
) -> None:
    if not images:
        raise ValueError("animation encoding requires at least one frame")
    with tempfile.TemporaryDirectory(prefix="motion-sticker-gif-") as temporary:
        paths = []
        for index, image in enumerate(images, start=1):
            path = Path(temporary) / f"{index:04d}.png"
            image.convert("RGBA").save(path)
            paths.append(path)
        encode_gif(
            paths, target, fps, alpha_threshold, max_colors, dither, optimize_delta_frames, loop
        )


def _encode_gif_ffmpeg(
    frame_paths: list[Path], target: Path, fps: float, alpha_threshold: int,
    max_colors: int, dither: str, optimize_delta_frames: bool, loop: int,
) -> None:
    with tempfile.TemporaryDirectory(prefix="motion-sticker-gif-ff-") as temporary:
        root = Path(temporary)
        for index, source in enumerate(frame_paths, start=1):
            destination = root / f"{index:04d}.png"
            try:
                destination.hardlink_to(source)
            except OSError:
                shutil.copyfile(source, destination)
        palette_colors = min(255, max_colors)
        palette_dither = "none" if dither == "none" else "bayer:bayer_scale=3"
        diff_mode = ":diff_mode=rectangle" if optimize_delta_frames else ""
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            str(fps),
            "-i",
            str(root / "%04d.png"),
            "-vf",
            f"split[s0][s1];[s0]palettegen=reserve_transparent=1:max_colors={palette_colors}[p];"
            f"[s1][p]paletteuse=dither={palette_dither}{diff_mode}:alpha_threshold={alpha_threshold}",
            "-loop",
            str(loop),
            str(target),
        ]
        completed = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completed.returncode or not target.is_file() or target.stat().st_size <= 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "ffmpeg GIF encoding failed")


def _rgba_to_transparent_gif(
    image: Image.Image, alpha_threshold: int, max_colors: int
) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    mask = alpha.point(lambda value: 255 if value < alpha_threshold else 0)
    opaque_colors = max(1, min(255, max_colors - 1))
    quantized = rgba.convert("RGB").quantize(colors=opaque_colors, method=Image.Quantize.FASTOCTREE)
    palette = list(quantized.getpalette() or [])
    palette.extend([0] * max(0, 768 - len(palette)))
    pixels = bytearray(quantized.tobytes())
    for index, flag in enumerate(mask.tobytes()):
        if flag:
            pixels[index] = 255
    result = Image.frombytes("P", quantized.size, bytes(pixels))
    result.putpalette(palette[:768])
    result.info["transparency"] = 255
    return result


def _encode_gif_pillow(
    frame_paths: list[Path], target: Path, duration_ms: int | list[int], alpha_threshold: int,
    max_colors: int, loop: int,
) -> None:
    converted: list[Image.Image] = []
    originals: list[Image.Image] = []
    try:
        for path in frame_paths:
            rgba = Image.open(path).convert("RGBA")
            originals.append(rgba)
            converted.append(_rgba_to_transparent_gif(rgba, alpha_threshold, max_colors))
        converted[0].save(
            target,
            save_all=True,
            append_images=converted[1:],
            duration=duration_ms,
            loop=loop,
            disposal=2,
            transparency=255,
            optimize=False,
        )
    finally:
        for image in converted + originals:
            image.close()
