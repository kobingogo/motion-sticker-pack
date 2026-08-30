#!/usr/bin/env python3
"""Execute exactly one selected external route without automatic paid retries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from artifact_manifest import record_artifact
from attempt_ledger import claim_attempt, finish_attempt, progress_path_for
from config_contract import (
    ContractError,
    object_sha256,
    read_json_object,
    validate_provider_config,
    validate_video_task,
)
from manage_job_state import read_state, verify_state
from video_background_qc import probe_video_alpha, validate_video_background, validate_video_grid_safety


GATEWAY = Path(__file__).with_name("video_gateway.mjs")
PASSTHROUGH_ENV = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM",
    "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "TZ",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    # Windows child processes need these for Path.home(), TLS, and PATHEXT lookup.
    "USERPROFILE", "USERNAME", "APPDATA", "LOCALAPPDATA",
    "HOMEDRIVE", "HOMEPATH", "HOMESHARE",
    "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "OS",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMDATA",
    "PUBLIC", "ALLUSERSPROFILE", "COMPUTERNAME", "NUMBER_OF_PROCESSORS",
}


def _env_key_allowed(key: str, allowed: set[str]) -> bool:
    if key in allowed:
        return True
    if os.name != "nt":
        return False
    allowed_upper = {name.upper() for name in allowed}
    return key.upper() in allowed_upper


def child_environment(provider: dict, environ: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    credentials = provider.get("credentials", {})
    allowed = (
        PASSTHROUGH_ENV
        | set(credentials.get("env", []))
        | set(credentials.get("optional_env", []))
    )
    return {key: value for key, value in source.items() if _env_key_allowed(key, allowed)}


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


def execute_attempt(
    config_path: Path,
    task_path: Path,
    route: dict,
    output: Path,
    attempt: int,
    *,
    ledger_path: Path | None = None,
    resume: bool = False,
) -> dict:
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
    dependencies = route.get("dependency_sha256")
    if not isinstance(dependencies, dict):
        raise ContractError("route is missing dependency_sha256; regenerate the route")
    for field in ("input_image", "layout_file", "prompt_file", "approval_file", "production_settings_file"):
        value = task.get(field)
        if not isinstance(value, str):
            continue
        path = Path(value)
        if not path.is_file():
            raise ContractError(f"task dependency no longer exists: {field}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if dependencies.get(field) != actual_hash:
            raise ContractError(f"task dependency changed after routing: {field}")
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
    if provider["driver"] == "native-tool":
        raise ContractError("native-tool routes must be invoked by the host Agent, not this subprocess executor")
    ledger = (
        ledger_path
        or Path(task.get("attempt_ledger_file") or task_path.resolve().parent / "attempt-ledger.json")
    ).expanduser().resolve()
    result_path = output.expanduser().resolve()
    protected_results = {
        config_path.expanduser().resolve(),
        task_path.expanduser().resolve(),
        ledger,
    }
    for field in (
        "input_image",
        "layout_file",
        "prompt_file",
        "approval_file",
        "production_settings_file",
        "artifact_manifest_file",
    ):
        value = task.get(field)
        if isinstance(value, str):
            protected_results.add(Path(value).expanduser().resolve())
    if result_path in protected_results:
        raise ContractError("result output must not overwrite task inputs, ledger, or artifact manifest")
    output = result_path
    claim = claim_attempt(ledger, route, attempt, output, resume=resume)
    if claim["idempotent"]:
        return {"idempotent": True, "ledger": str(ledger), "attempt": attempt}
    progress_path = progress_path_for(ledger, attempt)
    if provider["driver"] == "ai-sdk":
        command = [
            "node", str(GATEWAY), "--config", str(config_path.resolve()), "--task", str(task_path.resolve()),
            "--provider-id", provider["id"], "--output", str(output.resolve()),
        ]
    elif provider["driver"] in {"command", "http-job"}:
        command = list(provider.get("command") or provider.get("adapter_command") or [])
        command += ["--task", str(task_path.resolve()), "--output", str(output.resolve())]
        if provider["id"] == "xai-direct":
            command += [
                "--config", str(config_path.resolve()),
                "--provider-id", provider["id"],
                "--progress", str(progress_path),
            ]
    else:
        raise ContractError("selected provider driver is not executable by this subprocess executor")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and resume:
        try:
            prior_result = read_json_object(output)
        except ContractError:
            prior_result = {}
        if prior_result.get("status") == "failed" and prior_result.get("request_id") == claim["resume_request_id"]:
            output.unlink()
    if output.exists():
        finish_attempt(
            ledger, route, attempt, "uncertain",
            error=f"result path already existed before adapter execution: {output}",
        )
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
        finish_attempt(
            ledger, route, attempt, "uncertain",
            error=f"video adapter exceeded its {timeout:g}-second execution limit",
        )
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
        request_id = None
        if output.is_file():
            try:
                request_id = read_json_object(output).get("request_id")
            except ContractError:
                pass
        progress = read_json_object(progress_path) if progress_path.is_file() else {}
        ambiguous_request = bool(
            progress.get("request_id") and progress.get("status") not in {"failed", "succeeded"}
        )
        finish_attempt(
            ledger,
            route,
            attempt,
            "uncertain" if ambiguous_request else "failed",
            result_path=output if output.is_file() else None,
            error=detail,
            request_id=request_id if isinstance(request_id, str) else None,
        )
        raise ContractError(f"video adapter failed with exit code {completed.returncode}: {detail}")
    if not output.is_file():
        detail = diagnostic_tail(completed.stderr) or diagnostic_tail(completed.stdout) or "no diagnostic output"
        finish_attempt(ledger, route, attempt, "uncertain", error=detail)
        raise ContractError(f"video adapter exited without writing its result file: {detail}")
    generated: Path | None = None
    try:
        result = read_json_object(output)
        if result.get("status") != "succeeded" or not isinstance(result.get("output"), str):
            raise ContractError("adapter result must report status=succeeded and an output path")
        generated = Path(result["output"])
        if not generated.is_absolute() or not generated.is_file():
            raise ContractError("adapter output must be an existing absolute file")
        if result.get("provider") not in (None, provider["id"]):
            raise ContractError("adapter result provider does not match the selected route")
        try:
            alpha_qc = probe_video_alpha(generated)
        except ContractError as exc:
            raise ContractError(f"generated video was rejected before post-processing: {exc}") from exc
        result["alpha_qc"] = alpha_qc
        result["has_alpha"] = alpha_qc["has_meaningful_alpha"]
        if task.get("require_alpha") and not result["has_alpha"] and not task.get("allow_key_background"):
            raise ContractError("generated video is opaque but the task requires alpha and disallows key matting")
        if task.get("allow_key_background") and not result["has_alpha"]:
            key_color = str(task.get("key_color") or "#00FF00").upper()
            background_qc = validate_video_background(generated, key_color)
            grid_safety_qc = validate_video_grid_safety(
                generated, key_color, layout, fail_on_crossing=False
            )
            result["background_qc"] = background_qc
            result["grid_safety_qc"] = grid_safety_qc
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        finish_attempt(
            ledger,
            route,
            attempt,
            "rejected",
            result_path=output if output.is_file() else None,
            generated_path=generated if generated and generated.is_file() else None,
            error=str(exc),
        )
        raise
    finish_attempt(
        ledger,
        route,
        attempt,
        "succeeded",
        result_path=output,
        generated_path=generated,
        request_id=result.get("request_id") if isinstance(result.get("request_id"), str) else None,
    )
    manifest_value = task.get("artifact_manifest_file")
    if isinstance(manifest_value, str):
        manifest_path = Path(manifest_value).expanduser().resolve()
        config_id = record_artifact(manifest_path, config_path, kind="provider-config", stage="executed")
        task_id = record_artifact(manifest_path, task_path, kind="video-task", stage="executed")
        generated_id = record_artifact(
            manifest_path,
            generated,
            kind="generated-video",
            stage="executed",
            dependencies=[config_id, task_id],
        )
        result_id = record_artifact(
            manifest_path,
            output,
            kind="provider-result",
            stage="executed",
            dependencies=[generated_id],
        )
        record_artifact(
            manifest_path,
            ledger,
            kind="attempt-ledger",
            stage="executed",
            dependencies=[result_id],
        )
    return {"idempotent": False, "ledger": str(ledger), "attempt": attempt}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execution = execute_attempt(
        args.config,
        args.task,
        read_json_object(args.route),
        args.output,
        args.attempt,
        ledger_path=args.ledger,
        resume=args.resume,
    )
    print(json.dumps({"result": str(args.output.resolve()), **execution}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
