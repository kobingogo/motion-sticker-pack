#!/usr/bin/env python3
"""Assemble per-sticker key-pose PNGs into seamless Animated WebP and GIF files."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from animation_export import encode_gif_images, encode_webp_images
from keyframe_fallback import transparent_tile
from output_safety import prepare_output, validate_output_boundaries
from process_emoji_grid import load_layout
from manage_job_state import read_state, verify_state


def natural_key(path: Path) -> list[tuple[int, object]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", path.name)
        if part
    ]


def normalize_pose(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    cleaned = transparent_tile(rgba)
    canvas = Image.new("RGBA", size)
    canvas.alpha_composite(cleaned, ((size[0] - cleaned.width) // 2, (size[1] - cleaned.height) // 2))
    cleaned.close()
    return canvas


def loop_indices(count: int) -> list[int]:
    if count < 2:
        raise ValueError("each sticker requires at least two key poses")
    return list(range(count)) + list(range(count - 2, 0, -1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("keyposes", type=Path, help="directory containing one numbered subdirectory per sticker")
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps", type=int, default=6)
    parser.add_argument("--hold-frames", type=int, default=1)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True, help="approved source sheet used to create the key poses")
    parser.add_argument("--state", type=Path, required=True, help="hash-bound approved job state")
    parser.add_argument("--allow-low-confidence", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.fps <= 60 or not 1 <= args.hold_frames <= 10:
        raise ValueError("fps must be 1-60 and hold-frames must be 1-10")

    if not args.keyposes.is_dir():
        raise ValueError("keypose input must be a directory")
    sticker_dirs = sorted((path for path in args.keyposes.iterdir() if path.is_dir()), key=natural_key)
    if not sticker_dirs:
        raise ValueError("keypose directory has no sticker subdirectories")
    verify_state(read_state(args.state), args.image, args.layout)
    layout = load_layout(args.layout, args.allow_low_confidence)
    if len(sticker_dirs) != layout["count"]:
        raise ValueError(
            f"keypose directory has {len(sticker_dirs)} stickers; detected layout requires {layout['count']}"
        )
    args.output = validate_output_boundaries(
        args.output, [args.keyposes, args.image, args.layout, args.state]
    )
    prepare_output(args.output, overwrite=args.overwrite)
    outputs: list[str] = []
    cells: list[dict] = []
    digits = max(2, len(str(len(sticker_dirs))))

    for item_index, directory in enumerate(sticker_dirs, start=1):
        paths = sorted(directory.glob("*.png"), key=natural_key)
        if len(paths) < 2:
            raise ValueError(f"{directory} requires at least two PNG key poses")
        if len(paths) > 20:
            raise ValueError(f"{directory} exceeds the 20 key-pose safety limit")
        opened = [Image.open(path).convert("RGBA") for path in paths]
        size = (max(image.width for image in opened), max(image.height for image in opened))
        if size[0] > 4096 or size[1] > 4096:
            raise ValueError(f"{directory} key poses exceed the 4096px safety limit")
        poses = [normalize_pose(image, size) for image in opened]
        for image in opened:
            image.close()

        sequence = []
        indices = loop_indices(len(poses))
        for pose_index in indices:
            sequence.extend(poses[pose_index].copy() for _ in range(args.hold_frames))
        stem = f"{item_index:0{digits}d}"
        png_name, webp_name, gif_name = f"{stem}.png", f"{stem}.webp", f"{stem}.gif"
        poses[0].save(args.output / png_name, optimize=True)
        encode_webp_images(sequence, args.output / webp_name, args.fps)
        encode_gif_images(sequence, args.output / gif_name, args.fps)
        outputs.extend([webp_name, gif_name, png_name])
        cells.append(
            {
                "id": stem,
                "source_directory": str(directory.resolve()),
                "keyposes": len(poses),
                "output_frames": len(sequence),
                "sequence": indices,
            }
        )
        for image in sequence + poses:
            image.close()

    report = {
        "version": 1,
        "mode": "keypose-local",
        "output_fps": args.fps,
        "hold_frames": args.hold_frames,
        "cells": cells,
        "warnings": [
            "key poses are sequenced deterministically without optical-flow or generative interpolation"
        ],
        "detected_layout": {
            "columns": layout["columns"],
            "rows": layout["rows"],
            "count": layout["count"],
            "confidence": layout["confidence"],
        },
        "outputs": outputs + ["layout.json", "processing.json", "sticker-pack.zip"],
    }
    (args.output / "layout.json").write_text(
        json.dumps({"detected_layout": report["detected_layout"]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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
