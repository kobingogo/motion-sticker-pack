#!/usr/bin/env python3
"""Create local optical-flow in-betweens for a registered RGBA pose cycle."""

from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np
from PIL import Image


def _array(image: Image.Image | np.ndarray) -> np.ndarray:
    if isinstance(image, Image.Image):
        return np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    value = np.asarray(image, dtype=np.uint8)
    if value.ndim != 3 or value.shape[2] != 4:
        raise ValueError("optical-flow interpolation requires RGBA frames")
    return value.copy()


def _gray(frame: np.ndarray, ignore_mask: np.ndarray | None = None) -> np.ndarray:
    alpha = frame[:, :, 3:4].astype(np.float32) / 255.0
    rgb = frame[:, :, :3].astype(np.float32) * alpha
    if ignore_mask is not None:
        rgb[ignore_mask] = 0
    return cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)


def _flow(
    first: np.ndarray,
    second: np.ndarray,
    *,
    ignore_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    kwargs = dict(pyr_scale=0.5, levels=3, winsize=21, iterations=4, poly_n=7, poly_sigma=1.5, flags=0)
    return (
        cv2.calcOpticalFlowFarneback(_gray(first, ignore_mask), _gray(second, ignore_mask), None, **kwargs),
        cv2.calcOpticalFlowFarneback(_gray(second, ignore_mask), _gray(first, ignore_mask), None, **kwargs),
    )


def _warp(array: np.ndarray, flow: np.ndarray, factor: float) -> np.ndarray:
    height, width = array.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    map_x = xx - flow[:, :, 0] * factor
    map_y = yy - flow[:, :, 1] * factor
    return cv2.remap(array, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def _flow_validity(
    forward: np.ndarray,
    backward: np.ndarray,
    active_mask: np.ndarray,
    *,
    max_displacement: float,
) -> tuple[np.ndarray, float, float]:
    """Reject flow vectors that leave the canvas, stretch too far, or disagree."""
    height, width = active_mask.shape
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    destination_x = xx + forward[:, :, 0]
    destination_y = yy + forward[:, :, 1]
    backward_at_destination = cv2.remap(
        backward,
        destination_x,
        destination_y,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
    )
    magnitude = np.linalg.norm(forward, axis=2)
    consistency = np.linalg.norm(forward + backward_at_destination, axis=2)
    inside = (
        (destination_x >= 0)
        & (destination_x <= width - 1)
        & (destination_y >= 0)
        & (destination_y <= height - 1)
    )
    tolerance = np.maximum(2.0, 1.0 + 0.20 * magnitude)
    valid = active_mask & inside & (magnitude <= max_displacement) & (consistency <= tolerance)
    active_count = int(active_mask.sum())
    if not active_count:
        return valid, 0.0, 0.0
    return valid, float(np.mean(magnitude[active_mask])), float(1.0 - valid.sum() / active_count)


def stable_layer_mask(frames: Iterable[Image.Image | np.ndarray], tolerance: int = 18) -> np.ndarray:
    """Find pixels repeated across poses, used to lock approved text/decorations."""
    arrays = [_array(frame) for frame in frames]
    if not arrays:
        raise ValueError("at least one frame is required")
    stack = np.stack(arrays)
    present = np.all(stack[:, :, :, 3] >= 32, axis=0)
    rgb_range = stack[:, :, :, :3].max(axis=0).astype(np.int16) - stack[:, :, :, :3].min(axis=0).astype(np.int16)
    return present & np.all(rgb_range <= tolerance, axis=2)


def approved_text_mask(frame: Image.Image | np.ndarray) -> np.ndarray:
    """Lock the red vertical approved label so optical flow cannot melt its glyphs."""
    array = _array(frame)
    rgb = array[:, :, :3].astype(np.int16)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    red_ink = (red > 60) & (red > green * 1.08) & (red > blue * 1.08) & (green < 220)
    height, width = red_ink.shape
    side_regions = ((0, max(1, round(width * 0.38))), (min(width - 1, round(width * 0.62)), width))
    candidates: list[tuple[int, int, int, int, int, str]] = []
    for left, right in side_regions:
        # Keep the raw ink components: closing here can bridge the sign to
        # nearby scarf/cloud pixels and freeze part of the moving character.
        region = red_ink[:, left:right].astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(region, 8)
        for x, y, w, h, area in stats[1:]:
            if h >= w * 1.1 and h >= round(height * 0.24) and area >= 500:
                side = "left" if left == 0 else "right"
                candidates.append((int(x + left), int(y), int(w), int(h), int(area), side))
    if not candidates:
        return np.zeros((height, width), dtype=bool)
    left_candidates = [item for item in candidates if item[5] == "left"]
    right_candidates = [item for item in candidates if item[5] == "right"]
    left_best = max(left_candidates, key=lambda item: item[4], default=None)
    right_best = max(right_candidates, key=lambda item: item[4], default=None)
    selected = left_best or right_best
    # This pack places labels on the left except for the right-side 点赞
    # design. Prefer the left candidate when both sides also contain red
    # decorative marks; switch right only for a compact, clearly dominant
    # edge sign.
    if right_best is not None and (
        left_best is None
        or (right_best[4] >= left_best[4] * 1.5 and right_best[2] <= round(width * 0.30) and right_best[3] <= round(height * 0.55))
    ):
        selected = right_best
    x, y, w, h, _, _ = selected
    # A connected cloud can sit above the label in the 委屈 cell. Keep the
    # lower vertical sign while avoiding an unnecessary freeze of the fox's ear.
    if h > round(height * 0.55) and y < round(height * 0.20):
        y += round(h * 0.42)
        h -= round(h * 0.42)
    pad = 2
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(width, x + w + pad), min(height, y + h + pad)
    mask = np.zeros((height, width), dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


def disconnected_decoration_mask(frame: Image.Image | np.ndarray, threshold: int = 32) -> np.ndarray:
    """Lock detached decorative components while leaving the main subject free."""
    array = _array(frame)
    visible = (array[:, :, 3] >= threshold).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(visible, 8)
    if count <= 1:
        return np.zeros(visible.shape, dtype=bool)
    largest_label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return visible.astype(bool) & (labels != largest_label)


def _interpolate_rgba_guarded(
    first: Image.Image | np.ndarray,
    second: Image.Image | np.ndarray,
    t: float,
    *,
    stable_mask: np.ndarray | None = None,
    stable_source: Image.Image | np.ndarray | None = None,
) -> tuple[Image.Image, dict]:
    """Make one motion-compensated RGBA in-between without a plain crossfade."""
    if not 0.0 < t < 1.0:
        raise ValueError("interpolation t must be strictly between 0 and 1")
    a, b = _array(first), _array(second)
    if a.shape != b.shape:
        raise ValueError("interpolation frames must have identical dimensions")
    if stable_mask is not None and stable_mask.shape != a.shape[:2]:
        raise ValueError("stable mask dimensions do not match interpolation frame")
    ignore_mask = stable_mask if stable_mask is not None else None
    forward, backward = _flow(a, b, ignore_mask=ignore_mask)
    active_a = (a[:, :, 3] >= 32) & ~ignore_mask if ignore_mask is not None else a[:, :, 3] >= 32
    active_b = (b[:, :, 3] >= 32) & ~ignore_mask if ignore_mask is not None else b[:, :, 3] >= 32
    max_displacement = max(12.0, min(32.0, min(a.shape[:2]) * 0.12))
    valid_a, mean_flow, fallback_a = _flow_validity(
        forward, backward, active_a, max_displacement=max_displacement
    )
    valid_b, mean_flow_b, fallback_b = _flow_validity(
        backward, forward, active_b, max_displacement=max_displacement
    )
    motion_threshold = max(4.0, min(a.shape[:2]) * 0.015)
    large_motion = max(mean_flow, mean_flow_b) > motion_threshold
    wa = _warp(a[:, :, 3].astype(np.float32), forward, t)
    wb = _warp(b[:, :, 3].astype(np.float32), backward, 1.0 - t)
    a_rgb = a[:, :, :3].astype(np.float32) * (a[:, :, 3:4].astype(np.float32) / 255.0)
    b_rgb = b[:, :, :3].astype(np.float32) * (b[:, :, 3:4].astype(np.float32) / 255.0)
    warped_a = _warp(a_rgb, forward, t)
    warped_b = _warp(b_rgb, backward, 1.0 - t)
    left_confidence = np.clip(_warp(valid_a.astype(np.float32), forward, t), 0.0, 1.0)
    right_confidence = np.clip(_warp(valid_b.astype(np.float32), backward, 1.0 - t), 0.0, 1.0)
    left_weight = (1.0 - t) * left_confidence
    right_weight = t * right_confidence
    total = left_weight + right_weight
    alpha_a = np.clip(wa / 255.0, 0.0, 1.0)
    alpha_b = np.clip(wb / 255.0, 0.0, 1.0)
    flow_alpha = np.divide(alpha_a * left_weight + alpha_b * right_weight, np.maximum(total, 1e-5))
    # `warped_a`/`warped_b` are already premultiplied RGB. Multiplying by the
    # warped alpha a second time creates dark halos and black speckles around
    # transparent edges.
    flow_premult = warped_a * left_weight[:, :, None]
    flow_premult += warped_b * right_weight[:, :, None]
    flow_rgb = np.divide(flow_premult, np.maximum(flow_alpha[:, :, None], 1e-5))

    # A rejected vector falls back to a short, non-stretching dissolve. It may
    # be softer, but it cannot create the rubber-like tearing seen when an
    # unreliable flow is trusted.
    fallback_alpha = alpha_a * (1.0 - t) + alpha_b * t
    fallback_premult = a[:, :, :3].astype(np.float32) * alpha_a[:, :, None] * (1.0 - t)
    fallback_premult += b[:, :, :3].astype(np.float32) * alpha_b[:, :, None] * t
    fallback_rgb = np.divide(fallback_premult, np.maximum(fallback_alpha[:, :, None], 1e-5))
    nearest_source = b if t >= 0.5 else a
    nearest_alpha = nearest_source[:, :, 3].astype(np.float32) / 255.0
    nearest_rgb = nearest_source[:, :, :3].astype(np.float32)
    sample_mask = (wa >= 32) | (wb >= 32)
    if stable_mask is not None:
        sample_mask &= ~stable_mask
    guarded = total < 0.35
    initial_fallback_fraction = float(np.mean(guarded[sample_mask])) if np.any(sample_mask) else 0.0
    full_crossfade = initial_fallback_fraction > 0.25 or large_motion
    if full_crossfade:
        # A broad unreliable region is usually an occlusion or an identity
        # mismatch. Do not mix trusted and untrusted flow patches: dissolve
        # the complete dynamic region so no local patch can stretch the body.
        guarded |= sample_mask
    guarded_alpha = nearest_alpha if full_crossfade else fallback_alpha
    guarded_rgb = nearest_rgb if full_crossfade else fallback_rgb
    alpha = np.where(guarded, guarded_alpha, flow_alpha) * 255.0
    rgb = np.where(guarded[:, :, None], guarded_rgb, flow_rgb)
    output = np.dstack((np.clip(rgb, 0, 255), alpha)).astype(np.uint8)
    if stable_mask is not None:
        source = _array(stable_source if stable_source is not None else first)
        output[stable_mask] = source[stable_mask]
    fallback_fraction = float(np.mean(guarded[sample_mask])) if np.any(sample_mask) else 0.0
    return Image.fromarray(output, mode="RGBA"), {
        "mean_flow_px": round((mean_flow + mean_flow_b) / 2.0, 3),
        "forward_invalid_fraction": round(fallback_a, 6),
        "backward_invalid_fraction": round(fallback_b, 6),
        "fallback_fraction": round(fallback_fraction, 6),
        "fallback_mode": "full-dynamic-nearest-pose" if full_crossfade else "per-pixel-crossfade",
        "fallback_trigger": (
            "large-motion" if large_motion else "low-confidence-coverage" if initial_fallback_fraction > 0.25 else "none"
        ),
        "max_displacement_px": round(max_displacement, 3),
        "large_motion_threshold_px": round(motion_threshold, 3),
    }


def interpolate_rgba(
    first: Image.Image | np.ndarray,
    second: Image.Image | np.ndarray,
    t: float,
    *,
    stable_mask: np.ndarray | None = None,
    stable_source: Image.Image | np.ndarray | None = None,
) -> Image.Image:
    """Make one guarded motion-compensated RGBA in-between."""
    image, _ = _interpolate_rgba_guarded(
        first,
        second,
        t,
        stable_mask=stable_mask,
        stable_source=stable_source,
    )
    return image


def cycle_with_inbetweens(
    poses: list[Image.Image | np.ndarray],
    *,
    transition_frames: int = 3,
    lock_stable_layer: bool = True,
) -> tuple[list[Image.Image], dict]:
    """Expand [start, anticipation, peak, recovery, peak, anticipation] to a loop."""
    if len(poses) != 4:
        raise ValueError("keypose interpolation requires exactly four poses")
    if not 1 <= transition_frames <= 8:
        raise ValueError("transition_frames must be between 1 and 8")
    arrays = [_array(pose) for pose in poses]
    stable = stable_layer_mask(arrays) if lock_stable_layer else None
    text_mask = approved_text_mask(arrays[0]) if lock_stable_layer else None
    decoration_mask = disconnected_decoration_mask(arrays[0]) if lock_stable_layer else None
    if stable is not None and text_mask is not None and decoration_mask is not None:
        stable = stable | text_mask | decoration_mask
    if stable is not None:
        # Apply the lock to anchors as well as generated in-betweens. Otherwise
        # the approved sign can still pop once every four frames when the next
        # generated keypose contains slightly different rasterized glyphs.
        source = arrays[0]
        arrays = [array.copy() for array in arrays]
        for array in arrays:
            array[stable] = source[stable]
    anchor_indices = [0, 1, 2, 3, 2, 1]
    result: list[Image.Image] = []
    fallback_fractions: list[float] = []
    mean_flows: list[float] = []
    fallback_modes: list[str] = []
    for index, current_index in enumerate(anchor_indices):
        next_index = anchor_indices[(index + 1) % len(anchor_indices)]
        result.append(Image.fromarray(arrays[current_index], mode="RGBA"))
        for step in range(1, transition_frames + 1):
            linear = step / (transition_frames + 1)
            eased = linear * linear * (3.0 - 2.0 * linear)
            interpolated, transition_report = _interpolate_rgba_guarded(
                arrays[current_index],
                arrays[next_index],
                eased,
                stable_mask=stable,
                stable_source=arrays[0],
            )
            result.append(interpolated)
            fallback_fractions.append(transition_report["fallback_fraction"])
            mean_flows.append(transition_report["mean_flow_px"])
            fallback_modes.append(transition_report["fallback_mode"])
    return result, {
        "method": "opencv-farneback-optical-flow",
        "transition_frames": transition_frames,
        "easing": "smoothstep",
        "anchor_sequence": anchor_indices,
        "stable_layer_locked": lock_stable_layer,
        "stable_layer_fraction": round(float(stable.mean()), 6) if stable is not None else 0.0,
        "approved_text_locked": bool(lock_stable_layer),
        "detached_decorations_locked": bool(lock_stable_layer),
        "guardrails": {
            "mask_aware_flow": True,
            "bidirectional_consistency": True,
            "max_displacement_fraction": 0.12,
            "max_displacement_px": round(max(12.0, min(32.0, min(arrays[0].shape[:2]) * 0.12)), 3),
            "low_confidence_fallback": "per-pixel-crossfade; full dynamic-region nearest-pose above 25%",
            "mean_flow_px": round(float(np.mean(mean_flows)), 3) if mean_flows else 0.0,
            "mean_fallback_fraction": round(float(np.mean(fallback_fractions)), 6) if fallback_fractions else 0.0,
            "max_fallback_fraction": round(float(np.max(fallback_fractions)), 6) if fallback_fractions else 0.0,
            "transitions_with_full_nearest_pose": sum(
                mode == "full-dynamic-nearest-pose" for mode in fallback_modes
            ),
        },
        "output_frames": len(result),
    }
