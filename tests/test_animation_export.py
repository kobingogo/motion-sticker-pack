from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from animation_export import encode_gif, encode_webp  # noqa: E402


class AnimationExportTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
