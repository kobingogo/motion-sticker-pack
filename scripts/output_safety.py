"""Shared preflight for deterministic output directories."""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Iterable


NUMBERED_ARTIFACT = re.compile(r"^\d{2,}\.(?:png|webp|gif)$")
DELIVERY_VARIANT_DIRECTORY = re.compile(r"^\d+(?:\.\d+)?s$")
KNOWN_REPORTS = {
    "artifact-manifest.json",
    "attempt-ledger.json",
    "layout.json",
    "processing.json",
    "prompts.json",
    "route.json",
    "job-state.json",
    "preview.png",
    "sticker-production.json",
    "static-cells.json",
}


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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


class OutputTransaction:
    """Recoverable output-directory replacement with explicit commit.

    Writes still target the requested final path so existing processors need no
    path translation. The prior directory is moved aside first and a journal
    makes an interrupted run recoverable on the next invocation.
    """

    def __init__(
        self,
        output: Path,
        *,
        overwrite: bool,
        archive_names: set[str] | None = None,
        protected_paths: Iterable[Path] = (),
    ) -> None:
        self.output = validate_output_boundaries(output, protected_paths)
        self.overwrite = overwrite
        self.archive_names = archive_names or {"sticker-pack.zip"}
        self.journal = self.output.parent / f".{self.output.name}.output-transaction.json"
        self.backup: Path | None = None
        self.started = False
        self.committed = False

    def _write_journal(self, phase: str) -> None:
        _atomic_json(
            self.journal,
            {
                "version": 1,
                "output": str(self.output),
                "backup": str(self.backup) if self.backup else None,
                "pid": os.getpid(),
                "phase": phase,
            },
        )

    def _recover_abandoned(self) -> None:
        if not self.journal.is_file():
            return
        try:
            value = json.loads(self.journal.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot recover malformed output transaction journal: {self.journal}") from exc
        if value.get("version") != 1 or value.get("output") != str(self.output):
            raise ValueError(f"output transaction journal does not match target: {self.journal}")
        owner_pid = int(value.get("pid", 0))
        if _pid_is_alive(owner_pid):
            raise ValueError(f"output transaction is active in process {owner_pid}")
        phase = value.get("phase")
        if phase not in {"prepared", "active", "committing"}:
            raise ValueError(f"output transaction has an invalid phase: {self.journal}")
        backup_value = value.get("backup")
        backup = None
        if backup_value is not None:
            backup = Path(backup_value)
            if backup.parent != self.output.parent or not backup.name.startswith(f".{self.output.name}.backup-"):
                raise ValueError(f"unsafe output transaction backup path: {backup}")
        if phase == "committing":
            if not self.output.is_dir():
                raise ValueError(f"committing output transaction is missing its final directory: {self.output}")
            if backup is not None and backup.exists():
                _remove_path(backup)
            self.journal.unlink(missing_ok=True)
            return
        if phase == "prepared" and backup is not None and not backup.exists():
            if not self.output.is_dir():
                raise ValueError(f"prepared output transaction lost both output and backup: {self.journal}")
            self.journal.unlink(missing_ok=True)
            return
        if backup_value is None:
            if self.output.exists():
                _remove_path(self.output)
        else:
            if backup is None or not backup.exists():
                raise ValueError(f"active output transaction backup is missing: {self.journal}")
            if self.output.exists():
                _remove_path(self.output)
            os.replace(backup, self.output)
        self.journal.unlink(missing_ok=True)

    def begin(self) -> "OutputTransaction":
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self._recover_abandoned()
        if self.output.exists() and self.output.is_symlink():
            raise ValueError("output must not be a symbolic link")
        conflicts = []
        if self.output.is_dir():
            conflicts = [
                item for item in self.output.iterdir() if is_generated_artifact(item, self.archive_names)
            ]
        elif self.output.exists():
            raise ValueError("output must be a directory")
        if conflicts and not self.overwrite:
            names = ", ".join(sorted(item.name for item in conflicts)[:8])
            raise FileExistsError(f"output contains prior generated artifacts ({names}); use --overwrite")

        if self.output.exists():
            self.backup = self.output.parent / f".{self.output.name}.backup-{uuid.uuid4().hex}"
        self._write_journal("prepared")
        if self.backup is not None:
            os.replace(self.output, self.backup)
        self._write_journal("active")
        self.output.mkdir(parents=True, exist_ok=False)
        if self.backup is not None:
            for item in self.backup.iterdir():
                if is_generated_artifact(item, self.archive_names):
                    continue
                destination = self.output / item.name
                if item.is_dir() and not item.is_symlink():
                    shutil.copytree(item, destination, symlinks=True)
                else:
                    shutil.copy2(item, destination, follow_symlinks=False)
        self.started = True
        atexit.register(self.rollback)
        return self

    def commit(self) -> None:
        if not self.started or self.committed:
            return
        self._write_journal("committing")
        if self.backup is not None and self.backup.exists():
            _remove_path(self.backup)
        self.journal.unlink(missing_ok=True)
        self.committed = True
        atexit.unregister(self.rollback)

    def rollback(self) -> None:
        if not self.started or self.committed:
            return
        if self.output.exists():
            _remove_path(self.output)
        if self.backup is not None and self.backup.exists():
            os.replace(self.backup, self.output)
        self.journal.unlink(missing_ok=True)
        self.started = False


def begin_output_transaction(
    output: Path,
    *,
    overwrite: bool,
    archive_names: set[str] | None = None,
    protected_paths: Iterable[Path] = (),
) -> OutputTransaction:
    return OutputTransaction(
        output,
        overwrite=overwrite,
        archive_names=archive_names,
        protected_paths=protected_paths,
    ).begin()
