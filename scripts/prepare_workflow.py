#!/usr/bin/env python3
"""Create one consistent work directory for routing and execution."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from character_workspace import character_workspace, write_character_manifest
from config_contract import is_python_interpreter, validate_provider_config, validate_video_task
from sticker_production_config import default_settings_path, load_production_settings


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: dict, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite {path}; pass --overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_file(source: Path, target: Path, *, overwrite: bool) -> None:
    if source.expanduser().resolve() == target.expanduser().resolve():
        return
    if target.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite {target}; pass --overwrite")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def usable_provider_config(source: Path, skill_root: Path) -> dict:
    config = read_json(source)
    for provider in config.get("providers", []):
        for field in ("command", "adapter_command"):
            command = provider.get(field)
            if not isinstance(command, list):
                continue
            for index, value in enumerate(command):
                marker = "/absolute/path/to/motion-sticker-pack/scripts/"
                if isinstance(value, str) and value.startswith(marker):
                    command[index] = str(skill_root / "scripts" / Path(value).name)
            first = command[0]
            if isinstance(first, str) and is_python_interpreter(first) and not Path(first).is_absolute():
                command[0] = sys.executable
    return validate_provider_config(config)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--character", help="character display name; creates works/<slug>/ under --skill-root")
    parser.add_argument("--skill-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--prompts", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--tile-plan", type=Path, required=True)
    parser.add_argument("--provider-template", type=Path)
    parser.add_argument("--tool-manifest-template", type=Path)
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument(
        "--settings",
        type=Path,
        default=default_settings_path(),
        help="single editable sticker-production settings JSON",
    )
    parser.add_argument("--key-color", help="one-off override for generation.key_color")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    skill_root = args.skill_root.expanduser().resolve()
    if args.character:
        work = args.work_dir.expanduser().resolve() if args.work_dir else character_workspace(skill_root, args.character)
        write_character_manifest(work, args.character)
    elif args.work_dir:
        work = args.work_dir.expanduser().resolve()
        work.mkdir(parents=True, exist_ok=True)
    else:
        raise ValueError("pass --character <name> or --work-dir <path>")
    provider_template = args.provider_template or skill_root / "assets" / "video-providers.example.json"
    manifest_template = args.tool_manifest_template or skill_root / "assets" / "tool-manifest.example.json"
    for path in (args.image, args.layout, args.prompts, args.state, args.tile_plan, provider_template, manifest_template):
        if not path.expanduser().is_file():
            raise FileNotFoundError(path)
    settings_source = args.settings.expanduser().resolve()
    settings = load_production_settings(settings_source)
    provider = settings["generation"]["provider"]
    provider_durations = dict(settings["generation"]["provider_duration_seconds"])
    duration_seconds = provider_durations[provider]
    if args.duration_seconds is not None:
        rounded_duration = round(args.duration_seconds)
        if rounded_duration != args.duration_seconds or not 1 <= rounded_duration <= 15:
            raise ValueError("duration-seconds must be an integer between 1 and 15")
        duration_seconds = rounded_duration
        provider_durations[provider] = rounded_duration

    config = usable_provider_config(provider_template.expanduser().resolve(), skill_root)
    write_json(work / "video-providers.json", config, overwrite=args.overwrite)
    runtime_manifest = work / "runtime-tools.json"
    if not runtime_manifest.exists() or args.overwrite:
        copy_file(manifest_template.expanduser().resolve(), runtime_manifest, overwrite=args.overwrite)
    copy_file(args.tile_plan.expanduser().resolve(), work / "tile-plan.json", overwrite=args.overwrite)
    settings_snapshot = work / "sticker-production.json"
    copy_file(settings_source, settings_snapshot, overwrite=args.overwrite)

    output_directory = (work / "raw-video").resolve()
    task = read_json(skill_root / "assets" / "video-task.example.json")
    task.update(
        {
            "provider": provider,
            "max_retries": settings["generation"]["max_retries"],
            "input_image": str(args.image.expanduser().resolve()),
            "layout_file": str(args.layout.expanduser().resolve()),
            "prompt_file": str(args.prompts.expanduser().resolve()),
            "approval_file": str(args.state.expanduser().resolve()),
            "output_directory": str(output_directory),
            "duration_seconds": duration_seconds,
            "provider_duration_seconds": provider_durations,
            "key_color": args.key_color or settings["generation"]["key_color"],
            "production_settings_file": str(settings_snapshot.resolve()),
            "safe_grid_scale": 0.80,
            "min_guard_fraction": 0.10,
            "max_foreground_bbox_fraction": 0.80,
            "motion_active_seconds": 2.0,
            "loop_min_seconds": 1.5,
            "loop_max_seconds": 2.5,
        }
    )
    write_json(work / "video-task.json", validate_video_task(task, require_execution_fields=True), overwrite=args.overwrite)
    result = {
        "work_dir": str(work),
        "config": str((work / "video-providers.json").resolve()),
        "runtime_tools": str((work / "runtime-tools.json").resolve()),
        "tile_plan": str((work / "tile-plan.json").resolve()),
        "production_settings": str(settings_snapshot.resolve()),
        "task": str((work / "video-task.json").resolve()),
    }
    manifest = work / "character.json"
    if manifest.is_file():
        result["character"] = read_json(manifest)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
