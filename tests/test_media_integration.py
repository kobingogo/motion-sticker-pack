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
    def test_configured_three_second_trial_outputs_240px_at_eight_fps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = root / "frames"
            frames.mkdir()
            for frame_index in range(72):
                image = Image.new("RGB", (120, 120), (0, 255, 0))
                draw = ImageDraw.Draw(image)
                x = 35 + round(6 * abs(36 - frame_index) / 36)
                draw.rounded_rectangle((x, 32, x + 48, 88), radius=12, fill=(235, 55, 95))
                image.save(frames / f"{frame_index:03d}.png")
            video = root / "three-seconds.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-framerate", "24", "-i", str(frames / "%03d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
                ],
                check=True,
            )
            layout = root / "layout.json"
            layout.write_text(
                json.dumps({"detected_layout": {"columns": 1, "rows": 1, "count": 1, "confidence": 0.99}}),
                encoding="utf-8",
            )
            settings = json.loads(
                (ROOT / "assets" / "sticker-production.default.json").read_text(encoding="utf-8")
            )
            settings["generation"]["provider"] = "xai-direct"
            settings["trial"]["cell_id"] = "01"
            settings["budget"]["gif_max_bytes"] = 100_000_000
            settings_path = root / "settings.json"
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
            output = root / "trial-output"
            subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts" / "process_emoji_grid.py"),
                    str(video), str(output), "--layout", str(layout),
                    "--settings", str(settings_path), "--trial",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            report = json.loads((output / "processing.json").read_text(encoding="utf-8"))
            self.assertEqual(report["duration_profile"]["id"], "returned-3s")
            self.assertEqual(report["output_fps"], 8)
            self.assertEqual(report["frames_per_animation"], 24)
            self.assertEqual(report["selected_cells"], ["01"])
            self.assertTrue(report["cells"][0]["file_budget"]["passed"])
            self.assertEqual(report["delivery_variants"], {})
            with Image.open(output / "01.gif") as animation:
                self.assertEqual(animation.size, (240, 240))
                self.assertEqual(getattr(animation, "n_frames", 1), 24)

            settings["budget"]["gif_max_bytes"] = 1024
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
            warning_output = root / "full-pack-budget-warning"
            subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts" / "process_emoji_grid.py"),
                    str(video), str(warning_output), "--layout", str(layout),
                    "--settings", str(settings_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            warning_report = json.loads(
                (warning_output / "processing.json").read_text(encoding="utf-8")
            )
            self.assertEqual(warning_report["status"], "budget-warning")
            self.assertEqual(warning_report["budget_failures"], ["01"])

    def test_configured_six_second_output_adds_retimed_three_second_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = root / "frames"
            frames.mkdir()
            for frame_index in range(144):
                image = Image.new("RGB", (120, 120), (0, 255, 0))
                draw = ImageDraw.Draw(image)
                if frame_index <= 42:
                    size = 40 + round(50 * frame_index / 42)
                elif frame_index <= 83:
                    size = 90 - round(50 * (frame_index - 42) / 41)
                else:
                    size = 40
                inset = (120 - size) // 2
                fill = (235, 55, 95) if frame_index == 0 or frame_index >= 81 else (45, 75, 235)
                draw.rounded_rectangle(
                    (inset, inset, inset + size, inset + size),
                    radius=12,
                    fill=fill,
                )
                image.save(frames / f"{frame_index:03d}.png")
            video = root / "six-seconds.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-framerate", "24", "-i", str(frames / "%03d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
                ],
                check=True,
            )
            layout = root / "layout.json"
            layout.write_text(
                json.dumps({"detected_layout": {"columns": 1, "rows": 1, "count": 1, "confidence": 0.99}}),
                encoding="utf-8",
            )
            settings = json.loads(
                (ROOT / "assets" / "sticker-production.default.json").read_text(encoding="utf-8")
            )
            settings["trial"]["cell_id"] = "01"
            settings["budget"]["gif_max_bytes"] = 100_000_000
            settings_path = root / "settings.json"
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
            output = root / "trial-output"
            subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts" / "process_emoji_grid.py"),
                    str(video), str(output), "--layout", str(layout),
                    "--settings", str(settings_path), "--trial",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            report = json.loads((output / "processing.json").read_text(encoding="utf-8"))
            self.assertEqual(report["duration_profile"]["id"], "returned-6s")
            self.assertEqual(report["output_fps"], 8)
            self.assertEqual(report["frames_per_animation"], 48)
            with Image.open(output / "01.gif") as animation:
                self.assertEqual(animation.size, (240, 240))
                self.assertEqual(getattr(animation, "n_frames", 1), 48)
                self.assertLessEqual(len(set(animation.convert("RGBA").get_flattened_data())), 192)
            short = report["delivery_variants"]["3s"]
            self.assertEqual(short["status"], "succeeded")
            self.assertEqual(
                short["cells"][0]["loop_selection"]["mode"], "prefix-duration"
            )
            with Image.open(output / "3s" / "01.gif") as animation:
                self.assertEqual(animation.size, (240, 240))
                self.assertEqual(getattr(animation, "n_frames", 1), 24)
            with zipfile.ZipFile(output / "sticker-pack.zip") as bundle:
                self.assertIn("3s/01.gif", bundle.namelist())
                self.assertNotIn("3s/sticker-pack.zip", bundle.namelist())

    def test_native_frame_pipeline_recovers_crossing_and_avoids_fused_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = root / "frames"
            frames.mkdir()
            for frame_index in range(72):
                image = Image.new("RGB", (240, 120), (0, 255, 0))
                draw = ImageDraw.Draw(image)
                # Keep a small deterministic idle motion after the crossing so
                # lossless encoders still produce an animated selected window.
                left_x = 90 if 8 <= frame_index <= 12 else 30 + (frame_index % 4)
                draw.rectangle((left_x, 30, left_x + 55, 90), fill=(235, 45, 55))
                draw.rectangle((160 + ((frame_index + 1) % 4), 30, 210 + ((frame_index + 1) % 4), 90), fill=(45, 75, 235))
                if 20 <= frame_index <= 22:
                    draw.rectangle((80, 52, 160, 68), fill=(235, 45, 55))
                image.save(frames / f"{frame_index:03d}.png")
            # Use a lossless fixture so FFmpeg version differences cannot turn
            # the intentional three-frame merge into a different motion event.
            video = root / "crossing.mkv"
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-framerate", "24", "-i", str(frames / "%03d.png"),
                    "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv444p", str(video),
                ],
                check=True,
            )
            layout = root / "layout.json"
            layout.write_text(
                json.dumps(
                    {"detected_layout": {"columns": 2, "rows": 1, "count": 2, "confidence": 0.99}}
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
                    "--key-color",
                    "#00FF00",
                    "--supersample",
                    "1",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            report = json.loads((output / "processing.json").read_text(encoding="utf-8"))
            self.assertEqual(report["source_frames_analyzed"], 72)
            self.assertGreater(report["instance_assignment"]["recovered_crossings"], 0)
            self.assertGreater(report["instance_assignment"]["ambiguous_components"], 0)
            self.assertEqual(report["successful_cells"], 2)
            self.assertTrue(all(cell["encoded_qc"]["webp"]["frames_checked"] >= 2 for cell in report["cells"]))

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

    def test_checkerboard_video_is_rejected_before_matting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = root / "frames"
            frames.mkdir()
            for frame_index in range(2):
                image = Image.new("RGB", (100, 100), (255, 255, 255))
                draw = ImageDraw.Draw(image)
                for row in range(10):
                    for column in range(10):
                        if (row + column) % 2:
                            draw.rectangle(
                                (column * 10, row * 10, column * 10 + 9, row * 10 + 9),
                                fill=(239, 239, 239),
                            )
                draw.ellipse((35, 35, 65, 65), fill=(240, 60, 80))
                image.save(frames / f"{frame_index:03d}.png")
            video = root / "checkerboard.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-framerate", "2", "-i", str(frames / "%03d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
                ],
                check=True,
            )
            layout = root / "layout.json"
            layout.write_text(
                json.dumps({"detected_layout": {"columns": 1, "rows": 1, "count": 1, "confidence": 0.99}}),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts" / "process_emoji_grid.py"),
                    str(video), str(root / "output"), "--layout", str(layout),
                    "--fps", "2", "--key-color", "#00FF00",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("required key color", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
