"""Shared preflight for deterministic output directories."""

from __future__ import annotations

import re
import shutil
from pathlib import Path


NUMBERED_ARTIFACT = re.compile(r"^\d{2,}\.(?:png|webp|gif)$")
DELIVERY_VARIANT_DIRECTORY = re.compile(r"^\d+(?:\.\d+)?s$")
KNOWN_REPORTS = {
    "layout.json",
    "processing.json",
    "prompts.json",
    "route.json",
    "job-state.json",
    "preview.png",
    "sticker-production.json",
}


def validate_archive_name(value: str) -> str:
    path = Path(value)
    if path.name != value or path.suffix.lower() != ".zip" or value in {".zip", "..zip"}:
        raise ValueError("zip name must be a plain .zip filename without directory components")
    return value


def is_generated_artifact(path: Path, archive_names: set[str]) -> bool:
    return (
        NUMBERED_ARTIFACT.fullmatch(path.name) is not None
        or path.name in KNOWN_REPORTS
        or path.name in archive_names
        or path.name == "frames"
        or (path.is_dir() and DELIVERY_VARIANT_DIRECTORY.fullmatch(path.name) is not None)
    )


def prepare_output(output: Path, *, overwrite: bool, archive_names: set[str] | None = None) -> None:
    archive_names = archive_names or {"sticker-pack.zip"}
    output.mkdir(parents=True, exist_ok=True)
    conflicts = [item for item in output.iterdir() if is_generated_artifact(item, archive_names)]
    if conflicts and not overwrite:
        names = ", ".join(sorted(item.name for item in conflicts)[:8])
        raise FileExistsError(f"output contains prior generated artifacts ({names}); use --overwrite")
    if overwrite:
        for item in conflicts:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
