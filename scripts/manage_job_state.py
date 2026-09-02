#!/usr/bin/env python3
"""Create, approve, and verify hash-bound static-sheet state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


def artifact(path: Path) -> dict:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"artifact is not a file: {resolved}")
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def validate_layout(path: Path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    detected = value.get("detected_layout", value)
    columns = int(detected["columns"])
    rows = int(detected["rows"])
    count = int(detected.get("count", columns * rows))
    confidence = detected.get("confidence")
    if columns < 1 or rows < 1 or count != columns * rows:
        raise ValueError("layout count must equal positive columns * rows")
    if confidence is None or not 0.75 <= float(confidence) <= 1.0:
        raise ValueError("layout must have confidence >= 0.75 or a confirmed manual override")


def create_state(image: Path, layout: Path, static_prompt: Path | None, source_type: str) -> dict:
    validate_layout(layout)
    if source_type == "generated" and static_prompt is None:
        raise ValueError("a generated static sheet requires its static prompt artifact")
    image_record = artifact(image)
    state = {
        "version": 1,
        "revision": image_record["sha256"][:16],
        "phase": "static-approved" if source_type == "user-supplied" else "static-review",
        "source_type": source_type,
        "created_at": utc_now(),
        "static_image": image_record,
        "layout": artifact(layout),
        "static_prompt": artifact(static_prompt) if static_prompt else None,
        "approval": (
            {
                "kind": "user-supplied-source",
                "approved_at": utc_now(),
                "static_sha256": image_record["sha256"],
            }
            if source_type == "user-supplied"
            else None
        ),
    }
    return state


def read_state(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("job state must be a version 1 JSON object")
    return value


def verify_state(state: dict, image: Path, layout: Path) -> dict:
    validate_layout(layout)
    if state.get("phase") != "static-approved" or not isinstance(state.get("approval"), dict):
        raise ValueError("the current static revision has not been approved")
    image_hash = sha256_file(image.expanduser().resolve(strict=True))
    layout_hash = sha256_file(layout.expanduser().resolve(strict=True))
    if image_hash != state.get("static_image", {}).get("sha256"):
        raise ValueError("input image does not match the approved static revision")
    if layout_hash != state.get("layout", {}).get("sha256"):
        raise ValueError("layout file does not match the approved static revision")
    if image_hash != state.get("approval", {}).get("static_sha256"):
        raise ValueError("approval hash does not match the current static image")
    return {"valid": True, "revision": state.get("revision"), "static_sha256": image_hash}


def create_video_retry_approval(
    state_path: Path,
    image: Path,
    layout: Path,
    route: Path,
    provider: str,
    attempt: int,
) -> dict:
    """Create a hash-bound approval for an explicitly requested video retry."""

    state = read_state(state_path)
    verify_state(state, image, layout)
    route_value = json.loads(route.read_text(encoding="utf-8"))
    if not isinstance(route_value, dict) or route_value.get("version") != 1:
        raise ValueError("route must be a version 1 JSON object")
    attempts = route_value.get("attempts")
    if not isinstance(attempts, list) or not 1 <= attempt <= len(attempts):
        raise ValueError(f"route has no attempt {attempt}")
    selected = attempts[attempt - 1]
    if selected.get("attempt") != attempt or selected.get("id") != provider:
        raise ValueError("retry approval provider or attempt does not match the route")
    image_record = artifact(image)
    layout_record = artifact(layout)
    return {
        "version": 1,
        "kind": "explicit-user-video-retry-approval",
        "approved_at": utc_now(),
        "confirmed_by_user": True,
        "provider": provider,
        "attempt": attempt,
        "static_sha256": image_record["sha256"],
        "layout_sha256": layout_record["sha256"],
        "route_sha256": hashlib.sha256(
            json.dumps(route_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "job_state": artifact(state_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--image", type=Path, required=True)
    create.add_argument("--layout", type=Path, required=True)
    create.add_argument("--static-prompt", type=Path)
    create.add_argument("--source-type", choices=("generated", "user-supplied"), default="generated")
    create.add_argument("--output", type=Path, required=True)
    approve = subparsers.add_parser("approve")
    approve.add_argument("--state", type=Path, required=True)
    approve.add_argument("--image", type=Path, required=True)
    approve.add_argument("--layout", type=Path, required=True)
    approve.add_argument("--confirmed-by-user", action="store_true", required=True)
    retry = subparsers.add_parser("approve-video-retry")
    retry.add_argument("--state", type=Path, required=True)
    retry.add_argument("--image", type=Path, required=True)
    retry.add_argument("--layout", type=Path, required=True)
    retry.add_argument("--route", type=Path, required=True)
    retry.add_argument("--provider", required=True)
    retry.add_argument("--attempt", type=int, default=1)
    retry.add_argument("--output", type=Path, required=True)
    retry.add_argument("--confirmed-by-user", action="store_true", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--state", type=Path, required=True)
    verify.add_argument("--image", type=Path, required=True)
    verify.add_argument("--layout", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "create":
        state = create_state(args.image, args.layout, args.static_prompt, args.source_type)
        atomic_write(args.output, state)
        result = {"state": str(args.output.resolve()), "phase": state["phase"], "revision": state["revision"]}
    elif args.command == "approve":
        state = read_state(args.state)
        if state.get("phase") != "static-review":
            raise ValueError("only a static-review revision can be explicitly approved")
        image_hash = sha256_file(args.image.expanduser().resolve(strict=True))
        layout_hash = sha256_file(args.layout.expanduser().resolve(strict=True))
        if image_hash != state.get("static_image", {}).get("sha256") or layout_hash != state.get("layout", {}).get("sha256"):
            raise ValueError("image or layout changed after the review state was created")
        state["phase"] = "static-approved"
        state["approval"] = {
            "kind": "explicit-user-confirmation",
            "approved_at": utc_now(),
            "static_sha256": image_hash,
        }
        atomic_write(args.state, state)
        result = {"state": str(args.state.resolve()), "phase": state["phase"], "revision": state["revision"]}
    elif args.command == "approve-video-retry":
        approval = create_video_retry_approval(
            args.state,
            args.image,
            args.layout,
            args.route,
            args.provider,
            args.attempt,
        )
        atomic_write(args.output, approval)
        result = {
            "approval": str(args.output.resolve()),
            "provider": approval["provider"],
            "attempt": approval["attempt"],
            "route_sha256": approval["route_sha256"],
        }
    else:
        result = verify_state(read_state(args.state), args.image, args.layout)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
