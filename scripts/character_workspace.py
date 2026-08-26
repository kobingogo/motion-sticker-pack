#!/usr/bin/env python3
"""Resolve a per-character generated-asset directory under works/<slug>/."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path


WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def character_slug(name: str) -> str:
    text = unicodedata.normalize("NFC", name).strip()
    text = ILLEGAL.sub("-", text)
    text = text.replace("..", "-")
    text = re.sub(r"[\s._]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-.")
    if not text:
        text = "unnamed"
    if text.casefold() in WINDOWS_RESERVED:
        text = f"_{text}"
    return text[:80]


def character_workspace(skill_root: Path, name: str) -> Path:
    slug = character_slug(name)
    if slug in {".", ".."}:
        raise ValueError("invalid character slug")
    works_root = skill_root.expanduser().resolve() / "works"
    path = (works_root / slug).resolve()
    try:
        path.relative_to(works_root)
    except ValueError as exc:
        raise ValueError("character workspace escaped works/") from exc
    if path == works_root:
        raise ValueError("character workspace escaped works/")
    return path


def write_character_manifest(work: Path, name: str) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    record = {"name": unicodedata.normalize("NFC", name).strip(), "slug": character_slug(name)}
    (work / "character.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--skill-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    skill_root = args.skill_root.expanduser().resolve()
    work = character_workspace(skill_root, args.name)
    record = write_character_manifest(work, args.name)
    print(json.dumps({"work_dir": str(work), **record}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
