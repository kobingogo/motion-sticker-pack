#!/usr/bin/env python3
"""Prepare an image_gen payload without sending unsupported arguments."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


OPTIONAL_PROVIDER_ARGUMENTS = {"background", "output_format"}


def _call_arguments(
    contract: dict,
    supported_arguments: set[str],
    *,
    prompt: str,
    requested_arguments: dict[str, str],
) -> dict:
    call_arguments: dict[str, object] = {"prompt": prompt}
    reference = contract.get("reference_image")
    if reference:
        path = reference.get("path") if isinstance(reference, dict) else None
        expected_sha256 = reference.get("sha256") if isinstance(reference, dict) else None
        if not isinstance(path, str) or not path:
            raise ValueError("reference_image is missing its path")
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise ValueError("reference_image is missing its sha256 binding")
        resolved = Path(path).expanduser().resolve(strict=True)
        actual_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError("reference_image sha256 no longer matches the bound source")
        if "referenced_image_paths" not in supported_arguments:
            raise ValueError("reference-image generation requires referenced_image_paths support")
        call_arguments["referenced_image_paths"] = [str(resolved)]
    call_arguments.update(
        {key: value for key, value in requested_arguments.items() if key in supported_arguments}
    )
    return call_arguments


def _call_record(
    contract: dict,
    supported_arguments: set[str],
    *,
    prompt: str,
    requested_arguments: dict[str, str],
) -> dict:
    passed = {key: value for key, value in requested_arguments.items() if key in supported_arguments}
    omitted = {key: value for key, value in requested_arguments.items() if key not in supported_arguments}
    return {
        "requested_arguments": requested_arguments,
        "passed_arguments": passed,
        "omitted_unsupported_arguments": omitted,
        "call_arguments": _call_arguments(
            contract,
            supported_arguments,
            prompt=prompt,
            requested_arguments=requested_arguments,
        ),
    }


def prepare_call(contract: dict, supported_arguments: set[str]) -> dict:
    request = contract.get("image_generation_request")
    if not isinstance(request, dict):
        raise ValueError("static prompt is missing image_generation_request")
    requested = request.get("arguments")
    if not isinstance(requested, dict) or set(requested) != OPTIONAL_PROVIDER_ARGUMENTS:
        raise ValueError("image_generation_request must declare background and output_format")
    prompt = contract.get("static_sheet_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("static prompt is missing static_sheet_prompt")
    if "prompt" not in supported_arguments:
        raise ValueError("the callable image_gen schema must expose prompt")

    initial = _call_record(
        contract,
        supported_arguments,
        prompt=prompt,
        requested_arguments=requested,
    )
    fallback = request.get("opaque_fallback")
    if not isinstance(fallback, dict):
        raise ValueError("image_generation_request is missing opaque_fallback")
    fallback_arguments = fallback.get("arguments")
    fallback_suffix = fallback.get("prompt_suffix")
    if not isinstance(fallback_arguments, dict) or set(fallback_arguments) != OPTIONAL_PROVIDER_ARGUMENTS:
        raise ValueError("opaque_fallback must declare background and output_format")
    if not isinstance(fallback_suffix, str) or not fallback_suffix.strip():
        raise ValueError("opaque_fallback is missing prompt_suffix")
    fallback_record = _call_record(
        contract,
        supported_arguments,
        prompt=f"{prompt}\n\n{fallback_suffix.strip()}",
        requested_arguments=fallback_arguments,
    )
    reference = contract.get("reference_image")
    verified_reference = None
    if isinstance(reference, dict):
        verified_reference = {
            "path": str(Path(reference["path"]).expanduser().resolve(strict=True)),
            "sha256": reference["sha256"],
            "verified": True,
        }
    return {
        "version": 1,
        "tool": request.get("preferred_tool", "image_gen"),
        "requested_arguments": initial["requested_arguments"],
        "passed_arguments": initial["passed_arguments"],
        "omitted_unsupported_arguments": initial["omitted_unsupported_arguments"],
        "supported_arguments": sorted(supported_arguments),
        "call_arguments": initial["call_arguments"],
        "generation_policy": {
            "mode": "transparent-first",
            "first_attempt": "prompt-real-alpha-plus-supported-native-arguments",
            "on_omitted_transparency_arguments": "continue-transparent-first-via-prompt",
            "schema_omission_implies_no_transparency": False,
            "reference_image_changes_background_policy": False,
            "inspect_result_before_acceptance": True,
            "fallback_trigger": "local-alpha-normalization-failure-only",
            "on_missing_real_alpha_or_simulated_transparency": "use-opaque-fallback-call",
            "max_static_generation_attempts": 2,
        },
        "opaque_fallback_call": fallback_record,
        "requires_alpha_normalization": True,
        "verified_reference_image": verified_reference,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static-prompt", type=Path, required=True)
    parser.add_argument(
        "--supported-argument",
        action="append",
        default=[],
        help="repeat for every argument exposed by the current image_gen schema",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(args.static_prompt.read_text(encoding="utf-8"))
    result = prepare_call(contract, set(args.supported_argument))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
