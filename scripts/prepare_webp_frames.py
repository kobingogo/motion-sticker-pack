#!/usr/bin/env python3
"""Decode animated WebP stickers with Pillow and flatten alpha onto an X-safe background."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageOps


GROUPS = ("black-cat", "child", "gold-dress-girl", "musk-3d", "trump")
BACKGROUND = (18, 19, 22)  # #121316, matching the X-ready MP4 treatment
CANVAS_SIZE = (512, 512)
ART_SIZE = (420, 420)


def numeric_key(path: Path) -> tuple[int, str]:
    match = re.match(r"(\d+)", path.stem)
    return (int(match.group(1)) if match else 999, path.name)


def flatten_frame(frame: Image.Image) -> Image.Image:
    rgba = frame.convert("RGBA")
    fitted = ImageOps.contain(rgba, ART_SIZE, method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", CANVAS_SIZE, BACKGROUND)
    x = (CANVAS_SIZE[0] - fitted.width) // 2
    y = (CANVAS_SIZE[1] - fitted.height) // 2
    canvas.paste(fitted.convert("RGB"), (x, y), fitted.getchannel("A"))
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--frames-root", type=Path, required=True)
    args = parser.parse_args()

    total = 0
    for group in GROUPS:
        sources = sorted((args.source_root / group).glob("*.webp"), key=numeric_key)
        if not sources:
            raise SystemExit(f"No WebP files found for {group}")

        for index, source in enumerate(sources, start=1):
            output_dir = args.frames_root / group / f"{index:02d}"
            output_dir.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as animated:
                frame_count = getattr(animated, "n_frames", 1)
                if frame_count < 2:
                    print(f"{group}/{index:02d}: 1 frame (static source)")
                for frame_index in range(frame_count):
                    animated.seek(frame_index)
                    flatten_frame(animated).save(
                        output_dir / f"frame-{frame_index:03d}.png",
                        format="PNG",
                    )
                print(f"{group}/{index:02d}: {frame_count} frames from {source.name}")
                total += 1

    print(f"Prepared {total} sticker sources in {args.frames_root}")


if __name__ == "__main__":
    main()
