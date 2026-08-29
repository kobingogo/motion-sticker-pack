#!/usr/bin/env python3
"""Small shared helpers for command-based video adapters."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from config_contract import ContractError, read_json_object, validate_video_task


def duration_for_provider(task: dict[str, Any], provider_id: str, *, default: int) -> int:
    """Resolve an integer generation duration for one provider, with legacy fallback."""
    execution = task.get("provider_execution", {}).get(provider_id, {})
    value = execution.get("duration_seconds") if isinstance(execution, dict) else None
    durations = task.get("provider_duration_seconds")
    if durations is not None and not isinstance(durations, dict):
        raise ContractError("provider_duration_seconds must be an object")
    if value is None:
        value = durations.get(provider_id) if isinstance(durations, dict) else None
    if value is None:
        value = task.get("duration_seconds", default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{provider_id} duration must be numeric")
    duration = round(float(value))
    if duration != float(value) or not 1 <= duration <= 15:
        raise ContractError(f"{provider_id} duration must be an integer from 1 to 15 seconds")
    return duration


def resolution_for_provider(task: dict[str, Any], provider_id: str, *, default: str) -> str:
    execution = task.get("provider_execution", {}).get(provider_id, {})
    value = execution.get("resolution") if isinstance(execution, dict) else None
    resolution = value or default
    if resolution not in {"480p", "720p"}:
        raise ContractError(f"{provider_id} resolution must be 480p or 720p")
    return resolution


def load_task_and_prompt(task_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    task = validate_video_task(read_json_object(task_path), require_execution_fields=True)
    prompt = read_json_object(Path(task["prompt_file"]))
    grid_prompt = prompt.get("grid_video_prompt")
    if not isinstance(grid_prompt, str) or not grid_prompt.strip():
        raise ContractError("prompt file is missing grid_video_prompt")
    image = Path(task["input_image"])
    if not image.is_file():
        raise ContractError(f"input image does not exist: {image}")
    if image.stat().st_size > int(task.get("max_input_image_bytes", 25 * 1024 * 1024)):
        raise ContractError("input image exceeds max_input_image_bytes")
    Path(task["output_directory"]).mkdir(parents=True, exist_ok=True)
    return task, prompt


def write_result(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def copy_video(source: Path, destination: Path, max_bytes: int) -> Path:
    source = source.resolve()
    if not source.is_file():
        raise ContractError(f"generated video does not exist: {source}")
    size = source.stat().st_size
    if size <= 0 or size > max_bytes:
        raise ContractError(f"generated video size {size} is outside the allowed range")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source != destination.resolve():
        with source.open("rb") as reader, tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as writer:
            temp_path = Path(writer.name)
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
        temp_path.replace(destination)
    return destination.resolve()


def download_video(url: str, destination: Path, max_bytes: int, timeout: float = 120) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "motion-sticker-pack/1"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as writer:
            temp_path = Path(writer.name)
            total = 0
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ContractError("downloaded video exceeds max_output_bytes")
                writer.write(chunk)
        if total <= 0:
            raise ContractError("downloaded video is empty")
        temp_path.replace(destination)
        return destination.resolve()
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
