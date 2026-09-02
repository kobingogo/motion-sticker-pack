#!/usr/bin/env python3
"""Validate the public benchmark case contract without running paid routes."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_IDS = {
    "hair", "green-garment", "white-garment", "transparent-decor", "cross-cell",
    "12-cell-layout", "independent-stickers", "long-bad-frame", "provider-interruption",
}
VALID_STATUSES = {"fixture-covered", "real-run-required"}


def validate(path: Path = ROOT / "benchmarks" / "cases.json") -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    cases = value.get("cases") if isinstance(value, dict) else None
    if value.get("version") != 1 or not isinstance(cases, list):
        raise ValueError("benchmark cases must be a version 1 object with a cases array")
    ids = {case.get("id") for case in cases if isinstance(case, dict)}
    if ids != REQUIRED_IDS:
        raise ValueError(f"benchmark cases must cover exactly {sorted(REQUIRED_IDS)}")
    for case in cases:
        if not isinstance(case, dict) or case.get("status") not in VALID_STATUSES:
            raise ValueError("each benchmark case needs a valid status")
        if not isinstance(case.get("route"), str) or not isinstance(case.get("acceptance"), list) or not case["acceptance"]:
            raise ValueError(f"benchmark case {case.get('id')!r} is missing route or acceptance criteria")
    return {"valid": True, "cases": len(cases), "real_run_required": sum(case["status"] == "real-run-required" for case in cases)}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False))
