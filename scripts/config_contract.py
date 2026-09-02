#!/usr/bin/env python3
"""Validate provider and task configuration without loading secrets."""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any


DRIVERS = {"native-tool", "ai-sdk", "command", "http-job"}
FALLBACKS = {
    "keypose-local",
    "light-motion-local",
    "transform-local",  # deprecated v0.3 compatibility alias
    "keyframe-local",   # deprecated pre-v0.3 compatibility alias
    "prompt-only",
    "none",
}
KNOWN_AI_SDK_PACKAGES = {
    "xai": "@ai-sdk/xai",
    "klingai": "@ai-sdk/klingai",
    "bytedance": "@ai-sdk/bytedance",
    "alibaba": "@ai-sdk/alibaba",
    "fal": "@ai-sdk/fal",
}
AI_SDK_AUTH_ENV_SETS = {
    "xai": {("XAI_API_KEY",)},
    "klingai": {
        ("KLINGAI_API_KEY",),
        ("KLINGAI_ACCESS_KEY", "KLINGAI_SECRET_KEY"),
    },
    "bytedance": {("ARK_API_KEY",)},
    # The Vercel provider names ALIBABA_API_KEY, while Alibaba's own examples
    # commonly use DASHSCOPE_API_KEY. The gateway explicitly maps either name.
    "alibaba": {("ALIBABA_API_KEY",), ("DASHSCOPE_API_KEY",)},
    "fal": {("FAL_API_KEY",), ("FAL_KEY",)},
}
AI_SDK_REGIONS = {
    "xai": {"global"},
    "klingai": {"global"},
    "bytedance": {"international", "china"},
    "alibaba": {"international", "china"},
    "fal": {"global"},
}
AI_SDK_PROVIDER_OPTION_FIELDS = {
    "xai": {"mode", "videoUrl", "referenceImageUrls", "referenceVoiceIds", "resolution", "user"},
    "klingai": {
        "mode", "negativePrompt", "sound", "cfgScale", "cameraControl", "imageTail", "staticMask",
        "dynamicMasks", "multiShot", "shotType", "multiPrompt", "elementList", "voiceList",
        "watermarkEnabled", "videoUrl", "characterOrientation", "keepOriginalSound",
    },
    "bytedance": {
        "watermark", "generateAudio", "cameraFixed", "returnLastFrame", "serviceTier", "draft",
        "lastFrameImage", "referenceImages", "referenceVideos", "referenceAudio",
    },
    "alibaba": {
        "negativePrompt", "audioUrl", "promptExtend", "shotType", "watermark", "audio",
        "referenceUrls", "media", "ratio",
    },
    "fal": {"loop", "motionStrength", "resolution", "negativePrompt", "promptOptimizer"},
}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
PLACEHOLDER_RE = re.compile(r"replace-with|your-|example", re.IGNORECASE)
SECRET_FIELD_RE = re.compile(
    r"(?:api[_-]?key|secret|token|password|credential|authorization|auth(?:entication)?)",
    re.IGNORECASE,
)


def executable_stem(executable: str) -> str:
    # pathlib follows the host OS and therefore does not split a Windows path
    # when validation runs on macOS/Linux. Normalize both separators first.
    name = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def is_interpreter(executable: str) -> bool:
    name = executable_stem(executable)
    return name in {"node", "bun", "deno", "python", "python3", "py"} or name.startswith("python3")


