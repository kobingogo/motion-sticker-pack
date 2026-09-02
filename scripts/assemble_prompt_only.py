#!/usr/bin/env python3
"""Deliver auditable prompt artifacts when no video or local image processing is available."""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

from output_safety import begin_output_transaction, validate_archive_name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-prompt", type=Path, required=True)
    parser.add_argument("--tile-plan", type=Path, required=True)
    parser.add_argument("--prompts", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip-name", default="prompt-only.zip")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    archive_name = validate_archive_name(args.zip_name)
    sources = {
        "static-prompt.json": args.static_prompt,
        "tile-plan.json": args.tile_plan,
        "prompts.json": args.prompts,
        "route.json": args.route,
    }
    for source in sources.values():
        if not source.expanduser().is_file():
            raise FileNotFoundError(source)
    route = json.loads(args.route.expanduser().read_text(encoding="utf-8"))
    if not isinstance(route, dict):
        raise ValueError("route must be a JSON object")
    selected = route.get("selected")
    attempts = route.get("attempts")
    preflight = route.get("preflight")
    selected_id = selected.get("id") if isinstance(selected, dict) else None
    if selected_id != "prompt-only" or attempts not in ([], None):
        raise ValueError("prompt-only delivery requires a route that selected prompt-only with no executable attempts")
    if isinstance(preflight, dict) and preflight.get("selected_provider") not in {None, "prompt-only"}:
        raise ValueError("prompt-only delivery does not match route preflight")
    output_transaction = begin_output_transaction(
        args.output,
        overwrite=args.overwrite,
        archive_names={archive_name},
        protected_paths=sources.values(),
    )
    output = output_transaction.output
    names = list(sources)
    for name, source in sources.items():
        shutil.copyfile(source.expanduser().resolve(), output / name)
    manifest = {
        "version": 1,
        "mode": "prompt-only",
        "generated_video": False,
        "message": "No video or local image-processing capability was available; deliver prompts and stop.",
        "outputs": names + ["prompt-only.json", archive_name],
    }
    (output / "prompt-only.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    names.append("prompt-only.json")
    with zipfile.ZipFile(output / archive_name, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in names:
            bundle.write(output / name, arcname=name)
    output_transaction.commit()
    print(json.dumps({"output": str(output), "zip": str(output / archive_name), "generated_video": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
