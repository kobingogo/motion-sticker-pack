#!/usr/bin/env python3
"""Fail CI when public trust-surface and repository-size policies regress."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README_MIN = 150
README_MAX = 220
MAX_TRACKED_MEDIA_BYTES = 12 * 1024 * 1024
FORBIDDEN_PREFIXES = ("examples/", "promo-v020/", "works/")
MEDIA_SUFFIXES = {".png", ".gif", ".webp", ".mp4", ".mov", ".zip", ".wav", ".mp3"}


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [Path(item.decode("utf-8")) for item in completed.stdout.split(b"\0") if item]


def main() -> int:
    errors = []
    for name in ("README.md", "README.en.md"):
        lines = len((ROOT / name).read_text(encoding="utf-8").splitlines())
        if not README_MIN <= lines <= README_MAX:
            errors.append(f"{name} must contain {README_MIN}-{README_MAX} lines; got {lines}")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if "actions/workflows/ci.yml/badge.svg" not in readme:
        errors.append("README.md is missing the main CI status badge")
    tracked = tracked_files()
    forbidden = [str(path) for path in tracked if str(path).startswith(FORBIDDEN_PREFIXES)]
    if forbidden:
        errors.append(f"generated/legacy media directories are tracked: {forbidden[:8]}")
    full_gallery_media = [
        str(path)
        for path in tracked
        if len(path.parts) == 4
        and path.parts[:2] == ("gallery", "styles")
        and path.name in {"motion.gif", "motion.webp"}
    ]
    if full_gallery_media:
        errors.append(f"full gallery media must be in Release assets: {full_gallery_media[:8]}")
    media = [path for path in tracked if path.suffix.lower() in MEDIA_SUFFIXES]
    media_bytes = sum((ROOT / path).stat().st_size for path in media if (ROOT / path).is_file())
    if media_bytes > MAX_TRACKED_MEDIA_BYTES:
        errors.append(
            f"tracked compact media exceeds {MAX_TRACKED_MEDIA_BYTES} bytes: {media_bytes}"
        )
    if not (ROOT / ".github" / "workflows" / "release.yml").is_file():
        errors.append("release workflow gate is missing")
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        f"repository policy ok: {len(media)} compact media files, "
        f"{media_bytes} bytes, README bounds enforced"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
