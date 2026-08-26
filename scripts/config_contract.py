#!/usr/bin/env python3
"""Validate provider and task configuration without loading secrets."""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any


DRIVERS = {"native-tool", "ai-sdk", "command", "http-job"}
FALLBACKS = {"keypose-local", "transform-local", "keyframe-local", "prompt-only", "none"}
KNOWN_AI_SDK_PACKAGES = {
    "xai": "@ai-sdk/xai",
    "klingai": "@ai-sdk/klingai",
    "bytedance": "@ai-sdk/bytedance",
    "alibaba": "@ai-sdk/alibaba",
    "fal": "@ai-sdk/fal",
}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
PLACEHOLDER_RE = re.compile(r"replace-with|your-|example", re.IGNORECASE)
SECRET_FIELD_RE = re.compile(r"(?:api[_-]?key|secret|token|password|credential)", re.IGNORECASE)


def is_interpreter(executable: str) -> bool:
    name = Path(executable).name
    return name in {"node", "bun", "deno", "python"} or name.startswith("python3")


class ContractError(ValueError):
    """Raised when untrusted JSON does not satisfy the runtime contract."""


def object_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain a JSON object")
    return value


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ContractError(f"{field} must be a {'possibly empty ' if allow_empty else 'non-empty '}array")
    result = [_nonempty_string(item, f"{field}[]") for item in value]
    if len(result) != len(set(result)):
        raise ContractError(f"{field} must not contain duplicates")
    return result


def validate_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    unknown_top = set(config) - {"$schema", "version", "routing", "providers"}
    if unknown_top:
        raise ContractError(f"unknown provider config fields: {sorted(unknown_top)}")
    if config.get("version") != 1:
        raise ContractError("provider config version must be 1")
    routing = config.get("routing")
    if not isinstance(routing, dict):
        raise ContractError("routing must be an object")
    unknown_routing = set(routing) - {"policy", "max_attempts", "fallback"}
    if unknown_routing:
        raise ContractError(f"unknown routing fields: {sorted(unknown_routing)}")
    if routing.get("policy") != "local-first":
        raise ContractError("routing.policy must be local-first")
    max_attempts = routing.get("max_attempts")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 10:
        raise ContractError("routing.max_attempts must be an integer from 1 to 10")
    if routing.get("fallback") not in FALLBACKS:
        raise ContractError(f"routing.fallback must be one of {sorted(FALLBACKS)}")

    providers = config.get("providers")
    if not isinstance(providers, list):
        raise ContractError("providers must be an array")
    seen: set[str] = set()
    for index, provider in enumerate(providers):
        prefix = f"providers[{index}]"
        if not isinstance(provider, dict):
            raise ContractError(f"{prefix} must be an object")
        allowed_provider_fields = {
            "id", "driver", "enabled", "priority", "capabilities", "credentials", "provider",
            "package", "model", "tool", "command", "adapter_command", "provider_options",
        }
        unknown_provider = set(provider) - allowed_provider_fields
        if unknown_provider:
            raise ContractError(f"{prefix} contains unknown fields: {sorted(unknown_provider)}")
        provider_id = _nonempty_string(provider.get("id"), f"{prefix}.id")
        if not ID_RE.fullmatch(provider_id):
            raise ContractError(f"{prefix}.id has invalid characters")
        if provider_id in seen:
            raise ContractError(f"duplicate provider id: {provider_id}")
        seen.add(provider_id)
        driver = provider.get("driver")
        if driver not in DRIVERS:
            raise ContractError(f"{prefix}.driver must be one of {sorted(DRIVERS)}")
        if not isinstance(provider.get("enabled"), bool):
            raise ContractError(f"{prefix}.enabled must be boolean")
        priority = provider.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 100:
            raise ContractError(f"{prefix}.priority must be an integer from 0 to 100")
        _string_list(provider.get("capabilities"), f"{prefix}.capabilities")

        credentials = provider.get("credentials", {})
        if not isinstance(credentials, dict):
            raise ContractError(f"{prefix}.credentials must be an object")
        if set(credentials) - {"env", "optional_env"}:
            raise ContractError(f"{prefix}.credentials supports only env and optional_env")
        env_names = _string_list(credentials.get("env", []), f"{prefix}.credentials.env", allow_empty=True)
        optional_env_names = _string_list(
            credentials.get("optional_env", []),
            f"{prefix}.credentials.optional_env",
            allow_empty=True,
        )
        if any(not ENV_RE.fullmatch(name) for name in env_names):
            raise ContractError(f"{prefix}.credentials.env contains an invalid environment-variable name")
        if any(not ENV_RE.fullmatch(name) for name in optional_env_names):
            raise ContractError(f"{prefix}.credentials.optional_env contains an invalid environment-variable name")
        if set(env_names) & set(optional_env_names):
            raise ContractError(f"{prefix}.credentials env and optional_env must not overlap")

        if driver == "native-tool":
            _nonempty_string(provider.get("tool"), f"{prefix}.tool")
        elif driver == "ai-sdk":
            provider_name = _nonempty_string(provider.get("provider"), f"{prefix}.provider")
            package = _nonempty_string(provider.get("package"), f"{prefix}.package")
            model = _nonempty_string(provider.get("model"), f"{prefix}.model")
            expected = KNOWN_AI_SDK_PACKAGES.get(provider_name)
            if expected is None:
                raise ContractError(
                    f"{prefix}.provider {provider_name!r} has no bundled AI SDK executor; use driver=command"
                )
            if package != expected:
                raise ContractError(f"{prefix}.package must be {expected!r} for provider {provider_name!r}")
            if not env_names:
                raise ContractError(f"{prefix}.credentials.env must name at least one credential variable")
            if provider.get("enabled") and PLACEHOLDER_RE.search(model):
                raise ContractError(f"{prefix}.model is still a placeholder while the provider is enabled")
            options = provider.get("provider_options", {})
            if not isinstance(options, dict):
                raise ContractError(f"{prefix}.provider_options must be an object")
            stack: list[tuple[str, Any]] = [("provider_options", options)]
            while stack:
                location, value = stack.pop()
                if isinstance(value, dict):
                    for key, child in value.items():
                        if SECRET_FIELD_RE.search(str(key)):
                            raise ContractError(
                                f"{prefix}.{location} contains secret-like field {key!r}; use environment variables"
                            )
                        stack.append((f"{location}.{key}", child))
                elif isinstance(value, list):
                    stack.extend((f"{location}[]", child) for child in value)
        elif driver == "command":
            command = _string_list(provider.get("command"), f"{prefix}.command")
            if is_interpreter(command[0]):
                if len(command) < 2 or not Path(command[1]).expanduser().is_absolute():
                    raise ContractError(f"{prefix}.command interpreter entrypoint must be an absolute path")
        elif driver == "http-job":
            command = _string_list(provider.get("adapter_command"), f"{prefix}.adapter_command")
            if is_interpreter(command[0]):
                if len(command) < 2 or not Path(command[1]).expanduser().is_absolute():
                    raise ContractError(f"{prefix}.adapter_command interpreter entrypoint must be an absolute path")
    return config


