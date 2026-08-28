#!/usr/bin/env python3
"""Split a detected grid video, create real alpha, Animated WebP/GIF, PNG, and ZIP."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

from animation_export import choose_gif_alpha_threshold, encode_gif_images, encode_webp_images
from output_safety import prepare_output, validate_archive_name
from sticker_production_config import load_production_settings, match_duration_profile
from video_background_qc import validate_frame_background


def load_layout(path: Path, allow_low_confidence: bool = False) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    detected = data.get("detected_layout", data)
    columns = int(detected["columns"])
    rows = int(detected["rows"])
    count = columns * rows
    if int(detected.get("count", count)) != count:
        raise ValueError("layout count does not equal columns * rows")
    confidence = detected.get("confidence")
    if confidence is not None and float(confidence) < 0.75 and not allow_low_confidence:
        raise ValueError(
            f"layout confidence {confidence} is below 0.75; inspect the overlay or pass --allow-low-confidence"
        )
    return {"columns": columns, "rows": rows, "count": count, "confidence": confidence, "source": data}


def probe_video_details(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"input video does not exist: {path}")
    for executable in ("ffmpeg", "ffprobe"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"required executable is not installed: {executable}")
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration",
        "-of",
        "json",
        str(path),
    ]
    data = json.loads(subprocess.check_output(command, text=True))
    if not data.get("streams"):
        raise RuntimeError("input has no video stream")
    stream = data["streams"][0]
    rate = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")
    numerator, denominator = (int(part) for part in rate.split("/", 1))
    fps = numerator / denominator if denominator else 0.0
    if fps <= 0:
        raise RuntimeError("input video has no usable frame rate")
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": fps,
        "frame_count": int(stream["nb_frames"]) if str(stream.get("nb_frames", "")).isdigit() else None,
        "duration_seconds": float(stream["duration"]) if stream.get("duration") not in (None, "N/A") else None,
    }


def probe_video(path: Path) -> tuple[int, int]:
    details = probe_video_details(path)
    return int(details["width"]), int(details["height"])


def extract_rgba_frames(path: Path, width: int, height: int, fps: int | None = None):
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "pipe:1",
    ]
    if fps is not None:
        command[5:5] = ["-vf", f"fps={fps}"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_bytes = width * height * 4
    assert process.stdout is not None
    consumer_closed = False
    try:
        while True:
            raw = process.stdout.read(frame_bytes)
            if not raw:
                break
            if len(raw) != frame_bytes:
                raise RuntimeError("ffmpeg returned a truncated raw video frame")
            yield np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4)).copy()
    except GeneratorExit:
        consumer_closed = True
        raise
    finally:
        process.stdout.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        if process.stderr is not None:
            process.stderr.close()
        return_code = process.wait()
        if return_code and not consumer_closed:
            raise RuntimeError(
                f"ffmpeg frame extraction failed with exit code {return_code}: {stderr[-800:].strip()}"
            )


def tile_bounds(size: int, index: int, parts: int) -> tuple[int, int]:
    return size * index // parts, size * (index + 1) // parts


def median_background(rgb: np.ndarray) -> np.ndarray:
    height, width, _ = rgb.shape
    band = max(2, min(10, min(height, width) // 24))
    samples = np.concatenate(
        [
            rgb[:band, :band].reshape(-1, 3),
            rgb[:band, -band:].reshape(-1, 3),
            rgb[-band:, :band].reshape(-1, 3),
            rgb[-band:, -band:].reshape(-1, 3),
            rgb[:band, :].reshape(-1, 3),
            rgb[-band:, :].reshape(-1, 3),
            rgb[:, :band].reshape(-1, 3),
            rgb[:, -band:].reshape(-1, 3),
        ],
        axis=0,
    )
    return np.median(samples, axis=0).astype(np.float32)


def parse_key_color(value: str) -> np.ndarray:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError("key color must use #RRGGBB notation")
    return np.array([int(value[index:index + 2], 16) for index in (1, 3, 5)], dtype=np.float32)


def is_neutral_plate(background: np.ndarray, chroma_limit: float = 40.0) -> bool:
    plate = np.asarray(background, dtype=np.float32).reshape(-1)[:3]
    chroma = float(np.max(plate) - np.min(plate))
    luma = float(0.299 * plate[0] + 0.587 * plate[1] + 0.114 * plate[2])
    return chroma < chroma_limit and (luma <= 40.0 or luma >= 215.0)


def plate_key_tolerances(
    background: np.ndarray,
    hard_tolerance: float,
    soft_tolerance: float,
) -> tuple[float, float]:
    """Tighten the key when the plate is near-black or near-white.

    Default RGB radii (38/72) are meant for chroma plates such as green. On a
    black plate they also match black fur, so edge-connected matting eats the
    subject and GIF binary transparency turns those pixels into holes.
    """
    if is_neutral_plate(background):
        hard_tolerance = min(hard_tolerance, 12.0)
        soft_tolerance = min(soft_tolerance, 28.0)
        if soft_tolerance <= hard_tolerance:
            soft_tolerance = hard_tolerance + 8.0
    return hard_tolerance, soft_tolerance


def _remove_edge_background_native(
    rgb: np.ndarray,
    background: np.ndarray | None = None,
    hard_tolerance: float = 38.0,
    soft_tolerance: float = 72.0,
    remove_enclosed_key: bool = False,
) -> Image.Image:
    """Remove only background-like pixels connected to the crop boundary."""
    height, width, _ = rgb.shape
    background = median_background(rgb) if background is None else background.astype(np.float32)
    hard_tolerance, soft_tolerance = plate_key_tolerances(background, hard_tolerance, soft_tolerance)
    distance = np.sqrt(np.sum((rgb.astype(np.float32) - background) ** 2, axis=2))
    candidate = distance <= soft_tolerance
    connected = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    if remove_enclosed_key and float(np.max(background) - np.min(background)) >= 80.0:
        # Fixed chroma mode starts from every exact/near-exact key seed, including
        # enclosed background holes, then expands only a short distance through
        # the soft key band. The vectorized expansion avoids a full Python BFS
        # while preserving isolated subject-colored regions that merely fall
        # inside the broad soft radius.
        connected = distance <= hard_tolerance
        for _ in range(8):
            padded = np.pad(connected, 1, mode="constant", constant_values=False)
            expanded = np.logical_or.reduce(
                [
                    padded[1:-1, 1:-1],
                    padded[:-2, 1:-1],
                    padded[2:, 1:-1],
                    padded[1:-1, :-2],
                    padded[1:-1, 2:],
                    padded[:-2, :-2],
                    padded[:-2, 2:],
                    padded[2:, :-2],
                    padded[2:, 2:],
                ]
            ) & candidate
            if np.array_equal(expanded, connected):
                break
            connected = expanded
    else:
        for x in range(width):
            if candidate[0, x]:
                queue.append((0, x))
            if candidate[height - 1, x]:
                queue.append((height - 1, x))
        for y in range(height):
            if candidate[y, 0]:
                queue.append((y, 0))
            if candidate[y, width - 1]:
                queue.append((y, width - 1))
        while queue:
            y, x = queue.popleft()
            if connected[y, x] or not candidate[y, x]:
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

    alpha = np.full((height, width), 255, dtype=np.uint8)
    hard = connected & (distance <= hard_tolerance)
    soft = connected & ~hard
    alpha[hard] = 0
    alpha[soft] = np.clip(
        255.0 * (distance[soft] - hard_tolerance) / max(1.0, soft_tolerance - hard_tolerance),
        0,
        255,
    ).astype(np.uint8)
    output_rgb = rgb.copy()
    plate = np.asarray(background, dtype=np.float32).reshape(3)
    chroma = float(np.max(plate) - np.min(plate))
    green_plate = bool(background[1] - max(background[0], background[2]) >= 80.0)
    if green_plate:
        edge_band = connected.copy()
        for _ in range(2):
            padded = np.pad(edge_band, 1, mode="constant", constant_values=False)
            edge_band = np.logical_or.reduce(
                [
                    padded[1:-1, 1:-1],
                    padded[:-2, 1:-1],
                    padded[2:, 1:-1],
                    padded[1:-1, :-2],
                    padded[1:-1, 2:],
                    padded[:-2, :-2],
                    padded[:-2, 2:],
                    padded[2:, :-2],
                    padded[2:, 2:],
                ]
            )
        rgb_float = rgb.astype(np.float32)
        green_dominance = rgb_float[:, :, 1] - np.maximum(rgb_float[:, :, 0], rgb_float[:, :, 2])
        despill_scope = np.ones_like(edge_band) if remove_enclosed_key else edge_band
        despill = despill_scope & (alpha > 0) & (green_dominance > 2.0)
        if np.any(despill):
            plate_dominance = max(1.0, float(plate[1] - max(plate[0], plate[2])))
            chroma_alpha = 255.0 * (
                1.0 - np.clip(green_dominance[despill] / plate_dominance, 0.0, 1.0)
            )
            alpha[despill] = np.minimum(alpha[despill], chroma_alpha.astype(np.uint8))
        visible_edge = despill_scope & (alpha > 0) & (alpha < 255)
        if np.any(visible_edge):
            edge_alpha = np.maximum(alpha[visible_edge].astype(np.float32)[:, None] / 255.0, 0.08)
            reconstructed = (
                rgb[visible_edge].astype(np.float32) - (1.0 - edge_alpha) * plate[None, :]
            ) / edge_alpha
            reconstructed = np.clip(reconstructed, 0.0, 255.0)
            reconstructed[:, 1] = np.minimum(
                reconstructed[:, 1],
                np.maximum(reconstructed[:, 0], reconstructed[:, 2]) + 2.0,
            )
            output_rgb[visible_edge] = reconstructed.astype(np.uint8)
    elif np.any(soft) and chroma >= 80.0:
        edge_alpha = np.maximum(alpha[soft].astype(np.float32)[:, None] / 255.0, 0.08)
        reconstructed = (
            rgb[soft].astype(np.float32) - (1.0 - edge_alpha) * plate[None, :]
        ) / edge_alpha
        output_rgb[soft] = np.clip(reconstructed, 0.0, 255.0).astype(np.uint8)
    return Image.fromarray(np.dstack([output_rgb, alpha]), mode="RGBA")


def _downsample_premultiplied(rgba: np.ndarray, size: tuple[int, int]) -> Image.Image:
    alpha = rgba[:, :, 3].astype(np.float32) / 255.0
    premultiplied = np.rint(rgba[:, :, :3].astype(np.float32) * alpha[:, :, None]).clip(0, 255)
    rgb_image = Image.fromarray(premultiplied.astype(np.uint8), mode="RGB").resize(
        size, Image.Resampling.LANCZOS
    )
    alpha_image = Image.fromarray(rgba[:, :, 3], mode="L").resize(size, Image.Resampling.LANCZOS)
    down_alpha = np.asarray(alpha_image, dtype=np.uint8)
    down_premultiplied = np.asarray(rgb_image, dtype=np.float32)
    factor = np.maximum(down_alpha.astype(np.float32) / 255.0, 1.0 / 255.0)
    down_rgb = np.rint(down_premultiplied / factor[:, :, None]).clip(0, 255).astype(np.uint8)
    down_rgb[down_alpha == 0] = 0
    return Image.fromarray(np.dstack([down_rgb, down_alpha]), mode="RGBA")


def remove_edge_background(
    rgb: np.ndarray,
    background: np.ndarray | None = None,
    hard_tolerance: float = 38.0,
    soft_tolerance: float = 72.0,
    remove_enclosed_key: bool = False,
    supersample: int = 2,
) -> Image.Image:
    """Create continuous alpha with premultiplied, supersampled edge cleanup."""
    if supersample < 1 or supersample > 4:
        raise ValueError("supersample must be between 1 and 4")
    if supersample == 1:
        return _remove_edge_background_native(
            rgb, background, hard_tolerance, soft_tolerance, remove_enclosed_key
        )
    height, width, _ = rgb.shape
    enlarged = Image.fromarray(rgb, mode="RGB").resize(
        (width * supersample, height * supersample), Image.Resampling.LANCZOS
    )
    high = _remove_edge_background_native(
        np.asarray(enlarged, dtype=np.uint8),
        background,
        hard_tolerance,
        soft_tolerance,
        remove_enclosed_key,
    )
    return _downsample_premultiplied(np.asarray(high, dtype=np.uint8), (width, height))


def meaningful_alpha(alpha: np.ndarray) -> bool:
    return bool(np.mean(alpha < 250) >= 0.002)


def frame_qc(rgba: np.ndarray) -> tuple[float, float]:
    alpha = rgba[:, :, 3]
    coverage = float(np.mean(alpha >= 32))
    border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
    border_coverage = float(np.mean(border >= 32))
    return coverage, border_coverage


class GridBoundaryError(ValueError):
    """A generated cell contains foreground on an internal grid boundary."""


def remove_interior_border_fragments(
    rgba: np.ndarray,
    row: int,
    column: int,
    rows: int,
    columns: int,
) -> tuple[np.ndarray, int]:
    """Remove disconnected neighbor fragments and reject a clipped main subject."""
    if not rgba.flags.writeable:
        rgba = rgba.copy()
    alpha = rgba[:, :, 3]
    mask = alpha >= 32
    height, width = mask.shape
    internal_edges = set()
    if column > 0:
        internal_edges.add("L")
    if column + 1 < columns:
        internal_edges.add("R")
    if row > 0:
        internal_edges.add("T")
    if row + 1 < rows:
        internal_edges.add("B")
    if not internal_edges or not np.any(mask):
        return rgba, 0

    seen = np.zeros_like(mask)
    components: list[tuple[list[tuple[int, int]], set[str]]] = []
    for y, x in zip(*np.where(mask)):
        if seen[y, x]:
            continue
        queue: deque[tuple[int, int]] = deque([(int(y), int(x))])
        seen[y, x] = True
        pixels: list[tuple[int, int]] = []
        touched: set[str] = set()
        while queue:
            yy, xx = queue.popleft()
            pixels.append((yy, xx))
            if xx == 0:
                touched.add("L")
            if xx == width - 1:
                touched.add("R")
            if yy == 0:
                touched.add("T")
            if yy == height - 1:
                touched.add("B")
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not (dy or dx):
                        continue
                    ny, nx = yy + dy, xx + dx
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
        components.append((pixels, touched))

    largest_index = max(range(len(components)), key=lambda index: len(components[index][0]))
    largest_pixels, largest_touched = components[largest_index]
    clipped = largest_touched & internal_edges
    if clipped:
        raise GridBoundaryError(
            f"cell {row * columns + column + 1:02d} foreground touches internal grid edge "
            f"{''.join(sorted(clipped))}; refusing clipped sticker"
        )

    removed = 0
    for index, (pixels, touched) in enumerate(components):
        if index == largest_index or not (touched & internal_edges):
            continue
        ys = np.fromiter((point[0] for point in pixels), dtype=np.intp)
        xs = np.fromiter((point[1] for point in pixels), dtype=np.intp)
        rgba[ys, xs, 3] = 0
        rgba[ys, xs, :3] = 0
        removed += 1
    return rgba, removed


def connected_components(mask: np.ndarray, minimum_pixels: int = 4) -> list[dict]:
    """Return 8-connected foreground components without adding a heavy CV dependency."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    components: list[dict] = []
    for seed_y, seed_x in np.argwhere(mask):
        y0, x0 = int(seed_y), int(seed_x)
        if seen[y0, x0]:
            continue
        queue: deque[tuple[int, int]] = deque([(y0, x0)])
        seen[y0, x0] = True
        ys: list[int] = []
        xs: list[int] = []
        while queue:
            y, x = queue.popleft()
            ys.append(y)
            xs.append(x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not (dy or dx):
                        continue
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
        if len(xs) < minimum_pixels:
            continue
        y_values = np.asarray(ys, dtype=np.intp)
        x_values = np.asarray(xs, dtype=np.intp)
        components.append(
            {
                "ys": y_values,
                "xs": x_values,
                "size": len(xs),
                "bbox": (int(x_values.min()), int(y_values.min()), int(x_values.max()) + 1, int(y_values.max()) + 1),
                "centroid": (float(x_values.mean()), float(y_values.mean())),
            }
        )
    return components


def cell_centroids(mask: np.ndarray, columns: int, rows: int) -> list[tuple[float, float] | None]:
    height, width = mask.shape
    centroids: list[tuple[float, float] | None] = []
    for tile in range(columns * rows):
        row, column = divmod(tile, columns)
        x0, x1 = tile_bounds(width, column, columns)
        y0, y1 = tile_bounds(height, row, rows)
        ys, xs = np.where(mask[y0:y1, x0:x1])
        if len(xs) < max(8, (x1 - x0) * (y1 - y0) // 500):
            centroids.append(None)
        else:
            centroids.append((float(x0 + xs.mean()), float(y0 + ys.mean())))
    return centroids


def estimate_global_translation(
    current: list[tuple[float, float] | None],
    baseline: list[tuple[float, float] | None],
    cell_width: float,
    cell_height: float,
) -> tuple[int, int, dict]:
    deltas = [
        (now[0] - before[0], now[1] - before[1])
        for now, before in zip(current, baseline)
        if now is not None and before is not None
    ]
    if len(deltas) < 3:
        return 0, 0, {"applied": False, "reason": "insufficient-cells"}
    values = np.asarray(deltas, dtype=np.float32)
    median = np.median(values, axis=0)
    deviation = np.median(np.abs(values - median), axis=0)
    maximum = np.array([0.08 * cell_width, 0.08 * cell_height], dtype=np.float32)
    stable = bool(np.all(deviation <= np.maximum(1.5, maximum * 0.35)))
    bounded = bool(np.all(np.abs(median) <= maximum))
    shift_x, shift_y = -int(round(float(median[0]))), -int(round(float(median[1])))
    if not stable or not bounded or (shift_x == 0 and shift_y == 0):
        return 0, 0, {
            "applied": False,
            "reason": "not-global-or-not-needed",
            "median": [round(float(value), 3) for value in median],
            "mad": [round(float(value), 3) for value in deviation],
        }
    return shift_x, shift_y, {
        "applied": True,
        "translation": [shift_x, shift_y],
        "median": [round(float(value), 3) for value in median],
        "mad": [round(float(value), 3) for value in deviation],
    }


def translate_rgba(rgba: np.ndarray, shift_x: int, shift_y: int) -> np.ndarray:
    if not shift_x and not shift_y:
        return rgba
    height, width, _ = rgba.shape
    result = np.zeros_like(rgba)
    source_x0, source_x1 = max(0, -shift_x), min(width, width - shift_x)
    source_y0, source_y1 = max(0, -shift_y), min(height, height - shift_y)
    target_x0, target_x1 = source_x0 + shift_x, source_x1 + shift_x
    target_y0, target_y1 = source_y0 + shift_y, source_y1 + shift_y
    if source_x1 > source_x0 and source_y1 > source_y0:
        result[target_y0:target_y1, target_x0:target_x1] = rgba[source_y0:source_y1, source_x0:source_x1]
    return result


def assign_grid_components(
    rgba: np.ndarray,
    columns: int,
    rows: int,
    *,
    alpha_threshold: int = 32,
) -> tuple[list[np.ndarray], list[bool], list[list[str]], dict]:
    """Assign complete components to cells before cropping.

    A component that crosses a seam is retained when the neighboring cell has
    its own independent main component. If one large component is the only main
    component for multiple cells, those cells are marked ambiguous for later
    safe-window selection instead of being destructively split at the seam.
    """
    height, width, _ = rgba.shape
    count = columns * rows
    components = connected_components(rgba[:, :, 3] >= alpha_threshold)
    cell_area = width * height / count
    for component in components:
        ids = (component["ys"] * rows // height) * columns + component["xs"] * columns // width
        counts = np.bincount(ids, minlength=count)
        owner = int(np.argmax(counts))
        component["counts"] = counts
        component["owner"] = owner
        component["owner_fraction"] = float(counts[owner] / component["size"])
    substantial_by_owner = [False] * count
    for component in components:
        if component["size"] >= max(24, int(cell_area * 0.006)):
            substantial_by_owner[component["owner"]] = True
    valid = [True] * count
    reasons: list[list[str]] = [[] for _ in range(count)]
    ownership = [np.zeros((height, width), dtype=bool) for _ in range(count)]
    recovered_crossings = 0
    ambiguous_components = 0
    for component in components:
        owner = component["owner"]
        counts = component["counts"]
        affected = [
            index for index, value in enumerate(counts)
            if value / component["size"] >= 0.18
        ]
        is_large = component["size"] >= max(32, int(cell_area * 0.01))
        ambiguous = False
        if is_large and len(affected) > 1:
            secondary = [cell for cell in affected if cell != owner]
            ambiguous = any(not substantial_by_owner[cell] for cell in secondary)
            if ambiguous:
                ambiguous_components += 1
                for cell in affected:
                    valid[cell] = False
                    reasons[cell].append("merged-instance-ambiguous")
            else:
                recovered_crossings += 1
        if not ambiguous:
            ownership[owner][component["ys"], component["xs"]] = True
    return ownership, valid, reasons, {
        "components": len(components),
        "recovered_crossings": recovered_crossings,
        "ambiguous_components": ambiguous_components,
    }


def expanded_cell_frame(
    rgba: np.ndarray,
    owned: np.ndarray,
    tile: int,
    columns: int,
    rows: int,
    padding_fraction: float = 0.25,
) -> tuple[np.ndarray, bool]:
    height, width, _ = rgba.shape
    row, column = divmod(tile, columns)
    x0, x1 = tile_bounds(width, column, columns)
    y0, y1 = tile_bounds(height, row, rows)
    cell_width, cell_height = x1 - x0, y1 - y0
    pad_x, pad_y = round(cell_width * padding_fraction), round(cell_height * padding_fraction)
    logical_x0, logical_y0 = x0 - pad_x, y0 - pad_y
    logical_x1, logical_y1 = x1 + pad_x, y1 + pad_y
    source_x0, source_x1 = max(0, logical_x0), min(width, logical_x1)
    source_y0, source_y1 = max(0, logical_y0), min(height, logical_y1)
    canvas = np.zeros((cell_height + 2 * pad_y, cell_width + 2 * pad_x, 4), dtype=np.uint8)
    local_x, local_y = source_x0 - logical_x0, source_y0 - logical_y0
    region = rgba[source_y0:source_y1, source_x0:source_x1].copy()
    region_owned = owned[source_y0:source_y1, source_x0:source_x1]
    region[~region_owned] = 0
    canvas[local_y:local_y + region.shape[0], local_x:local_x + region.shape[1]] = region
    clipped = bool(
        np.any(owned[:source_y0])
        or np.any(owned[source_y1:])
        or np.any(owned[source_y0:source_y1, :source_x0])
        or np.any(owned[source_y0:source_y1, source_x1:])
    )
    return canvas, clipped


def frame_signature(rgba: np.ndarray, size: int = 24) -> np.ndarray:
    alpha = rgba[:, :, 3].astype(np.float32) / 255.0
    premultiplied = np.rint(rgba[:, :, :3].astype(np.float32) * alpha[:, :, None]).astype(np.uint8)
    packed = np.dstack([premultiplied, rgba[:, :, 3]])
    return np.asarray(
        Image.fromarray(packed, mode="RGBA").resize((size, size), Image.Resampling.BILINEAR),
        dtype=np.float32,
    )


def _nearest_valid(index: int, valid: list[bool], radius: int) -> int | None:
    if valid[index]:
        return index
    for distance in range(1, radius + 1):
        for candidate in (index - distance, index + distance):
            if 0 <= candidate < len(valid) and valid[candidate]:
                return candidate
    return None


def select_loop_indices(
    valid: list[bool],
    signatures: list[np.ndarray],
    source_fps: float,
    output_fps: int,
    minimum_seconds: float,
    maximum_seconds: float,
) -> dict:
    """Select the cleanest short loop after native-frame analysis."""
    if len(valid) != len(signatures) or len(valid) < 2:
        raise ValueError("loop selection requires matching native-frame metadata")
    available = max(2, int(np.floor(len(valid) * output_fps / source_fps)))
    minimum_count = min(available, max(2, int(np.ceil(minimum_seconds * output_fps))))
    maximum_count = min(available, max(minimum_count, int(np.floor(maximum_seconds * output_fps))))
    repair_radius = max(1, int(round(0.20 * source_fps)))
    step = source_fps / output_fps
    candidates: list[dict] = []
    for output_count in range(maximum_count, minimum_count - 1, -1):
        span = int(round((output_count - 1) * step))
        for start in range(0, max(1, len(valid) - span)):
            requested = [min(len(valid) - 1, int(round(start + offset * step))) for offset in range(output_count)]
            resolved: list[int] = []
            repairs = 0
            for index in requested:
                replacement = _nearest_valid(index, valid, repair_radius)
                if replacement is None:
                    break
                repairs += int(replacement != index)
                resolved.append(replacement)
            if len(resolved) != output_count or repairs > max(2, output_count // 5):
                continue
            loop = float(np.mean(np.abs(signatures[resolved[0]] - signatures[resolved[-1]])) / 255.0)
            changes = [
                float(np.mean(np.abs(signatures[right] - signatures[left])) / 255.0)
                for left, right in zip(resolved, resolved[1:])
            ]
            motion = float(np.mean(changes)) if changes else 0.0
            start_seconds = start / source_fps
            score = (
                loop
                + 0.08 * repairs
                + max(0.0, 0.006 - motion) * 8.0
                + max(0.0, start_seconds - 2.0) * 0.03
                - output_count * 0.0002
            )
            candidates.append(
                {
                    "requested": requested,
                    "indices": resolved,
                    "repairs": repairs,
                    "loop_difference": loop,
                    "motion_score": motion,
                    "score": score,
                }
            )
    if not candidates:
        usable = [index for index, is_valid in enumerate(valid) if is_valid]
        if len(usable) < 2:
            raise GridBoundaryError("no recoverable continuous animation window remains")
        chosen = usable[: min(len(usable), maximum_count)]
        candidates.append(
            {
                "requested": chosen,
                "indices": chosen,
                "repairs": 0,
                "loop_difference": float(
                    np.mean(np.abs(signatures[chosen[0]] - signatures[chosen[-1]])) / 255.0
                ),
                "motion_score": 0.0,
                "score": 1.0,
                "degraded": True,
            }
        )
    selected = min(candidates, key=lambda item: (item["score"], -len(item["indices"])))
    return {
        **selected,
        "source_start_frame": selected["requested"][0] + 1,
        "source_end_frame": selected["requested"][-1] + 1,
        "output_frames": len(selected["indices"]),
        "duration_seconds": round(len(selected["indices"]) / output_fps, 6),
    }


def preserve_full_duration_indices(
    valid: list[bool],
    signatures: list[np.ndarray],
    source_fps: float,
) -> dict:
    """Keep every native frame and fail closed if any frame is unsafe."""
    if len(valid) != len(signatures) or len(valid) < 2:
        raise ValueError("full-duration preservation requires matching native-frame metadata")
    invalid = [index + 1 for index, is_valid in enumerate(valid) if not is_valid]
    if invalid:
        preview = ", ".join(str(index) for index in invalid[:8])
        suffix = "..." if len(invalid) > 8 else ""
        raise GridBoundaryError(
            f"full-duration mode cannot skip unsafe native frames ({preview}{suffix})"
        )
    indices = list(range(len(valid)))
    loop = float(np.mean(np.abs(signatures[0] - signatures[-1])) / 255.0)
    changes = [
        float(np.mean(np.abs(right - left)) / 255.0)
        for left, right in zip(signatures, signatures[1:])
    ]
    return {
        "mode": "preserve-full-duration",
        "requested": indices,
        "indices": indices,
        "repairs": 0,
        "frame_step": 1,
        "loop_difference": loop,
        "motion_score": float(np.mean(changes)) if changes else 0.0,
        "source_start_frame": 1,
        "source_end_frame": len(indices),
        "output_frames": len(indices),
        "duration_seconds": round(len(indices) / source_fps, 6),
    }


def sample_full_duration_indices(
    valid: list[bool],
    signatures: list[np.ndarray],
    source_fps: float,
    output_fps: int,
) -> dict:
    """Sample the complete source timeline at a lower regular output rate."""
    if len(valid) != len(signatures) or len(valid) < 2:
        raise ValueError("full-duration sampling requires matching native-frame metadata")
    if output_fps > source_fps + 1e-6:
        raise ValueError("configured output fps cannot exceed the source fps")
    source_duration = len(valid) / source_fps
    output_count = max(2, int(round(source_duration * output_fps)))
    step = source_fps / output_fps
    requested = [min(len(valid) - 1, int(np.floor(index * step))) for index in range(output_count)]
    resolved: list[int] = []
    repairs = 0
    for output_index, requested_index in enumerate(requested):
        left = int(np.floor(output_index * step))
        right = min(len(valid), max(left + 1, int(np.floor((output_index + 1) * step))))
        candidates = [index for index in range(left, right) if valid[index]]
        if not candidates:
            raise GridBoundaryError(
                f"no safe native frame remains in output time bin {output_index + 1}"
            )
        selected = min(candidates, key=lambda index: abs(index - requested_index))
        repairs += int(selected != requested_index)
        resolved.append(selected)
    changes = [
        float(np.mean(np.abs(signatures[right] - signatures[left])) / 255.0)
        for left, right in zip(resolved, resolved[1:])
    ]
    return {
        "mode": "full-duration-sampled",
        "requested": requested,
        "indices": resolved,
        "repairs": repairs,
        "frame_step": round(step, 6),
        "loop_difference": float(
            np.mean(np.abs(signatures[resolved[0]] - signatures[resolved[-1]])) / 255.0
        ),
        "motion_score": float(np.mean(changes)) if changes else 0.0,
        "source_start_frame": 1,
        "source_end_frame": len(valid),
        "output_frames": len(resolved),
        "duration_seconds": round(len(resolved) / output_fps, 6),
        "source_duration_seconds": round(source_duration, 6),
    }


def select_short_delivery_indices(
    images: list[Image.Image],
    output_fps: int,
    target_seconds: float,
) -> dict:
    """Select the initial target-length prefix and audit, but do not gate on, endpoint drift."""
    if len(images) < 2:
        raise ValueError("short delivery selection requires at least two frames")
    target_count = int(round(target_seconds * output_fps))
    if target_count < 2 or len(images) < target_count:
        raise GridBoundaryError("source is too short for the requested delivery duration")
    signatures = [
        frame_signature(np.asarray(image.convert("RGBA"), dtype=np.uint8)) for image in images
    ]

    def loop_difference(end_index: int) -> float:
        left, right = signatures[0], signatures[end_index]
        visible = np.maximum(left[:, :, 3], right[:, :, 3]) >= 8
        if not np.any(visible):
            return 0.0
        return float(np.mean(np.abs(left[visible] - right[visible])) / 255.0)

    prefix_end = target_count - 1
    prefix_difference = loop_difference(prefix_end)
    indices = list(range(target_count))
    return {
        "mode": "prefix-duration",
        "indices": indices,
        "source_start_frame": 1,
        "source_end_frame": target_count,
        "source_duration_seconds": round(target_count / output_fps, 6),
        "output_frames": target_count,
        "duration_seconds": round(target_count / output_fps, 6),
        "endpoint_difference": prefix_difference,
        "endpoint_difference_policy": "informational-only",
        "speed_factor": 1.0,
    }


def normalize_stable_canvas(
    frames: list[np.ndarray],
    target_size: tuple[int, int],
    margin_fraction: float = 0.05,
) -> tuple[list[Image.Image], dict]:
    masks = [frame[:, :, 3] >= 16 for frame in frames]
    union = np.logical_or.reduce(masks)
    ys, xs = np.where(union)
    if not len(xs):
        raise GridBoundaryError("selected loop is empty after instance assignment")
    x0, x1 = max(0, int(xs.min()) - 2), min(union.shape[1], int(xs.max()) + 3)
    y0, y1 = max(0, int(ys.min()) - 2), min(union.shape[0], int(ys.max()) + 3)
    target_width, target_height = target_size
    available_width = max(1, round(target_width * (1.0 - 2.0 * margin_fraction)))
    available_height = max(1, round(target_height * (1.0 - 2.0 * margin_fraction)))
    scale = min(1.0, available_width / (x1 - x0), available_height / (y1 - y0))
    content_size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
    paste = ((target_width - content_size[0]) // 2, (target_height - content_size[1]) // 2)
    normalized: list[Image.Image] = []
    for frame in frames:
        content = frame[y0:y1, x0:x1]
        resized = _downsample_premultiplied(content, content_size) if content_size != (x1 - x0, y1 - y0) else Image.fromarray(content, mode="RGBA")
        canvas = Image.new("RGBA", target_size, (0, 0, 0, 0))
        canvas.alpha_composite(resized, paste)
        normalized.append(canvas)
    return normalized, {
        "union_bbox": [x0, y0, x1, y1],
        "scale": round(scale, 6),
        "paste": list(paste),
        "margin_fraction": margin_fraction,
    }


def read_animation_frames(path: Path) -> list[np.ndarray]:
    frames: list[np.ndarray] = []
    with Image.open(path) as animation:
        total = getattr(animation, "n_frames", 1)
        for index in range(total):
            animation.seek(index)
            frames.append(np.asarray(animation.convert("RGBA"), dtype=np.uint8).copy())
    return frames


def validate_encoded_animation(path: Path, *, expected_size: tuple[int, int]) -> dict:
    frames = read_animation_frames(path)
    if len(frames) < 2:
        raise GridBoundaryError(f"encoded {path.suffix} is not animated")
    coverages = []
    borders = []
    for index, frame in enumerate(frames, start=1):
        if (frame.shape[1], frame.shape[0]) != expected_size:
            raise GridBoundaryError(f"encoded frame {index} changed canvas size")
        coverage, border = frame_qc(frame)
        if coverage < 0.005:
            raise GridBoundaryError(f"encoded frame {index} is empty")
        if border > 0.03:
            raise GridBoundaryError(f"encoded frame {index} touches the canvas boundary")
        coverages.append(coverage)
        borders.append(border)
    if max(coverages) - min(coverages) > 0.15:
        raise GridBoundaryError("encoded alpha coverage flickers across frames")
    centroids: list[tuple[float, float]] = []
    for frame in frames:
        alpha = frame[:, :, 3].astype(np.float64)
        weight = float(alpha.sum())
        yy, xx = np.indices(alpha.shape)
        centroids.append(
            (
                float(np.sum(xx * alpha) / weight),
                float(np.sum(yy * alpha) / weight),
            )
        )
    centroid_steps = [
        float(np.hypot(right[0] - left[0], right[1] - left[1]))
        for left, right in zip(centroids, centroids[1:])
    ]
    hold_centroids = centroids[len(centroids) // 2 :]
    hold_steps = [
        float(np.hypot(right[0] - left[0], right[1] - left[1]))
        for left, right in zip(hold_centroids, hold_centroids[1:])
    ]
    return {
        "frames_checked": len(frames),
        "alpha_coverage_min": round(min(coverages), 6),
        "alpha_coverage_max": round(max(coverages), 6),
        "border_coverage_max": round(max(borders), 6),
        "centroid_step_pixels_mean": round(float(np.mean(centroid_steps)), 6),
        "centroid_step_pixels_max": round(max(centroid_steps), 6),
        "hold_centroid_step_pixels_mean": round(float(np.mean(hold_steps)), 6),
        "hold_centroid_step_pixels_max": round(max(hold_steps), 6),
    }


def encode_sticker_images(
    images: list[Image.Image],
    output: Path,
    stem: str,
    output_fps: float,
    target_size: tuple[int, int],
    settings: dict | None,
    gif_output: dict,
) -> dict:
    png_name, webp_name, gif_name = f"{stem}.png", f"{stem}.webp", f"{stem}.gif"
    alpha_candidates = (
        tuple(int(value) for value in settings["gif"]["alpha_threshold_candidates"])
        if settings else (96, 128, 160, 192)
    )
    gif_threshold, gif_strategy = choose_gif_alpha_threshold(images, alpha_candidates)
    images[0].save(output / png_name, optimize=True)
    webp_settings = settings["webp"] if settings else {
        "enabled": True, "lossless": True, "quality": 85, "method": 4, "loop": 0,
    }
    names: list[str] = []
    if webp_settings["enabled"]:
        encode_webp_images(
            images,
            output / webp_name,
            output_fps,
            lossless=bool(webp_settings["lossless"]),
            quality=int(webp_settings["quality"]),
            method=int(webp_settings["method"]),
            loop=int(webp_settings["loop"]),
        )
        names.append(webp_name)
    encode_gif_images(
        images,
        output / gif_name,
        output_fps,
        gif_threshold,
        int(gif_output.get("max_colors", 256)),
        str(gif_output.get("dither", "none")),
        bool(settings["gif"]["optimize_delta_frames"]) if settings else True,
        int(settings["gif"]["loop"]) if settings else 0,
    )
    names.extend([gif_name, png_name])
    encoded_qc = {
        "gif": validate_encoded_animation(output / gif_name, expected_size=target_size),
    }
    if webp_settings["enabled"]:
        encoded_qc["webp"] = validate_encoded_animation(
            output / webp_name, expected_size=target_size
        )
    gif_bytes = (output / gif_name).stat().st_size
    budget_report = None
    if settings:
        maximum = int(settings["budget"]["gif_max_bytes"])
        budget_report = {
            "gif_bytes": gif_bytes,
            "gif_max_bytes": maximum,
            "passed": gif_bytes <= maximum,
        }
    return {
        "names": names,
        "gif_alpha": gif_strategy,
        "gif_palette": {
            "max_colors": int(gif_output.get("max_colors", 256)),
            "dither": str(gif_output.get("dither", "none")),
        },
        "webp": webp_settings,
        "file_budget": budget_report,
        "encoded_qc": encoded_qc,
    }


def infer_plate_color(rgba: np.ndarray, known: np.ndarray | None = None) -> np.ndarray:
    if known is not None:
        return np.asarray(known, dtype=np.float32).reshape(3)
    alpha = rgba[:, :, 3]
    rgb = rgba[:, :, :3]
    transparent = alpha < 32
    if int(np.count_nonzero(transparent)) >= 8:
        return np.median(rgb[transparent], axis=0).astype(np.float32)
    return median_background(rgb)


def write_preview(
    path: Path,
    pngs: dict[int, Path],
    columns: int,
    rows: int,
    cell_size: tuple[int, int],
) -> None:
    preview_width = cell_size[0] * columns
    preview_height = cell_size[1] * rows
    checker_tile = Image.new("RGB", (16, 16), (255, 255, 255))
    checker_pixels = checker_tile.load()
    for yy in range(16):
        for xx in range(16):
            if (xx // 8 + yy // 8) % 2:
                checker_pixels[xx, yy] = (232, 232, 232)
    preview = Image.new("RGB", (preview_width, preview_height))
    for yy in range(0, preview_height, 16):
        for xx in range(0, preview_width, 16):
            preview.paste(checker_tile, (xx, yy))
    for tile, png in pngs.items():
        row, column = divmod(tile, columns)
        x0, _ = tile_bounds(preview_width, column, columns)
        y0, _ = tile_bounds(preview_height, row, rows)
        with Image.open(png) as image:
            sticker = image.convert("RGBA")
            preview.paste(sticker, (x0, y0), sticker)
    preview.save(path, optimize=True)


def subject_alpha_damage(
    rgba: np.ndarray,
    plate: np.ndarray | None = None,
    min_distance: float = 28.0,
    weak_alpha: int = 128,
) -> float:
    """Fraction of subject-colored pixels whose alpha is too weak to survive a GIF.

    Subject pixels are those whose RGB is farther than `min_distance` from the
    plate. Empty space around a small pose is ignored; moth-eaten dark fur is not.
    """
    plate_color = infer_plate_color(rgba, plate)
    rgb = rgba[:, :, :3].astype(np.float32)
    alpha = rgba[:, :, 3]
    distance = np.sqrt(np.sum((rgb - plate_color) ** 2, axis=2))
    subject = distance >= min_distance
    if int(np.count_nonzero(subject)) < 16:
        return 0.0
    return float(np.mean(alpha[subject] < weak_alpha))


def package_outputs(output: Path, names: list[str], zip_name: str) -> Path:
    archive = output / zip_name
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in names:
            bundle.write(output / name, arcname=name)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--settings", type=Path, help="sticker-production settings JSON")
    parser.add_argument("--trial", action="store_true", help="encode only the configured trial cell")
    parser.add_argument("--fps", type=int, default=6)
    parser.add_argument("--loop-min-seconds", type=float, default=1.5)
    parser.add_argument("--loop-max-seconds", type=float, default=2.5)
    parser.add_argument(
        "--preserve-full-duration",
        action="store_true",
        help="keep every native source frame at the source frame rate; disables loop-window selection",
    )
    parser.add_argument("--hard-tol", type=float, default=38.0)
    parser.add_argument("--soft-tol", type=float, default=72.0)
    parser.add_argument(
        "--supersample",
        type=int,
        default=1,
        help="optional 2x/4x matte refinement; native 720p is the fast quality default",
    )
    parser.add_argument("--background-mode", choices=("auto", "preserve-alpha", "edge-color"), default="auto")
    parser.add_argument("--key-color", help="fixed #RRGGBB background color used for deterministic matting")
    parser.add_argument("--keep-frames", action="store_true")
    parser.add_argument("--allow-low-confidence", action="store_true")
    parser.add_argument("--zip-name", default="sticker-pack.zip")
    parser.add_argument("--max-frames", type=int, default=600)
    parser.add_argument("--max-input-bytes", type=int, default=1024 * 1024 * 1024)
    parser.add_argument("--max-pixels", type=int, default=16_777_216)
    parser.add_argument(
        "--registration",
        choices=("auto", "off"),
        default="off",
        help=(
            "optional whole-grid translation correction; disabled by default because "
            "frame-by-frame integer corrections can introduce visible micro-jitter"
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    settings = load_production_settings(args.settings) if args.settings else None
    if not 1 <= args.fps <= 60:
        raise ValueError("fps must be between 1 and 60")
    if not 0.5 <= args.loop_min_seconds <= args.loop_max_seconds <= 6.0:
        raise ValueError("loop duration must satisfy 0.5 <= minimum <= maximum <= 6.0")
    if not 0 <= args.hard_tol < args.soft_tol <= 442:
        raise ValueError("tolerances must satisfy 0 <= hard < soft <= 442")
    if not 1 <= args.supersample <= 4:
        raise ValueError("supersample must be between 1 and 4")
    if args.max_frames < 2:
        raise ValueError("max-frames must be at least 2")
    configured_key_color = settings["generation"]["key_color"] if settings else None
    effective_key_color = args.key_color or configured_key_color
    key_color = parse_key_color(effective_key_color) if effective_key_color else None
    if args.max_input_bytes < 1 or args.video.stat().st_size > args.max_input_bytes:
        raise ValueError("input video exceeds max-input-bytes")
    args.zip_name = validate_archive_name(args.zip_name)

    layout = load_layout(args.layout, args.allow_low_confidence)
    columns, rows, count = layout["columns"], layout["rows"], layout["count"]
    video_details = probe_video_details(args.video)
    width, height = int(video_details["width"]), int(video_details["height"])
    source_fps = float(video_details["fps"])
    duration_profile = None
    measured_duration = video_details.get("duration_seconds")
    if settings:
        if measured_duration is None and video_details.get("frame_count"):
            measured_duration = float(video_details["frame_count"]) / source_fps
        if measured_duration is None:
            raise ValueError("configured duration routing requires a measurable source duration")
        duration_profile = match_duration_profile(settings, float(measured_duration))
        output_fps = float(duration_profile["output"]["fps"])
    else:
        output_fps = source_fps if args.preserve_full_duration else float(args.fps)
    if args.trial and not settings:
        raise ValueError("--trial requires --settings")
    if args.trial and not settings["trial"]["enabled"]:
        raise ValueError("trial mode is disabled in the production settings")
    if not 1 <= output_fps <= 60:
        raise ValueError("effective output fps must be between 1 and 60")
    if width * height > args.max_pixels:
        raise ValueError(f"video frame exceeds max-pixels ({width}x{height})")
    prepare_output(args.output, overwrite=args.overwrite, archive_names={args.zip_name})
    delivery_variant = None
    delivery_variant_name = None
    delivery_variant_root = None
    if settings and measured_duration is not None:
        provider = settings["generation"]["provider"]
        configured_variant = settings.get("delivery_variants", {}).get(provider)
        if configured_variant and float(measured_duration) > float(configured_variant["short_duration_seconds"]) + 0.25:
            delivery_variant = configured_variant
            short_seconds = float(configured_variant["short_duration_seconds"])
            delivery_variant_name = f"{short_seconds:g}s"
            delivery_variant_root = args.output / delivery_variant_name
            delivery_variant_root.mkdir(parents=True, exist_ok=True)
    settings_output_name = None
    if args.settings:
        settings_output_name = "sticker-production.json"
        shutil.copyfile(args.settings.expanduser().resolve(), args.output / settings_output_name)
        if delivery_variant_root is not None:
            shutil.copyfile(
                args.settings.expanduser().resolve(), delivery_variant_root / settings_output_name
            )
    digits = max(2, len(str(count)))

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_frames:
        frames_root = args.output / "frames"
        frames_root.mkdir(exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="motion-sticker-pack-")
        frames_root = Path(temporary.name)
    for index in range(1, count + 1):
        (frames_root / f"{index:0{digits}d}").mkdir(exist_ok=True)

    native_paths: list[list[Path]] = [[] for _ in range(count)]
    signatures: list[list[np.ndarray]] = [[] for _ in range(count)]
    valid_frames: list[list[bool]] = [[] for _ in range(count)]
    invalid_reasons: list[list[list[str]]] = [[] for _ in range(count)]
    assignment_reports: list[dict] = []
    registration_reports: list[dict] = []
    background_qc = {"frames_checked": 0}
    alpha_method = "unknown"
    baseline_centroids: list[tuple[float, float] | None] | None = None
    frame_count = 0

    try:
        for frame_count, rgba in enumerate(extract_rgba_frames(args.video, width, height), start=1):
            if frame_count > args.max_frames:
                raise RuntimeError(f"video exceeds max-frames ({args.max_frames})")
            use_alpha = args.background_mode == "preserve-alpha" or (
                args.background_mode == "auto" and meaningful_alpha(rgba[:, :, 3])
            )
            if args.background_mode == "preserve-alpha" and not meaningful_alpha(rgba[:, :, 3]):
                raise ValueError("preserve-alpha requested but the video has no meaningful alpha")
            if use_alpha:
                full_matte = rgba.copy()
                alpha_method = "source-alpha-full-frame"
            else:
                background = key_color if key_color is not None else median_background(rgba[:, :, :3])
                validate_frame_background(
                    rgba[:, :, :3],
                    background if key_color is not None else None,
                    label=f"native video frame {frame_count}",
                )
                background_qc["frames_checked"] += 1
                full_matte = np.asarray(
                    remove_edge_background(
                        rgba[:, :, :3],
                        background,
                        args.hard_tol,
                        args.soft_tol,
                        remove_enclosed_key=key_color is not None,
                        supersample=args.supersample,
                    ),
                    dtype=np.uint8,
                ).copy()
                alpha_method = "full-frame-continuous-fixed-key"

            current_centroids = cell_centroids(full_matte[:, :, 3] >= 32, columns, rows)
            if baseline_centroids is None:
                baseline_centroids = current_centroids
                registration = {"applied": False, "reason": "baseline"}
            elif args.registration == "auto":
                shift_x, shift_y, registration = estimate_global_translation(
                    current_centroids, baseline_centroids, width / columns, height / rows
                )
                full_matte = translate_rgba(full_matte, shift_x, shift_y)
            else:
                registration = {"applied": False, "reason": "disabled"}
            registration_reports.append(registration)

            ownership, frame_valid, frame_reasons, assignment = assign_grid_components(
                full_matte, columns, rows
            )
            assignment_reports.append(assignment)
            for tile in range(count):
                canvas, clipped = expanded_cell_frame(full_matte, ownership[tile], tile, columns, rows)
                reasons = list(frame_reasons[tile])
                if clipped:
                    frame_valid[tile] = False
                    reasons.append("owned-instance-exceeds-recovery-canvas")
                coverage, _ = frame_qc(canvas)
                if coverage < 0.004:
                    frame_valid[tile] = False
                    reasons.append("empty-instance")
                path = frames_root / f"{tile + 1:0{digits}d}" / f"{frame_count:04d}.png"
                Image.fromarray(canvas, mode="RGBA").save(path, compress_level=1)
                native_paths[tile].append(path)
                signatures[tile].append(frame_signature(canvas))
                valid_frames[tile].append(frame_valid[tile])
                invalid_reasons[tile].append(reasons)

        if frame_count == 0:
            raise RuntimeError("no video frames were extracted")

        outputs: list[str] = []
        cell_reports: list[dict] = []
        warnings: list[str] = []
        successful_pngs: dict[int, Path] = {}
        delivery_outputs: list[str] = []
        delivery_cell_reports: list[dict] = []
        delivery_successful_pngs: dict[int, Path] = {}
        delivery_failures: list[str] = []
        target_tiles = list(range(count))
        if args.trial:
            trial_tile = int(settings["trial"]["cell_id"]) - 1
            if not 0 <= trial_tile < count:
                raise ValueError("configured trial.cell_id is outside the detected layout")
            target_tiles = [trial_tile]
        budget_failures: list[str] = []
        for tile in target_tiles:
            stem = f"{tile + 1:0{digits}d}"
            png_name, webp_name, gif_name = f"{stem}.png", f"{stem}.webp", f"{stem}.gif"
            try:
                selection = (
                    sample_full_duration_indices(
                        valid_frames[tile], signatures[tile], source_fps, int(output_fps)
                    )
                    if duration_profile
                    else preserve_full_duration_indices(
                        valid_frames[tile], signatures[tile], source_fps
                    )
                    if args.preserve_full_duration
                    else select_loop_indices(
                        valid_frames[tile],
                        signatures[tile],
                        source_fps,
                        args.fps,
                        args.loop_min_seconds,
                        args.loop_max_seconds,
                    )
                )
                selected_arrays = [
                    np.asarray(Image.open(native_paths[tile][index]).convert("RGBA"), dtype=np.uint8).copy()
                    for index in selection["indices"]
                ]
                row, column = divmod(tile, columns)
                x0, x1 = tile_bounds(width, column, columns)
                y0, y1 = tile_bounds(height, row, rows)
                target_size = (
                    (
                        int(duration_profile["output"]["width"]),
                        int(duration_profile["output"]["height"]),
                    )
                    if duration_profile
                    else (x1 - x0, y1 - y0)
                )
                images, canvas_report = normalize_stable_canvas(
                    selected_arrays,
                    target_size,
                    margin_fraction=(
                        float(settings["canvas"]["margin_fraction"])
                        if settings else 0.05
                    ),
                )
                gif_output = duration_profile["output"]["gif"] if duration_profile else {}
                encoded = encode_sticker_images(
                    images, args.output, stem, output_fps, target_size, settings, gif_output
                )
                if encoded["file_budget"] and not encoded["file_budget"]["passed"]:
                    budget_failures.append(stem)
                outputs.extend(encoded["names"])
                successful_pngs[tile] = args.output / png_name
                cell_warnings = []
                if selection.get("repairs"):
                    cell_warnings.append("isolated-bad-frames-recovered")
                if selection.get("degraded"):
                    cell_warnings.append("short-or-fragmented-source-window")
                stability_qc = encoded["encoded_qc"].get(
                    "webp", encoded["encoded_qc"]["gif"]
                )
                if (
                    stability_qc["hold_centroid_step_pixels_mean"] > 1.5
                    or stability_qc["hold_centroid_step_pixels_max"] > 6.0
                ):
                    cell_warnings.append("residual-hold-jitter")
                warnings.extend(f"{stem}:{warning}" for warning in cell_warnings)
                cell_report = {
                        "id": stem,
                        "status": "succeeded",
                        "alpha_method": alpha_method,
                        "native_frames_analyzed": frame_count,
                        "invalid_native_frames": sum(not value for value in valid_frames[tile]),
                        "invalid_reasons": sorted(
                            {reason for frame in invalid_reasons[tile] for reason in frame}
                        ),
                        "loop_selection": selection,
                        "stable_canvas": canvas_report,
                        "matting": {
                            "stage": "full-frame-before-instance-extraction",
                            "supersample": args.supersample,
                            "continuous_alpha": True,
                            "premultiplied_resize": True,
                        },
                        "gif_alpha": encoded["gif_alpha"],
                        "gif_palette": encoded["gif_palette"],
                        "webp": encoded["webp"],
                        "file_budget": encoded["file_budget"],
                        "encoded_qc": encoded["encoded_qc"],
                        "warnings": cell_warnings,
                    }
                if delivery_variant is not None and delivery_variant_root is not None:
                    try:
                        short_selection = select_short_delivery_indices(
                            images,
                            int(output_fps),
                            float(delivery_variant["short_duration_seconds"]),
                        )
                        short_images = [images[index] for index in short_selection["indices"]]
                        short_encoded = encode_sticker_images(
                            short_images,
                            delivery_variant_root,
                            stem,
                            output_fps,
                            target_size,
                            settings,
                            gif_output,
                        )
                        if short_encoded["file_budget"] and not short_encoded["file_budget"]["passed"]:
                            budget_failures.append(f"{delivery_variant_name}/{stem}")
                        delivery_outputs.extend(short_encoded["names"])
                        delivery_successful_pngs[tile] = delivery_variant_root / png_name
                        short_report = {
                            "id": stem,
                            "status": "succeeded",
                            "loop_selection": short_selection,
                            "file_budget": short_encoded["file_budget"],
                            "encoded_qc": short_encoded["encoded_qc"],
                            "gif_alpha": short_encoded["gif_alpha"],
                            "gif_palette": short_encoded["gif_palette"],
                            "webp": short_encoded["webp"],
                        }
                    except (GridBoundaryError, OSError, ValueError, RuntimeError) as exc:
                        for name in (png_name, webp_name, gif_name):
                            (delivery_variant_root / name).unlink(missing_ok=True)
                        delivery_failures.append(stem)
                        warning = f"{delivery_variant_name}/{stem}:not-exported:{exc}"
                        warnings.append(warning)
                        short_report = {
                            "id": stem,
                            "status": "failed",
                            "error": str(exc),
                            "warnings": ["not-exported"],
                        }
                    delivery_cell_reports.append(short_report)
                    cell_report["delivery_variants"] = {delivery_variant_name: short_report}
                cell_reports.append(cell_report)
            except (GridBoundaryError, OSError, ValueError, RuntimeError) as exc:
                for name in (png_name, webp_name, gif_name):
                    (args.output / name).unlink(missing_ok=True)
                warning = f"{stem}:not-exported:{exc}"
                warnings.append(warning)
                cell_reports.append(
                    {
                        "id": stem,
                        "status": "failed",
                        "native_frames_analyzed": frame_count,
                        "invalid_native_frames": sum(not value for value in valid_frames[tile]),
                        "invalid_reasons": sorted(
                            {reason for frame in invalid_reasons[tile] for reason in frame}
                        ),
                        "error": str(exc),
                        "warnings": ["not-exported"],
                    }
                )

        normalized_layout = {
            "source_layout": str(args.layout.resolve()),
            "detected_layout": {
                "columns": columns,
                "rows": rows,
                "count": count,
                "confidence": layout["confidence"],
            },
        }
        (args.output / "layout.json").write_text(
            json.dumps(normalized_layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        preview_name = "preview.png"
        preview_cell_size = (
            (
                int(duration_profile["output"]["width"]),
                int(duration_profile["output"]["height"]),
            )
            if duration_profile else (width // columns, height // rows)
        )
        if successful_pngs:
            write_preview(
                args.output / preview_name,
                successful_pngs,
                columns,
                rows,
                preview_cell_size,
            )
            outputs.append(preview_name)

        delivery_report = None
        if delivery_variant_root is not None and delivery_variant_name is not None:
            shutil.copyfile(args.output / "layout.json", delivery_variant_root / "layout.json")
            if delivery_successful_pngs:
                write_preview(
                    delivery_variant_root / preview_name,
                    delivery_successful_pngs,
                    columns,
                    rows,
                    preview_cell_size,
                )
                delivery_outputs.append(preview_name)
            delivery_succeeded = sum(
                cell.get("status") == "succeeded" for cell in delivery_cell_reports
            )
            delivery_report = {
                "version": 2,
                "status": (
                    "budget-exceeded" if args.trial and any(
                        name.startswith(f"{delivery_variant_name}/") for name in budget_failures
                    )
                    else "budget-warning" if any(
                        name.startswith(f"{delivery_variant_name}/") for name in budget_failures
                    )
                    else "succeeded" if delivery_succeeded == len(target_tiles)
                    else "partial" if delivery_succeeded else "failed"
                ),
                "variant": delivery_variant_name,
                "source": str(args.video.resolve()),
                "output_fps": round(output_fps, 6),
                "target_duration_seconds": float(delivery_variant["short_duration_seconds"]),
                "frames_per_animation": int(round(
                    float(delivery_variant["short_duration_seconds"]) * output_fps
                )),
                "successful_cells": delivery_succeeded,
                "failed_cells": len(target_tiles) - delivery_succeeded,
                "trial_mode": args.trial,
                "selected_cells": [f"{tile + 1:0{digits}d}" for tile in target_tiles],
                "production_settings": settings["_meta"] if settings else None,
                "budget_failures": [
                    name for name in budget_failures
                    if name.startswith(f"{delivery_variant_name}/")
                ],
                "cells": delivery_cell_reports,
                "outputs": delivery_outputs
                + ([settings_output_name] if settings_output_name else [])
                + ["layout.json", "processing.json"],
            }
            (delivery_variant_root / "processing.json").write_text(
                json.dumps(delivery_report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            delivery_package_names = delivery_outputs + ["layout.json", "processing.json"]
            if settings_output_name:
                delivery_package_names.append(settings_output_name)
            nested_delivery_names = [
                f"{delivery_variant_name}/{name}"
                for name in delivery_package_names
            ]
            outputs.extend(nested_delivery_names)

        succeeded = sum(cell.get("status") == "succeeded" for cell in cell_reports)
        expected_cells = len(target_tiles)
        selected_counts = [
            int(cell["loop_selection"]["output_frames"])
            for cell in cell_reports if cell.get("status") == "succeeded"
        ]
        report = {
            "version": 2,
            "status": (
                "delivery-variant-failed" if delivery_failures
                else "budget-exceeded" if budget_failures and args.trial
                else "budget-warning" if budget_failures
                else "succeeded" if succeeded == expected_cells
                else "partial" if succeeded else "failed"
            ),
            "source": str(args.video.resolve()),
            "source_size": {"width": width, "height": height},
            "source_fps": round(source_fps, 6),
            "source_frames_analyzed": frame_count,
            "detected_layout": normalized_layout["detected_layout"],
            "output_fps": round(output_fps, 6),
            "frames_per_animation": max(selected_counts) if selected_counts else 0,
            "duration_mode": (
                "full-duration-sampled" if duration_profile
                else "preserve-full-duration" if args.preserve_full_duration else "selected-loop"
            ),
            "sampling": {
                "mode": (
                    "full-duration-regular-sampling" if duration_profile
                    else "native-all-frames" if args.preserve_full_duration else "fixed-step"
                ),
                "frame_step": (
                    round(source_fps / output_fps, 6) if duration_profile
                    else 1 if args.preserve_full_duration else round(source_fps / args.fps, 6)
                ),
            },
            "target_loop_seconds": (
                {
                    "mode": "source-full-duration",
                    "duration_seconds": round(frame_count / source_fps, 6),
                }
                if args.preserve_full_duration or duration_profile
                else {
                    "minimum": args.loop_min_seconds,
                    "maximum": args.loop_max_seconds,
                }
            ),
            "key_color": effective_key_color.upper() if effective_key_color else None,
            "pipeline": [
                "native-frame-decode",
                "full-frame-continuous-alpha",
                "optional-grid-registration",
                "component-instance-assignment",
                (
                    "full-duration-configured-sampling"
                    if duration_profile
                    else "full-duration-native-frame-preservation"
                    if args.preserve_full_duration
                    else "safe-loop-selection"
                ),
                "stable-canvas-normalization",
                "encoded-frame-qc",
            ],
            "background_qc": background_qc,
            "registration": {
                "mode": args.registration,
                "frames_adjusted": sum(bool(item.get("applied")) for item in registration_reports),
            },
            "instance_assignment": {
                "recovered_crossings": sum(item["recovered_crossings"] for item in assignment_reports),
                "ambiguous_components": sum(item["ambiguous_components"] for item in assignment_reports),
            },
            "successful_cells": succeeded,
            "failed_cells": expected_cells - succeeded,
            "trial_mode": args.trial,
            "selected_cells": [f"{tile + 1:0{digits}d}" for tile in target_tiles],
            "duration_profile": duration_profile,
            "production_settings": settings["_meta"] if settings else None,
            "budget_failures": budget_failures,
            "delivery_variants": (
                {delivery_variant_name: delivery_report} if delivery_report is not None else {}
            ),
            "cells": cell_reports,
            "warnings": warnings,
            "outputs": outputs + ([settings_output_name] if settings_output_name else [])
            + ["layout.json", "processing.json", args.zip_name],
        }
        (args.output / "processing.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        package_outputs(
            args.output,
            outputs + ([settings_output_name] if settings_output_name else [])
            + ["layout.json", "processing.json"],
            args.zip_name,
        )
        print(json.dumps(report, ensure_ascii=False))
        if not succeeded or delivery_failures:
            return 1
        if budget_failures and args.trial:
            return 2
    finally:
        if temporary is not None:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GridBoundaryError as exc:
        print(f"grid safety QC failed: {exc}", file=sys.stderr)
        sys.exit(1)
