#!/usr/bin/env python3
"""Assemble media and audit artifacts into the final delivery directory and ZIP."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import zipfile
from pathlib import Path

from output_safety import (
    DELIVERY_VARIANT_DIRECTORY,
    begin_output_transaction,
    validate_archive_name,
)


MEDIA_RE = re.compile(r"^\d{2,}\.(?:png|webp|gif)$")
BASE_REPORTS = ("layout.json", "processing.json")
OPTIONAL_MEDIA_REPORTS = ("sticker-production.json",)
AUDIT_REPORTS = (
    "artifact-manifest.json",
    "attempt-ledger.json",
    "job-state.json",
    "prompts.json",
    "route.json",
    "static-prompt.json",
    "static-generation.json",
    "static-alpha.json",
    "video-result.json",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media-dir", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip-name", default="sticker-pack.zip")
    parser.add_argument("--require-job-state", action="store_true")
    parser.add_argument("--require-prompts", action="store_true")
    parser.add_argument("--require-route", action="store_true")
    parser.add_argument(
        "--cleanup-media-dir",
        action="store_true",
        help="remove the intermediate media directory after the final delivery ZIP succeeds",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    archive_name = validate_archive_name(args.zip_name)
    media_dir = args.media_dir.expanduser().resolve()
    audit_dir = args.audit_dir.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not media_dir.is_dir() or not audit_dir.is_dir():
        raise ValueError("media-dir and audit-dir must be directories")
    if args.cleanup_media_dir and (
        media_dir in {audit_dir, output}
        or output.is_relative_to(media_dir)
        or media_dir.is_relative_to(output)
    ):
        raise ValueError("cleanup-media-dir requires separate sibling media and output directories")

    media = sorted(path for path in media_dir.iterdir() if path.is_file() and MEDIA_RE.fullmatch(path.name))
    if not media:
        raise ValueError("media-dir contains no numbered PNG/WebP/GIF artifacts")
    for name in BASE_REPORTS:
        if not (media_dir / name).is_file():
            raise FileNotFoundError(media_dir / name)
    required = {
        "job-state.json": args.require_job_state,
        "prompts.json": args.require_prompts,
        "route.json": args.require_route,
    }
    audit = [audit_dir / name for name, must_exist in required.items() if must_exist]
    for path in audit:
        if not path.is_file():
            raise FileNotFoundError(path)

    output_transaction = begin_output_transaction(
        output,
        overwrite=args.overwrite,
        archive_names={archive_name},
        protected_paths=[media_dir, audit_dir],
    )
    output = output_transaction.output
    names = [path.name for path in media] + list(BASE_REPORTS)
    for path in media:
        shutil.copyfile(path, output / path.name)
    for name in BASE_REPORTS:
        shutil.copyfile(media_dir / name, output / name)
    for name in OPTIONAL_MEDIA_REPORTS:
        source = media_dir / name
        if source.is_file():
            shutil.copyfile(source, output / name)
            names.append(name)
    preview = media_dir / "preview.png"
    if preview.is_file():
        shutil.copyfile(preview, output / preview.name)
        names.append(preview.name)
    variant_directories = sorted(
        path for path in media_dir.iterdir()
        if path.is_dir() and DELIVERY_VARIANT_DIRECTORY.fullmatch(path.name)
    )
    for source in variant_directories:
        target = output / source.name
        target.mkdir(parents=True)
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.suffix.lower() == ".zip":
                continue
            relative = path.relative_to(source)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            names.append(str(destination.relative_to(output)))
    for name in AUDIT_REPORTS:
        source = audit_dir / name
        if source.is_file():
            shutil.copyfile(source, output / name)
            names.append(name)
    with zipfile.ZipFile(output / archive_name, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in names:
            bundle.write(output / name, arcname=name)
    output_transaction.commit()
    if args.cleanup_media_dir:
        shutil.rmtree(media_dir)
    print(json.dumps({"output": str(output), "zip": str(output / archive_name), "files": names}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
