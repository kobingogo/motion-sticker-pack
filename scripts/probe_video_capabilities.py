#!/usr/bin/env python3
"""Probe configured video routes without exposing credential values."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

from config_contract import is_interpreter, object_sha256, read_json_object, validate_provider_config


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VIDEO_GATEWAY = Path(__file__).with_name("video_gateway.mjs")


def load_json(path: Path | None, default: dict) -> dict:
    if path is None:
        return default
    return read_json_object(path)


def package_available(package: str | None) -> bool:
    if not package or not shutil.which("node"):
        return False
    command = ["node", "-e", "require.resolve(process.argv[1])", package]
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def ai_sdk_executor_available(provider: dict) -> bool:
    if not VIDEO_GATEWAY.is_file() or not package_available(provider.get("package")):
        return False
    command = [
        "node",
        str(VIDEO_GATEWAY),
        "--check-provider",
        provider.get("provider", ""),
        "--package",
        provider.get("package", ""),
    ]
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def command_available(command: list[str] | None) -> bool:
    if not command:
        return False
    executable = command[0]
    if Path(executable).is_absolute():
        executable_ready = Path(executable).is_file() and os.access(executable, os.X_OK)
    else:
        executable_ready = shutil.which(executable) is not None
    if not executable_ready:
        return False
    if len(command) > 1 and is_interpreter(executable):
        entrypoint = Path(command[1]).expanduser()
        if entrypoint.is_absolute() and not entrypoint.is_file():
            return False
    return True


def credentials_status(provider: dict) -> tuple[bool, list[str]]:
    names = list(provider.get("credentials", {}).get("env", []))
    missing = [name for name in names if not os.environ.get(name)]
    return not missing, missing


def configured_provider_status(provider: dict, runtime_tools: dict[str, dict]) -> dict:
    credentials_ready, missing_env = credentials_status(provider)
    driver = provider["driver"]
    reasons: list[str] = []
    implementation_ready = False

    if not provider.get("enabled", False):
        reasons.append("disabled")
    if not credentials_ready:
        reasons.append("missing-credential-env")

    if driver == "native-tool":
        tool_id = provider.get("tool")
        tool = runtime_tools.get(tool_id, {})
        implementation_ready = bool(tool.get("available", False))
        if not implementation_ready:
            reasons.append("native-tool-not-callable")
    elif driver == "ai-sdk":
        implementation_ready = ai_sdk_executor_available(provider)
        if not implementation_ready:
            reasons.append("ai-sdk-executor-not-callable")
    elif driver == "command":
        implementation_ready = command_available(provider.get("command"))
        if not implementation_ready:
            reasons.append("adapter-command-not-executable")
    elif driver == "http-job":
        implementation_ready = command_available(provider.get("adapter_command"))
        if not implementation_ready:
            reasons.append("http-job-adapter-not-executable")
    else:
        reasons.append("unknown-driver")

    available = bool(provider.get("enabled", False) and credentials_ready and implementation_ready)
    return {
        "id": provider["id"],
        "driver": driver,
        "provider": provider.get("provider"),
        "model": provider.get("model"),
        "priority": int(provider.get("priority", 0)),
        "capabilities": sorted(set(provider.get("capabilities", []))),
        "available": available,
        "credentials_ready": credentials_ready,
        "missing_env": missing_env,
        "optional_env_present": sorted(
            name
            for name in provider.get("credentials", {}).get("optional_env", [])
            if os.environ.get(name)
        ),
        "implementation_ready": implementation_ready,
        "reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--tool-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = validate_provider_config(
        load_json(
            args.config,
            {
                "version": 1,
                "routing": {"policy": "local-first", "max_attempts": 3, "fallback": "keypose-local"},
                "providers": [],
            },
        )
    )
    manifest = load_json(args.tool_manifest, {"tools": []})
    tools = manifest.get("tools", [])
    if not isinstance(tools, list) or any(not isinstance(item, dict) for item in tools):
        raise ValueError("tool manifest tools must be an array of objects")
    runtime_tools = {}
    for item in tools:
        if not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("every runtime tool requires a non-empty id")
        if item["id"] in runtime_tools:
            raise ValueError(f"duplicate runtime tool id: {item['id']}")
        if not isinstance(item.get("available", False), bool):
            raise ValueError(f"runtime tool {item['id']} available must be boolean")
        if not isinstance(item.get("capabilities", []), list):
            raise ValueError(f"runtime tool {item['id']} capabilities must be an array")
        runtime_tools[item["id"]] = item
    providers = [configured_provider_status(item, runtime_tools) for item in config.get("providers", [])]

    configured_ids = {item["id"] for item in providers}
    for tool in runtime_tools.values():
        if not tool.get("available", False) or tool["id"] in configured_ids:
            continue
        providers.append(
            {
                "id": tool["id"],
                "driver": "native-tool",
                "provider": "local-runtime",
                "model": tool.get("model"),
                "priority": int(tool.get("priority", 100)),
                "capabilities": sorted(set(tool.get("capabilities", []))),
                "available": True,
                "credentials_ready": True,
                "missing_env": [],
                "implementation_ready": True,
                "reasons": [],
            }
        )

    local_processing = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "pillow": importlib.util.find_spec("PIL") is not None,
        "numpy": importlib.util.find_spec("numpy") is not None,
    }
    local_processing["image_generation"] = any(
        tool.get("available", False) and "image-generation" in tool.get("capabilities", [])
        for tool in runtime_tools.values()
    )
    local_processing["transform_local"] = local_processing["pillow"] and local_processing["numpy"]
    local_processing["keypose_local"] = (
        local_processing["transform_local"] and local_processing["image_generation"]
    )
    local_processing["keyframe_local"] = local_processing["transform_local"]
    local_processing["video_postprocess"] = (
        local_processing["ffmpeg"]
        and local_processing["ffprobe"]
        and local_processing["pillow"]
        and local_processing["numpy"]
    )
    report = {
        "version": 1,
        "config_sha256": object_sha256(config),
        "providers": providers,
        "local_processing": local_processing,
        "notes": [
            "availability proves configuration and implementation presence, not quota or remote service health",
            "credential values were not read into this report",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
