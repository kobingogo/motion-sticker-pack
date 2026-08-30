#!/usr/bin/env python3
"""Immutable-attempt ledger for billable video-provider execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from config_contract import ContractError, object_sha256


STATUSES = {"planned", "running", "submitted", "succeeded", "failed", "rejected", "uncertain"}
TERMINAL_STATUSES = {"succeeded", "failed", "rejected"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
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


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read attempt ledger JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("attempt ledger must be a JSON object")
    return value


def _transition(entry: dict[str, Any], status: str, **details: Any) -> None:
    if status not in STATUSES:
        raise ContractError(f"invalid attempt transition status: {status}")
    event = {"status": status, "at": utc_now(), **details}
    entry.setdefault("events", []).append(event)
    entry["status"] = status
    entry["updated_at"] = event["at"]


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


@contextmanager
def ledger_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f".{path.name}.lock")
    for _ in range(2):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            try:
                lock = json.loads(lock_path.read_text(encoding="utf-8"))
                pid = int(lock.get("pid", 0))
            except (OSError, ValueError, json.JSONDecodeError):
                pid = 0
            if _pid_is_alive(pid):
                raise ContractError(f"attempt ledger is locked by active process {pid}")
            lock_path.unlink(missing_ok=True)
    else:
        raise ContractError("could not acquire attempt ledger lock")
    try:
        os.write(descriptor, json.dumps({"pid": os.getpid(), "created_at": utc_now()}).encode("utf-8"))
        os.close(descriptor)
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)


def validate_ledger(ledger: dict[str, Any], route: dict[str, Any] | None = None) -> dict[str, Any]:
    if ledger.get("version") != 1:
        raise ContractError("attempt ledger version must be 1")
    attempts = ledger.get("attempts")
    if not isinstance(attempts, list):
        raise ContractError("attempt ledger attempts must be an array")
    numbers: set[int] = set()
    for entry in attempts:
        if not isinstance(entry, dict):
            raise ContractError("attempt ledger entries must be objects")
        number = entry.get("attempt")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number in numbers:
            raise ContractError("attempt ledger attempt numbers must be unique positive integers")
        numbers.add(number)
        if entry.get("status") not in STATUSES:
            raise ContractError(f"attempt {number} has an invalid status")
        events = entry.get("events")
        if not isinstance(events, list) or not events or any(
            not isinstance(event, dict) or event.get("status") not in STATUSES for event in events
        ):
            raise ContractError(f"attempt {number} must have an immutable status event history")
        if events[-1]["status"] != entry["status"]:
            raise ContractError(f"attempt {number} status differs from its last event")
        if not isinstance(entry.get("provider"), str) or not entry["provider"]:
            raise ContractError(f"attempt {number} is missing provider")
    if route is not None:
        if ledger.get("route_sha256") != object_sha256(route):
            raise ContractError("attempt ledger belongs to a different route")
        if ledger.get("task_sha256") != route.get("task_sha256"):
            raise ContractError("attempt ledger task hash differs from the route")
        if ledger.get("config_sha256") != route.get("config_sha256"):
            raise ContractError("attempt ledger config hash differs from the route")
    return ledger


def initialize_ledger(path: Path, route: dict[str, Any]) -> dict[str, Any]:
    if route.get("version") != 1 or not isinstance(route.get("attempts"), list):
        raise ContractError("route must be a version 1 object with attempts")
    now = utc_now()
    ledger = {
        "version": 1,
        "route_sha256": object_sha256(route),
        "task_sha256": route.get("task_sha256"),
        "config_sha256": route.get("config_sha256"),
        "created_at": now,
        "updated_at": now,
        "attempts": [
            {
                "attempt": item["attempt"],
                "provider": item["id"],
                "driver": item.get("driver"),
                "execution": item.get("execution"),
                "status": "planned",
                "cost_status": "not-started",
                "resumable": item["id"] == "xai-direct",
                "events": [{"status": "planned", "at": now}],
            }
            for item in route["attempts"]
        ],
    }
    validate_ledger(ledger, route)
    atomic_write_json(path, ledger)
    return ledger


def progress_path_for(ledger_path: Path, attempt: int) -> Path:
    return ledger_path.with_name(f"{ledger_path.stem}.attempt-{attempt}.progress.json")


def archive_ledger(path: Path) -> list[Path]:
    """Move a superseded ledger and its progress files to timestamped audit paths.

    The caller must hold ``ledger_lock(path)`` so route rollover and execution
    cannot race. Nothing is deleted.
    """

    if not path.is_file():
        return []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    counter = 0
    while True:
        suffix = f"-{counter}" if counter else ""
        archive = path.with_name(f"{path.stem}.archive-{stamp}{suffix}{path.suffix}")
        if not archive.exists():
            break
        counter += 1
    os.replace(path, archive)
    archived = [archive]
    for progress in sorted(path.parent.glob(f"{path.stem}.attempt-*.progress.json")):
        tail = progress.name[len(path.stem):]
        destination = archive.with_name(f"{archive.stem}{tail}")
        os.replace(progress, destination)
        archived.append(destination)
    return archived


def _progress(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read attempt progress file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("attempt progress must be a JSON object")
    return value


def claim_attempt(
    path: Path,
    route: dict[str, Any],
    attempt: int,
    result_path: Path,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    with ledger_lock(path):
        ledger = validate_ledger(read_json(path), route) if path.is_file() else initialize_ledger(path, route)
        entries = [entry for entry in ledger["attempts"] if entry["attempt"] == attempt]
        if len(entries) != 1:
            raise ContractError(f"attempt ledger has no unique attempt {attempt}")
        entry = entries[0]
        progress = _progress(progress_path_for(path, attempt))
        if entry["status"] == "running":
            _transition(
                entry,
                "submitted" if progress.get("request_id") else "uncertain",
                reason="reconciled-interrupted-running-attempt",
            )
            ledger["updated_at"] = utc_now()
            atomic_write_json(path, ledger)
        if entry["status"] in {"submitted", "uncertain"} and progress.get("request_id"):
            entry["request_id"] = str(progress["request_id"])
        if entry["status"] == "succeeded":
            stored_result = Path(str(entry.get("result", {}).get("path", "")))
            expected_hash = entry.get("result", {}).get("sha256")
            if stored_result != result_path.resolve() or not stored_result.is_file():
                raise ContractError("completed attempt result is missing or was requested at a different path")
            if file_sha256(stored_result) != expected_hash:
                raise ContractError("completed attempt result hash no longer matches the ledger")
            return {"idempotent": True, "entry": entry, "resume_request_id": None}
        resume_request_id = entry.get("request_id")
        if resume:
            if not entry.get("resumable") or entry["status"] not in {"submitted", "uncertain"}:
                raise ContractError("attempt is not in a resumable state")
            if not isinstance(resume_request_id, str) or not resume_request_id:
                raise ContractError("resumable attempt has no provider request id")
        elif entry["status"] != "planned":
            raise ContractError(
                f"attempt {attempt} is already {entry['status']}; use the next route attempt or --resume when supported"
            )
        _transition(entry, "running", resumed=resume)
        entry["cost_status"] = "possible"
        entry["started_at"] = entry.get("started_at") or utc_now()
        entry["last_started_at"] = utc_now()
        entry["resume_count"] = int(entry.get("resume_count", 0)) + (1 if resume else 0)
        entry["result_path"] = str(result_path.resolve())
        ledger["updated_at"] = utc_now()
        atomic_write_json(path, ledger)
        return {"idempotent": False, "entry": entry, "resume_request_id": resume_request_id}


def finish_attempt(
    path: Path,
    route: dict[str, Any],
    attempt: int,
    status: str,
    *,
    result_path: Path | None = None,
    generated_path: Path | None = None,
    error: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    if status not in TERMINAL_STATUSES | {"submitted", "uncertain"}:
        raise ContractError(f"cannot finish attempt with status {status!r}")
    with ledger_lock(path):
        ledger = validate_ledger(read_json(path), route)
        entry = next((item for item in ledger["attempts"] if item["attempt"] == attempt), None)
        if entry is None:
            raise ContractError(f"attempt ledger has no attempt {attempt}")
        if entry["status"] == "succeeded" and status != "succeeded":
            raise ContractError("a succeeded attempt cannot transition to another state")
        if entry["status"] != "running":
            raise ContractError(f"attempt {attempt} cannot finish from state {entry['status']}")
        progress = _progress(progress_path_for(path, attempt))
        effective_request_id = request_id or progress.get("request_id") or entry.get("request_id")
        event_details: dict[str, Any] = {}
        if error:
            event_details["error"] = error[:2000]
        if effective_request_id:
            event_details["request_id"] = str(effective_request_id)
        _transition(entry, status, **event_details)
        if effective_request_id:
            entry["request_id"] = str(effective_request_id)
        if error:
            entry["error"] = error[:2000]
        if result_path is not None and result_path.is_file():
            entry["result"] = {"path": str(result_path.resolve()), "sha256": file_sha256(result_path)}
        if generated_path is not None and generated_path.is_file():
            entry["generated"] = {
                "path": str(generated_path.resolve()),
                "sha256": file_sha256(generated_path),
                "bytes": generated_path.stat().st_size,
            }
        if status == "succeeded":
            entry["cost_status"] = "completed"
            entry["completed_at"] = utc_now()
        elif status in {"failed", "rejected"}:
            entry["cost_status"] = "possible"
            entry["completed_at"] = utc_now()
        else:
            entry["cost_status"] = "unknown"
        ledger["updated_at"] = utc_now()
        atomic_write_json(path, ledger)
        return entry


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--ledger", type=Path, required=True)
    init.add_argument("--route", type=Path, required=True)
    show = subparsers.add_parser("show")
    show.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init":
        route = read_json(args.route)
        with ledger_lock(args.ledger):
            if args.ledger.exists():
                ledger = validate_ledger(read_json(args.ledger), route)
            else:
                ledger = initialize_ledger(args.ledger, route)
    else:
        ledger = validate_ledger(read_json(args.ledger))
    print(json.dumps(ledger, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
