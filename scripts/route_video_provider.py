#!/usr/bin/env python3
"""Select an auditable video-provider attempt order."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from artifact_manifest import record_artifact
from attempt_ledger import (
    archive_ledger,
    atomic_write_json,
    initialize_ledger,
    ledger_lock,
    read_json as read_ledger,
    validate_ledger,
)
from config_contract import ContractError, object_sha256, read_json_object, validate_provider_config, validate_video_task


def read_json(path: Path) -> dict:
    return read_json_object(path)


def dependency_hashes(task: dict) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for field in ("input_image", "layout_file", "prompt_file", "approval_file", "production_settings_file"):
        value = task.get(field)
        if not isinstance(value, str):
            continue
        path = Path(value).expanduser()
        if not path.is_file():
            raise ValueError(f"task dependency does not exist: {field}={path}")
        hashes[field] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def route(config: dict, capabilities: dict, task: dict) -> dict:
    validate_provider_config(config)
    validate_video_task(task)
    if capabilities.get("version") != 1:
        raise ValueError("capabilities report version must be 1")
    if capabilities.get("config_sha256") not in (None, object_sha256(config)):
        raise ValueError("capabilities report was produced from a different provider config")
    all_providers = list(capabilities.get("providers", []))
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in all_providers):
        raise ValueError("capabilities.providers must contain objects with ids")
    ids = [item["id"] for item in all_providers]
    if len(ids) != len(set(ids)):
        raise ValueError("capabilities.providers contains duplicate ids")
    configured = {item["id"]: item for item in config.get("providers", [])}
    trusted_providers = []
    untrusted_rejected = []
    for item in all_providers:
        if item.get("driver") == "native-tool":
            trusted_providers.append(item)
            continue
        configured_item = configured.get(item["id"])
        if not configured_item or not configured_item.get("enabled"):
            untrusted_rejected.append({"id": item["id"], "reason": "not-enabled-in-provider-config"})
            continue
        if configured_item.get("driver") != item.get("driver"):
            untrusted_rejected.append({"id": item["id"], "reason": "driver-mismatch-with-provider-config"})
            continue
        trusted_providers.append(
            {
                **item,
                "driver": configured_item["driver"],
                "provider": configured_item.get("provider"),
                "model": configured_item.get("model"),
                "priority": configured_item["priority"],
                "capabilities": list(configured_item.get("capabilities", [])),
            }
        )
    all_providers = trusted_providers
    available = [item for item in all_providers if item.get("available")]
    operation = task.get("operation", "image-to-video")
    required = {operation} | set(task.get("required_capabilities", []))
    preferred = set(task.get("prefer_capabilities", []))
    explicit = task.get("provider", "auto")
    provider_chain = task.get("provider_chain")
    allow_fallback = bool(task.get("allow_fallback", True))
    local_postprocess = bool(capabilities.get("local_processing", {}).get("video_postprocess"))

    rejected: list[dict] = untrusted_rejected + [
        {
            "id": item["id"],
            "reason": "unavailable",
            "details": item.get("reasons", []),
        }
        for item in all_providers
        if not item.get("available")
    ]
    eligible: list[dict] = []
    for item in available:
        offered = set(item.get("capabilities", []))
        missing = sorted(required - offered)
        if missing:
            rejected.append({"id": item["id"], "reason": "missing-capabilities", "missing": missing})
            continue
        if task.get("require_alpha") and "alpha-output" not in offered:
            can_matte = task.get("allow_key_background", False) and local_postprocess
            if not can_matte:
                rejected.append({"id": item["id"], "reason": "alpha-unavailable"})
                continue
        class_rank = 0 if item.get("driver") == "native-tool" else 1
        preference_hits = len(preferred & offered)
        eligible.append(
            {
                **item,
                "postprocess_alpha": bool(task.get("require_alpha") and "alpha-output" not in offered),
                "_sort": (class_rank, -int(item.get("priority", 0)), -preference_hits, item["id"]),
            }
        )

    eligible.sort(key=lambda item: item["_sort"])
    if provider_chain:
        eligible_by_id = {item["id"]: item for item in eligible}
        ordered = []
        for provider_id in provider_chain:
            item = eligible_by_id.get(provider_id)
            if item is None:
                rejected.append({"id": provider_id, "reason": "provider-chain-member-not-eligible"})
            else:
                ordered.append(item)
        eligible = ordered if allow_fallback else ordered[:1]
    elif explicit != "auto":
        selected = [item for item in eligible if item["id"] == explicit]
        if selected and allow_fallback:
            eligible = selected + [item for item in eligible if item["id"] != explicit]
        elif selected:
            eligible = selected
        else:
            rejected.append({"id": explicit, "reason": "explicit-provider-not-eligible"})
            eligible = eligible if allow_fallback else []

    max_attempts = int(config.get("routing", {}).get("max_attempts", 3))
    attempts = []
    for index, item in enumerate(eligible[:max_attempts], start=1):
        cleaned = {key: value for key, value in item.items() if key != "_sort"}
        execution = task.get("provider_execution", {}).get(item["id"])
        if execution:
            cleaned["execution"] = execution
        attempts.append({"attempt": index, **cleaned})

    fallback = None
    local = capabilities.get("local_processing", {})
    fallback_policy = config.get("routing", {}).get("fallback", "none")
    if allow_fallback and fallback_policy in {"keypose-local", "transform-local", "keyframe-local"} and local.get("keypose_local"):
        fallback = {
            "id": "keypose-local",
            "driver": "local-processing",
            "reason": "use callable image generation for key poses, then assemble locally",
        }
    elif allow_fallback and fallback_policy in {"keypose-local", "transform-local", "keyframe-local"} and (
        local.get("transform_local") or local.get("keyframe_local")
    ):
        fallback = {
            "id": "transform-local",
            "driver": "local-processing",
            "reason": "last-resort whole-sticker affine keyframes",
        }
    elif allow_fallback and (fallback_policy == "prompt-only" or not local.get("transform_local")):
        fallback = {"id": "prompt-only", "driver": "none"}

    external_attempts = [item for item in attempts if item.get("driver") != "native-tool"]
    preflight = {
        "ready": bool(attempts or fallback),
        "selected_provider": attempts[0]["id"] if attempts else fallback.get("id") if fallback else None,
        "billable_external_attempts": [item["attempt"] for item in external_attempts],
        "charge_authorization_required": bool(external_attempts),
        "cost_estimate": "unknown-check-provider-account-before-execution" if external_attempts else "local-or-host-tool",
        "resume_support": {
            str(item["attempt"]): item.get("id") == "xai-direct" for item in external_attempts
        },
        "blockers": [item for item in rejected if item.get("reason") == "unavailable"],
        "notes": [
            "availability does not prove remote quota or service health",
            "each external attempt requires an explicit executor invocation and is never auto-retried",
        ],
    }
    return {
        "version": 1,
        "config_sha256": object_sha256(config),
        "capabilities_sha256": object_sha256(capabilities),
        "task_sha256": object_sha256(task),
        "dependency_sha256": dependency_hashes(task),
        "operation": task.get("operation", "image-to-video"),
        "required_capabilities": sorted(required),
        "selected": attempts[0] if attempts else fallback,
        "attempts": attempts,
        "fallback": fallback,
        "rejected": rejected,
        "max_attempts": max_attempts,
        "provider_chain": provider_chain,
        "selection_reason": (
            "task-provider-chain" if provider_chain else
            "explicit-provider" if explicit != "auto" else
            "local-first-priority"
        ),
        "preflight": preflight,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--capabilities", type=Path, required=True)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument(
        "--archive-existing-ledger",
        action="store_true",
        help="archive a ledger bound to an older route before creating the new one",
    )
    args = parser.parse_args()
    config = read_json(args.config)
    capability_report = read_json(args.capabilities)
    task = read_json(args.task)
    result = route(config, capability_report, task)
    ledger_path = (
        args.ledger
        or Path(task.get("attempt_ledger_file") or args.output.resolve().parent / "attempt-ledger.json")
    ).expanduser().resolve()
    route_path = args.output.expanduser().resolve()
    manifest_value = task.get("artifact_manifest_file")
    manifest_path = Path(manifest_value).expanduser().resolve() if isinstance(manifest_value, str) else None
    route_inputs = {
        args.config.expanduser().resolve(),
        args.capabilities.expanduser().resolve(),
        args.task.expanduser().resolve(),
    }
    if ledger_path in route_inputs or (manifest_path is not None and manifest_path in route_inputs | {ledger_path}):
        raise ValueError("route inputs, attempt ledger, and artifact manifest must use distinct paths")
    protected_paths = route_inputs | {ledger_path}
    if manifest_path is not None:
        protected_paths.add(manifest_path)
    if route_path in protected_paths:
        raise ValueError("route output must use a path distinct from its inputs, ledger, and manifest")
    archived_ledgers: list[Path] = []
    with ledger_lock(ledger_path):
        if ledger_path.is_file():
            try:
                validate_ledger(read_ledger(ledger_path), result)
            except ContractError:
                if not args.archive_existing_ledger:
                    raise
                archived_ledgers = archive_ledger(ledger_path)
        route_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(route_path, result)
        if not ledger_path.is_file():
            initialize_ledger(ledger_path, result)
    if manifest_path is not None:
        for archived in archived_ledgers:
            record_artifact(
                manifest_path,
                archived,
                kind="attempt-ledger-archive",
                stage="routed",
            )
        config_id = record_artifact(manifest_path, args.config, kind="provider-config", stage="routed")
        capabilities_id = record_artifact(
            manifest_path, args.capabilities, kind="capability-report", stage="routed"
        )
        task_id = record_artifact(manifest_path, args.task, kind="video-task", stage="routed")
        record_artifact(
            manifest_path,
            route_path,
            kind="provider-route",
            stage="routed",
            dependencies=[config_id, capabilities_id, task_id],
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
