#!/usr/bin/env python3
"""Import a personal-ip-studio IP_HANDOFF v2 into a motion job.

This adapter deliberately has no image-generation or image-copying behavior.  It
only validates the handoff, verifies the existing anchor, and writes the two
small job metadata files consumed by later motion stages.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


PROTOCOL = "ip-handoff/v2"
SOURCE_SKILL = "personal-ip-studio"
TARGET_SKILL = "motion-sticker-pack"
APPROVED = "approved"
RENDERING_POLICY = "preserve-source-appearance"
ORIGINAL_PHOTO_POLICY = "do-not-use"
LEGAL_SKINS = ("toy", "wash", "doodle", "ink", "flat")

STYLE_LABELS = {
    "toy": "3D toy",
    "wash": "ink wash",
    "doodle": "doodle",
    "ink": "hand-drawn ink",
    "flat": "flat illustration",
}

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CREDENTIAL_KEY_RE = re.compile(
    r"(?:^|[_-])(api[_-]?key|access[_-]?key|secret|password|passwd|token|"
    r"authorization|cookie|credential|private[_-]?key|client[_-]?secret)(?:$|[_-])",
    re.IGNORECASE,
)
_SECRET_VALUE_RES = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\bBasic\s+[A-Za-z0-9+/=]{16,}", re.IGNORECASE),
    re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:ghp|github_pat|xox[baprs])-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)


class HandoffError(Exception):
    """A stable, user-facing import failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _error(code: str, message: str) -> HandoffError:
    return HandoffError(code, message)


def _json_path(parts: Iterable[str]) -> str:
    return ".".join(parts) or "$"


def _find_suspected_credential(value: Any, path: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if _CREDENTIAL_KEY_RE.search(key_text):
                return _json_path((*path, key_text))
            found = _find_suspected_credential(child, (*path, key_text))
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_suspected_credential(child, (*path, str(index)))
            if found:
                return found
    elif isinstance(value, str):
        for pattern in _SECRET_VALUE_RES:
            if pattern.search(value):
                return _json_path(path)
    return None


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _error("validation-error", f"{key} must be a non-empty string")
    return value.strip()


def _require_identity_version(payload: dict[str, Any]) -> int | str:
    value = payload.get("identity_version")
    if isinstance(value, bool):
        raise _error("validation-error", "identity_version must be a positive integer or vN string")
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str) and re.fullmatch(r"v?[1-9][0-9]*", value.strip(), re.IGNORECASE):
        return value.strip()
    raise _error("validation-error", "identity_version must be a positive integer or vN string")


def _version_key(value: Any) -> str:
    text = str(value).strip().lower()
    return text[1:] if text.startswith("v") else text


