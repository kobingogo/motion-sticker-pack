#!/usr/bin/env python3
"""Assemble per-sticker key-pose PNGs into seamless Animated WebP and GIF files."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from animation_export import encode_gif_images, encode_webp_images
from artifact_manifest import record_artifact
from keyframe_fallback import transparent_tile
from output_profile import DEFAULT_OUTPUT_FPS, DEFAULT_OUTPUT_SIZE, MAX_OUTPUT_SIZE
from output_safety import begin_output_transaction
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
    fitted = ImageOps.contain(cleaned, size, Image.Resampling.LANCZOS)
    cleaned.close()
    canvas = Image.new("RGBA", size)
    canvas.alpha_composite(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    fitted.close()
    return canvas


def loop_indices(count: int) -> list[int]:
    if count < 2:
        raise ValueError("each sticker requires at least two key poses")
    return list(range(count)) + list(range(count - 2, 0, -1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("keyposes", type=Path, help="directory containing one numbered subdirectory per sticker")
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps", type=int, default=DEFAULT_OUTPUT_FPS)
    parser.add_argument("--size", type=int, default=DEFAULT_OUTPUT_SIZE)
    parser.add_argument("--hold-frames", type=int, default=1)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True, help="approved source sheet used to create the key poses")
    parser.add_argument("--state", type=Path, required=True, help="hash-bound approved job state")
    parser.add_argument("--manifest", type=Path, help="artifact-manifest.json for hash lineage")
    parser.add_argument("--allow-low-confidence", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.fps <= 60 or not 1 <= args.hold_frames <= 10:
        raise ValueError("fps must be 1-60 and hold-frames must be 1-10")
    if not 64 <= args.size <= MAX_OUTPUT_SIZE:
        raise ValueError(f"size must be between 64 and {MAX_OUTPUT_SIZE}")

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
    protected_paths = [args.keyposes, args.image, args.layout, args.state]
    if args.manifest:
        protected_paths.append(args.manifest)
    output_transaction = begin_output_transaction(
        args.output,
        overwrite=args.overwrite,
        protected_paths=protected_paths,
    )
    args.output = output_transaction.output
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
        source_size = (max(image.width for image in opened), max(image.height for image in opened))
        if source_size[0] > 4096 or source_size[1] > 4096:
            raise ValueError(f"{directory} key poses exceed the 4096px safety limit")
        size = (args.size, args.size)
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
        "output_size": [args.size, args.size],
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
    output_transaction.commit()
    manifest_result = None
    if args.manifest:
        manifest = args.manifest.expanduser().resolve()
        source_ids = [
            record_artifact(manifest, path, kind=kind, stage="keypose-rendered", workspace=manifest.parent)
            for path, kind in (
                (args.image, "static-sheet"),
                (args.layout, "layout"),
                (args.state, "approval-state"),
            )
        ]
        keypose_ids = [
            record_artifact(
                manifest,
                path,
                kind="keypose-frame",
                stage="keypose-rendered",
                dependencies=source_ids,
            )
            for directory in sticker_dirs
            for path in sorted(directory.glob("*.png"), key=natural_key)
        ]
        output_ids = [
            record_artifact(
                manifest,
                args.output / name,
                kind="sticker-output",
                stage="keypose-rendered",
                dependencies=[*source_ids, *keypose_ids],
            )
            for name in outputs
        ]
        report_id = record_artifact(
            manifest,
            args.output / "processing.json",
            kind="processing-report",
            stage="keypose-rendered",
            dependencies=output_ids,
        )
        bundle_id = record_artifact(
            manifest,
            args.output / "sticker-pack.zip",
            kind="sticker-pack",
            stage="keypose-rendered",
            dependencies=[*output_ids, report_id],
        )
        manifest_result = {"path": str(manifest), "bundle_artifact_id": bundle_id}
        report["artifact_manifest"] = manifest_result
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
