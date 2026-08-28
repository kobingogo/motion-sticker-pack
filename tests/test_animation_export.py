from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from animation_export import (  # noqa: E402
    choose_gif_alpha_threshold,
    encode_gif,
    encode_webp,
    frame_durations_ms,
)


class AnimationExportTests(unittest.TestCase):
    def test_frame_durations_preserve_source_clock(self) -> None:
        durations = frame_durations_ms(145, 24.0)
        self.assertEqual(len(durations), 145)
        self.assertEqual(sum(durations), 6042)
        self.assertEqual(set(durations), {41, 42})

    def test_adaptive_gif_threshold_is_bounded_and_reported(self) -> None:
        frames = []
        for alpha in (80, 128, 220):
            image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle((4, 4, 11, 11), fill=(220, 40, 60, alpha))
            frames.append(image)
        threshold, report = choose_gif_alpha_threshold(frames)
        self.assertIn(threshold, (96, 128, 160, 192))
        self.assertEqual(report["selected"]["threshold"], threshold)

    def test_gif_and_webp_are_looping_animations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = []
            for index in range(4):
                image = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                draw.ellipse((8 + index, 10, 28 + index, 30), fill=(210, 40, 50, 255))
                path = root / f"{index:04d}.png"
                image.save(path)
                frames.append(path)
            gif = root / "sticker.gif"
            webp = root / "sticker.webp"
            encode_gif(frames, gif, 6)
            encode_webp(frames, webp, 6)
            with Image.open(gif) as animation:
                self.assertEqual(animation.format, "GIF")
                self.assertGreaterEqual(getattr(animation, "n_frames", 1), 2)
            with Image.open(webp) as animation:
                self.assertGreaterEqual(getattr(animation, "n_frames", 1), 2)
            self.assertGreater(gif.stat().st_size, 0)

    def test_gif_keeps_opaque_black_interior(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = []
            for index in range(3):
                image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                inset = 12 + (index % 2)
                draw.ellipse((inset, inset, 64 - inset, 64 - inset), fill=(18, 14, 16, 255))
                path = root / f"{index:04d}.png"
                image.save(path)
                frames.append(path)
            gif = root / "black.gif"
            encode_gif(frames, gif, 6)
            with Image.open(gif) as animation:
                animation.seek(0)
                alpha = animation.convert("RGBA").getchannel("A")
                interior = alpha.crop((24, 24, 40, 40))
                self.assertGreaterEqual(min(interior.getextrema()), 250)

    def test_gif_rejects_low_alpha_edge_halo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle((8, 8, 24, 24), fill=(240, 60, 80, 64))
            ImageDraw.Draw(image).rectangle((10, 10, 22, 22), fill=(240, 60, 80, 255))
            frame = root / "0000.png"
            gif = root / "halo.gif"
            image.save(frame)
            encode_gif([frame], gif, 6, alpha_threshold=128)
            with Image.open(gif) as animation:
                rgba = animation.convert("RGBA")
                self.assertLess(int(rgba.getpixel((8, 16))[3]), 32)
                self.assertGreaterEqual(int(rgba.getpixel((16, 16))[3]), 250)

    def test_gif_color_budget_is_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = []
            for frame_index in range(2):
                image = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
                for value in range(32):
                    ImageDraw.Draw(image).line(
                        (8, 8 + value, 39, 8 + value),
                        fill=(value * 7, 220 - value * 4, 80 + value * 3, 255),
                    )
                path = root / f"{frame_index:04d}.png"
                image.save(path)
                frames.append(path)
            gif = root / "reduced.gif"
            encode_gif(frames, gif, 8, max_colors=32)
            with Image.open(gif) as animation:
                animation.seek(0)
                self.assertLessEqual(len(animation.convert("RGBA").getcolors(maxcolors=256) or []), 32)


if __name__ == "__main__":
    unittest.main()
