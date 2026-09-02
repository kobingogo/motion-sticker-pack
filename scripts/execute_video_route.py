#!/usr/bin/env python3
"""Execute or register exactly one selected route without automatic paid retries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from artifact_manifest import record_artifact
from attempt_ledger import atomic_write_json, claim_attempt, finish_attempt, progress_path_for
from config_contract import (
    ContractError,
    object_sha256,
    read_json_object,
    validate_provider_config,
    validate_video_task,
)
from manage_job_state import read_state, verify_state
from route_video_provider import dependency_hashes
from video_adapter_common import key_color_for_provider, write_result
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


def provider_by_id(config: dict, provider_id: str, route_provider: dict | None = None) -> dict:
    matches = [item for item in config["providers"] if item["id"] == provider_id]
    if len(matches) == 1:
        return matches[0]
    if (
        not matches
        and isinstance(route_provider, dict)
        and route_provider.get("id") == provider_id
        and route_provider.get("driver") == "native-tool"
        and isinstance(route_provider.get("tool"), str)
    ):
        # Runtime-discovered native tools are intentionally not duplicated in
        # the provider template. The route is their signed capability record.
        return route_provider
    raise ContractError(f"provider {provider_id!r} does not exist exactly once")


def validate_retry_approval(
    approval_path: Path,
    route: dict,
    task: dict,
    selected_provider: str,
    attempt: int,
) -> str:
    """Verify a user-confirmed retry is bound to the exact current execution inputs."""

    approval = read_json_object(approval_path)
    if approval.get("version") != 1 or approval.get("kind") != "explicit-user-video-retry-approval":
        raise ContractError("retry approval has an unsupported format")
    if approval.get("confirmed_by_user") is not True:
        raise ContractError("retry approval is not confirmed by the user")
    if approval.get("provider") != selected_provider or approval.get("attempt") != attempt:
        raise ContractError("retry approval does not match the selected provider attempt")
    if approval.get("route_sha256") != object_sha256(route):
        raise ContractError("retry approval does not match the current route")
    image_path = Path(task["input_image"]).expanduser().resolve(strict=True)
    layout_path = Path(task["layout_file"]).expanduser().resolve(strict=True)
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    layout_hash = hashlib.sha256(layout_path.read_bytes()).hexdigest()
    if approval.get("static_sha256") != image_hash:
        raise ContractError("retry approval does not match the approved static image")
    if approval.get("layout_sha256") != layout_hash:
        raise ContractError("retry approval does not match the approved layout")
    return hashlib.sha256(approval_path.expanduser().resolve(strict=True).read_bytes()).hexdigest()


def write_rejected_result(output: Path, result: dict, error: Exception) -> None:
    result["status"] = "rejected"
    result["executor_status"] = "rejected"
    result["qc_status"] = "rejected"
    result["error"] = str(error)
    write_result(output, result)


def record_execution_artifacts(
    task: dict,
    config_path: Path,
    task_path: Path,
    ledger: Path,
    output: Path,
    generated: Path | None,
    *,
    stage: str,
    retry_approval: Path | None = None,
) -> None:
    manifest_value = task.get("artifact_manifest_file")
    if not isinstance(manifest_value, str):
        return
    manifest_path = Path(manifest_value).expanduser().resolve()
    config_id = record_artifact(manifest_path, config_path, kind="provider-config", stage=stage)
    task_id = record_artifact(manifest_path, task_path, kind="video-task", stage=stage)
    dependencies = [config_id, task_id]
    if retry_approval is not None and retry_approval.is_file():
        retry_id = record_artifact(
            manifest_path,
            retry_approval,
            kind="video-retry-approval",
            stage=stage,
            dependencies=dependencies,
        )
        dependencies = [retry_id]
    if generated is not None and generated.is_file():
        generated_id = record_artifact(
            manifest_path,
            generated,
            kind="generated-video",
            stage=stage,
            dependencies=dependencies,
        )
        dependencies = [generated_id]
    if output.is_file():
        result_id = record_artifact(
            manifest_path,
            output,
            kind="provider-result",
            stage=stage,
            dependencies=dependencies,
        )
        dependencies = [result_id]
    ledger_value = read_json_object(ledger)
    ledger_snapshot = ledger.with_name(
        f"{ledger.stem}.snapshot-{object_sha256(ledger_value)[:16]}{ledger.suffix}"
    )
    if ledger_snapshot.is_file():
        if object_sha256(read_json_object(ledger_snapshot)) != object_sha256(ledger_value):
            raise ContractError(f"attempt ledger snapshot collision: {ledger_snapshot}")
    else:
        atomic_write_json(ledger_snapshot, ledger_value)
    record_artifact(
        manifest_path,
        ledger_snapshot,
        kind="attempt-ledger",
        stage=stage,
        dependencies=dependencies,
    )


def execute_attempt(
    config_path: Path,
    task_path: Path,
    route: dict,
    output: Path,
    attempt: int,
    *,
    ledger_path: Path | None = None,
    resume: bool = False,
    retry_approval_path: Path | None = None,
    native_video_path: Path | None = None,
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
    try:
        current_dependencies = dependency_hashes(task)
    except (OSError, ValueError) as exc:
        raise ContractError(f"task dependency is no longer usable: {exc}") from exc
    if dependencies != current_dependencies:
        changed = sorted(set(dependencies) | set(current_dependencies))
        changed = [field for field in changed if dependencies.get(field) != current_dependencies.get(field)]
        raise ContractError(f"task dependency changed after routing: {changed}")
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
    provider = provider_by_id(config, selected["id"], selected)
    if provider["driver"] == "native-tool" and native_video_path is None:
        raise ContractError(
            "native-tool routes require the host-generated video via --native-video; "
            "the executor only registers and QC-checks host output"
        )
    native_generated = None
    if provider["driver"] == "native-tool":
        native_generated = native_video_path.expanduser().resolve(strict=True)
        if not native_generated.is_file():
            raise ContractError(f"native video is not a file: {native_generated}")
    ledger = (
        ledger_path
        or Path(task.get("attempt_ledger_file") or task_path.resolve().parent / "attempt-ledger.json")
    ).expanduser().resolve()
    retry_approval_sha256 = None
    if retry_approval_path is not None:
        retry_approval_sha256 = validate_retry_approval(
            retry_approval_path.expanduser().resolve(),
            route,
            task,
            selected["id"],
            attempt,
        )
    result_path = output.expanduser().resolve()
    protected_results = {
        config_path.expanduser().resolve(),
        task_path.expanduser().resolve(),
        ledger,
    }
    if retry_approval_path is not None:
        protected_results.add(retry_approval_path.expanduser().resolve())
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
    if native_generated is not None and native_generated == result_path:
        raise ContractError("native video and result output must be different files")
    execution_context = selected.get("execution_context")
    if not isinstance(execution_context, dict):
        raise ContractError("route attempt is missing execution_context; regenerate the route")
    if execution_context.get("provider_id") != provider["id"]:
        raise ContractError("route execution_context provider does not match the selected route")
    expected_input = execution_context.get("input_image")
    expected_input_hash = execution_context.get("input_image_sha256")
    if not isinstance(expected_input, str) or not isinstance(expected_input_hash, str):
        raise ContractError("route execution_context is missing the input image binding")
    actual_input = str(
        task.get("provider_input_images", {}).get(provider["id"])
        if isinstance(task.get("provider_input_images"), dict)
        else task.get("input_image")
    )
    actual_input_path = Path(actual_input).expanduser().resolve()
    if str(actual_input_path) != str(Path(expected_input).expanduser().resolve()):
        raise ContractError("route execution_context input image does not match the task")
    if hashlib.sha256(actual_input_path.read_bytes()).hexdigest() != expected_input_hash:
        raise ContractError("route execution_context input image hash does not match the current file")
    output = result_path
    claim = claim_attempt(
        ledger,
        route,
        attempt,
        output,
        resume=resume,
        retry_approval_sha256=retry_approval_sha256,
    )
    if claim["idempotent"]:
        return {"idempotent": True, "ledger": str(ledger), "attempt": attempt}
    progress_path = progress_path_for(ledger, attempt)
    if provider["driver"] == "native-tool":
        pass
    elif provider["driver"] == "ai-sdk":
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
        record_execution_artifacts(
            task, config_path, task_path, ledger, output, None, stage="uncertain",
            retry_approval=retry_approval_path,
        )
        raise FileExistsError(f"result file already exists: {output}")
    child_env = child_environment(provider)
    timeout = float(task.get("timeout_seconds", 900)) + 30
    if provider["driver"] == "native-tool":
        try:
            write_result(
                output,
                {
                    "status": "succeeded",
                    "provider": provider["id"],
                    "tool": provider["tool"],
                    "output": str(native_generated),
                },
            )
        except OSError as exc:
            finish_attempt(ledger, route, attempt, "uncertain", error=f"cannot register native video: {exc}")
            record_execution_artifacts(
                task, config_path, task_path, ledger, output, None, stage="uncertain",
                retry_approval=retry_approval_path,
            )
            raise ContractError(f"cannot register native video: {exc}") from exc
        completed = None
    else:
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
            record_execution_artifacts(
                task, config_path, task_path, ledger, output, None, stage="uncertain",
                retry_approval=retry_approval_path,
            )
            raise ContractError(f"video adapter exceeded its {timeout:g}-second execution limit") from exc
        except OSError as exc:
            finish_attempt(
                ledger, route, attempt, "uncertain",
                error=f"cannot launch video adapter: {exc}",
            )
            record_execution_artifacts(
                task, config_path, task_path, ledger, output, None, stage="uncertain",
                retry_approval=retry_approval_path,
            )
            raise ContractError(f"cannot launch video adapter: {exc}") from exc
    if completed is not None and completed.returncode:
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
        attempt_status = "uncertain" if ambiguous_request else "failed"
        finish_attempt(
            ledger,
            route,
            attempt,
            attempt_status,
            result_path=output if output.is_file() else None,
            error=detail,
            request_id=request_id if isinstance(request_id, str) else None,
        )
        if attempt_status == "failed":
            record_execution_artifacts(
                task,
                config_path,
                task_path,
                ledger,
                output,
                None,
                stage="failed",
                retry_approval=retry_approval_path,
            )
        raise ContractError(f"video adapter failed with exit code {completed.returncode}: {detail}")
    if not output.is_file():
        detail = diagnostic_tail(completed.stderr) or diagnostic_tail(completed.stdout) or "no diagnostic output"
        finish_attempt(ledger, route, attempt, "uncertain", error=detail)
        record_execution_artifacts(
            task, config_path, task_path, ledger, output, None, stage="uncertain",
            retry_approval=retry_approval_path,
        )
        raise ContractError(f"video adapter exited without writing its result file: {detail}")
    generated: Path | None = None
    result: dict | None = None
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
            key_color = key_color_for_provider(task, provider["id"])
            background_qc = validate_video_background(generated, key_color)
            grid_safety_qc = validate_video_grid_safety(
                generated, key_color, layout, fail_on_crossing=False
            )
            result["background_qc"] = background_qc
            result["grid_safety_qc"] = grid_safety_qc
        result["executor_status"] = "accepted"
        result["qc_status"] = "passed"
        result["execution_context"] = {
            **execution_context,
            "provider_id": provider["id"],
            "output": str(generated.resolve()),
            "output_sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
            "route_sha256": object_sha256(route),
        }
        write_result(output, result)
    except Exception as exc:
        if result is not None:
            write_rejected_result(output, result, exc)
        finish_attempt(
            ledger,
            route,
            attempt,
            "rejected",
            result_path=output if output.is_file() else None,
            generated_path=generated if generated and generated.is_file() else None,
            error=str(exc),
        )
        record_execution_artifacts(
            task,
            config_path,
            task_path,
            ledger,
            output,
            generated,
            stage="rejected",
            retry_approval=retry_approval_path,
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
    record_execution_artifacts(
        task,
        config_path,
        task_path,
        ledger,
        output,
        generated,
        stage="executed",
        retry_approval=retry_approval_path,
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
    parser.add_argument(
        "--retry-approval",
        type=Path,
        help="hash-bound approval created after the user explicitly requested a new billable retry",
    )
    parser.add_argument(
        "--native-video",
        type=Path,
        help="host-generated video for a native-tool route; executor registers and QC-checks it",
    )
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
        retry_approval_path=args.retry_approval,
        native_video_path=args.native_video,
    )
    print(json.dumps({"result": str(args.output.resolve()), **execution}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
