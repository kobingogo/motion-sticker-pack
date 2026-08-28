#!/usr/bin/env python3
"""Strict background checks for video generation and chroma-key matting."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from config_contract import ContractError


class BackgroundQCError(ContractError):
    """The generated media cannot satisfy the declared background contract."""


def parse_key_color(value: str) -> np.ndarray:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise BackgroundQCError("key color must use #RRGGBB notation")
    return np.array([int(value[index:index + 2], 16) for index in (1, 3, 5)], dtype=np.float32)


def _corner_samples(rgb: np.ndarray) -> np.ndarray:
    height, width, channels = rgb.shape
    if channels != 3 or height < 8 or width < 8:
        raise BackgroundQCError("background QC requires an RGB frame at least 8x8")
    band = max(4, min(24, min(height, width) // 20))
    return np.concatenate(
        [
            rgb[:band, :band].reshape(-1, 3),
            rgb[:band, -band:].reshape(-1, 3),
            rgb[-band:, :band].reshape(-1, 3),
            rgb[-band:, -band:].reshape(-1, 3),
        ],
        axis=0,
    ).astype(np.float32)


def frame_background_profile(rgb: np.ndarray, expected: np.ndarray | None = None) -> dict:
    """Profile corner uniformity and whole-frame/border key-color coverage."""
    samples = _corner_samples(rgb)
    median = np.median(samples, axis=0)
    spread = np.ptp(samples, axis=0)
    std = np.std(samples, axis=0)
    reference = median if expected is None else np.asarray(expected, dtype=np.float32).reshape(3)
    distances = np.sqrt(np.sum((samples - reference) ** 2, axis=1))
    profile = {
        "median": [round(float(value), 3) for value in median],
        "spread": [round(float(value), 3) for value in spread],
        "std": [round(float(value), 3) for value in std],
        "reference_distance_median": round(float(np.median(distances)), 3),
        "reference_distance_p95": round(float(np.percentile(distances, 95)), 3),
    }
    if expected is not None:
        all_pixels = rgb.reshape(-1, 3).astype(np.float32)
        all_distances = np.sqrt(np.sum((all_pixels - reference) ** 2, axis=1))
        height, width, _ = rgb.shape
        band = max(2, min(12, min(height, width) // 64))
        border = np.concatenate(
            [
                rgb[:band].reshape(-1, 3),
                rgb[-band:].reshape(-1, 3),
                rgb[:, :band].reshape(-1, 3),
                rgb[:, -band:].reshape(-1, 3),
            ],
            axis=0,
        ).astype(np.float32)
        border_distances = np.sqrt(np.sum((border - reference) ** 2, axis=1))
        profile.update(
            {
                "key_pixel_fraction_hard": round(float(np.mean(all_distances <= 24.0)), 6),
                "key_pixel_fraction_soft": round(float(np.mean(all_distances <= 58.0)), 6),
                "border_key_fraction_soft": round(float(np.mean(border_distances <= 58.0)), 6),
            }
        )
    return profile


def validate_frame_background(
    rgb: np.ndarray,
    expected: np.ndarray | None = None,
    *,
    label: str = "frame",
) -> dict:
    """Fail closed on a non-uniform plate or a plate different from the key.

    H.264 can move a solid key by a few RGB values. A checkerboard, gradient,
    gray/white plate, or shadow is materially different and must not reach the
    single-color matting algorithm.
    """
    profile = frame_background_profile(rgb, expected)
    if expected is None:
        spread = np.asarray(profile["spread"], dtype=np.float32)
        std = np.asarray(profile["std"], dtype=np.float32)
        if float(np.max(spread)) > 48.0 or float(np.max(std)) > 4.0:
            raise BackgroundQCError(
                f"{label} has a non-uniform background; refusing single-color matting "
                f"(spread={profile['spread']}, std={profile['std']})"
            )
    elif (
        profile["key_pixel_fraction_hard"] < 0.08
        or profile["key_pixel_fraction_soft"] < 0.12
        or profile["border_key_fraction_soft"] < 0.75
    ):
        raise BackgroundQCError(
            f"{label} background does not match the required key color "
            f"(hard_fraction={profile['key_pixel_fraction_hard']}, "
            f"soft_fraction={profile['key_pixel_fraction_soft']}, "
            f"border_fraction={profile['border_key_fraction_soft']})"
        )
    profile["valid"] = True
    return profile


def validate_grok_input(path: Path, key_color: str) -> dict:
    """Require real alpha or a verified uniform key before calling Grok."""
    try:
        with Image.open(path) as image:
            rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except OSError as exc:
        raise BackgroundQCError(f"cannot inspect Grok input image: {path}") from exc
    alpha = rgba[:, :, 3]
    if bool(np.mean(alpha < 250) >= 0.002):
        return {"source_alpha": True, "source": str(path.resolve())}
    expected = parse_key_color(key_color)
    try:
        profile = validate_frame_background(rgba[:, :, :3], expected, label="Grok input")
    except BackgroundQCError as exc:
        raise BackgroundQCError(
            f"Grok input must have real alpha or a uniform {key_color} background; {exc}"
        ) from exc
    return {"source_alpha": False, "source": str(path.resolve()), "background": profile}


def materialize_green_input(
    source: Path,
    destination: Path,
    key_color: str,
    *,
    layout: dict | None = None,
    safe_scale: float = 0.80,
    min_guard_fraction: float = 0.10,
    max_foreground_bbox_fraction: float = 0.80,
) -> dict:
    """Flatten source alpha onto green so Grok never has to infer transparency.

    When a detected grid is supplied, each tile is first scaled into a smaller
    transparent tile and reassembled.  The resulting green corridors are a
    deliberate guard band: a video model may animate inside a cell, but it has
    no character pixels immediately adjacent to an internal seam to borrow.
    """
    return _materialize_green_input(
        source,
        destination,
        key_color,
        layout=layout,
        safe_scale=safe_scale,
        min_guard_fraction=min_guard_fraction,
        max_foreground_bbox_fraction=max_foreground_bbox_fraction,
    )


def _grid_dimensions(layout: dict) -> tuple[int, int, int]:
    detected = layout.get("detected_layout", layout)
    columns = int(detected["columns"])
    rows = int(detected["rows"])
    count = int(detected.get("count", columns * rows))
    if columns < 1 or rows < 1 or count != columns * rows:
        raise BackgroundQCError("safe grid layout must have count = columns * rows")
    return columns, rows, count


def _safe_grid_source(
    rgba: Image.Image,
    layout: dict,
    safe_scale: float,
    min_guard_fraction: float,
    max_foreground_bbox_fraction: float,
) -> tuple[Image.Image, int, int, list[dict]]:
    if not 0.75 <= safe_scale <= 0.95:
        raise BackgroundQCError("safe grid scale must be between 0.75 and 0.95")
    if not 0.05 <= min_guard_fraction <= 0.20:
        raise BackgroundQCError("minimum guard fraction must be between 0.05 and 0.20")
    if not 0.60 <= max_foreground_bbox_fraction <= 0.90:
        raise BackgroundQCError("maximum foreground bbox fraction must be between 0.60 and 0.90")
    if max_foreground_bbox_fraction > 1.0 - 2.0 * min_guard_fraction + 1e-6:
        raise BackgroundQCError("foreground bbox fraction exceeds the requested two-sided guard")
    columns, rows, count = _grid_dimensions(layout)
    width, height = rgba.size
    if width < columns or height < rows:
        raise BackgroundQCError("safe grid layout is larger than the source image")
    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    guard_x = guard_y = 1 << 30
    tiles: list[dict] = []
    for index in range(count):
        row, column = divmod(index, columns)
        x0, x1 = width * column // columns, width * (column + 1) // columns
        y0, y1 = height * row // rows, height * (row + 1) // rows
        tile = rgba.crop((x0, y0, x1, y1))
        alpha = np.asarray(tile.getchannel("A"), dtype=np.uint8)
        ys, xs = np.where(alpha >= 8)
        if not len(xs):
            tiles.append({"id": index + 1, "empty": True})
            continue
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        content = tile.crop(bbox)
        tile_width, tile_height = x1 - x0, y1 - y0
        maximum_width = max(1, int(tile_width * max_foreground_bbox_fraction))
        maximum_height = max(1, int(tile_height * max_foreground_bbox_fraction))
        # Preserve native detail whenever the alpha bbox already fits. safe_scale
        # remains a conservative cap for unusually full cells and old task files.
        scale = min(
            1.0,
            maximum_width / max(1, content.width),
            maximum_height / max(1, content.height),
        )
        if content.width / tile_width > 0.95 or content.height / tile_height > 0.95:
            scale = min(scale, safe_scale)
        target_width = max(1, round(content.width * scale))
        target_height = max(1, round(content.height * scale))
        if (target_width, target_height) != content.size:
            content = content.resize((target_width, target_height), Image.Resampling.LANCZOS)
        paste_x = x0 + (tile_width - target_width) // 2
        paste_y = y0 + (tile_height - target_height) // 2
        result.alpha_composite(content, (paste_x, paste_y))
        cell_guard_x = min(paste_x - x0, x1 - (paste_x + target_width))
        cell_guard_y = min(paste_y - y0, y1 - (paste_y + target_height))
        guard_x = min(guard_x, cell_guard_x)
        guard_y = min(guard_y, cell_guard_y)
        tiles.append(
            {
                "id": index + 1,
                "source_alpha_bbox": list(bbox),
                "output_foreground_size": {"width": target_width, "height": target_height},
                "scale": round(scale, 6),
                "guard": {"x": cell_guard_x, "y": cell_guard_y},
            }
        )
    if guard_x == 1 << 30:
        guard_x = guard_y = 0
    return result, guard_x, guard_y, tiles


def _materialize_green_input(
    source: Path,
    destination: Path,
    key_color: str,
    *,
    layout: dict | None = None,
    safe_scale: float = 0.80,
    min_guard_fraction: float = 0.10,
    max_foreground_bbox_fraction: float = 0.80,
) -> dict:
    """Materialize an optional safe grid and flatten it onto the exact key."""
    expected = parse_key_color(key_color)
    try:
        with Image.open(source) as image:
            rgba = image.convert("RGBA")
            safe_grid = False
            guard_x = guard_y = 0
            tile_reports: list[dict] = []
            if layout is not None and bool(np.mean(np.asarray(rgba)[:, :, 3] < 250) >= 0.002):
                rgba, guard_x, guard_y, tile_reports = _safe_grid_source(
                    rgba,
                    layout,
                    safe_scale,
                    min_guard_fraction,
                    max_foreground_bbox_fraction,
                )
                safe_grid = True
            background = Image.new("RGBA", rgba.size, tuple(int(value) for value in expected) + (255,))
            flattened = Image.alpha_composite(background, rgba).convert("RGB")
            array = np.asarray(flattened, dtype=np.uint8)
            profile = validate_frame_background(array, expected, label="Grok green input")
            destination.parent.mkdir(parents=True, exist_ok=True)
            flattened.save(destination, format="PNG", optimize=True)
    except OSError as exc:
        raise BackgroundQCError(f"cannot create Grok green input: {destination}") from exc
    return {
        "source": str(source.resolve()),
        "path": str(destination.resolve()),
        "key_color": key_color.upper(),
        "background": profile,
        "safe_grid": safe_grid,
        "safe_grid_scale": safe_scale if safe_grid else None,
        "min_guard_fraction": min_guard_fraction if safe_grid else None,
        "max_foreground_bbox_fraction": max_foreground_bbox_fraction if safe_grid else None,
        "guard_band": {"x": guard_x, "y": guard_y} if safe_grid else None,
        "tiles": tile_reports if safe_grid else None,
    }


def probe_video_metadata(path: Path) -> dict:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise BackgroundQCError("ffmpeg and ffprobe are required for video background QC")
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration", "-of", "json", str(path),
    ]
    try:
        data = json.loads(subprocess.check_output(command, text=True))
        stream = data["streams"][0]
        rate = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")
        numerator, denominator = (int(part) for part in rate.split("/", 1))
        fps = numerator / denominator if denominator else 0.0
        metadata = {
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "fps": fps,
            "frame_count": int(stream["nb_frames"]) if str(stream.get("nb_frames", "")).isdigit() else None,
            "duration_seconds": float(stream["duration"]) if stream.get("duration") not in (None, "N/A") else None,
        }
    except (KeyError, IndexError, json.JSONDecodeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise BackgroundQCError(f"cannot probe generated video: {path}") from exc
    if metadata["width"] < 1 or metadata["height"] < 1 or metadata["fps"] <= 0:
        raise BackgroundQCError(f"generated video has invalid stream metadata: {path}")
    return metadata


def _probe_video(path: Path) -> tuple[int, int]:
    metadata = probe_video_metadata(path)
    return int(metadata["width"]), int(metadata["height"])


def _sample_frames(path: Path, width: int, height: int, fps: int, limit: int):
    command = [
        "ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={fps}",
        "-frames:v", str(limit), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_bytes = width * height * 3
    assert process.stdout is not None
    consumer_closed = False
    try:
        while True:
            raw = process.stdout.read(frame_bytes)
            if not raw:
                break
            if len(raw) != frame_bytes:
                raise BackgroundQCError("ffmpeg returned a truncated QC frame")
            yield np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3)).copy()
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
            raise BackgroundQCError(f"video background QC extraction failed: {stderr[-800:].strip()}")


def _native_frames(path: Path, width: int, height: int, limit: int):
    command = [
        "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", str(limit),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_bytes = width * height * 3
    assert process.stdout is not None
    consumer_closed = False
    try:
        while True:
            raw = process.stdout.read(frame_bytes)
            if not raw:
                break
            if len(raw) != frame_bytes:
                raise BackgroundQCError("ffmpeg returned a truncated native QC frame")
            yield np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3)).copy()
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
            raise BackgroundQCError(f"native video QC extraction failed: {stderr[-800:].strip()}")


def validate_video_background(
    path: Path,
    key_color: str,
    *,
    sample_fps: int | None = None,
    limit: int = 600,
) -> dict:
    """Validate the native frames unless an explicit legacy sample rate is requested."""
    if not path.is_file():
        raise BackgroundQCError(f"generated video does not exist: {path}")
    if sample_fps is not None and not 1 <= sample_fps <= 60 or not 1 <= limit <= 1200:
        raise ValueError("invalid video background QC sampling limits")
    expected = parse_key_color(key_color)
    width, height = _probe_video(path)
    reports = []
    frames = _native_frames(path, width, height, limit) if sample_fps is None else _sample_frames(
        path, width, height, sample_fps, limit
    )
    for index, frame in enumerate(frames, start=1):
        reports.append(validate_frame_background(frame, expected, label=f"video frame {index}"))
    if not reports:
        raise BackgroundQCError("generated video contains no frames for background QC")
    return {
        "valid": True,
        "key_color": key_color.upper(),
        "frames_checked": len(reports),
        "resolution": {"width": width, "height": height},
        "sampling": "native" if sample_fps is None else f"{sample_fps}fps",
        "minimum_key_pixel_fraction_soft": min(item["key_pixel_fraction_soft"] for item in reports),
        "minimum_border_key_fraction_soft": min(item["border_key_fraction_soft"] for item in reports),
    }


def validate_video_grid_safety(
    path: Path,
    key_color: str,
    layout: dict,
    *,
    sample_fps: int | None = None,
    limit: int = 600,
    seam_band: int = 3,
    max_foreground_fraction: float = 0.01,
    fail_on_crossing: bool = True,
) -> dict:
    """Report seam crossings; strict callers may still request fail-closed behavior."""
    if not path.is_file():
        raise BackgroundQCError(f"generated video does not exist: {path}")
    if seam_band < 1 or sample_fps is not None and not 1 <= sample_fps <= 60 or not 1 <= limit <= 1200:
        raise ValueError("invalid grid safety QC limits")
    if not 0 < max_foreground_fraction < 1:
        raise ValueError("max_foreground_fraction must be between 0 and 1")
    columns, rows, _ = _grid_dimensions(layout)
    expected = parse_key_color(key_color)
    width, height = _probe_video(path)
    boundaries_x = [width * column // columns for column in range(1, columns)]
    boundaries_y = [height * row // rows for row in range(1, rows)]
    maximums = {"vertical": 0.0, "horizontal": 0.0}
    worst: dict | None = None
    crossings: list[dict] = []
    frames_checked = 0
    frames = _native_frames(path, width, height, limit) if sample_fps is None else _sample_frames(
        path, width, height, sample_fps, limit
    )
    for frame_index, frame in enumerate(frames, start=1):
        frames_checked = frame_index
        distances = np.sqrt(np.sum((frame.astype(np.float32) - expected) ** 2, axis=2))
        foreground = distances > 58.0
        for orientation, boundaries, size in (
            ("vertical", boundaries_x, width),
            ("horizontal", boundaries_y, height),
        ):
            for boundary in boundaries:
                start = max(0, boundary - seam_band)
                end = min(size, boundary + seam_band)
                if orientation == "vertical":
                    fraction = float(np.mean(foreground[:, start:end]))
                else:
                    fraction = float(np.mean(foreground[start:end, :]))
                if fraction > maximums[orientation]:
                    maximums[orientation] = fraction
                    worst = {
                        "frame": frame_index,
                        "orientation": orientation,
                        "boundary": boundary,
                        "foreground_fraction": round(fraction, 6),
                    }
                if fraction > max_foreground_fraction:
                    crossing = {
                        "frame": frame_index,
                        "orientation": orientation,
                        "boundary": boundary,
                        "foreground_fraction": round(fraction, 6),
                    }
                    crossings.append(crossing)
                    if fail_on_crossing:
                        raise BackgroundQCError(
                            f"video frame {frame_index} foreground enters {orientation} grid seam at {boundary} "
                            f"(fraction={fraction:.4f}, limit={max_foreground_fraction:.4f})"
                        )
    if not frames_checked:
        raise BackgroundQCError("generated video contains no frames for grid safety QC")
    return {
        "valid": not crossings,
        "recoverable_by_postprocess": bool(crossings),
        "frames_checked": frames_checked,
        "sampling": "native" if sample_fps is None else f"{sample_fps}fps",
        "seam_band": seam_band,
        "max_foreground_fraction": max_foreground_fraction,
        "maximum_foreground_fraction": {key: round(value, 6) for key, value in maximums.items()},
        "worst": worst,
        "crossings": crossings,
    }
