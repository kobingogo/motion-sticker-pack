from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class MediaIntegrationTests(unittest.TestCase):
    def test_grid_video_to_transparent_animated_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = root / "frames"
            frames.mkdir()
            columns, rows, count = 4, 3, 12
            width, height = 400, 300
            for frame_index in range(6):
                image = Image.new("RGB", (width, height), (0, 255, 0))
                draw = ImageDraw.Draw(image)
                for tile in range(count):
                    row, column = divmod(tile, columns)
                    x = column * 100 + 28 + ((frame_index + tile) % 3 - 1)
                    y = row * 100 + 25
                    color = (220, 40 + tile * 8, 60 + tile * 9)
                    draw.rounded_rectangle((x, y, x + 44, y + 50), radius=10, fill=color)
                image.save(frames / f"{frame_index:03d}.png")
            video = root / "grid.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-framerate", "6", "-i", str(frames / "%03d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
                ],
                check=True,
            )
            layout = root / "layout.json"
            layout.write_text(
                json.dumps(
                    {
                        "detected_layout": {
                            "columns": columns,
                            "rows": rows,
                            "count": count,
                            "confidence": 0.98,
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = root / "output"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "process_emoji_grid.py"),
                    str(video),
                    str(output),
                    "--layout",
                    str(layout),
                    "--fps",
                    "6",
                    "--background-mode",
                    "edge-color",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            report = json.loads((output / "processing.json").read_text(encoding="utf-8"))
            self.assertEqual(report["detected_layout"]["count"], count)
            self.assertEqual(report["frames_per_animation"], 6)
            self.assertEqual(len(report["cells"]), count)
            for index in range(1, count + 1):
                stem = f"{index:02d}"
                self.assertTrue((output / f"{stem}.png").is_file())
                self.assertTrue((output / f"{stem}.webp").is_file())
                self.assertTrue((output / f"{stem}.gif").is_file())
                with Image.open(output / f"{stem}.png") as first:
                    self.assertEqual(first.mode, "RGBA")
                    self.assertLess(first.getextrema()[3][0], 32)
                with Image.open(output / f"{stem}.webp") as animation:
                    self.assertGreaterEqual(getattr(animation, "n_frames", 1), 2)
                with Image.open(output / f"{stem}.gif") as animation:
                    self.assertGreaterEqual(getattr(animation, "n_frames", 1), 2)
                    self.assertEqual(animation.format, "GIF")
            with zipfile.ZipFile(output / "sticker-pack.zip") as bundle:
                names = set(bundle.namelist())
                self.assertNotIn("frames", names)
                self.assertIn("12.webp", names)
                self.assertIn("12.gif", names)
                self.assertIn("processing.json", names)


if __name__ == "__main__":
    unittest.main()
