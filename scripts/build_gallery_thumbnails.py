#!/usr/bin/env python3
"""Create deterministic, lightweight animated GIF previews for the public gallery."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageSequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "gallery" / "styles"
DEFAULT_OUTPUT = DEFAULT_SOURCE
DEFAULT_SIZE = 160


def build_thumbnail(source: Path, target: Path, size: int) -> None:
    rgba_frames: list[Image.Image] = []
    durations: list[int] = []
    with Image.open(source) as animation:
        for frame in ImageSequence.Iterator(animation):
            rgba = frame.convert("RGBA")
            rgba = rgba.resize((size, size), Image.Resampling.LANCZOS)
            rgba_frames.append(rgba)
            durations.append(int(frame.info.get("duration", animation.info.get("duration", 100))))
        loop = int(animation.info.get("loop", 0))
    if len(rgba_frames) < 2:
        raise ValueError(f"gallery preview is not animated: {source}")
    # Quantize every frame against one shared 255-color palette.  Per-frame
    # adaptive palettes cause visible color pumping and jagged edges in small
    # README previews; reserving palette index 0 keeps the source transparency.
    palette_source = Image.new("RGB", (size, size * len(rgba_frames)), (0, 0, 0))
    for index, frame in enumerate(rgba_frames):
        palette_source.paste(frame.convert("RGB"), (0, index * size), frame.getchannel("A"))
    palette = palette_source.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    palette_values = [0, 0, 0] + palette.getpalette()[: 255 * 3]
    frames: list[Image.Image] = []
    for frame in rgba_frames:
        quantized = frame.convert("RGB").quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)
        rgb_values = list(quantized.get_flattened_data()) if hasattr(quantized, "get_flattened_data") else list(quantized.getdata())
        alpha_values = list(frame.getchannel("A").get_flattened_data()) if hasattr(frame.getchannel("A"), "get_flattened_data") else list(frame.getchannel("A").getdata())
        pixels = [value + 1 if alpha else 0 for value, alpha in zip(rgb_values, alpha_values)]
        quantized.putdata(pixels)
        quantized.putpalette(palette_values + [0] * (768 - len(palette_values)))
        quantized.info["transparency"] = 0
        frames.append(quantized)
    target.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        target,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=loop,
        disposal=2,
        optimize=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE)
    args = parser.parse_args()
    if not 32 <= args.size <= 240:
        raise SystemExit("--size must be between 32 and 240 pixels")
    sources = sorted(args.source_root.glob("*/motion.gif"))
    if not sources:
        raise SystemExit(f"no gallery GIFs found under {args.source_root}")
    for source in sources:
        target = args.output_root / source.parent.name / "motion-thumb.gif"
        build_thumbnail(source, target, args.size)
    print(f"built {len(sources)} gallery GIF thumbnails at {args.size}x{args.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
