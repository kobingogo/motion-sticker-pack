#!/usr/bin/env python3
"""Append-only, hash-bound lineage manifest for workflow artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from attempt_ledger import atomic_write_json, file_sha256, ledger_lock, utc_now
from config_contract import ContractError


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read artifact manifest from {path}: {exc}") from exc
    if value.get("version") != 1 or not isinstance(value.get("artifacts"), list):
        raise ContractError("artifact manifest must be a version 1 object with artifacts")
    return value


def initialize_manifest(path: Path, workspace: Path) -> dict[str, Any]:
    now = utc_now()
    manifest = {
        "version": 1,
        "workspace": str(workspace.expanduser().resolve()),
        "created_at": now,
        "updated_at": now,
        "artifacts": [],
    }
    atomic_write_json(path, manifest)
    return manifest


def artifact_id(path: Path, sha256: str) -> str:
    return f"sha256:{sha256}:{path.name}"


def record_artifact(
    manifest_path: Path,
    artifact_path: Path,
    *,
    kind: str,
    stage: str,
    dependencies: Iterable[str] = (),
    workspace: Path | None = None,
) -> str:
    artifact = artifact_path.expanduser().resolve()
    if not artifact.is_file():
        raise ContractError(f"artifact does not exist or is not a file: {artifact}")
    digest = file_sha256(artifact)
    identifier = artifact_id(artifact, digest)
    dependency_ids = sorted(set(dependencies))
    with ledger_lock(manifest_path):
        if manifest_path.is_file():
            manifest = read_manifest(manifest_path)
        else:
            manifest = initialize_manifest(manifest_path, workspace or manifest_path.parent)
        known = {item.get("id") for item in manifest["artifacts"] if isinstance(item, dict)}
        missing = [item for item in dependency_ids if item not in known]
        if missing:
            raise ContractError(f"artifact dependencies are not present in the manifest: {missing}")
        existing = next((item for item in manifest["artifacts"] if item.get("id") == identifier), None)
        if existing is not None:
            if existing.get("path") != str(artifact):
                raise ContractError("artifact id collision")
            if not existing.get("current", True):
                for prior in manifest["artifacts"]:
                    if prior.get("path") == str(artifact):
                        prior["current"] = False
                existing["current"] = True
                existing["reactivated_at"] = utc_now()
                manifest["updated_at"] = utc_now()
                atomic_write_json(manifest_path, manifest)
            return identifier
        for prior in manifest["artifacts"]:
            if prior.get("path") == str(artifact) and prior.get("current", True):
                prior["current"] = False
        manifest["artifacts"].append(
            {
                "id": identifier,
                "path": str(artifact),
                "sha256": digest,
                "bytes": artifact.stat().st_size,
                "kind": kind,
                "stage": stage,
                "dependencies": dependency_ids,
                "recorded_at": utc_now(),
                "current": True,
            }
        )
        manifest["updated_at"] = utc_now()
        atomic_write_json(manifest_path, manifest)
    return identifier


def verify_manifest(path: Path) -> dict[str, Any]:
    manifest = read_manifest(path)
    known: set[str] = set()
    for item in manifest["artifacts"]:
        if not isinstance(item, dict):
            raise ContractError("artifact entries must be objects")
        identifier = item.get("id")
        artifact = Path(str(item.get("path", "")))
        if not isinstance(identifier, str) or not identifier:
            raise ContractError("artifact entry is missing id")
        if identifier in known:
            raise ContractError(f"duplicate artifact id: {identifier}")
        if item.get("current", True):
            if not artifact.is_absolute() or not artifact.is_file():
                raise ContractError(f"artifact is missing: {artifact}")
            if file_sha256(artifact) != item.get("sha256"):
                raise ContractError(f"artifact hash mismatch: {artifact}")
        dependencies = item.get("dependencies")
        if not isinstance(dependencies, list) or any(value not in known for value in dependencies):
            raise ContractError(f"artifact has missing or forward dependencies: {identifier}")
        known.add(identifier)
    return {"valid": True, "artifacts": len(known), "manifest": str(path.resolve())}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    record = subparsers.add_parser("record")
    record.add_argument("--manifest", type=Path, required=True)
    record.add_argument("--artifact", type=Path, required=True)
    record.add_argument("--kind", required=True)
    record.add_argument("--stage", required=True)
    record.add_argument("--dependency", action="append", default=[])
    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    show = subparsers.add_parser("show")
    show.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "record":
        result: Any = {
            "id": record_artifact(
                args.manifest,
                args.artifact,
                kind=args.kind,
                stage=args.stage,
                dependencies=args.dependency,
            )
        }
    elif args.command == "verify":
        result = verify_manifest(args.manifest)
    else:
        result = read_manifest(args.manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
