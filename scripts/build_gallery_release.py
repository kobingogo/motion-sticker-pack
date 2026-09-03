#!/usr/bin/env python3
"""Build and verify the full gallery media Release asset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
MEDIA_FILES = ("static.png", "motion.gif", "motion.webp")
DEFAULT_INDEX = ROOT / "gallery" / "index.json"
DEFAULT_MANIFEST = ROOT / "gallery" / "release-manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected an object in {path}")
    return value


def build_manifest(index_path: Path, source_root: Path) -> dict:
    index = read_json(index_path)
    styles = []
    for entry in index.get("styles", []):
        gallery_id = str(entry["gallery"])
        style_dir = source_root / gallery_id
        files = []
        for name in MEDIA_FILES:
            path = style_dir / name
            if not path.is_file():
                raise FileNotFoundError(path)
            files.append(
                {
                    "path": f"gallery/styles/{gallery_id}/{name}",
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
        styles.append({"id": entry["id"], "gallery": gallery_id, "files": files})
    return {
        "version": 1,
        "asset_name_template": "motion-sticker-pack-gallery-v{version}.zip",
        "media_files": list(MEDIA_FILES),
        "styles": styles,
    }


def verify_manifest(manifest: dict, index_path: Path, source_root: Path) -> None:
    expected = build_manifest(index_path, source_root)
    if manifest.get("version") != 1:
        raise ValueError("gallery release manifest version must be 1")
    if manifest.get("media_files") != list(MEDIA_FILES):
        raise ValueError("gallery release manifest media_files are invalid")
    if manifest.get("styles") != expected["styles"]:
        raise ValueError("gallery release manifest does not match the full media source")


def write_zip(output: Path, index_path: Path, manifest_path: Path, source_root: Path) -> None:
    manifest = read_json(manifest_path)
    verify_manifest(manifest, index_path, source_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    files: list[tuple[str, Path]] = [
        ("gallery/index.json", index_path),
        ("gallery/release-manifest.json", manifest_path),
    ]
    for style in manifest["styles"]:
        for record in style["files"]:
            archive_path = str(record["path"])
            files.append((archive_path, source_root / style["gallery"] / Path(archive_path).name))
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for archive_path, source in files:
            if not source.is_file():
                raise FileNotFoundError(source)
            info = ZipInfo(archive_path)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    generated = build_manifest(args.index.resolve(), args.source_root.resolve())
    if args.manifest_output:
        args.manifest_output.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.manifest.is_file():
        verify_manifest(read_json(args.manifest.resolve()), args.index.resolve(), args.source_root.resolve())
    elif not args.manifest_output:
        raise FileNotFoundError(args.manifest)
    if args.output:
        manifest_path = args.manifest_output.resolve() if args.manifest_output else args.manifest.resolve()
        write_zip(args.output.resolve(), args.index.resolve(), manifest_path, args.source_root.resolve())
    print(json.dumps({"valid": True, "styles": len(generated["styles"]), "asset": str(args.output) if args.output else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
