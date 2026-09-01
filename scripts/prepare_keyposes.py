#!/usr/bin/env python3
"""Validate 2×2 generated pose sheets and build renderable keypose folders.

The first frame is always the approved static cell.  The generated START cell
is audited but never trusted as the loop anchor, which prevents identity drift
at the seam of a keypose animation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from artifact_manifest import record_artifact
from normalize_static_sheet import StaticSheetAlphaError, classify_background, matte_background
from output_profile import DEFAULT_OUTPUT_SIZE, MAX_OUTPUT_SIZE
from output_safety import begin_output_transaction


POSE_NAMES = ("01-start.png", "02-anticipation.png", "03-peak.png", "04-recovery.png")


def natural_key(path: Path) -> list[tuple[int, object]]:
    return [(0, int(part)) if part.isdigit() else (1, part.casefold()) for part in re.split(r"(\d+)", path.name) if part]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_image(path: Path) -> tuple[Image.Image, str, dict]:
    with Image.open(path) as source:
        image = source.convert("RGBA")
    rgba = np.asarray(image, dtype=np.uint8)
    alpha = rgba[:, :, 3]
    if float(np.mean(alpha < 250)) >= 0.002:
        visible = int(np.count_nonzero(alpha >= 32))
        if visible == 0:
            image.close()
            raise ValueError(f"{path.name} is empty")
        return image, "native-alpha", {"visible_pixels": visible}
    try:
        background = classify_background(rgba[:, :, :3])
        palette = np.asarray(background["palette"], dtype=np.float32)
        normalized, matte_report = matte_background(rgba[:, :, :3], palette)
    except StaticSheetAlphaError:
        image.close()
        raise
    image.close()
    if int(np.count_nonzero(np.asarray(normalized)[:, :, 3] >= 32)) == 0:
        normalized.close()
        raise ValueError(f"{path.name} became empty after background removal")
    return normalized, "uniform-key-matte", {
        "background": {key: value.tolist() if isinstance(value, np.ndarray) else value for key, value in background.items()},
        "matte": matte_report,
    }


def fit_canvas(image: Image.Image, size: int) -> Image.Image:
    fitted = ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size))
    canvas.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    fitted.close()
    return canvas


def pose_difference(first: Image.Image, second: Image.Image) -> float:
    left = np.asarray(first, dtype=np.float32) / 255.0
    right = np.asarray(second, dtype=np.float32) / 255.0
    visible = np.maximum(left[:, :, 3], right[:, :, 3])
    alpha_difference = float(np.abs(left[:, :, 3] - right[:, :, 3]).mean())
    rgb_difference = float((np.abs(left[:, :, :3] - right[:, :, :3]).mean(axis=2) * visible).sum() / max(float(visible.sum()), 1.0))
    return round(0.60 * alpha_difference + 0.40 * rgb_difference, 6)


def split_2x2(sheet: Image.Image) -> tuple[list[Image.Image], float]:
    width, height = sheet.size
    if width != height or width < 128:
        raise ValueError("each pose sheet must be square and at least 128px")
    x_mid, y_mid = width // 2, height // 2
    crops = [
        sheet.crop((0, 0, x_mid, y_mid)),
        sheet.crop((x_mid, 0, width, y_mid)),
        sheet.crop((0, y_mid, x_mid, height)),
        sheet.crop((x_mid, y_mid, width, height)),
    ]
    seam_alpha = np.asarray(sheet, dtype=np.uint8)[:, max(0, x_mid - 2):min(width, x_mid + 2), 3]
    seam_alpha = np.concatenate((seam_alpha.ravel(), np.asarray(sheet, dtype=np.uint8)[max(0, y_mid - 2):min(height, y_mid + 2), :, 3].ravel()))
    seam_occupancy = float(np.mean(seam_alpha >= 32))
    confidence = round(max(0.0, 1.0 - seam_occupancy), 6)
    if confidence < 0.75:
        for crop in crops:
            crop.close()
        raise ValueError(f"pose sheet has insufficient 2×2 gutter separation (confidence {confidence:.3f})")
    return crops, confidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-cells", type=Path, required=True, help="approved numbered static cells")
    parser.add_argument("--pose-sheets", type=Path, required=True, help="numbered generated 2×2 PNG pose sheets")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--size", type=int, default=DEFAULT_OUTPUT_SIZE, help="fixed per-pose canvas size")
    parser.add_argument("--manifest", type=Path, help="artifact-manifest.json for hash lineage")
    parser.add_argument("--workspace", type=Path, help="manifest workspace; defaults to the manifest parent")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not 64 <= args.size <= MAX_OUTPUT_SIZE:
        raise ValueError(f"size must be between 64 and {MAX_OUTPUT_SIZE}")
    source_dir = args.source_cells.expanduser().resolve()
    sheet_dir = args.pose_sheets.expanduser().resolve()
    if not source_dir.is_dir() or not sheet_dir.is_dir():
        raise ValueError("source-cells and pose-sheets must be directories")
    sources = sorted((path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() == ".png"), key=natural_key)
    sheets = sorted((path for path in sheet_dir.iterdir() if path.is_file() and path.suffix.lower() == ".png"), key=natural_key)
    if not sources or len(sources) != len(sheets) or len(sources) > 48:
        raise ValueError("source-cells and pose-sheets must contain the same 1-48 PNG files")
    protected = [source_dir, sheet_dir]
    if args.manifest:
        protected.append(args.manifest.expanduser().resolve())
    transaction = begin_output_transaction(args.output_dir, overwrite=args.overwrite, protected_paths=protected)
    output = transaction.output
    digits = max(2, len(str(len(sources))))
    cells = []
    warnings = []
    for index, (source_path, sheet_path) in enumerate(zip(sources, sheets), start=1):
        source, source_method, source_report = normalize_image(source_path)
        source_fixed = fit_canvas(source, args.size)
        source.close()
        sheet, sheet_method, sheet_report = normalize_image(sheet_path)
        crops, confidence = split_2x2(sheet)
        sheet.close()
        generated = [fit_canvas(crop, args.size) for crop in crops]
        for crop in crops:
            crop.close()
        if any(int(np.count_nonzero(np.asarray(p)[:, :, 3] >= 32)) == 0 for p in generated):
            for pose in generated:
                pose.close()
            source_fixed.close()
            raise ValueError(f"pose sheet {sheet_path.name} contains an empty cell")
        poses = [source_fixed, generated[1], generated[2], generated[3]]
        differences = [pose_difference(source_fixed, pose) for pose in poses[1:]]
        if differences[1] < 0.02 or max(differences) < 0.025:
            for pose in generated:
                pose.close()
            source_fixed.close()
            raise ValueError(f"pose sheet {sheet_path.name} has no meaningful action change")
        if differences[0] > 0.30:
            warnings.append(f"{sheet_path.name}: generated START differs strongly; original static cell remains the loop anchor")
        cell_dir = output / f"{index:0{digits}d}"
        cell_dir.mkdir(parents=True, exist_ok=True)
        for name, pose in zip(POSE_NAMES, poses):
            pose.save(cell_dir / name, optimize=True)
        cells.append({
            "id": f"{index:0{digits}d}",
            "source": str(source_path),
            "source_sha256": sha256_file(source_path),
            "pose_sheet": str(sheet_path),
            "pose_sheet_sha256": sha256_file(sheet_path),
            "source_normalization": {"method": source_method, **source_report},
            "pose_sheet_normalization": {"method": sheet_method, **sheet_report},
            "layout": {"columns": 2, "rows": 2, "confidence": confidence},
            "motion_difference_from_start": {"anticipation": differences[0], "peak": differences[1], "recovery": differences[2]},
            "outputs": list(POSE_NAMES),
        })
        for pose in generated:
            pose.close()
        source_fixed.close()
    report = {
        "version": 1,
        "mode": "keypose-local-preparation",
        "count": len(sources),
        "poses_per_sticker": 4,
        "fixed_canvas": [args.size, args.size],
        "start_frame_policy": "exact-approved-static-cell",
        "cells": cells,
        "warnings": warnings,
    }
    report_path = output / "keypose-preparation.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    transaction.commit()
    manifest_result = None
    if args.manifest:
        manifest = args.manifest.expanduser().resolve()
        workspace = args.workspace.expanduser().resolve() if args.workspace else manifest.parent
        source_ids = [
            record_artifact(manifest, source, kind="keypose-source-cell", stage="keypose-prepared", workspace=workspace)
            for source in sources
        ]
        sheet_ids = [
            record_artifact(manifest, sheet, kind="keypose-pose-sheet", stage="keypose-prepared", workspace=workspace)
            for sheet in sheets
        ]
        pose_ids = []
        for index in range(1, len(sources) + 1):
            dependencies = [source_ids[index - 1], sheet_ids[index - 1]]
            for name in POSE_NAMES:
                pose_ids.append(
                    record_artifact(
                        manifest,
                        output / f"{index:0{digits}d}" / name,
                        kind="keypose-frame",
                        stage="keypose-prepared",
                        dependencies=dependencies,
                    )
                )
        report_id = record_artifact(
            manifest,
            report_path,
            kind="keypose-preparation-report",
            stage="keypose-prepared",
            dependencies=pose_ids,
        )
        manifest_result = {"path": str(manifest), "report_artifact_id": report_id}
    print(json.dumps({"report": str(report_path.resolve()), "count": len(sources), "warnings": warnings, "artifact_manifest": manifest_result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
