#!/usr/bin/env python3
"""Load and validate the single editable motion-sticker production profile."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def default_settings_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "sticker-production.default.json"


def settings_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return float(value)


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer between {minimum} and {maximum}")
    return value


def load_production_settings(path: Path | None = None) -> dict[str, Any]:
    source = (path or default_settings_path()).expanduser().resolve()
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("sticker production settings must be a version 1 JSON object")
    required_sections = {
        "generation", "duration_profiles", "canvas", "gif", "webp", "budget", "trial",
        "unexpected_duration",
    }
    if not required_sections.issubset(data):
        raise ValueError(f"sticker production settings are missing {sorted(required_sections - set(data))}")
    generation = data.get("generation")
    if not isinstance(generation, dict):
        raise ValueError("generation settings must be an object")
    provider = generation.get("provider")
    if not isinstance(provider, str) or not ID_RE.fullmatch(provider):
        raise ValueError("generation.provider must be kebab-case")
    duration_map = generation.get("provider_duration_seconds")
    legacy_duration = generation.get("request_duration_seconds")
    if duration_map is not None and legacy_duration is not None:
        raise ValueError("use provider_duration_seconds instead of defining both duration formats")
    if duration_map is None:
        duration_map = {
            provider: _integer(
                legacy_duration,
                "generation.request_duration_seconds",
                1,
                15,
            )
        }
    if not isinstance(duration_map, dict) or not duration_map:
        raise ValueError("generation.provider_duration_seconds must be a non-empty object")
    normalized_durations: dict[str, int] = {}
    for provider_id, value in duration_map.items():
        if not isinstance(provider_id, str) or not ID_RE.fullmatch(provider_id):
            raise ValueError("generation.provider_duration_seconds keys must be kebab-case provider ids")
        normalized_durations[provider_id] = _integer(
            value,
            f"generation.provider_duration_seconds.{provider_id}",
            1,
            15,
        )
    if provider not in normalized_durations:
        raise ValueError(f"generation.provider_duration_seconds is missing selected provider {provider!r}")
    generation.pop("request_duration_seconds", None)
    generation["provider_duration_seconds"] = normalized_durations
    duration = min(normalized_durations.values())
    resolution = generation.get("resolution")
    if resolution not in {"480p", "720p"}:
        raise ValueError("generation.resolution must be 480p or 720p")
    key_color = generation.get("key_color")
    if not isinstance(key_color, str) or not HEX_RE.fullmatch(key_color):
        raise ValueError("generation.key_color must use #RRGGBB notation")
    if generation.get("provider") == "grok-build-local" and key_color.upper() != "#00FF00":
        raise ValueError("grok-build-local requires generation.key_color #00FF00")
    if generation.get("max_retries") != 0:
        raise ValueError("Grok grid generation requires generation.max_retries 0")
    timeline = generation.get("motion_timeline")
    if not isinstance(timeline, dict):
        raise ValueError("generation.motion_timeline must be an object")
    start = _number(timeline.get("start_hold_seconds"), "motion_timeline.start_hold_seconds", 0, duration)
    action_end = _number(timeline.get("action_end_seconds"), "motion_timeline.action_end_seconds", 0.1, duration)
    return_end = _number(timeline.get("return_end_seconds"), "motion_timeline.return_end_seconds", 0.1, duration)
    final_hold = _number(timeline.get("final_hold_seconds"), "motion_timeline.final_hold_seconds", 0, duration)
    if not start < action_end < return_end <= duration or return_end + final_hold > duration + 1e-6:
        raise ValueError("motion timeline must progress within the requested duration")

    delivery_variants = data.get("delivery_variants", {})
    if not isinstance(delivery_variants, dict):
        raise ValueError("delivery_variants must be an object")
    for provider_id, variant in delivery_variants.items():
        label = f"delivery_variants.{provider_id}"
        if not isinstance(provider_id, str) or not ID_RE.fullmatch(provider_id):
            raise ValueError("delivery_variants keys must be kebab-case provider ids")
        if provider_id not in normalized_durations:
            raise ValueError(f"{label} has no matching provider duration")
        if not isinstance(variant, dict):
            raise ValueError(f"{label} must be an object")
        short_duration = _number(
            variant.get("short_duration_seconds"), f"{label}.short_duration_seconds", 0.1, 15
        )
        provider_duration = normalized_durations[provider_id]
        if short_duration > provider_duration:
            raise ValueError(f"{label} must fit within the provider duration")

        # Legacy snapshots may still contain the former endpoint-loop gate.
        # Validate those fields when present, but keep them informational only.
        if "loop_difference_max" in variant:
            _number(variant["loop_difference_max"], f"{label}.loop_difference_max", 0, 1)
        if "cycle_search_seconds" in variant:
            search = variant["cycle_search_seconds"]
            if not isinstance(search, dict):
                raise ValueError(f"{label}.cycle_search_seconds must be an object")
            search_min = _number(
                search.get("minimum_seconds"),
                f"{label}.cycle_search_seconds.minimum_seconds",
                0.1,
                15,
            )
            search_max = _number(
                search.get("maximum_seconds"),
                f"{label}.cycle_search_seconds.maximum_seconds",
                0.1,
                15,
            )
            if search_min > search_max:
                raise ValueError(f"{label}.cycle_search_seconds range is reversed")

    profiles = data.get("duration_profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("duration_profiles must be a non-empty array")
    ids: set[str] = set()
    ranges: list[tuple[float, float, str]] = []
    for index, profile in enumerate(profiles):
        label = f"duration_profiles[{index}]"
        if not isinstance(profile, dict):
            raise ValueError(f"{label} must be an object")
        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not ID_RE.fullmatch(profile_id) or profile_id in ids:
            raise ValueError(f"{label}.id must be unique kebab-case")
        ids.add(profile_id)
        match = profile.get("match_duration")
        output = profile.get("output")
        if not isinstance(match, dict) or not isinstance(output, dict):
            raise ValueError(f"{label} requires match_duration and output objects")
        minimum = _number(match.get("minimum_seconds"), f"{label}.minimum_seconds", 0.1, 30)
        maximum = _number(match.get("maximum_seconds"), f"{label}.maximum_seconds", 0.1, 30)
        if minimum > maximum:
            raise ValueError(f"{label} duration range is reversed")
        for left, right, other in ranges:
            if max(left, minimum) <= min(right, maximum):
                raise ValueError(f"duration profiles {other} and {profile_id} overlap")
        ranges.append((minimum, maximum, profile_id))
        if output.get("duration_mode") != "full":
            raise ValueError(f"{label}.output.duration_mode must be full")
        _integer(output.get("width"), f"{label}.output.width", 16, 2048)
        _integer(output.get("height"), f"{label}.output.height", 16, 2048)
        _integer(output.get("fps"), f"{label}.output.fps", 1, 60)
        gif = output.get("gif")
        if not isinstance(gif, dict):
            raise ValueError(f"{label}.output.gif must be an object")
        _integer(gif.get("max_colors"), f"{label}.output.gif.max_colors", 2, 256)
        if gif.get("dither") not in {"none", "bayer"}:
            raise ValueError(f"{label}.output.gif.dither must be none or bayer")

    canvas = data.get("canvas")
    gif_settings = data.get("gif")
    webp = data.get("webp")
    budget = data.get("budget")
    trial = data.get("trial")
    if not isinstance(canvas, dict) or canvas.get("resize_mode") != "contain":
        raise ValueError("canvas.resize_mode must be contain")
    if canvas.get("preserve_aspect_ratio") is not True or canvas.get("premultiplied_alpha_resize") is not True:
        raise ValueError("canvas must preserve aspect ratio and use premultiplied alpha resize")
    _number(canvas.get("margin_fraction"), "canvas.margin_fraction", 0, 0.25)
    if not isinstance(gif_settings, dict):
        raise ValueError("gif settings must be an object")
    candidates = gif_settings.get("alpha_threshold_candidates")
    if (
        not isinstance(candidates, list) or not candidates
        or any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 254 for value in candidates)
    ):
        raise ValueError("gif.alpha_threshold_candidates must contain integers from 1 to 254")
    if gif_settings.get("optimize_delta_frames") not in {True, False}:
        raise ValueError("gif.optimize_delta_frames must be boolean")
    _integer(gif_settings.get("loop"), "gif.loop", 0, 65535)
    _number(
        gif_settings.get("max_alpha_coverage_delta", 0.15),
        "gif.max_alpha_coverage_delta",
        0,
        1,
    )
    if not isinstance(webp, dict) or webp.get("enabled") not in {True, False} or webp.get("lossless") not in {True, False}:
        raise ValueError("webp.enabled and webp.lossless must be boolean")
    _integer(webp.get("quality"), "webp.quality", 1, 100)
    _integer(webp.get("method"), "webp.method", 0, 6)
    _integer(webp.get("loop"), "webp.loop", 0, 65535)
    if not isinstance(budget, dict) or not isinstance(trial, dict):
        raise ValueError("budget and trial settings must be objects")
    _integer(budget.get("gif_max_bytes"), "budget.gif_max_bytes", 1024, 1024 * 1024 * 1024)
    if budget.get("on_exceeded") != "stop-trial-and-warn-pack":
        raise ValueError("budget.on_exceeded must be stop-trial-and-warn-pack")
    if not isinstance(trial.get("enabled"), bool) or not isinstance(trial.get("continue_pack_when_passed"), bool):
        raise ValueError("trial boolean settings are invalid")
    if not isinstance(trial.get("cell_id"), str) or not trial["cell_id"].isdigit():
        raise ValueError("trial.cell_id must be a numeric string")
    if data.get("unexpected_duration", {}).get("action") != "stop-and-report":
        raise ValueError("unexpected_duration.action must be stop-and-report")
    data["_meta"] = {"path": str(source), "sha256": settings_sha256(source)}
    return data


def match_duration_profile(settings: dict[str, Any], duration_seconds: float) -> dict[str, Any]:
    matches = [
        profile for profile in settings["duration_profiles"]
        if profile["match_duration"]["minimum_seconds"]
        <= duration_seconds
        <= profile["match_duration"]["maximum_seconds"]
    ]
    if len(matches) != 1:
        raise ValueError(f"no unique duration profile matches {duration_seconds:.6f} seconds")
    return matches[0]
