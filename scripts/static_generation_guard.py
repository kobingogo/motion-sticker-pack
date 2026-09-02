#!/usr/bin/env python3
"""Guard and audit static image-generation attempts.

The image tool may return a top-level ``image_url``/``output_hint`` instead of
an MCP ``content`` array. This ledger makes the call boundary explicit so a
wrapper visibility problem cannot silently become a second generation call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class StaticGenerationGuardError(ValueError):
    """The requested attempt would violate the static-generation contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generation_binding(path: Path) -> str:
    """Hash the immutable generation contract, excluding selected_attempt."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StaticGenerationGuardError("static-generation.json must be a JSON object")
    immutable = dict(value)
    immutable.pop("selected_attempt", None)
    return object_sha256(immutable)


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def load_generation(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise StaticGenerationGuardError("static-generation.json must be a version 1 object")
    if not isinstance(value.get("call_arguments"), dict):
        raise StaticGenerationGuardError("static-generation.json is missing initial call_arguments")
    policy = value.get("generation_policy")
    if not isinstance(policy, dict):
        raise StaticGenerationGuardError("static-generation.json is missing generation_policy")
    max_attempts = int(policy.get("max_static_generation_attempts", 0))
    if max_attempts < 1:
        raise StaticGenerationGuardError("static generation must allow at least one attempt")
    return value


def load_ledger(path: Path, generation: Path) -> dict:
    if not path.exists():
        return {
            "version": 1,
            "kind": "static-generation-attempt-ledger",
            "generation": {"path": str(generation.resolve()), "sha256": generation_binding(generation)},
            "attempts": [],
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("kind") != "static-generation-attempt-ledger":
        raise StaticGenerationGuardError("static-generation-attempts.json has an unsupported format")
    expected = value.get("generation", {}).get("sha256")
    actual = generation_binding(generation)
    if expected != actual:
        raise StaticGenerationGuardError("static-generation.json changed after the attempt ledger was created")
    if not isinstance(value.get("attempts"), list):
        raise StaticGenerationGuardError("static-generation-attempts.json is missing attempts")
    return value


def _attempt(ledger: dict, number: int) -> dict:
    for item in ledger["attempts"]:
        if item.get("attempt") == number:
            return item
    raise StaticGenerationGuardError(f"static generation attempt {number} is not recorded")


def claim_attempt(generation_path: Path, ledger_path: Path, number: int, source_output: Path | None) -> dict:
    generation = load_generation(generation_path)
    ledger = load_ledger(ledger_path, generation_path)
    attempts = ledger["attempts"]
    max_attempts = int(generation["generation_policy"]["max_static_generation_attempts"])
    if not 1 <= number <= max_attempts:
        raise StaticGenerationGuardError(f"attempt must be between 1 and {max_attempts}")
    if any(item.get("attempt") == number for item in attempts):
        raise StaticGenerationGuardError(
            f"static generation attempt {number} is already recorded; inspect its result instead of calling again"
        )
    expected_number = len(attempts) + 1
    if number != expected_number:
        raise StaticGenerationGuardError(
            f"attempt {number} cannot start before attempt {expected_number} is explicitly rejected"
        )
    if attempts and attempts[-1].get("status") not in {"rejected"}:
        raise StaticGenerationGuardError(
            "the previous static-generation attempt is unresolved; check image_url/output_hint and local files first"
        )
    if source_output is not None and source_output.exists():
        raise StaticGenerationGuardError(
            f"raw static output already exists at {source_output}; do not overwrite it with another generation call"
        )
    if number == 1:
        call_arguments = generation["call_arguments"]
        call_kind = "initial"
    else:
        fallback = generation.get("opaque_fallback_call")
        if not isinstance(fallback, dict) or not isinstance(fallback.get("call_arguments"), dict):
            raise StaticGenerationGuardError("static-generation.json is missing opaque fallback call_arguments")
        call_arguments = fallback["call_arguments"]
        call_kind = "opaque-fallback"
    item = {
        "attempt": number,
        "kind": call_kind,
        "status": "claimed",
        "claimed_at": utc_now(),
        "call_arguments_sha256": object_sha256(call_arguments),
        "call_argument_names": sorted(call_arguments),
    }
    ledger["attempts"].append(item)
    atomic_write(ledger_path, ledger)
    return item


def update_attempt(
    generation_path: Path,
    ledger_path: Path,
    number: int,
    command: str,
    source: Path | None = None,
    reason: str | None = None,
) -> dict:
    generation = load_generation(generation_path)
    ledger = load_ledger(ledger_path, generation_path)
    item = _attempt(ledger, number)
    if item is not ledger["attempts"][-1]:
        raise StaticGenerationGuardError("only the current static-generation attempt can be updated")
    status = item.get("status")
    if command == "invoked":
        if status != "claimed":
            raise StaticGenerationGuardError(f"attempt {number} must be claimed before invoked (current: {status})")
        item["status"] = "invoked"
        item["invoked_at"] = utc_now()
    elif command == "accept":
        if status != "invoked":
            raise StaticGenerationGuardError(f"attempt {number} must be invoked before accept (current: {status})")
        if source is None:
            raise StaticGenerationGuardError("accept requires --source")
        resolved = source.expanduser().resolve(strict=True)
        if not resolved.is_file() or resolved.stat().st_size == 0:
            raise StaticGenerationGuardError("accepted static source must be a non-empty file")
        item["status"] = "accepted"
        item["accepted_at"] = utc_now()
        item["source"] = {
            "path": str(resolved),
            "sha256": sha256_file(resolved),
            "size_bytes": resolved.stat().st_size,
        }
        generation["selected_attempt"] = {
            "attempt": number,
            "kind": item["kind"],
            "status": "accepted",
            "source": item["source"],
            "ledger": str(ledger_path.expanduser().resolve()),
        }
        atomic_write(generation_path, generation)
    elif command == "reject":
        if status != "invoked":
            raise StaticGenerationGuardError(f"attempt {number} must be invoked before reject (current: {status})")
        if not reason or not reason.strip():
            raise StaticGenerationGuardError("reject requires a non-empty --reason")
        item["status"] = "rejected"
        item["rejected_at"] = utc_now()
        item["reason"] = reason.strip()
        item["retryable"] = True
    else:
        raise StaticGenerationGuardError(f"unsupported update command: {command}")
    atomic_write(ledger_path, ledger)
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("claim", "invoked", "accept", "reject"))
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--source-output", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--reason")
    args = parser.parse_args()
    generation = args.generation.expanduser().resolve(strict=True)
    ledger = args.ledger.expanduser().resolve()
    if args.command == "claim":
        result = claim_attempt(generation, ledger, args.attempt, args.source_output)
    else:
        result = update_attempt(generation, ledger, args.attempt, args.command, args.source, args.reason)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