def is_python_interpreter(executable: str) -> bool:
    name = executable_stem(executable)
    return name in {"python", "python3", "py"} or name.startswith("python3")


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
            "package", "model", "region", "tool", "command", "adapter_command", "provider_options",
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
            accepted_auth = AI_SDK_AUTH_ENV_SETS[provider_name]
            if tuple(env_names) not in accepted_auth:
                readable = [" + ".join(names) for names in sorted(accepted_auth)]
                raise ContractError(
                    f"{prefix}.credentials.env must be one supported authentication set for "
                    f"{provider_name!r}: {readable}"
                )
            region = provider.get("region")
            if region is not None:
                region = _nonempty_string(region, f"{prefix}.region")
                allowed_regions = AI_SDK_REGIONS[provider_name]
                if region not in allowed_regions:
                    raise ContractError(
                        f"{prefix}.region must be one of {sorted(allowed_regions)} for provider {provider_name!r}"
                    )
            if provider.get("enabled") and PLACEHOLDER_RE.search(model):
                raise ContractError(f"{prefix}.model is still a placeholder while the provider is enabled")
            if provider.get("enabled") and "image-to-video" in provider.get("capabilities", []):
                lowered_model = model.lower()
                incompatible = (
                    (provider_name == "klingai" and ("-t2v" in lowered_model or "motion-control" in lowered_model))
                    or (provider_name == "bytedance" and "-t2v-" in lowered_model)
                    or (provider_name == "alibaba" and ("-t2v" in lowered_model or "-r2v" in lowered_model))
                    or (
                        provider_name == "fal"
                        and lowered_model
                        in {
                            "minimax-video",
                            "minimax-video-01",
                            "luma-dream-machine",
                            "luma-ray-2",
                            "luma-ray-2-flash",
                            "luma-dream-machine/ray-2",
                        }
                    )
                )
                if incompatible:
                    raise ContractError(
                        f"{prefix}.model {model!r} is not an image-to-video model but the provider claims that capability"
                    )
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
            unknown_options = set(options) - AI_SDK_PROVIDER_OPTION_FIELDS[provider_name]
            if unknown_options:
                raise ContractError(
                    f"{prefix}.provider_options contains unsupported fields for {provider_name!r}: "
                    f"{sorted(unknown_options)}"
                )
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
        "allow_key_background", "key_color", "provider_key_colors", "provider_input_images", "screen_selection",
        "allow_fallback", "provider", "input_image", "layout_file", "prompt_file",
        "provider_chain", "provider_execution", "provider_selection_source",
        "approval_file", "output_directory", "duration_seconds", "provider_duration_seconds", "timeout_seconds", "max_output_bytes",
        "max_input_image_bytes", "aspect_ratio", "resolution", "fps", "poll_interval_ms", "max_retries",
        "safe_grid_scale", "min_guard_fraction", "max_foreground_bbox_fraction",
        "motion_active_seconds", "loop_min_seconds", "loop_max_seconds", "production_settings_file",
        "attempt_ledger_file", "artifact_manifest_file",
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
    provider_chain = task.get("provider_chain")
    if provider_chain is not None:
        provider_chain = _string_list(provider_chain, "provider_chain")
        if any(not ID_RE.fullmatch(provider_id) for provider_id in provider_chain):
            raise ContractError("provider_chain entries must be valid provider ids")
        if provider == "auto" or provider_chain[0] != provider:
            raise ContractError("provider_chain must start with the explicit task.provider")
    if "provider_selection_source" in task and task["provider_selection_source"] not in {
        "production-settings", "task-override"
    }:
        raise ContractError("provider_selection_source must be production-settings or task-override")
    provider_execution = task.get("provider_execution")
    if provider_execution is not None:
        if not isinstance(provider_execution, dict) or not provider_execution:
            raise ContractError("provider_execution must be a non-empty object")
        for provider_id, execution in provider_execution.items():
            if not isinstance(provider_id, str) or not ID_RE.fullmatch(provider_id):
                raise ContractError("provider_execution keys must be valid provider ids")
            if not isinstance(execution, dict) or set(execution) != {"duration_seconds", "resolution"}:
                raise ContractError(
                    "provider_execution values must contain only duration_seconds and resolution"
                )
            duration = execution["duration_seconds"]
            if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 15:
                raise ContractError("provider_execution duration_seconds must be an integer from 1 to 15")
            if execution["resolution"] not in {"480p", "720p"}:
                raise ContractError("provider_execution resolution must be 480p or 720p")
        required_execution = provider_chain or ([] if provider == "auto" else [provider])
        missing_execution = [provider_id for provider_id in required_execution if provider_id not in provider_execution]
        if missing_execution:
            raise ContractError(f"provider_execution is missing providers: {missing_execution}")
    provider_durations = task.get("provider_duration_seconds")
    if provider_durations is not None:
        if not isinstance(provider_durations, dict) or not provider_durations:
            raise ContractError("provider_duration_seconds must be a non-empty object")
        for provider_id, duration in provider_durations.items():
            if not isinstance(provider_id, str) or not ID_RE.fullmatch(provider_id):
                raise ContractError("provider_duration_seconds keys must be valid provider ids")
            if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 15:
                raise ContractError("provider_duration_seconds values must be integers from 1 to 15")
        if provider != "auto" and provider not in provider_durations:
            raise ContractError(f"provider_duration_seconds is missing selected provider {provider}")
    for field in ("allow_fallback", "require_alpha", "allow_key_background"):
        if field in task and not isinstance(task[field], bool):
            raise ContractError(f"{field} must be boolean")
    if provider_chain is not None and not task.get("allow_fallback", False) and len(provider_chain) > 1:
        raise ContractError("provider_chain may contain fallbacks only when allow_fallback is true")
    if "key_color" in task and (
        not isinstance(task["key_color"], str) or not HEX_COLOR_RE.fullmatch(task["key_color"])
    ):
        raise ContractError("key_color must use #RRGGBB notation")
    for field in ("provider_key_colors", "provider_input_images"):
        values = task.get(field)
        if values is None:
            continue
        if not isinstance(values, dict) or set(values) != set(provider_chain or [provider]):
            raise ContractError(f"{field} must contain exactly one entry per provider_chain provider")
        for provider_id, value in values.items():
            if not ID_RE.fullmatch(provider_id) or not isinstance(value, str) or not value:
                raise ContractError(f"{field} contains an invalid provider entry")
            if field == "provider_key_colors" and not HEX_COLOR_RE.fullmatch(value):
                raise ContractError("provider_key_colors values must use #RRGGBB notation")
    if "screen_selection" in task and not isinstance(task["screen_selection"], dict):
        raise ContractError("screen_selection must be an object")
    if "safe_grid_scale" in task:
        scale = task["safe_grid_scale"]
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0.75 <= scale <= 0.95:
            raise ContractError("safe_grid_scale must be between 0.75 and 0.95")
    numeric_ranges = {
        "min_guard_fraction": (0.05, 0.20),
        "max_foreground_bbox_fraction": (0.60, 0.75),
        "motion_active_seconds": (0.5, 4.0),
        "loop_min_seconds": (0.5, 5.0),
        "loop_max_seconds": (0.5, 6.0),
    }
    for field, (minimum, maximum) in numeric_ranges.items():
        if field not in task:
            continue
        value = task[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
            raise ContractError(f"{field} must be between {minimum} and {maximum}")
    if task.get("loop_min_seconds", 1.5) > task.get("loop_max_seconds", 2.5):
        raise ContractError("loop_min_seconds must not exceed loop_max_seconds")
    guard = float(task.get("min_guard_fraction", 0.10))
    foreground = float(task.get("max_foreground_bbox_fraction", 0.75))
    if foreground > 1.0 - 2.0 * guard + 1e-6:
        raise ContractError("max_foreground_bbox_fraction exceeds the requested two-sided guard")
    if require_execution_fields:
        resolved_paths: dict[str, Path] = {}
        for field in ("input_image", "layout_file", "prompt_file", "approval_file", "output_directory"):
            value = Path(_nonempty_string(task.get(field), field)).expanduser()
            if not value.is_absolute():
                raise ContractError(f"{field} must be an absolute path")
            resolved_paths[field] = value.resolve(strict=False)
        for field in ("production_settings_file", "attempt_ledger_file", "artifact_manifest_file"):
            if field not in task:
                continue
            state_file = Path(_nonempty_string(task.get(field), field)).expanduser()
            if not state_file.is_absolute():
                raise ContractError(f"{field} must be an absolute path")
            resolved_paths[field] = state_file.resolve(strict=False)
        for provider_id, value in task.get("provider_input_images", {}).items():
            provider_input = Path(value).expanduser()
            if not provider_input.is_absolute() or not provider_input.is_file():
                raise ContractError(f"provider_input_images[{provider_id!r}] must be an existing absolute file")
        protected_state = [
            field for field in ("attempt_ledger_file", "artifact_manifest_file") if field in resolved_paths
        ]
        for field in protected_state:
            collisions = [
                other
                for other, path in resolved_paths.items()
                if other != field and path == resolved_paths[field]
            ]
            if collisions:
                raise ContractError(f"{field} must not overlap task path fields: {collisions}")
            if resolved_paths[field].is_relative_to(resolved_paths["output_directory"]):
                raise ContractError(f"{field} must live outside output_directory")
        duration = task.get("duration_seconds", 6)
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not 1 <= duration <= 30:
            raise ContractError("duration_seconds must be between 1 and 30")
        timeout = task.get("timeout_seconds", 900)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 30 <= timeout <= 3600:
            raise ContractError("timeout_seconds must be between 30 and 3600")
        fps = task.get("fps")
        if fps is not None and (not isinstance(fps, int) or isinstance(fps, bool) or not 1 <= fps <= 60):
            raise ContractError("fps must be an integer from 1 to 60")
        poll_interval = task.get("poll_interval_ms", 5000)
        if (
            not isinstance(poll_interval, int)
            or isinstance(poll_interval, bool)
            or not 100 <= poll_interval <= 60000
        ):
            raise ContractError("poll_interval_ms must be an integer from 100 to 60000")
        max_retries = task.get("max_retries", 0)
        if (
            not isinstance(max_retries, int)
            or isinstance(max_retries, bool)
            or not 0 <= max_retries <= 3
        ):
            raise ContractError("max_retries must be an integer from 0 to 3")
        for field, default in (("max_output_bytes", 200 * 1024 * 1024), ("max_input_image_bytes", 25 * 1024 * 1024)):
            value = task.get(field, default)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1024:
                raise ContractError(f"{field} must be an integer of at least 1024")
    return task
