#!/usr/bin/env python3
"""Encode looping sticker animations as WebP and GIF."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image


def frame_duration_ms(fps: int) -> int:
    if fps < 1:
        raise ValueError("fps must be at least 1")
    return max(20, round(1000 / fps))


def encode_webp(frame_paths: list[Path], target: Path, fps: int) -> None:
    if not frame_paths:
        raise ValueError("animation encoding requires at least one frame")
    frames = [Image.open(path).convert("RGBA") for path in frame_paths]
    try:
        frames[0].save(
            target,
            save_all=True,
            append_images=frames[1:],
            duration=round(1000 / fps),
            loop=0,
            lossless=True,
            method=4,
        )
    finally:
        for frame in frames:
            frame.close()


def encode_webp_images(images: list[Image.Image], target: Path, fps: int) -> None:
    if not images:
        raise ValueError("animation encoding requires at least one frame")
    converted = [image.convert("RGBA") for image in images]
    converted[0].save(
        target,
        save_all=True,
        append_images=converted[1:],
        duration=round(1000 / fps),
        loop=0,
        lossless=True,
        method=4,
    )


def encode_gif(frame_paths: list[Path], target: Path, fps: int) -> None:
    if not frame_paths:
        raise ValueError("animation encoding requires at least one frame")
    if shutil.which("ffmpeg"):
        try:
            _encode_gif_ffmpeg(frame_paths, target, fps)
            return
        except (OSError, subprocess.CalledProcessError, RuntimeError):
            pass
    _encode_gif_pillow(frame_paths, target, frame_duration_ms(fps))


def encode_gif_images(images: list[Image.Image], target: Path, fps: int) -> None:
    if not images:
        raise ValueError("animation encoding requires at least one frame")
    with tempfile.TemporaryDirectory(prefix="motion-sticker-gif-") as temporary:
        paths = []
        for index, image in enumerate(images, start=1):
            path = Path(temporary) / f"{index:04d}.png"
            image.convert("RGBA").save(path)
            paths.append(path)
        encode_gif(paths, target, fps)


def _encode_gif_ffmpeg(frame_paths: list[Path], target: Path, fps: int) -> None:
    with tempfile.TemporaryDirectory(prefix="motion-sticker-gif-ff-") as temporary:
        root = Path(temporary)
        for index, source in enumerate(frame_paths, start=1):
            destination = root / f"{index:04d}.png"
            try:
                destination.hardlink_to(source)
            except OSError:
                shutil.copyfile(source, destination)
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
            "split[s0][s1];[s0]palettegen=reserve_transparent=1:max_colors=255[p];"
            "[s1][p]paletteuse=dither=bayer:bayer_scale=4:alpha_threshold=32",
            "-loop",
            "0",
            str(target),
        ]
        completed = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completed.returncode or not target.is_file() or target.stat().st_size <= 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "ffmpeg GIF encoding failed")


def _rgba_to_transparent_gif(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    mask = alpha.point(lambda value: 255 if value < 32 else 0)
    quantized = rgba.convert("RGB").quantize(colors=255, method=Image.Quantize.FASTOCTREE)
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


def _encode_gif_pillow(frame_paths: list[Path], target: Path, duration_ms: int) -> None:
    converted: list[Image.Image] = []
    originals: list[Image.Image] = []
    try:
        for path in frame_paths:
            rgba = Image.open(path).convert("RGBA")
            originals.append(rgba)
            converted.append(_rgba_to_transparent_gif(rgba))
        converted[0].save(
            target,
            save_all=True,
            append_images=converted[1:],
            duration=duration_ms,
            loop=0,
            disposal=2,
            transparency=255,
            optimize=False,
        )
    finally:
        for image in converted + originals:
            image.close()
