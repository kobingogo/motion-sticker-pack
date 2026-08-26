#!/usr/bin/env python3
"""Execute exactly one selected external route without automatic paid retries."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from config_contract import (
    ContractError,
    object_sha256,
    read_json_object,
    validate_provider_config,
    validate_video_task,
)
from manage_job_state import read_state, verify_state


GATEWAY = Path(__file__).with_name("video_gateway.mjs")
PASSTHROUGH_ENV = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM",
    "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "TZ",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
}


def child_environment(provider: dict, environ: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    credentials = provider.get("credentials", {})
    allowed = (
        PASSTHROUGH_ENV
        | set(credentials.get("env", []))
        | set(credentials.get("optional_env", []))
    )
    return {key: value for key, value in source.items() if key in allowed}


def diagnostic_tail(raw: bytes, limit: int = 4000) -> str:
    text = raw.decode("utf-8", errors="replace")[-limit:]
    text = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+", r"\1[REDACTED]", text)
    return text.strip()


def provider_by_id(config: dict, provider_id: str) -> dict:
    matches = [item for item in config["providers"] if item["id"] == provider_id]
    if len(matches) != 1:
        raise ContractError(f"provider {provider_id!r} does not exist exactly once")
    return matches[0]


def execute_attempt(config_path: Path, task_path: Path, route: dict, output: Path, attempt: int) -> None:
    attempts = route.get("attempts", [])
    if not isinstance(attempts, list) or not 1 <= attempt <= len(attempts):
        raise ContractError(f"attempt must be between 1 and {len(attempts)}")
    selected = attempts[attempt - 1]
    if selected.get("attempt") != attempt:
        raise ContractError("route attempt numbering is inconsistent")
    config = validate_provider_config(read_json_object(config_path))
    task = validate_video_task(read_json_object(task_path), require_execution_fields=True)
    if route.get("config_sha256") != object_sha256(config):
        raise ContractError("route was produced from a different provider config")
    if route.get("task_sha256") != object_sha256(task):
        raise ContractError("route was produced from a different video task")
    verify_state(
        read_state(Path(task["approval_file"])),
        Path(task["input_image"]),
        Path(task["layout_file"]),
    )
    layout_data = read_json_object(Path(task["layout_file"]))
    prompt_data = read_json_object(Path(task["prompt_file"]))
    layout = layout_data.get("detected_layout", layout_data)
    prompt_layout = prompt_data.get("detected_layout", prompt_data)
    expected = (int(layout["columns"]), int(layout["rows"]), int(layout.get("count", int(layout["columns"]) * int(layout["rows"]))))
    actual = (
        int(prompt_layout["columns"]),
        int(prompt_layout["rows"]),
        int(prompt_layout.get("count", int(prompt_layout["columns"]) * int(prompt_layout["rows"]))),
    )
    if expected != actual or expected[2] != expected[0] * expected[1]:
        raise ContractError("prompt layout differs from the approved detected layout")
    if not isinstance(prompt_data.get("grid_video_prompt"), str) or not prompt_data["grid_video_prompt"].strip():
        raise ContractError("prompt file is missing grid_video_prompt")
    provider = provider_by_id(config, selected["id"])
    if provider["driver"] == "ai-sdk":
        command = [
            "node", str(GATEWAY), "--config", str(config_path.resolve()), "--task", str(task_path.resolve()),
            "--provider-id", provider["id"], "--output", str(output.resolve()),
        ]
    elif provider["driver"] in {"command", "http-job"}:
        command = list(provider.get("command") or provider.get("adapter_command") or [])
        command += ["--task", str(task_path.resolve()), "--output", str(output.resolve())]
    else:
        raise ContractError("native-tool routes must be invoked by the host Agent, not this subprocess executor")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"result file already exists: {output}")
    child_env = child_environment(provider)
    timeout = float(task.get("timeout_seconds", 900)) + 30
    try:
        completed = subprocess.run(
            command,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ContractError(f"video adapter exceeded its {timeout:g}-second execution limit") from exc
    if completed.returncode:
        detail = ""
        if output.is_file():
            try:
                failed_result = read_json_object(output)
                error = failed_result.get("error") or failed_result.get("message")
                if isinstance(error, str) and error.strip():
                    detail = error.strip()
            except ContractError:
                pass
        if not detail:
            detail = diagnostic_tail(completed.stderr) or diagnostic_tail(completed.stdout) or "no diagnostic output"
        raise ContractError(f"video adapter failed with exit code {completed.returncode}: {detail}")
    if not output.is_file():
        detail = diagnostic_tail(completed.stderr) or diagnostic_tail(completed.stdout) or "no diagnostic output"
        raise ContractError(f"video adapter exited without writing its result file: {detail}")
    result = read_json_object(output)
    if result.get("status") != "succeeded" or not isinstance(result.get("output"), str):
        raise ContractError("adapter result must report status=succeeded and an output path")
    generated = Path(result["output"])
    if not generated.is_absolute() or not generated.is_file():
        raise ContractError("adapter output must be an existing absolute file")
    if result.get("provider") not in (None, provider["id"]):
        raise ContractError("adapter result provider does not match the selected route")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute_attempt(args.config, args.task, read_json_object(args.route), args.output, args.attempt)
    print(json.dumps({"result": str(args.output.resolve()), "attempt": args.attempt}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
