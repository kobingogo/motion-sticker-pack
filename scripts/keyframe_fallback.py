#!/usr/bin/env python3
"""Create simple seamless local keyframe motion when no video backend exists."""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image
from PIL import ImageOps

from animation_export import encode_gif_images, encode_webp_images
from process_emoji_grid import load_layout, median_background, remove_edge_background, tile_bounds
from output_safety import prepare_output, validate_output_boundaries
from manage_job_state import read_state, verify_state


RECIPES = ("bounce", "sway", "pulse", "shake", "float")


def transparent_tile(rgba: np.ndarray) -> Image.Image:
    if float(np.mean(rgba[:, :, 3] < 250)) >= 0.002:
        return Image.fromarray(rgba, mode="RGBA")
    return remove_edge_background(rgba[:, :, :3], median_background(rgba[:, :, :3]))


def transformed(base: Image.Image, recipe: str, phase: float) -> Image.Image:
    width, height = base.size
    angle = 0.0
    scale = 1.0
    x_shift = 0
    y_shift = 0
    if recipe == "bounce":
        y_shift = -round(height * 0.025 * (1.0 - math.cos(phase)))
    elif recipe == "sway":
        angle = 2.5 * math.sin(phase)
    elif recipe == "pulse":
        scale = 1.0 + 0.035 * math.sin(phase)
    elif recipe == "shake":
        x_shift = round(width * 0.012 * math.sin(phase * 2.0))
    elif recipe == "float":
        x_shift = round(width * 0.008 * math.sin(phase))
        y_shift = -round(height * 0.015 * (1.0 - math.cos(phase)))

    work = base
    if scale != 1.0:
        scaled = work.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.BICUBIC,
        )
        canvas = Image.new("RGBA", (width, height))
        canvas.alpha_composite(scaled, ((width - scaled.width) // 2, (height - scaled.height) // 2))
        work = canvas
    if angle:
        work = work.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)
    canvas = Image.new("RGBA", (width, height))
    canvas.alpha_composite(work, (x_shift, y_shift))
    return canvas


def add_motion_margin(base: Image.Image, fraction: float = 0.90) -> Image.Image:
    width, height = base.size
    contained = ImageOps.contain(
        base,
        (max(1, round(width * fraction)), max(1, round(height * fraction))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (width, height))
    canvas.alpha_composite(contained, ((width - contained.width) // 2, (height - contained.height) // 2))
    contained.close()
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True, help="hash-bound approved job state")
    parser.add_argument("--fps", type=int, default=6)
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--allow-low-confidence", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.fps <= 60 or not 0.25 <= args.duration <= 30:
        raise ValueError("fps must be 1-60 and duration must be 0.25-30 seconds")

    verify_state(read_state(args.state), args.image, args.layout)
    layout = load_layout(args.layout, args.allow_low_confidence)
    columns, rows, count = layout["columns"], layout["rows"], layout["count"]
    args.output = validate_output_boundaries(args.output, [args.image, args.layout, args.state])
    with Image.open(args.image) as source:
        if source.width * source.height > 64_000_000:
            raise ValueError("input image exceeds the 64 megapixel safety limit")
        rgba = np.asarray(source.convert("RGBA"), dtype=np.uint8)
    height, width, _ = rgba.shape
    frame_count = max(2, round(args.fps * args.duration))
    digits = max(2, len(str(count)))
    prepare_output(args.output, overwrite=args.overwrite)
    outputs: list[str] = []
    cell_reports = []

    with tempfile.TemporaryDirectory(prefix="motion-sticker-pack-keyframes-") as temporary:
        root = Path(temporary)
        for tile in range(count):
            row, column = divmod(tile, columns)
            x0, x1 = tile_bounds(width, column, columns)
            y0, y1 = tile_bounds(height, row, rows)
            raw_base = transparent_tile(rgba[y0:y1, x0:x1])
            base = add_motion_margin(raw_base)
            raw_base.close()
            recipe = RECIPES[tile % len(RECIPES)]
            frames = [
                transformed(base, recipe, 2.0 * math.pi * index / frame_count)
                for index in range(frame_count)
            ]
            stem = f"{tile + 1:0{digits}d}"
            png_name, webp_name, gif_name = f"{stem}.png", f"{stem}.webp", f"{stem}.gif"
            base.save(args.output / png_name, optimize=True)
            encode_webp_images(frames, args.output / webp_name, args.fps)
            encode_gif_images(frames, args.output / gif_name, args.fps)
            outputs.extend([webp_name, gif_name, png_name])
            cell_reports.append({"id": stem, "recipe": recipe, "frames": frame_count})
            for frame in frames:
                frame.close()
            base.close()

    layout_report = {
        "detected_layout": {
            "columns": columns,
            "rows": rows,
            "count": count,
            "confidence": layout["confidence"],
        }
    }
    (args.output / "layout.json").write_text(
        json.dumps(layout_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = {
        "version": 1,
        "mode": "keyframe-local",
        "source": str(args.image.resolve()),
        "output_fps": args.fps,
        "duration_seconds": args.duration,
        "cells": cell_reports,
        "warnings": [
            "local fallback animates each whole sticker with small affine keyframes; it does not synthesize new limb poses"
        ],
        "outputs": outputs + ["layout.json", "processing.json", "sticker-pack.zip"],
    }
    (args.output / "processing.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with zipfile.ZipFile(args.output / "sticker-pack.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in outputs + ["layout.json", "processing.json"]:
            bundle.write(args.output / name, arcname=name)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
