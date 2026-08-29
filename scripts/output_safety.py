"""Shared preflight for deterministic output directories."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Iterable


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


def validate_output_boundaries(output: Path, protected_paths: Iterable[Path] = ()) -> Path:
    """Resolve an output directory and reject destructive input/output overlap.

    A protected directory must be disjoint from the output directory. A protected
    file must not be the output directory itself or live below it. This deliberately
    rejects nested layouts: generated artifacts should be written to a sibling
    directory so that ``--overwrite`` can never remove source material.
    """

    resolved_output = output.expanduser().resolve(strict=False)
    if resolved_output == Path(resolved_output.anchor):
        raise ValueError("output must not be a filesystem root")
    if output.expanduser().is_symlink():
        raise ValueError("output must not be a symbolic link")

    for protected in protected_paths:
        resolved_protected = protected.expanduser().resolve(strict=False)
        if resolved_protected == resolved_output:
            raise ValueError(f"output overlaps protected input: {resolved_protected}")
        if resolved_protected.is_dir():
            if resolved_output.is_relative_to(resolved_protected) or resolved_protected.is_relative_to(
                resolved_output
            ):
                raise ValueError(
                    f"output and protected input directory must be disjoint: {resolved_protected}"
                )
        elif resolved_protected.is_relative_to(resolved_output):
            raise ValueError(f"output contains protected input: {resolved_protected}")
    return resolved_output


def is_generated_artifact(path: Path, archive_names: set[str]) -> bool:
    return (
        NUMBERED_ARTIFACT.fullmatch(path.name) is not None
        or path.name in KNOWN_REPORTS
        or path.name in archive_names
        or path.name == "frames"
        or (path.is_dir() and DELIVERY_VARIANT_DIRECTORY.fullmatch(path.name) is not None)
    )


def prepare_output(output: Path, *, overwrite: bool, archive_names: set[str] | None = None) -> None:
    output = validate_output_boundaries(output)
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