def validate_video_task(task: dict[str, Any], *, require_execution_fields: bool = False) -> dict[str, Any]:
    allowed_fields = {
        "$schema", "version", "operation", "required_capabilities", "prefer_capabilities", "require_alpha",
        "allow_key_background", "allow_fallback", "provider", "input_image", "layout_file", "prompt_file",
        "approval_file", "output_directory", "duration_seconds", "timeout_seconds", "max_output_bytes",
        "max_input_image_bytes", "aspect_ratio", "resolution", "fps",
    }
    unknown = set(task) - allowed_fields
    if unknown:
        raise ContractError(f"unknown video task fields: {sorted(unknown)}")
    if any(SECRET_FIELD_RE.search(str(key)) for key in task):
        raise ContractError("video task must not contain literal credential fields")
    if task.get("version") != 1:
        raise ContractError("video task version must be 1")
    if task.get("operation", "image-to-video") != "image-to-video":
        raise ContractError("this workflow currently executes only image-to-video tasks")
    for field in ("required_capabilities", "prefer_capabilities"):
        if field in task:
            _string_list(task[field], field, allow_empty=True)
    provider = task.get("provider", "auto")
    if provider != "auto" and (not isinstance(provider, str) or not ID_RE.fullmatch(provider)):
        raise ContractError("task.provider must be auto or a valid provider id")
    for field in ("allow_fallback", "require_alpha", "allow_key_background"):
        if field in task and not isinstance(task[field], bool):
            raise ContractError(f"{field} must be boolean")
    if require_execution_fields:
        for field in ("input_image", "layout_file", "prompt_file", "approval_file", "output_directory"):
            value = Path(_nonempty_string(task.get(field), field)).expanduser()
            if not value.is_absolute():
                raise ContractError(f"{field} must be an absolute path")
        duration = task.get("duration_seconds", 3)
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not 1 <= duration <= 30:
            raise ContractError("duration_seconds must be between 1 and 30")
        timeout = task.get("timeout_seconds", 900)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 30 <= timeout <= 3600:
            raise ContractError("timeout_seconds must be between 30 and 3600")
        fps = task.get("fps")
        if fps is not None and (not isinstance(fps, int) or isinstance(fps, bool) or not 1 <= fps <= 60):
            raise ContractError("fps must be an integer from 1 to 60")
        for field, default in (("max_output_bytes", 200 * 1024 * 1024), ("max_input_image_bytes", 25 * 1024 * 1024)):
            value = task.get(field, default)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1024:
                raise ContractError(f"{field} must be an integer of at least 1024")
    return task
