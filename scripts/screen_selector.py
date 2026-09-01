#!/usr/bin/env python3
"""Choose and materialize a chroma screen that conflicts least with foreground."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from inspect_sticker_sheet import foreground_mask


SCREEN_COLORS = {
    "green": "#00FF00",
    "blue": "#0066FF",
    "magenta": "#FF00FF",
    "cyan": "#00FFFF",
}


def hex_rgb(value: str) -> np.ndarray:
    return np.asarray([int(value[index:index + 2], 16) for index in (1, 3, 5)], dtype=np.float32)


def foreground_pixels(path: Path) -> tuple[Image.Image, np.ndarray, str]:
    with Image.open(path) as source:
        image = source.convert("RGBA")
    rgba = np.asarray(image, dtype=np.uint8)
    alpha = rgba[:, :, 3]
    if float(np.mean(alpha < 250)) >= 0.002:
        mask = alpha >= 32
        method = "source-alpha"
    else:
        mask, method = foreground_mask(image)
    if not np.any(mask):
        # Approval/layout validation is the authoritative empty-sheet gate.
        # A uniformly colored synthetic fixture or tightly cropped opaque
        # sticker has no inferable edge background, so conservatively treat
        # every visible pixel as foreground for conflict scoring.
        mask = alpha >= 32
        method = "opaque-full-frame-fallback"
    if not np.any(mask):
        image.close()
        raise ValueError("screen selection requires non-empty foreground")
    return image, mask, method


def choose_screen(path: Path) -> dict:
    image, mask, method = foreground_pixels(path)
    try:
        pixels = np.asarray(image, dtype=np.uint8)[:, :, :3][mask].astype(np.float32)
        if len(pixels) > 200_000:
            pixels = pixels[:: max(1, len(pixels) // 200_000)]
        scores = []
        for name, value in SCREEN_COLORS.items():
            distance = np.sqrt(np.sum((pixels - hex_rgb(value)) ** 2, axis=1))
            collision = float(np.mean(distance < 80.0))
            p05 = float(np.percentile(distance, 5))
            scores.append(
                {
                    "id": name,
                    "color": value,
                    "foreground_collision_fraction": round(collision, 6),
                    "foreground_distance_p05": round(p05, 4),
                    "score": round(p05 - 255.0 * collision, 4),
                }
            )
        selected = max(scores, key=lambda item: (item["score"], item["foreground_distance_p05"]))
        return {
            "version": 1,
            "method": "foreground-conflict",
            "mask_method": method,
            "selected": selected,
            "candidates": scores,
        }
    finally:
        image.close()


def materialize_screen(source_path: Path, target: Path, color: str) -> dict:
    image, mask, method = foreground_pixels(source_path)
    try:
        rgba = np.asarray(image, dtype=np.uint8).copy()
        alpha = rgba[:, :, 3]
        if method != "source-alpha":
            alpha = np.where(mask, 255, 0).astype(np.uint8)
        screen = np.broadcast_to(hex_rgb(color).astype(np.uint8), rgba[:, :, :3].shape).copy()
        weight = alpha.astype(np.float32)[:, :, None] / 255.0
        rgb = np.rint(rgba[:, :, :3] * weight + screen * (1.0 - weight)).astype(np.uint8)
        output = Image.fromarray(rgb, mode="RGB")
        target.parent.mkdir(parents=True, exist_ok=True)
        output.save(target, format="PNG", optimize=True)
        output.close()
        return {
            "source": str(source_path.expanduser().resolve()),
            "output": str(target.expanduser().resolve()),
            "color": color.upper(),
            "mask_method": method,
        }
    finally:
        image.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = choose_screen(args.image)
    if args.output:
        report["materialized"] = materialize_screen(
            args.image, args.output, report["selected"]["color"]
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
