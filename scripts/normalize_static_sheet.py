#!/usr/bin/env python3
"""Normalize a generated sticker sheet to a real-alpha PNG.

The preferred path is native alpha from the image backend.  Only a verified
uniform, high-contrast chroma-key plate is accepted as an opaque fallback.
Checkerboards, two-tone previews, scenery, and gradients fail closed so they
cannot silently enter image-to-video generation.
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


class StaticSheetAlphaError(ValueError):
    """The generated sheet cannot be normalized safely."""


def meaningful_alpha(alpha: np.ndarray) -> bool:
    return bool(np.mean(alpha < 250) >= 0.002)


def _border_samples(rgb: np.ndarray) -> np.ndarray:
    height, width, _ = rgb.shape
    band = max(4, min(24, min(height, width) // 40))
    return np.concatenate(
        [
            rgb[:band].reshape(-1, 3),
            rgb[-band:].reshape(-1, 3),
            rgb[:, :band].reshape(-1, 3),
            rgb[:, -band:].reshape(-1, 3),
        ],
        axis=0,
    ).astype(np.float32)


def _fit_palette(samples: np.ndarray, colors: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if colors not in (1, 2):
        raise ValueError("only one- and two-color background models are supported")
    center = np.median(samples, axis=0)
    centers = [center]
    if colors == 2:
        distances = np.sqrt(np.sum((samples - center) ** 2, axis=1))
        centers.append(samples[int(np.argmax(distances))])
    palette = np.asarray(centers, dtype=np.float32)
    labels = np.zeros(len(samples), dtype=np.int8)
    for _ in range(12):
        distances = np.sqrt(np.sum((samples[:, None, :] - palette[None, :, :]) ** 2, axis=2))
        labels = np.argmin(distances, axis=1).astype(np.int8)
        updated = palette.copy()
        for index in range(colors):
            cluster = samples[labels == index]
            if len(cluster):
                updated[index] = np.median(cluster, axis=0)
        if np.allclose(updated, palette, atol=0.25):
            palette = updated
            break
        palette = updated
    residual = np.min(
        np.sqrt(np.sum((samples[:, None, :] - palette[None, :, :]) ** 2, axis=2)),
        axis=1,
    )
    fractions = np.asarray([np.mean(labels == index) for index in range(colors)], dtype=np.float32)
    return palette, residual, fractions


def classify_background(rgb: np.ndarray) -> dict:
    samples = _border_samples(rgb)
    one_palette, one_residual, _ = _fit_palette(samples, 1)
    one_p95 = float(np.percentile(one_residual, 95))
    if one_p95 <= 8.0:
        return {
            "kind": "uniform",
            "palette": one_palette,
            "border_residual_p95": one_p95,
            "cluster_fractions": [1.0],
        }

    two_palette, two_residual, fractions = _fit_palette(samples, 2)
    two_p95 = float(np.percentile(two_residual, 95))
    separation = float(np.linalg.norm(two_palette[0] - two_palette[1]))
    if two_p95 <= 14.0 and float(np.min(fractions)) >= 0.12 and separation >= 8.0:
        raise StaticSheetAlphaError(
            "simulated-transparency-detected: checkerboard or two-tone preview backgrounds "
            "are not safe to matte; regenerate with real alpha or the configured #00FF00 fallback"
        )
    raise StaticSheetAlphaError(
        "opaque sheet has an ambiguous non-uniform background; regenerate with real alpha "
        "or the configured #00FF00 opaque fallback"
    )


def validate_chroma_key(background: dict) -> None:
    color = np.asarray(background["palette"][0], dtype=np.float32)
    chroma = float(np.max(color) - np.min(color))
    luma = float(0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2])
    if chroma < 60.0 or not 30.0 <= luma <= 225.0:
        raise StaticSheetAlphaError(
            "unsafe-key-background-detected: opaque static sheets require a uniform, "
            "high-contrast chroma key; regenerate with the configured #00FF00 fallback"
        )


def _edge_connected(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    connected = np.zeros_like(mask, dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        if mask[0, x]:
            queue.append((0, x))
        if mask[-1, x]:
            queue.append((height - 1, x))
    for y in range(height):
        if mask[y, 0]:
            queue.append((y, 0))
        if mask[y, -1]:
            queue.append((y, width - 1))
    while queue:
        y, x = queue.popleft()
        if connected[y, x] or not mask[y, x]:
            continue
        connected[y, x] = True
        if y:
            queue.append((y - 1, x))
        if y + 1 < height:
            queue.append((y + 1, x))
        if x:
            queue.append((y, x - 1))
        if x + 1 < width:
            queue.append((y, x + 1))
    return connected


def matte_background(
    rgb: np.ndarray,
    palette: np.ndarray,
    *,
    hard_tolerance: float = 18.0,
    soft_tolerance: float = 46.0,
) -> tuple[Image.Image, dict[str, int | str]]:
    distances = np.sqrt(
        np.sum((rgb[:, :, None, :].astype(np.float32) - palette[None, None, :, :]) ** 2, axis=3)
    )
    nearest = np.min(distances, axis=2)
    nearest_palette = palette[np.argmin(distances, axis=2)]
    connected = _edge_connected(nearest <= soft_tolerance)
    palette_chroma = np.ptp(palette, axis=1)
    uniform_chroma_key = len(palette) == 1 and float(palette_chroma[0]) >= 80.0
    alpha = np.full(nearest.shape, 255, dtype=np.uint8)
    if uniform_chroma_key:
        # A chroma plate can remain visible in enclosed gaps between limbs,
        # clothing, or props. Those pixels are legitimate background even when
        # they are not connected to the outer canvas, so remove hard key-color
        # matches globally and include a narrow soft neighborhood around them.
        hard = nearest <= hard_tolerance
        hard_neighborhood = np.asarray(
            Image.fromarray((hard.astype(np.uint8) * 255), mode="L")
            .filter(ImageFilter.MaxFilter(size=7))
        ) >= 32
        soft_domain = connected | hard_neighborhood
    else:
        hard = connected & (nearest <= hard_tolerance)
        soft_domain = connected
    soft = soft_domain & ~hard & (nearest <= soft_tolerance)
    alpha[hard] = 0
    alpha[soft] = np.clip(
        255.0 * (nearest[soft] - hard_tolerance) / max(1.0, soft_tolerance - hard_tolerance),
        0,
        255,
    ).astype(np.uint8)
    green_key = bool(
        uniform_chroma_key
        and float(palette[0, 1] - max(palette[0, 0], palette[0, 2])) >= 80.0
    )
    chroma_alpha_pixel_count = 0
    if green_key:
        source_rgb = rgb.astype(np.float32)
        green_dominance = source_rgb[:, :, 1] - np.maximum(
            source_rgb[:, :, 0], source_rgb[:, :, 2]
        )
        key_dominance = float(palette[0, 1] - max(palette[0, 0], palette[0, 2]))
        spill_strength = np.clip(
            (green_dominance - 4.0) / max(1.0, key_dominance - 4.0),
            0.0,
            1.0,
        )
        chroma_alpha = np.rint(255.0 * (1.0 - spill_strength)).astype(np.uint8)
        chroma_affected = chroma_alpha < alpha
        alpha[chroma_affected] = chroma_alpha[chroma_affected]
        chroma_alpha_pixel_count = int(np.count_nonzero(chroma_affected))
    output_rgb = rgb.astype(np.float32)
    decontaminated_pixel_count = 0
    balanced_despill_pixel_count = 0
    partial = (alpha > 0) & (alpha < 255)
    if np.any(partial):
        alpha_float = np.maximum(alpha.astype(np.float32) / 255.0, 1.0 / 255.0)
        # Undo the source composite for anti-aliased edge pixels. Without this
        # step a green/gray key leaks into the fur and becomes a visible halo
        # when the RGBA result is composited on a dark chat background.
        decontaminated = (
            rgb.astype(np.float32) - (1.0 - alpha_float[:, :, None]) * nearest_palette
        ) / alpha_float[:, :, None]
        output_rgb[partial] = np.clip(decontaminated[partial], 0.0, 255.0)
        decontaminated_pixel_count = int(np.count_nonzero(partial))
    if green_key and np.any(partial):
        # Green mixed with warm beige can become a yellow/lime fringe after
        # alpha unmixing even when G is no longer greater than R. For pixels
        # whose source color still points toward the green plate, cap G at the
        # red/blue balance used by standard chroma-key despill. This preserves
        # the warm fur hue without eroding the alpha edge.
        source_rgb = rgb.astype(np.float32)
        source_green_excess = source_rgb[:, :, 1] - np.maximum(
            source_rgb[:, :, 0], source_rgb[:, :, 2]
        )
        balanced_green = 0.5 * (output_rgb[:, :, 0] + output_rgb[:, :, 2])
        mixed_spill = (
            partial
            & (source_green_excess > 4.0)
            & (output_rgb[:, :, 1] > balanced_green)
        )
        output_rgb[mixed_spill, 1] = balanced_green[mixed_spill]
        balanced_despill_pixel_count = int(np.count_nonzero(mixed_spill))
    output_rgb[alpha == 0] = 0
    # A generated green plate can leave fully opaque, green-dominant pixels
    # along the subject contour. For a verified uniform green plate, green is
    # known to be synthetic and even a weak excess can form a visible lime rim;
    # neutralize it throughout the remaining foreground. For other backgrounds,
    # keep the stronger threshold restricted to the outer contour.
    foreground_mask = alpha >= 32
    eroded_mask = np.asarray(
        Image.fromarray((foreground_mask.astype(np.uint8) * 255), mode="L")
        .filter(ImageFilter.MinFilter(size=9))
    ) >= 32
    contour = foreground_mask & ~eroded_mask
    red_or_blue = np.maximum(output_rgb[:, :, 0], output_rgb[:, :, 2])
    green_excess = output_rgb[:, :, 1].astype(np.int16) - red_or_blue.astype(np.int16)
    spill_scope = (alpha > 0) if green_key else contour
    spill_threshold = 3 if green_key else 8
    spill = spill_scope & (green_excess > spill_threshold)
    if np.any(spill):
        output_rgb[spill, 1] = red_or_blue[spill]
    despill_pixel_count = int(np.count_nonzero(spill))
    output_rgb = np.rint(output_rgb).astype(np.uint8)
    return Image.fromarray(np.dstack([output_rgb, alpha]), mode="RGBA"), {
        "decontamination": "alpha-unmix" if decontaminated_pixel_count else "none",
        "decontaminated_pixel_count": decontaminated_pixel_count,
        "chroma_alpha": "green-dominance" if chroma_alpha_pixel_count else "none",
        "chroma_alpha_pixel_count": chroma_alpha_pixel_count,
        "balanced_despill": "red-blue-average" if balanced_despill_pixel_count else "none",
        "balanced_despill_pixel_count": balanced_despill_pixel_count,
        "contour_despill": "green-dominance" if despill_pixel_count else "none",
        "despill_pixel_count": despill_pixel_count,
    }


def normalize_static_sheet(source: Path, output: Path, *, max_pixels: int = 64_000_000) -> dict:
    try:
        with Image.open(source) as opened:
            if opened.width * opened.height > max_pixels:
                raise StaticSheetAlphaError("static sheet exceeds the configured pixel limit")
            rgba = np.asarray(opened.convert("RGBA"), dtype=np.uint8)
    except OSError as exc:
        raise StaticSheetAlphaError(f"cannot open static sheet: {source}") from exc

    source_alpha = meaningful_alpha(rgba[:, :, 3])
    warnings: list[str] = []
    if source_alpha:
        normalized = Image.fromarray(rgba, mode="RGBA")
        method = "source-alpha"
        background = None
        edge_color_correction = {
            "decontamination": "none",
            "decontaminated_pixel_count": 0,
            "chroma_alpha": "none",
            "chroma_alpha_pixel_count": 0,
            "balanced_despill": "none",
            "balanced_despill_pixel_count": 0,
            "contour_despill": "none",
            "despill_pixel_count": 0,
        }
    else:
        background = classify_background(rgba[:, :, :3])
        validate_chroma_key(background)
        normalized, edge_color_correction = matte_background(
            rgba[:, :, :3], background["palette"]
        )
        method = "edge-connected-palette"

    normalized_rgba = np.asarray(normalized, dtype=np.uint8)
    alpha = normalized_rgba[:, :, 3]
    border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
    transparent_fraction = float(np.mean(alpha < 250))
    foreground_fraction = float(np.mean(alpha >= 32))
    border_opaque_fraction = float(np.mean(border >= 32))
    if transparent_fraction < 0.002:
        raise StaticSheetAlphaError("normalization did not produce meaningful transparency")
    if not 0.005 <= foreground_fraction <= 0.95:
        raise StaticSheetAlphaError(
            f"normalized foreground coverage is unsafe ({foreground_fraction:.4f})"
        )
    if border_opaque_fraction > 0.05:
        warnings.append("foreground-or-background-remnant-touches-outer-border")

    output.parent.mkdir(parents=True, exist_ok=True)
    normalized.save(output, format="PNG")
    report = {
        "version": 1,
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "source_had_real_alpha": source_alpha,
        "alpha_method": method,
        "background": (
            {
                key: (
                    [[round(float(channel), 3) for channel in color] for color in value]
                    if key == "palette"
                    else [round(float(item), 6) for item in value]
                    if key == "cluster_fractions"
                    else round(float(value), 6)
                    if isinstance(value, float)
                    else value
                )
                for key, value in background.items()
            }
            if background
            else None
        ),
        "transparent_fraction": round(transparent_fraction, 6),
        "foreground_fraction": round(foreground_fraction, 6),
        "border_opaque_fraction": round(border_opaque_fraction, 6),
        "edge_color_correction": edge_color_correction,
        "warnings": warnings,
        "requires_visual_review": bool(warnings),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="raw image-generation result")
    parser.add_argument("output", type=Path, help="normalized real-alpha PNG")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-pixels", type=int, default=64_000_000)
    args = parser.parse_args()
    try:
        report = normalize_static_sheet(args.source, args.output, max_pixels=args.max_pixels)
    except StaticSheetAlphaError as exc:
        raise SystemExit(str(exc)) from exc
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
