#!/usr/bin/env python3
"""List and verify the evidence-backed visual style selector."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRESETS = ROOT / "references" / "style-presets.json"
DEFAULT_GALLERY = ROOT / "gallery" / "index.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def layout_count(path: Path) -> int:
    value = json.loads(path.read_text(encoding="utf-8"))
    layout = value.get("detected_layout", value)
    return int(layout.get("count", int(layout["columns"]) * int(layout["rows"])))


def verify_gallery(presets_path: Path = DEFAULT_PRESETS, gallery_path: Path = DEFAULT_GALLERY) -> dict:
    presets = json.loads(presets_path.read_text(encoding="utf-8"))["presets"]
    gallery = json.loads(gallery_path.read_text(encoding="utf-8"))
    policy = gallery["policy"]
    entries = gallery["styles"]
    minimum, maximum = int(policy["minimum_verified_styles"]), int(policy["maximum_verified_styles"])
    if not minimum <= len(entries) <= maximum:
        raise ValueError(f"gallery must contain {minimum}-{maximum} verified styles")
    ids = [entry["id"] for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("gallery style ids must be unique")
    verified = []
    for entry in entries:
        style_id = entry["id"]
        preset = presets.get(style_id)
        if not preset or preset.get("verified") is not True:
            raise ValueError(f"gallery style {style_id!r} lacks a verified preset")
        if preset.get("gallery") != entry["gallery"]:
            raise ValueError(f"gallery mapping mismatch for {style_id!r}")
        directory = gallery_path.parent / "styles" / entry["gallery"]
        files = {name: directory / name for name in policy["required_files"]}
        missing = [name for name, path in files.items() if not path.is_file()]
        if missing:
            raise ValueError(f"gallery style {style_id!r} is missing {missing}")
        if layout_count(files["layout.json"]) != 9:
            raise ValueError(f"gallery style {style_id!r} is not a verified nine-cell source")
        processing = json.loads(files["processing.json"].read_text(encoding="utf-8"))
        successful_cells = int(processing.get("successful_cells", len(processing.get("cells", []))))
        if successful_cells < 9:
            raise ValueError(f"gallery style {style_id!r} processing report lacks nine successful cells")
        source_fps = float(processing.get("output_fps", 0))
        if source_fps < 1:
            raise ValueError(f"gallery style {style_id!r} processing report lacks output fps")
        route = json.loads(files["route.json"].read_text(encoding="utf-8"))
        selected = route.get("selected")
        if not isinstance(selected, dict) or not isinstance(selected.get("id"), str):
            raise ValueError(f"gallery style {style_id!r} has no source route")
        with Image.open(files["static.png"]) as static:
            if static.size != (240, 240):
                raise ValueError(f"gallery style {style_id!r} static preview must be 240x240")
        with Image.open(files["motion.gif"]) as motion:
            if motion.size != (240, 240) or int(getattr(motion, "n_frames", 1)) < 2:
                raise ValueError(f"gallery style {style_id!r} motion preview is not animated 240x240")
        verified.append(
            {
                "id": style_id,
                "label": preset["label"],
                "aliases": preset["aliases"],
                "prompt": preset["prompt"],
                "source_case": entry["source_case"],
                "source_route": selected["id"],
                "source_output_fps": source_fps,
                "files": {
                    name: {
                        "path": str(path.relative_to(ROOT)),
                        "sha256": file_sha256(path),
                        "bytes": path.stat().st_size,
                    }
                    for name, path in files.items()
                },
            }
        )
    return {"version": 1, "verified_count": len(verified), "styles": verified}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--style")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = verify_gallery()
    if args.style:
        matches = [
            style
            for style in result["styles"]
            if args.style.casefold() in {style["id"].casefold(), *(alias.casefold() for alias in style["aliases"])}
        ]
        if len(matches) != 1:
            raise ValueError(f"style selector resolved {len(matches)} matches for {args.style!r}")
        result = matches[0]
    if args.verify_only:
        print(json.dumps({"valid": True, "verified_count": result.get("verified_count", 1)}, ensure_ascii=False))
    elif args.format == "markdown":
        styles = result["styles"] if "styles" in result else [result]
        print("\n".join(f"- `{style['id']}` — {style['label']} ({style['source_route']})" for style in styles))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
