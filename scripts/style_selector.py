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
    preset_document = json.loads(presets_path.read_text(encoding="utf-8"))
    presets = preset_document["presets"]
    catalog = preset_document.get("core_catalog", {})
    gallery = json.loads(gallery_path.read_text(encoding="utf-8"))
    policy = gallery["policy"]
    release_manifest_path = gallery_path.parent / policy["release_manifest"]
    if not release_manifest_path.is_file():
        raise ValueError(f"gallery release manifest is missing: {release_manifest_path}")
    release_manifest = json.loads(release_manifest_path.read_text(encoding="utf-8"))
    if release_manifest.get("version") != 1:
        raise ValueError("gallery release manifest version must be 1")
    release_media_files = list(policy.get("release_media_files", []))
    if release_manifest.get("media_files") != release_media_files:
        raise ValueError("gallery release manifest media_files do not match gallery policy")
    release_entries = {entry.get("gallery"): entry for entry in release_manifest.get("styles", [])}
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
        release_entry = release_entries.get(entry["gallery"])
        if not isinstance(release_entry, dict) or release_entry.get("id") != style_id:
            raise ValueError(f"gallery style {style_id!r} lacks release media manifest")
        release_records = release_entry.get("files", [])
        if not isinstance(release_records, list) or not all(isinstance(record, dict) for record in release_records):
            raise ValueError(f"gallery style {style_id!r} has invalid release media records")
        if {record.get("path", "").rsplit("/", 1)[-1] for record in release_records} != set(release_media_files):
            raise ValueError(f"gallery style {style_id!r} has incomplete release media manifest")
        for record in release_records:
            if not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64:
                raise ValueError(f"gallery style {style_id!r} has invalid release media hash")
            if not isinstance(record.get("bytes"), int) or record["bytes"] <= 0:
                raise ValueError(f"gallery style {style_id!r} has invalid release media size")
        if layout_count(files["layout.json"]) != 9:
            raise ValueError(f"gallery style {style_id!r} is not a verified nine-cell source")
        processing = json.loads(files["processing.json"].read_text(encoding="utf-8"))
        provenance = json.loads(files["provenance.json"].read_text(encoding="utf-8"))
        if provenance.get("version") != 1 or provenance.get("style_id") != style_id:
            raise ValueError(f"gallery style {style_id!r} has invalid provenance identity")
        if provenance.get("case_status") not in {"legacy-evidence-partial", "audited-complete"}:
            raise ValueError(f"gallery style {style_id!r} has invalid provenance status")
        if not isinstance(provenance.get("missing_historical_fields"), list):
            raise ValueError(f"gallery style {style_id!r} provenance must declare missing historical fields")
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
                "release_media": release_records,
                "provenance": provenance,
            }
        )
    core_summary = None
    if catalog:
        catalog_styles = catalog.get("styles", [])
        target_count = int(catalog.get("target_count", 0))
        if target_count != len(catalog_styles):
            raise ValueError("core catalog target_count must match its style count")
        catalog_ids = [entry.get("id") for entry in catalog_styles]
        if any(not isinstance(style_id, str) for style_id in catalog_ids):
            raise ValueError("core catalog style ids must be strings")
        if len(catalog_ids) != len(set(catalog_ids)):
            raise ValueError("core catalog style ids must be unique")
        valid_statuses = {"route-verified", "pending-controlled-evidence"}
        display_ids = []
        status_by_id = {}
        for entry in catalog_styles:
            if not isinstance(entry, dict):
                raise ValueError("core catalog entries must be objects")
            style_id = entry["id"]
            status = entry.get("status")
            if status not in valid_statuses:
                raise ValueError(f"core catalog style {style_id!r} has an invalid status")
            display_id = entry.get("display_id", style_id)
            if not isinstance(display_id, str) or not display_id.strip():
                raise ValueError(f"core catalog style {style_id!r} is missing display_id")
            cues = entry.get("distinguishing_cues", [])
            if not isinstance(cues, list) or len(cues) < 2 or not all(isinstance(cue, str) and cue.strip() for cue in cues):
                raise ValueError(f"core catalog style {style_id!r} needs at least two distinguishing cues")
            display_ids.append(display_id)
            status_by_id[style_id] = status
        if len(display_ids) != len(set(display_ids)):
            raise ValueError("core catalog display ids must be unique")
        gallery_ids = {entry["id"] for entry in entries}
        unknown_gallery_ids = gallery_ids.difference(catalog_ids)
        if unknown_gallery_ids:
            raise ValueError(f"gallery styles are outside the core catalog: {sorted(unknown_gallery_ids)}")
        route_verified_ids = {style_id for style_id, status in status_by_id.items() if status == "route-verified"}
        missing_verified_evidence = route_verified_ids.difference(gallery_ids)
        if missing_verified_evidence:
            raise ValueError(f"route-verified core styles lack gallery evidence: {sorted(missing_verified_evidence)}")
        extra_gallery_evidence = gallery_ids.difference(route_verified_ids)
        if extra_gallery_evidence:
            raise ValueError(f"gallery evidence is not marked route-verified: {sorted(extra_gallery_evidence)}")
        pending_in_gallery = sorted(
            style_id for style_id in gallery_ids if status_by_id.get(style_id) == "pending-controlled-evidence"
        )
        if pending_in_gallery:
            raise ValueError(f"pending core styles cannot be exposed by the verified selector: {pending_in_gallery}")
        verified_core_count = len(route_verified_ids)
        core_summary = {
            "version": catalog.get("version"),
            "target_count": target_count,
            "verified_core_count": verified_core_count,
            "pending_core_count": target_count - verified_core_count,
            "selector_policy": catalog.get("selector_policy", "verified-only"),
            "custom": catalog.get("custom", {}),
        }
    return {
        "version": 1,
        "verified_count": len(verified),
        "styles": verified,
        **({"core_catalog": core_summary} if core_summary else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--style")
    parser.add_argument("--format", choices=("json", "markdown", "core"), default="json")
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
        payload = {"valid": True, "verified_count": result.get("verified_count", 1)}
        if result.get("core_catalog"):
            payload["core_catalog"] = result["core_catalog"]
        print(json.dumps(payload, ensure_ascii=False))
    elif args.format == "core":
        catalog = json.loads(DEFAULT_PRESETS.read_text(encoding="utf-8")).get("core_catalog", {})
        for style in catalog.get("styles", []):
            status = style.get("status", "pending")
            print(f"- `{style['id']}` — {style.get('display_id', style['id'])} [{status}]")
        custom = catalog.get("custom", {})
        if custom.get("enabled"):
            print("- `custom` — long-tail style description [enabled, not a preset]")
    elif args.format == "markdown":
        styles = result["styles"] if "styles" in result else [result]
        print("\n".join(f"- `{style['id']}` — {style['label']} ({style['source_route']})" for style in styles))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
