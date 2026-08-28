#!/usr/bin/env python3
"""Use the logged-in local Grok Build agent as an image-to-video command adapter."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

from config_contract import ContractError
from sticker_production_config import default_settings_path, load_production_settings
from video_adapter_common import copy_video, download_video, duration_for_provider, load_task_and_prompt, write_result
from video_background_qc import (
    materialize_green_input,
    validate_grok_input,
    validate_video_background,
    validate_video_grid_safety,
)


PROVIDER_ID = "grok-build-local"
DEFAULT_KEY_COLOR = "#00FF00"
MAX_GROK_INSTRUCTION_BYTES = 3800
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


def _runnable_file(candidate: str | None) -> str | None:
    if not candidate or not str(candidate).strip():
        return None
    path = Path(candidate).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
        return str(path.resolve())
    return None


def find_grok(environ: dict[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    grok_home = resolve_grok_home(source)
    search_path = source.get("PATH")
    candidates = [
        source.get("GROK_BIN"),
        shutil.which("grok", path=search_path),
        shutil.which("grok.exe", path=search_path),
        str(grok_home / "bin" / "grok"),
        str(grok_home / "bin" / "grok.exe"),
    ]
    for candidate in candidates:
        resolved = _runnable_file(candidate)
        if resolved:
            return resolved
    raise ContractError("local Grok CLI was not found; install/login to Grok Build first")


def grok_session_videos(grok_home: Path, output_dir: Path) -> list[Path]:
    """Find videos produced by Grok before its agent tries to copy them."""
    scope = grok_home / "sessions" / quote(str(output_dir.resolve()), safe="")
    if not scope.is_dir():
        return []
    return sorted(
        (path.resolve() for path in scope.glob("*/videos/*.mp4") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


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


def _motion_schedule(timeline: dict[str, Any] | None = None) -> str:
    values = timeline or {
        "start_hold_seconds": 0.3,
        "action_end_seconds": 1.8,
        "return_end_seconds": 2.6,
        "final_hold_seconds": 0.4,
    }
    expression_end = float(values["return_end_seconds"]) + float(values["final_hold_seconds"])
    return (
        f"hold the start pose until {float(values['start_hold_seconds']):g}s, "
        f"complete the action by {float(values['action_end_seconds']):g}s, "
        f"return to the start pose by {float(values['return_end_seconds']):g}s, "
        f"then hold through {expression_end:g}s"
    )


def compact_motion_prompt(
    prompt: dict[str, Any], timeline: dict[str, Any] | None = None
) -> str:
    """Build a short Grok prompt from the approved, vision-informed tile plan."""
    layout = prompt.get("detected_layout")
    tiles = prompt.get("tile_plan")
    if isinstance(layout, dict) and isinstance(tiles, list) and tiles:
        columns = int(layout.get("columns", 0))
        rows = int(layout.get("rows", 0))
        count = int(layout.get("count", columns * rows))
        motions = []
        for index, tile in enumerate(tiles, start=1):
            if not isinstance(tile, dict):
                continue
            motion = tile.get("grok_motion", tile.get("motion"))
            if isinstance(motion, str) and motion.strip():
                normalized_motion = re.sub(r"\s+", " ", motion).strip()
                motions.append(f"{index:02d}:{normalized_motion}")
        if len(motions) == count:
            return (
                f"{columns}x{rows} grid, {count} cells. Fixed camera/canvas; preserve each cell's identity, "
                f"outfit, props, pose and placement. Each cell: one small in-place action; {_motion_schedule(timeline)}. "
                "No crop, reorder, cross-cell motion, new content, text or backdrop. "
                "Cell actions: " + "; ".join(motions)
            )
    return re.sub(r"\s+", " ", str(prompt["grid_video_prompt"])).strip()


def build_instruction(
    task: dict[str, Any], prompt: dict[str, Any], target: Path, duration: int, resolution: str,
    key_color: str, source_image: Path | None = None, timeline: dict[str, Any] | None = None,
) -> str:
    source = source_image or Path(task["input_image"])
    instruction = f"""Use image_to_video exactly once on this approved image: {source.resolve()}
