#!/usr/bin/env python3
"""Split a detected grid video, create real alpha, Animated WebP/GIF, PNG, and ZIP."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

from animation_export import encode_gif, encode_webp
from output_safety import prepare_output, validate_archive_name


def load_layout(path: Path, allow_low_confidence: bool = False) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    detected = data.get("detected_layout", data)
    columns = int(detected["columns"])
    rows = int(detected["rows"])
    count = columns * rows
    if int(detected.get("count", count)) != count:
        raise ValueError("layout count does not equal columns * rows")
    confidence = detected.get("confidence")
    if confidence is not None and float(confidence) < 0.75 and not allow_low_confidence:
        raise ValueError(
            f"layout confidence {confidence} is below 0.75; inspect the overlay or pass --allow-low-confidence"
        )
    return {"columns": columns, "rows": rows, "count": count, "confidence": confidence, "source": data}


def probe_video(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise FileNotFoundError(f"input video does not exist: {path}")
    for executable in ("ffmpeg", "ffprobe"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"required executable is not installed: {executable}")
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(path),
    ]
    data = json.loads(subprocess.check_output(command, text=True))
    if not data.get("streams"):
        raise RuntimeError("input has no video stream")
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"])


def extract_rgba_frames(path: Path, width: int, height: int, fps: int):
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-vf",
        f"fps={fps}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    frame_bytes = width * height * 4
    assert process.stdout is not None
    try:
        while True:
            raw = process.stdout.read(frame_bytes)
            if not raw:
                break
            if len(raw) != frame_bytes:
                raise RuntimeError("ffmpeg returned a truncated raw video frame")
            yield np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4)).copy()
    finally:
        process.stdout.close()
        return_code = process.wait()
        if return_code:
            raise RuntimeError(f"ffmpeg frame extraction failed with exit code {return_code}")


def tile_bounds(size: int, index: int, parts: int) -> tuple[int, int]:
    return size * index // parts, size * (index + 1) // parts


def median_background(rgb: np.ndarray) -> np.ndarray:
    height, width, _ = rgb.shape
    band = max(2, min(10, min(height, width) // 24))
    samples = np.concatenate(
        [
            rgb[:band, :band].reshape(-1, 3),
            rgb[:band, -band:].reshape(-1, 3),
            rgb[-band:, :band].reshape(-1, 3),
            rgb[-band:, -band:].reshape(-1, 3),
            rgb[:band, :].reshape(-1, 3),
            rgb[-band:, :].reshape(-1, 3),
            rgb[:, :band].reshape(-1, 3),
            rgb[:, -band:].reshape(-1, 3),
        ],
        axis=0,
    )
    return np.median(samples, axis=0).astype(np.float32)


def remove_edge_background(
    rgb: np.ndarray,
    background: np.ndarray | None = None,
    hard_tolerance: float = 38.0,
    soft_tolerance: float = 72.0,
) -> Image.Image:
    """Remove only background-like pixels connected to the crop boundary."""
    height, width, _ = rgb.shape
    background = median_background(rgb) if background is None else background.astype(np.float32)
    distance = np.sqrt(np.sum((rgb.astype(np.float32) - background) ** 2, axis=2))
    candidate = distance <= soft_tolerance
    connected = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        if candidate[0, x]:
            queue.append((0, x))
        if candidate[height - 1, x]:
            queue.append((height - 1, x))
    for y in range(height):
        if candidate[y, 0]:
            queue.append((y, 0))
        if candidate[y, width - 1]:
            queue.append((y, width - 1))
    while queue:
        y, x = queue.popleft()
        if connected[y, x] or not candidate[y, x]:
            continue
        connected[y, x] = True
        if y:
            queue.append((y - 1, x))
        if y + 1 < height:
            queue.append((y + 1, x))
        if x:
            queue.append((y, x - 1))
        if x + 1 < width:
            queue.append((y, x + 1))

    alpha = np.full((height, width), 255, dtype=np.uint8)
    hard = connected & (distance <= hard_tolerance)
    soft = connected & ~hard
    alpha[hard] = 0
    alpha[soft] = np.clip(
        255.0 * (distance[soft] - hard_tolerance) / max(1.0, soft_tolerance - hard_tolerance),
        0,
        255,
    ).astype(np.uint8)
    return Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA")


def meaningful_alpha(alpha: np.ndarray) -> bool:
    return bool(np.mean(alpha < 250) >= 0.002)


def frame_qc(rgba: np.ndarray) -> tuple[float, float]:
    alpha = rgba[:, :, 3]
    coverage = float(np.mean(alpha >= 32))
    border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
    border_coverage = float(np.mean(border >= 32))
    return coverage, border_coverage


def package_outputs(output: Path, names: list[str], zip_name: str) -> Path:
    archive = output / zip_name
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in names:
            bundle.write(output / name, arcname=name)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=6)
    parser.add_argument("--hard-tol", type=float, default=38.0)
    parser.add_argument("--soft-tol", type=float, default=72.0)
    parser.add_argument("--background-mode", choices=("auto", "preserve-alpha", "edge-color"), default="auto")
    parser.add_argument("--keep-frames", action="store_true")
    parser.add_argument("--allow-low-confidence", action="store_true")
    parser.add_argument("--zip-name", default="sticker-pack.zip")
    parser.add_argument("--max-frames", type=int, default=600)
    parser.add_argument("--max-input-bytes", type=int, default=1024 * 1024 * 1024)
    parser.add_argument("--max-pixels", type=int, default=16_777_216)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.fps <= 60:
        raise ValueError("fps must be between 1 and 60")
    if not 0 <= args.hard_tol < args.soft_tol <= 442:
        raise ValueError("tolerances must satisfy 0 <= hard < soft <= 442")
    if args.max_frames < 2:
        raise ValueError("max-frames must be at least 2")
    if args.max_input_bytes < 1 or args.video.stat().st_size > args.max_input_bytes:
        raise ValueError("input video exceeds max-input-bytes")
    args.zip_name = validate_archive_name(args.zip_name)

    layout = load_layout(args.layout, args.allow_low_confidence)
    columns, rows, count = layout["columns"], layout["rows"], layout["count"]
    width, height = probe_video(args.video)
    if width * height > args.max_pixels:
        raise ValueError(f"video frame exceeds max-pixels ({width}x{height})")
    prepare_output(args.output, overwrite=args.overwrite, archive_names={args.zip_name})
    digits = max(2, len(str(count)))

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_frames:
        frames_root = args.output / "frames"
        frames_root.mkdir(exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="motion-sticker-pack-")
        frames_root = Path(temporary.name)
    for index in range(1, count + 1):
        (frames_root / f"{index:0{digits}d}").mkdir(exist_ok=True)

    backgrounds: list[np.ndarray | None] = [None] * count
    alpha_method: list[str] = ["unknown"] * count
    coverages: list[list[float]] = [[] for _ in range(count)]
    border_coverages: list[list[float]] = [[] for _ in range(count)]
    first_arrays: list[np.ndarray | None] = [None] * count
    last_arrays: list[np.ndarray | None] = [None] * count
    frame_count = 0

    try:
        for frame_count, rgba in enumerate(extract_rgba_frames(args.video, width, height, args.fps), start=1):
            if frame_count > args.max_frames:
                raise RuntimeError(f"video exceeds max-frames ({args.max_frames})")
            if frame_count == 1 and args.background_mode == "preserve-alpha":
                missing = []
                for tile in range(count):
                    row, column = divmod(tile, columns)
                    x0, x1 = tile_bounds(width, column, columns)
                    y0, y1 = tile_bounds(height, row, rows)
                    if not meaningful_alpha(rgba[y0:y1, x0:x1, 3]):
                        missing.append(tile + 1)
                if missing:
                    raise ValueError(f"preserve-alpha requested but cells have no meaningful alpha: {missing}")
            for tile in range(count):
                row, column = divmod(tile, columns)
                x0, x1 = tile_bounds(width, column, columns)
                y0, y1 = tile_bounds(height, row, rows)
                crop = rgba[y0:y1, x0:x1]
                use_alpha = args.background_mode == "preserve-alpha" or (
                    args.background_mode == "auto" and meaningful_alpha(crop[:, :, 3])
                )
                if use_alpha:
                    image = Image.fromarray(crop, mode="RGBA")
                    alpha_method[tile] = "source-alpha"
                else:
                    if backgrounds[tile] is None:
                        backgrounds[tile] = median_background(crop[:, :, :3])
                    image = remove_edge_background(
                        crop[:, :, :3], backgrounds[tile], args.hard_tol, args.soft_tol
                    )
                    alpha_method[tile] = "edge-connected-fixed-color"
                array = np.asarray(image, dtype=np.uint8)
                coverage, border_coverage = frame_qc(array)
                coverages[tile].append(coverage)
                border_coverages[tile].append(border_coverage)
                first_arrays[tile] = array.copy() if first_arrays[tile] is None else first_arrays[tile]
                last_arrays[tile] = array.copy()
                image.save(frames_root / f"{tile + 1:0{digits}d}" / f"{frame_count:04d}.png", optimize=True)

        if frame_count == 0:
            raise RuntimeError("no video frames were extracted")

        outputs: list[str] = []
        cell_reports: list[dict] = []
        warnings: list[str] = []
        for tile in range(count):
            stem = f"{tile + 1:0{digits}d}"
            paths = sorted((frames_root / stem).glob("*.png"))
            png_name, webp_name, gif_name = f"{stem}.png", f"{stem}.webp", f"{stem}.gif"
            shutil.copyfile(paths[0], args.output / png_name)
            encode_webp(paths, args.output / webp_name, args.fps)
            encode_gif(paths, args.output / gif_name, args.fps)
            outputs.extend([webp_name, gif_name, png_name])
            first = first_arrays[tile]
            last = last_arrays[tile]
            assert first is not None and last is not None
            loop_difference = float(np.mean(np.abs(first.astype(np.float32) - last.astype(np.float32))) / 255.0)
            coverage_range = max(coverages[tile]) - min(coverages[tile])
            cell_warnings = []
            if max(border_coverages[tile]) > 0.03:
                cell_warnings.append("foreground-touches-cell-boundary")
            if coverage_range > 0.12:
                cell_warnings.append("alpha-coverage-varies-across-frames")
            if loop_difference > 0.12:
                cell_warnings.append("loop-end-differs-from-start")
            if min(coverages[tile]) < 0.008:
                cell_warnings.append("cell-empty-or-nearly-empty")
            if max(coverages[tile]) > 0.95:
                cell_warnings.append("cell-nearly-full-frame-foreground")
            warnings.extend(f"{stem}:{warning}" for warning in cell_warnings)
            cell_reports.append(
                {
                    "id": stem,
                    "alpha_method": alpha_method[tile],
                    "alpha_coverage_min": round(min(coverages[tile]), 6),
                    "alpha_coverage_max": round(max(coverages[tile]), 6),
                    "border_coverage_max": round(max(border_coverages[tile]), 6),
                    "loop_difference": round(loop_difference, 6),
                    "warnings": cell_warnings,
                }
            )

        normalized_layout = {
            "source_layout": str(args.layout.resolve()),
            "detected_layout": {
                "columns": columns,
                "rows": rows,
                "count": count,
                "confidence": layout["confidence"],
            },
        }
        (args.output / "layout.json").write_text(
            json.dumps(normalized_layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        report = {
            "version": 1,
            "source": str(args.video.resolve()),
            "source_size": {"width": width, "height": height},
            "detected_layout": normalized_layout["detected_layout"],
            "output_fps": args.fps,
            "frames_per_animation": frame_count,
            "cells": cell_reports,
            "warnings": warnings,
            "outputs": outputs + ["layout.json", "processing.json", args.zip_name],
        }
        (args.output / "processing.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        package_outputs(args.output, outputs + ["layout.json", "processing.json"], args.zip_name)
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if temporary is not None:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