def _path_value(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None and isinstance(payload.get("paths"), dict):
        value = payload["paths"].get(key)
    if not isinstance(value, str) or not value.strip():
        raise _error("path-error", f"{key} must be an absolute file path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise _error("path-error", f"{key} must be an absolute file path")
    resolved = path.resolve()
    if not resolved.is_file():
        raise _error("path-error", f"{key} does not point to a file: {resolved}")
    return str(resolved)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise _error("path-error", f"cannot read anchor: {path}") from exc
    return digest.hexdigest()


def _optional_handoff_marker(payload: dict[str, Any]) -> None:
    """Accept common marker spellings while keeping marker validation strict."""
    for key in ("handoff_type", "handoff", "kind", "type"):
        if key in payload and payload[key] not in ("IP_HANDOFF", "ip-handoff"):
            raise _error("validation-error", f"{key} must be IP_HANDOFF when present")


def _resolve_style(payload: dict[str, Any], skin_id: str) -> dict[str, Any]:
    raw_style = payload.get("style", payload.get("resolved_style"))
    if raw_style is None and (
        payload.get("motion_style_id") is not None
        or payload.get("motion_style_prompt") is not None
    ):
        raw_style = {
            "id": payload.get("motion_style_id", "custom"),
            "prompt": payload.get("motion_style_prompt", ""),
        }
    if raw_style is None:
        style: dict[str, Any] = {}
    elif isinstance(raw_style, str):
        style = {"label": raw_style}
    elif isinstance(raw_style, dict):
        style = copy.deepcopy(raw_style)
    else:
        raise _error("validation-error", "style must be a string or object when present")

    style.setdefault("skin_id", skin_id)
    style.setdefault("label", STYLE_LABELS[skin_id])
    style.setdefault(
        "prompt",
        f"Use {STYLE_LABELS[skin_id]} rendering while preserving the source character appearance.",
    )
    if not isinstance(style.get("prompt"), str) or not style["prompt"].strip():
        style["prompt"] = (
            f"Use {STYLE_LABELS[skin_id]} rendering while preserving the source character appearance."
        )
    style["preserve_source_appearance"] = True
    return style


def _resolve_reactions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("reactions", payload.get("reaction_metadata"))
    if raw is None and "requested_reactions" in payload:
        raw = payload["requested_reactions"]
    if raw is None and isinstance(payload.get("reaction_overlays"), dict):
        raw = [
            {"id": key, "label": value, "text": value}
            for key, value in payload["reaction_overlays"].items()
        ]
    if raw is None:
        raw = []
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _error("validation-error", "reactions must be a list when present")

    resolved: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str):
            label = item.strip()
            if not label:
                raise _error("validation-error", f"reactions[{index - 1}] must not be empty")
            resolved.append({"id": f"{index:02d}", "label": label, "text": label})
            continue
        if not isinstance(item, dict):
            raise _error("validation-error", f"reactions[{index - 1}] must be a string or object")
        entry = copy.deepcopy(item)
        label = entry.get("label", entry.get("name", entry.get("text", entry.get("reaction"))))
        if not isinstance(label, str) or not label.strip():
            raise _error(f"validation-error", f"reactions[{index - 1}] needs label/name/text/reaction")
        entry.setdefault("id", f"{index:02d}")
        entry.setdefault("label", label.strip())
        resolved.append(entry)
    return resolved


def _identity_value(record: dict[str, Any], key: str) -> Any:
    if key in record:
        return record[key]
    identity = record.get("identity")
    if isinstance(identity, dict):
        return identity.get(key)
    return None


def _merge_missing(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge only absent keys; existing scalars and arrays always win."""
    result = copy.deepcopy(existing)
    for key, value in incoming.items():
        if key not in result:
            result[key] = copy.deepcopy(value)
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_missing(result[key], value)
    return result


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise _error("input-error", f"{label} not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _error("input-error", f"invalid JSON in {label}: {path}") from exc
    if not isinstance(data, dict):
        raise _error("validation-error", f"{label} must contain a JSON object")
    return data


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise _error("write-error", f"cannot write {path}") from exc


def load_handoff(path: Path, stdin_text: str | None = None) -> dict[str, Any]:
    path = Path(path)
    if str(path) == "-":
        if stdin_text is None:
            stdin_text = sys.stdin.read()
        try:
            value = json.loads(stdin_text)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise _error("input-error", "invalid JSON in HANDOFF stdin") from exc
        if not isinstance(value, dict):
            raise _error("validation-error", "HANDOFF must contain a JSON object")
        return value
    return _read_json(path.expanduser().resolve(), "HANDOFF")


def validate_handoff(payload: dict[str, Any], anchor_override: Path | None = None) -> dict[str, Any]:
    credential_path = _find_suspected_credential(payload)
    if credential_path:
        raise _error("credential-detected", f"suspected credential at {credential_path}")

    _optional_handoff_marker(payload)
    if payload.get("protocol") != PROTOCOL:
        raise _error("validation-error", f"protocol must be {PROTOCOL}")
    source_skill = payload.get("source_skill", payload.get("source"))
    if source_skill != SOURCE_SKILL:
        raise _error("validation-error", f"source_skill must be {SOURCE_SKILL}")
    if "source_skill" in payload and "source" in payload and payload["source_skill"] != payload["source"]:
        raise _error("validation-error", "source and source_skill must match")
    target_skill = payload.get("target_skill", payload.get("target"))
    if target_skill != TARGET_SKILL:
        raise _error("validation-error", f"target_skill must be {TARGET_SKILL}")
    if payload.get("identity_status") != APPROVED:
        raise _error("validation-error", "identity_status must be approved")
    if payload.get("rendering_policy") != RENDERING_POLICY:
        raise _error("validation-error", f"rendering_policy must be {RENDERING_POLICY}")
    if payload.get("original_photo_policy") != ORIGINAL_PHOTO_POLICY:
        raise _error("validation-error", f"original_photo_policy must be {ORIGINAL_PHOTO_POLICY}")

    identity_version = _require_identity_version(payload)
    character_id = payload.get("id", payload.get("character_id", payload.get("name")))
    if not isinstance(character_id, str) or not _SLUG_RE.fullmatch(character_id.strip()):
        raise _error("validation-error", "id must be a lowercase slug")
    skin_id = _require_string(payload, "skin_id").lower()
    if skin_id not in LEGAL_SKINS:
        raise _error("validation-error", f"skin_id must be one of {', '.join(LEGAL_SKINS)}")

    card = _path_value(payload, "card")
    declared_anchor = _path_value(payload, "anchor")
    anchor = declared_anchor
    if anchor_override is not None:
        override = anchor_override.expanduser()
        if not override.is_absolute():
            raise _error("path-error", "--anchor must be an absolute file path")
        override = override.resolve()
        if not override.is_file():
            raise _error("path-error", f"--anchor does not point to a file: {override}")
        if override != Path(declared_anchor).expanduser().resolve():
            raise _error("path-error", "--anchor must resolve to the handoff anchor path")
        anchor = str(override)

    expected_hash = _require_string(payload, "anchor_sha256")
    if not _SHA256_RE.fullmatch(expected_hash):
        raise _error("hash-mismatch", "anchor_sha256 must be a 64-character SHA-256 hex digest")
    actual_hash = _sha256(Path(anchor))
    if actual_hash.casefold() != expected_hash.casefold():
        raise _error("hash-mismatch", "anchor_sha256 does not match the anchor file")

    resolved_style = _resolve_style(payload, skin_id)
    resolved_reactions = _resolve_reactions(payload)
    return {
        "character_id": character_id.strip(),
        "card": card,
        "anchor": anchor,
        "identity_version": identity_version,
        "skin_id": skin_id,
        "anchor_sha256": actual_hash,
        "resolved_style": resolved_style,
        "resolved_reactions": resolved_reactions,
    }


def _job_record(payload: dict[str, Any], resolved: dict[str, Any]) -> dict[str, Any]:
    character_id = resolved["character_id"]
    return {
        "name": character_id,
        "slug": character_id,
        "source_of_truth": "personal-ip-studio",
        "identity_status": APPROVED,
        "identity_version": resolved["identity_version"],
        "card": resolved["card"],
        "anchor": resolved["anchor"],
        "anchor_sha256": resolved["anchor_sha256"],
        "skin_id": resolved["skin_id"],
        "rendering_policy": RENDERING_POLICY,
        "original_photo_policy": ORIGINAL_PHOTO_POLICY,
        "resolved_style": resolved["resolved_style"],
        "resolved_reactions": resolved["resolved_reactions"],
        "motion_job": {
            "source": "IP_HANDOFF",
            "generated": False,
            "original_photo_used": False,
        },
    }


def import_handoff(
    handoff: dict[str, Any], work_dir: Path, anchor_override: Path | None = None
) -> dict[str, Any]:
    resolved = validate_handoff(handoff, anchor_override)
    work = work_dir.expanduser().resolve()
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _error("write-error", f"cannot create work directory: {work}") from exc

    character_path = work / "character.json"
    existing_character: dict[str, Any] = {}
    if character_path.exists():
        existing_character = _read_json(character_path, "existing character.json")
        credential_path = _find_suspected_credential(existing_character)
        if credential_path:
            raise _error("credential-detected", f"suspected credential at existing character.json.{credential_path}")
        for key in ("identity_version", "anchor_sha256"):
            old_value = _identity_value(existing_character, key)
            new_value = resolved[key]
            if key == "identity_version":
                different = old_value is not None and _version_key(old_value) != _version_key(new_value)
            else:
                different = old_value is not None and str(old_value).casefold() != str(new_value).casefold()
            if different:
                raise _error("stale-job", f"existing character.json has a different {key}")

    handoff_path = work / "handoff.json"
    if handoff_path.exists():
        existing_handoff = _read_json(handoff_path, "existing handoff.json")
        old_version = existing_handoff.get("identity_version")
        old_hash = existing_handoff.get("anchor_sha256")
        if old_version is not None and _version_key(old_version) != _version_key(resolved["identity_version"]):
            raise _error("stale-job", "existing handoff.json has a different identity_version")
        if old_hash is not None and str(old_hash).casefold() != resolved["anchor_sha256"]:
            raise _error("stale-job", "existing handoff.json has a different anchor_sha256")

    merged_character = _merge_missing(existing_character, _job_record(handoff, resolved))
    output_handoff = copy.deepcopy(handoff)
    _atomic_write_json(handoff_path, output_handoff)
    try:
        _atomic_write_json(character_path, merged_character)
    except HandoffError:
        # handoff.json is still a valid, independently re-importable source; do
        # not attempt a destructive rollback of an existing user's file.
        raise

    return {
        "ok": True,
        "work_dir": str(work),
        "files": [str(handoff_path), str(character_path)],
        "resolved": {
            "style": resolved["resolved_style"],
            "reactions": resolved["resolved_reactions"],
        },
        "identity": {
            "version": resolved["identity_version"],
            "anchor": resolved["anchor"],
            "anchor_sha256": resolved["anchor_sha256"],
        },
        "original_photo_used": False,
        "generated": False,
    }


def _result(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handoff", metavar="HANDOFF", help="path to IP_HANDOFF v2 JSON, or - for stdin")
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--anchor", type=Path, help="optional absolute path, must match handoff anchor")
    args = parser.parse_args(argv)

    try:
        handoff = load_handoff(args.handoff)
        outcome = import_handoff(handoff, args.work_dir, args.anchor)
    except HandoffError as exc:
        print(_result({"ok": False, "error": {"code": exc.code, "message": exc.message}}))
        return 2
    except (OSError, ValueError, TypeError) as exc:
        print(_result({"ok": False, "error": {"code": "input-error", "message": str(exc)}}))
        return 2

    print(_result(outcome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
