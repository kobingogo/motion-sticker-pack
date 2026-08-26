#!/usr/bin/env python3
"""Select an auditable video-provider attempt order."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from config_contract import object_sha256, read_json_object, validate_provider_config, validate_video_task


def read_json(path: Path) -> dict:
    return read_json_object(path)


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
    if explicit != "auto":
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

    return {
        "version": 1,
        "config_sha256": object_sha256(config),
        "task_sha256": object_sha256(task),
        "operation": task.get("operation", "image-to-video"),
        "required_capabilities": sorted(required),
        "selected": attempts[0] if attempts else fallback,
        "attempts": attempts,
        "fallback": fallback,
        "rejected": rejected,
        "max_attempts": max_attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--capabilities", type=Path, required=True)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = route(read_json(args.config), read_json(args.capabilities), read_json(args.task))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