Settings: duration={duration}s, resolution_name={resolution}.
Motion: {compact_motion_prompt(prompt, timeline)}
Hard rules: keep the full grid and every cell's identity, outfit, props, pose and placement. Fixed camera/canvas. Keep each action subtle, independent and inside its cell; {_motion_schedule(timeline)}. After that loop-ready cycle, keep holding the start pose through {duration}s; do not repeat the action. No crop, reorder, merge, cross-cell motion, new character/content, text, backdrop, shadow or camera move.
This is green screen, not transparency: render every empty/background pixel as exactly one flat RGB color: {key_color}. Never draw a checkerboard. Keep {key_color} uniform in every frame, including corners and grid gutters; keep foreground away from seams.
Do not call another generation tool; do not retry; this task permits exactly one image_to_video generation. Save a successful MP4 to exactly {target}. Finish with one JSON object only: status=ok plus an existing absolute local MP4 path (or url); on failure use status=failed plus a concise message.
"""
    if len(instruction.encode("utf-8")) > MAX_GROK_INSTRUCTION_BYTES:
        raise ContractError(
            f"compact Grok instruction exceeds {MAX_GROK_INSTRUCTION_BYTES} UTF-8 bytes; shorten tile motions"
        )
    return instruction


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


def promote_accepted_video(candidate: Path, target: Path, max_bytes: int) -> Path:
    """Promote one accepted attempt without retaining a byte-identical duplicate."""
    candidate = candidate.expanduser().resolve()
    target = target.expanduser().resolve()
    size = candidate.stat().st_size
    if size < 1 or size > max_bytes:
        raise ContractError("accepted video is empty or exceeds max_output_bytes")
    if candidate.parent == target.parent:
        candidate.replace(target)
        return target
    return copy_video(candidate, target, max_bytes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        task, prompt = load_task_and_prompt(args.task)
        settings_path = Path(task.get("production_settings_file") or default_settings_path())
        settings = load_production_settings(settings_path)
        configured_generation = settings["generation"]
        duration = duration_for_provider(task, PROVIDER_ID, default=6)
        resolution = os.environ.get("GROK_VIDEO_RESOLUTION", configured_generation["resolution"])
        if resolution not in {"480p", "720p"}:
            raise ContractError("GROK_VIDEO_RESOLUTION must be 480p or 720p")
        output_dir = Path(task["output_directory"]).resolve()
        key_color = str(task.get("key_color") or DEFAULT_KEY_COLOR).upper()
        if key_color != DEFAULT_KEY_COLOR:
            raise ContractError("Grok image-to-video requires the exact #00FF00 key color")
        validate_grok_input(Path(task["input_image"]), key_color)
        green_input = output_dir / "grok-input-green.png"
        layout_data = json.loads(Path(task["layout_file"]).read_text(encoding="utf-8"))
        input_report = materialize_green_input(
            Path(task["input_image"]),
            green_input,
            key_color,
            layout=layout_data,
            safe_scale=float(task.get("safe_grid_scale", 0.80)),
            min_guard_fraction=float(task.get("min_guard_fraction", 0.10)),
            max_foreground_bbox_fraction=float(task.get("max_foreground_bbox_fraction", 0.80)),
        )
        target = output_dir / "grok-build-local.mp4"
        if target.exists():
            raise ContractError(f"refusing to overwrite existing video: {target}")
        grok_home = resolve_grok_home()
        child_env = dict(os.environ)
        child_env["GROK_HOME"] = str(grok_home)
        if os.environ.get("GROK_USE_XAI_API_KEY") != "1":
            child_env.pop("XAI_API_KEY", None)
        timeout = float(task.get("timeout_seconds", 900))
        max_bytes = int(task.get("max_output_bytes", 200 * 1024 * 1024))
        max_background_retries = int(task.get("max_retries", 0))
        if max_background_retries != 0:
            raise ContractError("Grok grid generation requires max_retries=0; one source produces one video")
        attempts: list[dict[str, Any]] = []
        video = None
        structured: dict[str, Any] = {}
        for retry_number in range(1, 2):
            attempt_target = output_dir / f"grok-build-local-attempt-{retry_number}.mp4"
            if attempt_target.exists():
                raise ContractError(f"refusing to overwrite existing video: {attempt_target}")
            before = {path.resolve() for path in output_dir.glob("*.mp4")}
            before_session = set(grok_session_videos(grok_home, output_dir))
            command = grok_command(
                find_grok(),
                build_instruction(
                    task, prompt, attempt_target, duration, resolution, key_color,
                    Path(input_report["path"]), configured_generation["motion_timeline"],
                ),
                output_dir,
                grok_home,
            )
            completed = subprocess.run(
                command,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
            recovered = [
                path for path in grok_session_videos(grok_home, output_dir)
                if path not in before_session
            ]
            if completed.returncode and not recovered:
                detail = completed.stderr.decode("utf-8", errors="replace")[-3000:].strip()
                raise ContractError(
                    annotate_error(
                        f"Grok Build exited with code {completed.returncode}: {detail or 'no diagnostic output'}"
                    )
                )
            if completed.returncode:
                structured = {
                    "status": "ok",
                    "recovered_from_grok_session": True,
                    "cli_exit_code": completed.returncode,
                }
            else:
                structured = parse_structured(completed.stdout)
            if structured.get("status") != "ok":
                raise ContractError(
                    annotate_error(str(structured.get("message") or "Grok Build reported generation failure"))
                )
            returned_path = structured.get("output")
            if recovered:
                candidate = copy_video(recovered[0], attempt_target, max_bytes)
            elif isinstance(returned_path, str) and returned_path.strip() and Path(returned_path).expanduser().is_file():
                candidate = copy_video(Path(returned_path).expanduser(), attempt_target, max_bytes)
            elif isinstance(structured.get("url"), str) and structured["url"].startswith(("https://", "http://")):
                candidate = download_video(structured["url"], attempt_target, max_bytes)
            elif attempt_target.is_file():
                candidate = attempt_target.resolve()
            else:
                created = sorted(
                    (path for path in output_dir.glob("*.mp4") if path.resolve() not in before),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                if not created:
                    raise ContractError("Grok Build returned success but no local MP4 or downloadable URL")
                candidate = copy_video(created[0], attempt_target, max_bytes)
            try:
                qc = validate_video_background(candidate, key_color)
                grid_qc = validate_video_grid_safety(
                    candidate, key_color, layout_data, fail_on_crossing=False
                )
            except ContractError as exc:
                attempts.append({"attempt": retry_number, "status": "rejected", "reason": str(exc)})
                raise ContractError(
                    f"Grok generated video failed the uniform {key_color} background gate: {exc}"
                ) from exc
            video = promote_accepted_video(candidate, target, max_bytes)
            attempts.append(
                {
                    "attempt": retry_number,
                    "status": "accepted",
                    "background_qc": qc,
                    "grid_safety_qc": grid_qc,
                    "recovered_from_grok_session": bool(structured.get("recovered_from_grok_session")),
                }
            )
            break
        if video is None:
            raise ContractError("Grok video generation ended without an accepted video")
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
                "grok_input": input_report,
                "background_qc": attempts[-1].get("background_qc"),
                "grid_safety_qc": attempts[-1].get("grid_safety_qc"),
                "generation_attempts": attempts,
                "production_settings": settings["_meta"],
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
