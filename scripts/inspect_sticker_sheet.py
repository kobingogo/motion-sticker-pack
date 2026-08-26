#!/usr/bin/env python3
"""Infer the actual sticker grid returned by an image model.

Layout notation is always columns x rows. Detection scores a bounded candidate
set instead of trusting the generation prompt.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def parse_grid(value: str) -> tuple[int, int]:
    try:
        columns, rows = (int(part) for part in value.lower().split("x", 1))
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("grid must use columnsxrows, for example 4x3") from exc
    if not (1 <= columns <= 12 and 1 <= rows <= 12):
        raise argparse.ArgumentTypeError("grid dimensions must be between 1 and 12")
    return columns, rows


def _border_background(rgb: np.ndarray) -> np.ndarray:
    height, width, _ = rgb.shape
    band = max(2, min(12, min(height, width) // 40))
    samples = np.concatenate(
        [
            rgb[:band, :].reshape(-1, 3),
            rgb[-band:, :].reshape(-1, 3),
            rgb[:, :band].reshape(-1, 3),
            rgb[:, -band:].reshape(-1, 3),
        ],
        axis=0,
    )
    return np.median(samples, axis=0).astype(np.float32)


def _edge_connected(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    connected = np.zeros_like(mask, dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        if mask[0, x]:
            queue.append((0, x))
        if mask[height - 1, x]:
            queue.append((height - 1, x))
    for y in range(height):
        if mask[y, 0]:
            queue.append((y, 0))
        if mask[y, width - 1]:
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


def foreground_mask(image: Image.Image, color_tolerance: float = 48.0) -> tuple[np.ndarray, str]:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[:, :, 3]
    transparent_fraction = float(np.mean(alpha < 224))
    if transparent_fraction >= 0.005:
        return alpha >= 32, "source-alpha"

    rgb = rgba[:, :, :3]
    background = _border_background(rgb)
    distance = np.sqrt(np.sum((rgb.astype(np.float32) - background) ** 2, axis=2))
    connected_background = _edge_connected(distance <= color_tolerance)
    return ~connected_background, "edge-connected-color"


def _band_mean(mask: np.ndarray, axis: int, center: float, width: int) -> float:
    limit = mask.shape[axis]
    start = max(0, int(round(center)) - width)
    end = min(limit, int(round(center)) + width + 1)
    if axis == 1:
        return float(mask[:, start:end].mean())
    return float(mask[start:end, :].mean())


def score_layout(mask: np.ndarray, columns: int, rows: int) -> dict[str, float]:
    height, width = mask.shape
    band_x = max(1, width // 160)
    band_y = max(1, height // 160)
    seam_values = [
        _band_mean(mask, 1, width * index / columns, band_x)
        for index in range(1, columns)
    ] + [
        _band_mean(mask, 0, height * index / rows, band_y)
        for index in range(1, rows)
    ]
    seam_occupancy = float(np.mean(seam_values)) if seam_values else 0.0

    occupancies: list[float] = []
    boundary_touch: list[float] = []
    for row in range(rows):
        y0, y1 = height * row // rows, height * (row + 1) // rows
        for column in range(columns):
            x0, x1 = width * column // columns, width * (column + 1) // columns
            cell = mask[y0:y1, x0:x1]
            occupancies.append(float(cell.mean()))
            edge = np.concatenate([cell[0], cell[-1], cell[:, 0], cell[:, -1]])
            boundary_touch.append(float(edge.mean()))

    nonempty = float(np.mean(np.asarray(occupancies) >= 0.008))
    mean_occupancy = float(np.mean(occupancies))
    variation = float(np.std(occupancies) / max(mean_occupancy, 1e-6))
    balance = math.exp(-variation)
    touch = float(np.mean(boundary_touch))
    seam_cleanliness = max(0.0, 1.0 - seam_occupancy / 0.12)
    boundary_cleanliness = max(0.0, 1.0 - touch / 0.20)
    score = 0.55 * seam_cleanliness + 0.25 * nonempty + 0.10 * balance + 0.10 * boundary_cleanliness
    return {
        "score": round(score, 6),
        "seam_occupancy": round(seam_occupancy, 6),
        "nonempty_fraction": round(nonempty, 6),
        "occupancy_balance": round(balance, 6),
        "boundary_occupancy": round(touch, 6),
    }


def detect_layout(
    image: Image.Image,
    candidates: list[tuple[int, int]],
    requested: tuple[int, int] | None = None,
) -> dict:
    mask, mask_method = foreground_mask(image)
    scored = []
    for columns, rows in candidates:
        metrics = score_layout(mask, columns, rows)
        scored.append({"columns": columns, "rows": rows, "count": columns * rows, **metrics})
    scored.sort(
        key=lambda item: (item["score"], requested == (item["columns"], item["rows"])),
        reverse=True,
    )
    best = scored[0]
    runner_up = scored[1]["score"] if len(scored) > 1 else 0.0
    margin = max(0.0, best["score"] - runner_up)
    confidence = min(0.99, max(0.0, 0.50 * best["score"] + 2.5 * margin))
    best = {**best, "confidence": round(confidence, 6)}
    warnings = []
    if confidence < 0.75:
        warnings.append("low-layout-confidence: inspect overlay or provide an explicit override")
    if best["nonempty_fraction"] < 1.0:
        warnings.append("one-or-more-detected-cells-appear-empty")
    return {
        "image_size": {"width": image.width, "height": image.height},
        "mask_method": mask_method,
        "requested_layout": (
            {"columns": requested[0], "rows": requested[1], "count": requested[0] * requested[1]}
            if requested
            else None
        ),
        "detected_layout": best,
        "candidates": scored,
        "warnings": warnings,
    }


def draw_overlay(image: Image.Image, layout: dict) -> Image.Image:
    overlay = image.convert("RGBA").copy()
    draw = ImageDraw.Draw(overlay)
    columns = int(layout["detected_layout"]["columns"])
    rows = int(layout["detected_layout"]["rows"])
    for column in range(1, columns):
        x = overlay.width * column // columns
        draw.line((x, 0, x, overlay.height), fill=(255, 0, 0, 230), width=max(1, overlay.width // 256))
    for row in range(1, rows):
        y = overlay.height * row // rows
        draw.line((0, y, overlay.width, y), fill=(255, 0, 0, 230), width=max(1, overlay.height // 256))
    font = ImageFont.load_default()
    digits = max(2, len(str(columns * rows)))
    for row in range(rows):
        for column in range(columns):
            index = row * columns + column + 1
            x = overlay.width * column // columns + 6
            y = overlay.height * row // rows + 6
            label = f"{index:0{digits}d}"
            draw.rectangle((x - 3, y - 3, x + 8 * len(label), y + 13), fill=(0, 0, 0, 180))
            draw.text((x, y), label, font=font, fill=(255, 255, 255, 255))
    return overlay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--requested", type=parse_grid)
    parser.add_argument("--override", type=parse_grid, help="confirmed columnsxrows when auto detection is ambiguous")
    parser.add_argument("--max-pixels", type=int, default=64_000_000)
    parser.add_argument(
        "--candidates",
        type=str,
        default="2x2,3x2,4x2,3x3,4x3,5x3,3x4,4x4,5x4,6x4",
        help="comma-separated columnsxrows candidates",
    )
    args = parser.parse_args()

    candidates = list(dict.fromkeys(parse_grid(value.strip()) for value in args.candidates.split(",") if value.strip()))
    if not candidates:
        raise SystemExit("at least one candidate layout is required")
    if args.requested and args.requested not in candidates:
        candidates.append(args.requested)
    with Image.open(args.image) as source:
        if source.width * source.height > args.max_pixels:
            raise ValueError("input image exceeds max-pixels")
        image = source.convert("RGBA")
    report = detect_layout(image, candidates, args.requested)
    if args.override:
        columns, rows = args.override
        metrics = score_layout(foreground_mask(image)[0], columns, rows)
        report["detected_layout"] = {
            "columns": columns,
            "rows": rows,
            "count": columns * rows,
            **metrics,
            "confidence": 1.0,
            "detection_mode": "manual-override",
        }
        report["warnings"] = [
            warning for warning in report["warnings"] if not warning.startswith("low-layout-confidence")
        ]
    report["source"] = str(args.image.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.overlay:
        args.overlay.parent.mkdir(parents=True, exist_ok=True)
        draw_overlay(image, report).save(args.overlay)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
