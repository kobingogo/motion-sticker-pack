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
from artifact_manifest import record_artifact
from output_profile import DEFAULT_OUTPUT_FPS, DEFAULT_OUTPUT_SIZE, MAX_OUTPUT_SIZE
from process_emoji_grid import GridBoundaryError, load_layout, median_background, remove_edge_background, tile_bounds, validate_encoded_animation, write_preview
from output_safety import begin_output_transaction
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


def fixed_canvas(base: Image.Image, size: int) -> Image.Image:
    contained = ImageOps.contain(base, (size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size))
    canvas.alpha_composite(contained, ((size - contained.width) // 2, (size - contained.height) // 2))
    contained.close()
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True, help="hash-bound approved job state")
    parser.add_argument("--manifest", type=Path, help="artifact-manifest.json for hash lineage")
    parser.add_argument("--fps", type=int, default=DEFAULT_OUTPUT_FPS)
    parser.add_argument("--size", type=int, default=DEFAULT_OUTPUT_SIZE)
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--allow-low-confidence", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.fps <= 60 or not 0.25 <= args.duration <= 30:
        raise ValueError("fps must be 1-60 and duration must be 0.25-30 seconds")
    if not 64 <= args.size <= MAX_OUTPUT_SIZE:
        raise ValueError(f"size must be between 64 and {MAX_OUTPUT_SIZE}")

    verify_state(read_state(args.state), args.image, args.layout)
    layout = load_layout(args.layout, args.allow_low_confidence)
    columns, rows, count = layout["columns"], layout["rows"], layout["count"]
    output_transaction = begin_output_transaction(
        args.output,
        overwrite=args.overwrite,
        protected_paths=[args.image, args.layout, args.state, *([args.manifest] if args.manifest else [])],
    )
    args.output = output_transaction.output
    with Image.open(args.image) as source:
        if source.width * source.height > 64_000_000:
            raise ValueError("input image exceeds the 64 megapixel safety limit")
        rgba = np.asarray(source.convert("RGBA"), dtype=np.uint8)
    height, width, _ = rgba.shape
    # Three phase samples avoid a false single-frame animation when a recipe's
    # sine/cosine happens to be identical at phase 0 and π.
    frame_count = max(3, round(args.fps * args.duration))
    digits = max(2, len(str(count)))
    outputs: list[str] = []
    cell_reports = []
    preview_pngs: dict[int, Path] = {}

    with tempfile.TemporaryDirectory(prefix="motion-sticker-pack-keyframes-") as temporary:
        root = Path(temporary)
        for tile in range(count):
            row, column = divmod(tile, columns)
            x0, x1 = tile_bounds(width, column, columns)
            y0, y1 = tile_bounds(height, row, rows)
            raw_base = transparent_tile(rgba[y0:y1, x0:x1])
            normalized_base = fixed_canvas(raw_base, args.size)
            raw_base.close()
            base = add_motion_margin(normalized_base)
            normalized_base.close()
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
            try:
                encoded_qc = {
                    "webp": validate_encoded_animation(args.output / webp_name, expected_size=(args.size, args.size), output_fps=args.fps),
                    "gif": validate_encoded_animation(args.output / gif_name, expected_size=(args.size, args.size), output_fps=args.fps),
                }
            except GridBoundaryError as exc:
                raise ValueError(f"encoded light-motion output failed QC for {stem}: {exc}") from exc
            outputs.extend([webp_name, gif_name, png_name])
            preview_pngs[tile] = args.output / png_name
            cell_reports.append({"id": stem, "recipe": recipe, "frames": frame_count, "encoded_qc": encoded_qc})
            for frame in frames:
                frame.close()
            base.close()

    if preview_pngs:
        write_preview(args.output / "preview.png", preview_pngs, columns, rows, (args.size, args.size))
        outputs.append("preview.png")
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
        "mode": "light-motion-local",
        "source": str(args.image.resolve()),
        "output_fps": args.fps,
        "output_size": [args.size, args.size],
        "duration_seconds": args.duration,
        "cells": cell_reports,
        "warnings": [
            "zero-generation-cost light motion animates each whole sticker with small affine keyframes; it does not synthesize new limb poses"
        ],
        "outputs": outputs + ["layout.json", "processing.json", "sticker-pack.zip"],
    }
    (args.output / "processing.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with zipfile.ZipFile(args.output / "sticker-pack.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in outputs + ["layout.json", "processing.json"]:
            bundle.write(args.output / name, arcname=name)
    output_transaction.commit()
    if args.manifest:
        manifest = args.manifest.expanduser().resolve()
        source_ids = [
            record_artifact(manifest, path, kind=kind, stage="light-motion-rendered", workspace=manifest.parent)
            for path, kind in (
                (args.image, "static-sheet"),
                (args.layout, "layout"),
                (args.state, "approval-state"),
            )
        ]
        output_ids = [
            record_artifact(
                manifest,
                args.output / name,
                kind="sticker-output" if Path(name).suffix.lower() in {".png", ".webp", ".gif"} else "processing-report",
                stage="light-motion-rendered",
                dependencies=source_ids,
                workspace=manifest.parent,
            )
            for name in outputs + ["layout.json", "processing.json"]
        ]
        record_artifact(
            manifest,
            args.output / "sticker-pack.zip",
            kind="sticker-pack",
            stage="light-motion-rendered",
            dependencies=output_ids,
            workspace=manifest.parent,
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
