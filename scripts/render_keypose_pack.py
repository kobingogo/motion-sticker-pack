#!/usr/bin/env python3
"""Assemble per-sticker key-pose PNGs into seamless Animated WebP and GIF files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from animation_export import encode_gif_images, encode_webp_images
from artifact_manifest import record_artifact
from keyframe_fallback import transparent_tile
from output_profile import DEFAULT_OUTPUT_FPS, DEFAULT_OUTPUT_SIZE, MAX_OUTPUT_SIZE
from output_safety import begin_output_transaction
from interpolate_keypose_frames import cycle_with_inbetweens
from process_emoji_grid import GridBoundaryError, load_layout, validate_encoded_animation, write_preview
from prepare_keyposes import POSE_NAMES
from manage_job_state import read_state, verify_state


def natural_key(path: Path) -> list[tuple[int, object]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", path.name)
        if part
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _alpha_metrics(frame: np.ndarray) -> dict:
    alpha = frame[:, :, 3].astype(np.float64)
    visible = alpha >= 16
    ys, xs = np.where(visible)
    if not len(xs):
        raise GridBoundaryError("keypose is empty after transparency cleanup")
    weights = alpha[visible]
    return {
        "mass": float(weights.sum()),
        "centroid": (float(np.average(xs, weights=weights)), float(np.average(ys, weights=weights))),
        "bbox": (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)),
    }


def normalize_poses(images: list[Image.Image], size: tuple[int, int]) -> tuple[list[Image.Image], dict]:
    """Normalize every pose against the approved start frame's scale and centroid."""
    cleaned = [transparent_tile(np.asarray(image.convert("RGBA"), dtype=np.uint8)) for image in images]
    frames = [np.asarray(frame, dtype=np.uint8).copy() for frame in cleaned]
    for frame in cleaned:
        frame.close()
    source = _alpha_metrics(frames[0])
    source_cx, source_cy = source["centroid"]
    source_mass = source["mass"]
    margin_fraction = 0.08
    max_width = size[0] * (1.0 - 2.0 * margin_fraction)
    max_height = size[1] * (1.0 - 2.0 * margin_fraction)
    normalized: list[Image.Image] = []
    scales: list[float] = []
    centroids: list[list[float]] = []
    for frame in frames:
        metrics = _alpha_metrics(frame)
        x0, y0, x1, y1 = metrics["bbox"]
        raw_scale = (source_mass / max(metrics["mass"], 1.0)) ** 0.5
        safe_scale = min(max_width / max(x1 - x0, 1), max_height / max(y1 - y0, 1))
        scale = min(raw_scale, safe_scale)
        resized_size = (max(1, round(frame.shape[1] * scale)), max(1, round(frame.shape[0] * scale)))
        resized = Image.fromarray(frame, mode="RGBA").resize(resized_size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", size)
        cx, cy = metrics["centroid"]
        paste = (round(source_cx - cx * scale), round(source_cy - cy * scale))
        canvas.paste(resized, paste, resized)
        resized.close()
        normalized.append(canvas)
        scales.append(round(scale, 6))
        centroids.append([round(source_cx, 3), round(source_cy, 3)])
    return normalized, {
        "method": "start-anchor-scale-and-centroid",
        "anchor": "approved-start-frame",
        "source_mass": round(source_mass, 3),
        "source_centroid": [round(source_cx, 3), round(source_cy, 3)],
        "scales": scales,
        "margin_fraction": margin_fraction,
        "centroids_after": centroids,
    }


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
    parser.add_argument("--hold-frames", type=int, default=4, help="frames held per pose; default 4 gives a 3s loop at 8fps")
    parser.add_argument("--interpolation", choices=("optical-flow", "none"), default="optical-flow")
    parser.add_argument("--transition-frames", type=int, default=3, help="in-between frames per pose transition")
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True, help="approved source sheet used to create the key poses")
    parser.add_argument("--state", type=Path, required=True, help="hash-bound approved job state")
    parser.add_argument("--manifest", type=Path, help="artifact-manifest.json for hash lineage")
    parser.add_argument("--plan", type=Path, help="compiled keypose-plan.json")
    parser.add_argument("--preparation-report", type=Path, help="keypose-preparation.json")
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
    if args.manifest and (args.plan is None or args.preparation_report is None):
        raise ValueError("an audited keypose render requires --plan and --preparation-report")
    plan = None
    if args.plan:
        plan = json.loads(args.plan.expanduser().resolve().read_text(encoding="utf-8"))
        if not isinstance(plan, dict) or plan.get("version") != 1 or plan.get("mode") != "keypose-local":
            raise ValueError("keypose plan must be a version 1 keypose-local object")
        approval = plan.get("approval")
        expected = {
            "image_sha256": sha256_file(args.image.expanduser().resolve()),
            "layout_sha256": sha256_file(args.layout.expanduser().resolve()),
            "state_sha256": sha256_file(args.state.expanduser().resolve()),
        }
        if not isinstance(approval, dict) or any(approval.get(key) != value for key, value in expected.items()):
            raise ValueError("keypose plan approval anchor does not match the approved static revision")
    preparation = None
    if args.preparation_report:
        preparation = json.loads(args.preparation_report.expanduser().resolve().read_text(encoding="utf-8"))
        if (
            not isinstance(preparation, dict)
            or preparation.get("version") != 1
            or preparation.get("mode") != "keypose-local-preparation"
            or int(preparation.get("poses_per_sticker", 0)) != 4
            or int(preparation.get("count", -1)) != layout["count"]
        ):
            raise ValueError("keypose preparation report does not match the four-pose layout contract")
        report_cells = preparation.get("cells")
        if not isinstance(report_cells, list) or [str(cell.get("id")) for cell in report_cells] != [path.name for path in sticker_dirs]:
            raise ValueError("keypose preparation report does not match numbered sticker directories")
    if len(sticker_dirs) != layout["count"]:
        raise ValueError(
            f"keypose directory has {len(sticker_dirs)} stickers; detected layout requires {layout['count']}"
        )
    protected_paths = [args.keyposes, args.image, args.layout, args.state]
    if args.plan:
        protected_paths.append(args.plan)
    if args.preparation_report:
        protected_paths.append(args.preparation_report)
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
    preview_pngs: dict[int, Path] = {}
    digits = max(2, len(str(len(sticker_dirs))))

    for item_index, directory in enumerate(sticker_dirs, start=1):
        paths = [directory / name for name in POSE_NAMES]
        actual = {path.name for path in directory.glob("*.png")}
        if actual != set(POSE_NAMES):
            missing = sorted(set(POSE_NAMES) - actual)
            extra = sorted(actual - set(POSE_NAMES))
            raise ValueError(f"{directory} must contain exactly the four contract pose files; missing={missing}, extra={extra}")
        if preparation is not None:
            report_cell = preparation["cells"][item_index - 1]
            frame_hashes = report_cell.get("frame_sha256", {})
            if any(frame_hashes.get(name) != sha256_file(path) for name, path in zip(POSE_NAMES, paths)):
                raise ValueError(f"{directory} keypose frames do not match the preparation report")
        opened = [Image.open(path).convert("RGBA") for path in paths]
        source_size = (max(image.width for image in opened), max(image.height for image in opened))
        if source_size[0] > 4096 or source_size[1] > 4096:
            raise ValueError(f"{directory} key poses exceed the 4096px safety limit")
        size = (args.size, args.size)
        poses, canvas_transform = normalize_poses(opened, size)
        for image in opened:
            image.close()

        if args.interpolation == "optical-flow":
            sequence, interpolation_report = cycle_with_inbetweens(
                poses, transition_frames=args.transition_frames, lock_stable_layer=True
            )
            indices = [0, 1, 2, 3, 2, 1]
        else:
            sequence = []
            indices = loop_indices(len(poses))
            for pose_index in indices:
                sequence.extend(poses[pose_index].copy() for _ in range(args.hold_frames))
            interpolation_report = {
                "method": "none",
                "transition_frames": 0,
                "easing": None,
                "anchor_sequence": indices,
                "stable_layer_locked": False,
                "stable_layer_fraction": 0.0,
                "output_frames": len(sequence),
            }
        stem = f"{item_index:0{digits}d}"
        png_name, webp_name, gif_name = f"{stem}.png", f"{stem}.webp", f"{stem}.gif"
        poses[0].save(args.output / png_name, optimize=True)
        encode_webp_images(sequence, args.output / webp_name, args.fps)
        encode_gif_images(sequence, args.output / gif_name, args.fps)
        try:
            webp_qc = validate_encoded_animation(
                args.output / webp_name,
                expected_size=size,
                output_fps=args.fps,
            )
            gif_qc = validate_encoded_animation(
                args.output / gif_name,
                expected_size=size,
                output_fps=args.fps,
            )
        except GridBoundaryError as exc:
            raise ValueError(f"encoded keypose output failed QC for {directory.name}: {exc}") from exc
        outputs.extend([webp_name, gif_name, png_name])
        preview_pngs[item_index - 1] = args.output / png_name
        cells.append(
            {
                "id": stem,
                "source_directory": str(directory.resolve()),
                "keyposes": len(poses),
                "output_frames": len(sequence),
                "duration_seconds": round(len(sequence) / args.fps, 6),
                "sequence": indices,
                "canvas_transform": canvas_transform,
                "interpolation": interpolation_report,
                "encoded_qc": {"webp": webp_qc, "gif": gif_qc},
            }
        )
        for image in sequence + poses:
            image.close()

    if preview_pngs:
        write_preview(
            args.output / "preview.png",
            preview_pngs,
            layout["columns"],
            layout["rows"],
            (args.size, args.size),
        )
        outputs.append("preview.png")

    report = {
        "version": 1,
        "mode": "keypose-local",
        "output_fps": args.fps,
        "output_size": [args.size, args.size],
        "hold_frames": args.hold_frames if args.interpolation == "none" else 0,
        "cells": cells,
        "warnings": ([
            "key poses use local OpenCV optical-flow in-betweens; no generative interpolation"
        ] if args.interpolation == "optical-flow" else [
            "key poses are sequenced deterministically without optical-flow or generative interpolation"
        ]),
        "interpolation": {
            "method": args.interpolation,
            "transition_frames": args.transition_frames if args.interpolation == "optical-flow" else 0,
            "easing": "smoothstep" if args.interpolation == "optical-flow" else None,
        },
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
        lineage_ids = [*source_ids]
        if args.plan:
            lineage_ids.append(record_artifact(manifest, args.plan.expanduser().resolve(), kind="keypose-plan", stage="keypose-rendered", dependencies=source_ids, workspace=manifest.parent))
        if args.preparation_report:
            lineage_ids.append(record_artifact(manifest, args.preparation_report.expanduser().resolve(), kind="keypose-preparation-report", stage="keypose-rendered", dependencies=lineage_ids, workspace=manifest.parent))
        keypose_ids = [
            record_artifact(
                manifest,
                path,
                kind="keypose-frame",
                stage="keypose-rendered",
                dependencies=lineage_ids,
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
                dependencies=[*lineage_ids, *keypose_ids],
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
