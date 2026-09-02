#!/usr/bin/env python3
"""Split an approved static sheet into hash-bound row-major source cells."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from artifact_manifest import record_artifact
from manage_job_state import read_state, sha256_file, verify_state
from output_safety import begin_output_transaction


def tile_bounds(size: int, index: int, parts: int) -> tuple[int, int]:
    return size * index // parts, size * (index + 1) // parts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    image = args.image.expanduser().resolve(strict=True)
    layout_path = args.layout.expanduser().resolve(strict=True)
    state_path = args.state.expanduser().resolve(strict=True)
    verify_state(read_state(state_path), image, layout_path)
    layout_value = json.loads(layout_path.read_text(encoding="utf-8"))
    detected = layout_value.get("detected_layout", layout_value)
    columns = int(detected["columns"])
    rows = int(detected["rows"])
    count = columns * rows
    if int(detected.get("count", count)) != count or not 1 <= count <= 48:
        raise ValueError("approved layout must contain 1-48 cells")

    transaction = begin_output_transaction(
        args.output_dir,
        overwrite=args.overwrite,
        protected_paths=[image, layout_path, state_path, *([args.manifest] if args.manifest else [])],
    )
    output = transaction.output
    with Image.open(image) as source:
        rgba = source.convert("RGBA")
        width, height = rgba.size
        digits = max(2, len(str(count)))
        cells = []
        for index in range(count):
            row, column = divmod(index, columns)
            x0, x1 = tile_bounds(width, column, columns)
            y0, y1 = tile_bounds(height, row, rows)
            target = output / f"{index + 1:0{digits}d}.png"
            rgba.crop((x0, y0, x1, y1)).save(target, format="PNG", optimize=True)
            cells.append(
                {
                    "id": f"{index + 1:0{digits}d}",
                    "path": str(target.resolve()),
                    "sha256": sha256_file(target),
                    "box": [x0, y0, x1, y1],
                }
            )
        rgba.close()

    report = {
        "version": 1,
        "mode": "approved-static-cells",
        "source_image": str(image),
        "source_image_sha256": sha256_file(image),
        "count": count,
        "layout": str(layout_path),
        "layout_sha256": sha256_file(layout_path),
        "state": str(state_path),
        "state_sha256": sha256_file(state_path),
        "detected_layout": {"columns": columns, "rows": rows, "count": count},
        "cells": cells,
    }
    report_path = output / "static-cells.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    transaction.commit()

    manifest_result = None
    if args.manifest:
        manifest = args.manifest.expanduser().resolve()
        source_ids = [
            record_artifact(manifest, path, kind=kind, stage="static-cells-split", workspace=manifest.parent)
            for path, kind in ((image, "static-sheet"), (layout_path, "layout"), (state_path, "approval-state"))
        ]
        cell_ids = [
            record_artifact(
                manifest,
                Path(cell["path"]),
                kind="approved-static-cell",
                stage="static-cells-split",
                dependencies=source_ids,
            )
            for cell in cells
        ]
        report_id = record_artifact(
            manifest,
            report_path,
            kind="approved-static-cells-report",
            stage="static-cells-split",
            dependencies=[*source_ids, *cell_ids],
        )
        manifest_result = {"path": str(manifest), "report_artifact_id": report_id}
    print(json.dumps({"report": str(report_path.resolve()), "count": count, "artifact_manifest": manifest_result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
