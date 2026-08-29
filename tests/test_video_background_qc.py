from __future__ import annotations

import sys
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from PIL import Image, ImageDraw

from video_background_qc import (  # noqa: E402
    BackgroundQCError,
    materialize_green_input,
    probe_video_alpha,
    validate_frame_background,
    validate_video_grid_safety,
)


class VideoBackgroundQCTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_alpha_probe_distinguishes_real_alpha_from_opaque_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle((8, 8, 24, 24), fill=(255, 0, 0, 255))
            image.save(source)
            alpha_video = root / "alpha.mov"
            opaque_video = root / "opaque.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-loop", "1", "-i", str(source), "-t", "0.25", "-c:v", "qtrle", "-pix_fmt", "argb", str(alpha_video)],
                check=True,
            )
            subprocess.run(
                ["ffmpeg", "-v", "error", "-loop", "1", "-i", str(source), "-t", "0.25", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(opaque_video)],
                check=True,
            )
            self.assertTrue(probe_video_alpha(alpha_video)["has_meaningful_alpha"])
            self.assertFalse(probe_video_alpha(opaque_video)["has_meaningful_alpha"])
    def test_uniform_green_background_passes(self) -> None:
        frame = np.zeros((128, 128, 3), dtype=np.uint8)
        frame[:, :] = (0, 255, 0)
        frame[40:88, 40:88] = (240, 80, 90)
        report = validate_frame_background(frame, np.array([0, 255, 0], dtype=np.float32))
        self.assertTrue(report["valid"])

    def test_sparse_h264_outlier_does_not_reject_an_otherwise_flat_green_plate(self) -> None:
        frame = np.zeros((128, 128, 3), dtype=np.uint8)
        frame[:, :] = (0, 255, 0)
        frame[0, 0] = (30, 232, 24)
        report = validate_frame_background(frame, np.array([0, 255, 0], dtype=np.float32))
        self.assertTrue(report["valid"])

    def test_checkerboard_is_rejected_as_non_uniform(self) -> None:
        frame = np.zeros((128, 128, 3), dtype=np.uint8)
        size = 16
        for row in range(8):
            for column in range(8):
                frame[row * size:(row + 1) * size, column * size:(column + 1) * size] = (
                    (255, 255, 255) if (row + column) % 2 == 0 else (239, 239, 239)
                )
        with self.assertRaisesRegex(BackgroundQCError, "required key color"):
            validate_frame_background(frame, np.array([0, 255, 0], dtype=np.float32))

    def test_uniform_gray_background_is_rejected_as_wrong_key(self) -> None:
        frame = np.full((128, 128, 3), (245, 245, 245), dtype=np.uint8)
        with self.assertRaisesRegex(BackgroundQCError, "does not match"):
            validate_frame_background(frame, np.array([0, 255, 0], dtype=np.float32))

    def test_alpha_source_is_materialized_on_exact_green_before_grok(self) -> None:
        source = Path("/tmp/motion-sticker-alpha-source.png")
        destination = Path("/tmp/motion-sticker-green-input.png")
        try:
            image = Image.new("RGBA", (32, 32), (255, 255, 255, 0))
            image.putpixel((16, 16), (240, 60, 80, 255))
            image.save(source)
            report = materialize_green_input(source, destination, "#00FF00")
            with Image.open(destination) as flattened:
                self.assertEqual(flattened.mode, "RGB")
                self.assertEqual(flattened.getpixel((0, 0)), (0, 255, 0))
                self.assertEqual(flattened.getpixel((16, 16)), (240, 60, 80))
            self.assertEqual(report["key_color"], "#00FF00")
        finally:
            source.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_safe_grid_repack_leaves_green_guard_bands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            destination = root / "green.png"
            image = Image.new("RGBA", (120, 120), (255, 255, 255, 0))
            draw = ImageDraw.Draw(image)
            for row in range(2):
                for column in range(2):
                    draw.rectangle(
                        (column * 60 + 3, row * 60 + 3, column * 60 + 57, row * 60 + 57),
                        fill=(240, 60, 80, 255),
                    )
            image.save(source)
            report = materialize_green_input(
                source,
                destination,
                "#00FF00",
                layout={"detected_layout": {"columns": 2, "rows": 2, "count": 4}},
            )
            with Image.open(destination) as green:
                self.assertEqual(green.getpixel((59, 59)), (0, 255, 0))
                self.assertTrue(report["safe_grid"])
                self.assertGreater(report["guard_band"]["x"], 0)
                self.assertGreaterEqual(report["min_guard_fraction"], 0.10)
                self.assertTrue(all(tile.get("scale", 1.0) <= 1.0 for tile in report["tiles"]))
                self.assertTrue(
                    all(tile["output_foreground_size"]["width"] <= 48 for tile in report["tiles"])
                )

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_grid_safety_rejects_foreground_crossing_internal_seam(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame_dir = root / "frames"
            frame_dir.mkdir()
            for index in range(2):
                image = Image.new("RGB", (120, 120), (0, 255, 0))
                pixels = image.load()
                for y in range(35, 85):
                    for x in range(57, 64):
                        pixels[x, y] = (240, 60, 80)
                image.save(frame_dir / f"{index:03d}.png")
            video = root / "crossing.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-framerate", "2", "-i", str(frame_dir / "%03d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
                ],
                check=True,
            )
            with self.assertRaisesRegex(BackgroundQCError, "foreground enters vertical grid seam"):
                validate_video_grid_safety(
                    video,
                    "#00FF00",
                    {"detected_layout": {"columns": 2, "rows": 1, "count": 2}},
                    sample_fps=2,
                    limit=4,
                )

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_grid_safety_can_report_crossing_for_postprocess_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame_dir = root / "frames"
            frame_dir.mkdir()
            for index in range(3):
                image = Image.new("RGB", (120, 80), (0, 255, 0))
                ImageDraw.Draw(image).rectangle((54, 20, 70, 60), fill=(240, 60, 80))
                image.save(frame_dir / f"{index:03d}.png")
            video = root / "crossing.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-framerate", "3", "-i", str(frame_dir / "%03d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
                ],
                check=True,
            )
            report = validate_video_grid_safety(
                video,
                "#00FF00",
                {"detected_layout": {"columns": 2, "rows": 1, "count": 2}},
                fail_on_crossing=False,
            )
            self.assertFalse(report["valid"])
            self.assertTrue(report["recoverable_by_postprocess"])
            self.assertEqual(report["sampling"], "native")


if __name__ == "__main__":
    unittest.main()
