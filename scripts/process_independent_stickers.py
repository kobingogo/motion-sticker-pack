#!/usr/bin/env python3
"""Animate a directory of independent static stickers without requiring a grid layout."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from animation_export import encode_gif_images, encode_webp_images
from artifact_manifest import record_artifact
from keyframe_fallback import add_motion_margin, fixed_canvas, transformed, transparent_tile
from process_emoji_grid import GridBoundaryError, validate_encoded_animation, write_preview
from output_profile import DEFAULT_OUTPUT_FPS, DEFAULT_OUTPUT_SIZE, MAX_OUTPUT_SIZE
from output_safety import begin_output_transaction
from video_background_qc import BackgroundQCError, validate_frame_background


def natural_key(path: Path) -> list[tuple[int, object]]:
    return [(0, int(part)) if part.isdigit() else (1, part.casefold()) for part in re.split(r"(\d+)", path.name) if part]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps", type=int, default=DEFAULT_OUTPUT_FPS)
    parser.add_argument("--size", type=int, default=DEFAULT_OUTPUT_SIZE)
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--manifest", type=Path, help="artifact-manifest.json for hash lineage")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.fps <= 60 or not 0.25 <= args.duration <= 30:
        raise ValueError("fps must be 1-60 and duration must be 0.25-30 seconds")
    if not 64 <= args.size <= MAX_OUTPUT_SIZE:
        raise ValueError(f"size must be between 64 and {MAX_OUTPUT_SIZE}")
    source_dir = args.input_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise ValueError("input_dir must be a directory")
    sources = sorted(
        (path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}),
        key=natural_key,
    )
    if not 1 <= len(sources) <= 48:
        raise ValueError("input_dir must contain between 1 and 48 static stickers")
    output_transaction = begin_output_transaction(
        args.output,
        overwrite=args.overwrite,
        protected_paths=[source_dir, *([args.manifest] if args.manifest else [])],
    )
    args.output = output_transaction.output
    # Keep at least three distinct phase samples so short loops cannot encode
    # as a static one-frame WebP/GIF for a periodic recipe.
    frame_count = max(3, round(args.fps * args.duration))
    outputs: list[str] = []
    cells: list[dict] = []
    preview_pngs: dict[int, Path] = {}
    digits = max(2, len(str(len(sources))))
    for index, source_path in enumerate(sources, start=1):
        with Image.open(source_path) as source:
            if source.width * source.height > 64_000_000:
                raise ValueError(f"input image exceeds the 64 megapixel limit: {source_path}")
            rgba = np.asarray(source.convert("RGBA"), dtype=np.uint8)
            if float(np.mean(rgba[:, :, 3] < 250)) >= 0.002:
                raw = Image.fromarray(rgba, mode="RGBA")
            else:
                try:
                    validate_frame_background(
                        rgba[:, :, :3],
                        label=f"independent sticker {source_path.name}",
                    )
                except BackgroundQCError as exc:
                    raise ValueError(
                        f"opaque independent sticker requires a uniform edge background: {source_path}"
                    ) from exc
                raw = transparent_tile(rgba)
            normalized = fixed_canvas(raw, args.size)
            raw.close()
            base = add_motion_margin(normalized)
            normalized.close()
        frames = [transformed(base, ("bounce", "sway", "pulse", "shake", "float")[(index - 1) % 5], 2.0 * np.pi * frame / frame_count) for frame in range(frame_count)]
        stem = f"{index:0{digits}d}"
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
            raise ValueError(f"encoded independent-sticker output failed QC for {stem}: {exc}") from exc
        outputs.extend([webp_name, gif_name, png_name])
        preview_pngs[index - 1] = args.output / png_name
        cells.append({"id": stem, "source": str(source_path), "frames": frame_count, "encoded_qc": encoded_qc})
        base.close()
        for frame in frames:
            frame.close()

    if preview_pngs:
        write_preview(args.output / "preview.png", preview_pngs, len(sources), 1, (args.size, args.size))
        outputs.append("preview.png")
    layout = {
        "source_type": "separate-static-stickers",
        "numbering_layout": "synthetic single row for output ordering only",
        "detected_layout": {"columns": len(sources), "rows": 1, "count": len(sources), "confidence": 1.0, "detection_mode": "separate-input"},
    }
    (args.output / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"version": 1, "mode": "independent-stickers", "source": str(source_dir), "output_fps": args.fps, "output_size": [args.size, args.size], "duration_seconds": args.duration, "cells": cells, "warnings": ["independent inputs are user-supplied assets; no grid detection was performed"], "outputs": outputs + ["layout.json", "processing.json", "sticker-pack.zip"]}
    (args.output / "processing.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(args.output / "sticker-pack.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in outputs + ["layout.json", "processing.json"]:
            bundle.write(args.output / name, arcname=name)
    output_transaction.commit()
    if args.manifest:
        manifest = args.manifest.expanduser().resolve()
        source_ids = [
            record_artifact(manifest, path, kind="independent-source", stage="independent-rendered", workspace=manifest.parent)
            for path in sources
        ]
        output_ids = [
            record_artifact(
                manifest,
                args.output / name,
                kind="sticker-output" if Path(name).suffix.lower() in {".png", ".webp", ".gif"} else "processing-report",
                stage="independent-rendered",
                dependencies=source_ids,
                workspace=manifest.parent,
            )
            for name in outputs + ["layout.json", "processing.json"]
        ]
        record_artifact(
            manifest,
            args.output / "sticker-pack.zip",
            kind="sticker-pack",
            stage="independent-rendered",
            dependencies=output_ids,
            workspace=manifest.parent,
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
