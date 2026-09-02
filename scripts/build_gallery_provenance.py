#!/usr/bin/env python3
"""Build or verify compact provenance records for public Gallery cases.

Gallery media predates the audited workflow in some repositories.  Those cases
must remain useful evidence without pretending that an approval state or an
artifact manifest was preserved.  The generated record therefore carries an
explicit completeness status and a list of missing historical fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "gallery" / "index.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected an object in {path}")
    return value


def output_record(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def build_record(index_path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    gallery_dir = index_path.parent / "styles" / str(entry["gallery"])
    route = read_json(gallery_dir / "route.json")
    processing = read_json(gallery_dir / "processing.json")
    layout = read_json(gallery_dir / "layout.json")
    selected = route.get("selected") if isinstance(route.get("selected"), dict) else {}
    layout_value = layout.get("detected_layout", layout)
    cells = processing.get("cells", [])
    successful_cells = int(processing.get("successful_cells", len(cells)))
    requested_cells = int(layout_value.get("count", 0))
    withheld_cells = [
        str(cell.get("id"))
        for cell in cells
        if isinstance(cell, dict) and cell.get("status") in {"withheld", "rejected"}
    ]
    media = [gallery_dir / name for name in ("static.png", "motion.webp", "motion.gif")]
    total_bytes = sum(path.stat().st_size for path in media)
    missing_historical = ["approval_hash", "artifact_manifest"]
    return {
        "version": 1,
        "case_status": "legacy-evidence-partial",
        "style_id": entry["id"],
        "gallery_id": entry["gallery"],
        "source_case": entry.get("source_case"),
        # Keep the required benchmark fields flat so downstream tooling does
        # not need to understand the richer human-facing sections below.
        "input_type": "legacy-gallery-case",
        "route": selected.get("id"),
        "route_id": selected.get("id"),
        "model": selected.get("model"),
        "approval_sha256": None,
        "native_frame_count": processing.get("frames_per_animation"),
        "successful_cells": successful_cells,
        "withheld_cells": withheld_cells,
        "output_size_bytes": total_bytes,
        "input": {
            "type": "legacy-gallery-case",
            "source_case": entry.get("source_case"),
        },
        "route_details": {
            "id": selected.get("id"),
            "driver": selected.get("driver"),
            "provider": selected.get("provider"),
            "model": selected.get("model"),
            "config_sha256": route.get("config_sha256"),
            "task_sha256": route.get("task_sha256"),
        },
        "approval": {
            "sha256": None,
            "status": "not-preserved",
            "reason": "This legacy public case predates hash-bound static approval records; do not treat route/task hashes as approval proof.",
        },
        "native": {
            "source_size": processing.get("source_size"),
            "fps": processing.get("output_fps"),
            "frames_per_animation": processing.get("frames_per_animation"),
        },
        "cells": {
            "requested": requested_cells,
            "successful": successful_cells,
            "withheld": withheld_cells,
            "quality_warnings": processing.get("warnings", []),
        },
        "outputs": {
            "media_bytes": total_bytes,
            "files": [output_record(path) for path in media],
        },
        "artifact_manifest": {
            "status": "not-preserved",
            "path": None,
        },
        "missing_historical_fields": missing_historical,
    }


def verify_record(path: Path, index_path: Path, entry: dict[str, Any]) -> None:
    record = read_json(path)
    expected = build_record(index_path, entry)
    if record != expected:
        raise ValueError(f"provenance is stale or does not match current media: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--write", action="store_true", help="write one provenance.json per Gallery style")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    index_path = args.index.expanduser().resolve()
    document = read_json(index_path)
    entries = document.get("styles")
    if not isinstance(entries, list) or not entries:
        raise ValueError("gallery index must contain a non-empty styles array")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("gallery"), str):
            raise ValueError("gallery entries must contain a gallery id")
        target = index_path.parent / "styles" / entry["gallery"] / "provenance.json"
        record = build_record(index_path, entry)
        if args.write:
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.verify_only or not args.write:
            if not target.is_file():
                raise FileNotFoundError(target)
            verify_record(target, index_path, entry)
    print(json.dumps({"valid": True, "styles": len(entries), "mode": "write" if args.write else "verify"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
