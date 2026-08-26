#!/usr/bin/env python3
"""Use the logged-in local Grok Build agent as an image-to-video command adapter."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from config_contract import ContractError
from video_adapter_common import copy_video, download_video, load_task_and_prompt, write_result


PROVIDER_ID = "grok-build-local"
ZDR_HELP = (
    "Grok Build refuses video tools when the account uses team ZDR or /privacy "
    "data-retention opt-out, unless [tools.zdr_video_output_s3] is loaded from "
    "console-synced managed_config.toml in the GROK_HOME this adapter launches; "
    "Grok CLI 1.0.10 deletes unsigned local managed_config files. "
    "https://docs.x.ai/build/settings/zdr-video-storage"
)


def resolve_grok_home(environ: dict[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    configured = source.get("GROK_HOME")
    if configured and configured.strip():
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".grok").resolve()


def find_grok(environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    configured = source.get("GROK_BIN")
    candidates = [configured, shutil.which("grok"), str(Path.home() / ".grok" / "bin" / "grok")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise ContractError("local Grok CLI was not found; install/login to Grok Build first")


def parse_structured(stdout: bytes) -> dict[str, Any]:
    text = stdout.decode("utf-8", errors="replace").strip()
    try:
        outer = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"Grok did not return JSON: {text[-1000:]}") from exc
    if not isinstance(outer, dict):
        raise ContractError("Grok JSON response must be an object")
    candidates = [outer.get("text"), outer.get("structuredOutput"), outer.get("structured_output")]
    for value in candidates:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.startswith("```"):
                candidate = candidate.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
            decoder = json.JSONDecoder()
            recovered: dict[str, Any] | None = None
            for index, character in enumerate(candidate):
                if character != "{":
                    continue
                try:
                    possible, _ = decoder.raw_decode(candidate[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(possible, dict):
                    recovered = possible
            if recovered is not None:
                return recovered
    raise ContractError("Grok response is missing a valid JSON final response")


def build_instruction(task: dict[str, Any], prompt: dict[str, Any], target: Path, duration: int, resolution: str) -> str:
    return f"""Use your image_to_video tool exactly once to generate a video from this approved image:
{Path(task['input_image']).resolve()}

Motion prompt:
{prompt['grid_video_prompt'].strip()}

Requirements:
- Use duration={duration} seconds and resolution_name={resolution}.
- Treat the source image as the locked first frame. Preserve the complete sticker grid, cell boundaries, character identity, outfit, and transparent-looking/key background composition.
- Keep motion subtle, loop-friendly, and independent inside each cell. Do not crop, reorder, merge, or redraw the grid.
- Do not call any other generation tool and do not retry if generation fails.
- If the tool returns a downloadable video URL, download the MP4 to exactly: {target}
- Do not claim that generation started or succeeded unless the tool call actually occurred and returned success.
- Finish with only one JSON object: status=ok plus output set to an existing absolute local MP4 path; if local download is impossible, status=ok plus url. On tool failure use status=failed and copy its concise error into message.
"""


def grok_command(
    grok_bin: str,
    instruction: str,
    output_dir: Path,
    grok_home: Path,
    environ: dict[str, str] | None = None,
) -> list[str]:
    source = os.environ if environ is None else environ
    command = [
        grok_bin,
        "-p",
        instruction,
        "--output-format",
        "json",
        "--max-turns",
        "8",
        "--no-subagents",
        "--disable-web-search",
        "--always-approve",
        "--permission-mode",
        "bypassPermissions",
        "--tools",
        "image_to_video",
        "--verbatim",
        "--no-auto-update",
        "--cwd",
        str(output_dir),
        "--leader-socket",
        str(grok_home / "leader.sock"),
    ]
    debug_file = source.get("GROK_DEBUG_FILE")
    if debug_file and debug_file.strip():
        command.extend(["--debug", "--debug-file", str(Path(debug_file).expanduser())])
    return command


def annotate_error(message: str) -> str:
    lowered = message.lower()
    if "zero data retention" in lowered or "output.upload_url" in lowered or "zdr" in lowered:
        if "docs.x.ai/build/settings/zdr-video-storage" not in lowered:
            return f"{message} ({ZDR_HELP})"
    return message


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        task, prompt = load_task_and_prompt(args.task)
        duration = 6 if float(task.get("duration_seconds", 6)) <= 6 else 10
        resolution = os.environ.get("GROK_VIDEO_RESOLUTION", "480p")
        if resolution not in {"480p", "720p"}:
            raise ContractError("GROK_VIDEO_RESOLUTION must be 480p or 720p")
        output_dir = Path(task["output_directory"]).resolve()
        target = output_dir / "grok-build-local.mp4"
        if target.exists():
            raise ContractError(f"refusing to overwrite existing video: {target}")
        before = {path.resolve() for path in output_dir.glob("*.mp4")}
        grok_home = resolve_grok_home()
        child_env = dict(os.environ)
        child_env["GROK_HOME"] = str(grok_home)
        if os.environ.get("GROK_USE_XAI_API_KEY") != "1":
            child_env.pop("XAI_API_KEY", None)
        command = grok_command(
            find_grok(),
            build_instruction(task, prompt, target, duration, resolution),
            output_dir,
            grok_home,
        )
        timeout = float(task.get("timeout_seconds", 900))
        completed = subprocess.run(
            command,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", errors="replace")[-3000:].strip()
            raise ContractError(
                annotate_error(
                    f"Grok Build exited with code {completed.returncode}: {detail or 'no diagnostic output'}"
                )
            )
        structured = parse_structured(completed.stdout)
        if structured.get("status") != "ok":
            raise ContractError(
                annotate_error(str(structured.get("message") or "Grok Build reported generation failure"))
            )
        max_bytes = int(task.get("max_output_bytes", 200 * 1024 * 1024))
        returned_path = structured.get("output")
        if isinstance(returned_path, str) and returned_path.strip() and Path(returned_path).expanduser().is_file():
            video = copy_video(Path(returned_path).expanduser(), target, max_bytes)
        elif isinstance(structured.get("url"), str) and structured["url"].startswith(("https://", "http://")):
            video = download_video(structured["url"], target, max_bytes)
        elif target.is_file():
            video = copy_video(target, target, max_bytes)
        else:
            created = sorted(
                (path for path in output_dir.glob("*.mp4") if path.resolve() not in before),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if not created:
                raise ContractError("Grok Build returned success but no local MP4 or downloadable URL")
            video = copy_video(created[0], target, max_bytes)
        write_result(
            args.output,
            {
                "status": "succeeded",
                "provider": PROVIDER_ID,
                "model": "grok-build/image_to_video",
                "output": str(video),
                "duration_seconds": duration,
                "resolution": resolution,
                "request_id": structured.get("request_id"),
                "has_alpha": False,
            },
        )
        print(json.dumps({"status": "succeeded", "output": str(video)}, ensure_ascii=False))
        return 0
    except (ContractError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        message = annotate_error(str(exc))
        write_result(args.output, {"status": "failed", "provider": PROVIDER_ID, "error": message})
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
