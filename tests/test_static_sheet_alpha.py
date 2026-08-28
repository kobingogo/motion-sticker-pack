from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from normalize_static_sheet import StaticSheetAlphaError, normalize_static_sheet  # noqa: E402


class StaticSheetAlphaTests(unittest.TestCase):
    def test_native_alpha_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            output = root / "static-sheet.png"
            image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
            ImageDraw.Draw(image).ellipse((24, 20, 72, 76), fill=(230, 70, 80, 255))
            image.save(source)
            report = normalize_static_sheet(source, output)
            self.assertTrue(report["source_had_real_alpha"])
            self.assertEqual(report["alpha_method"], "source-alpha")
            with Image.open(output) as normalized:
                self.assertEqual(normalized.mode, "RGBA")
                self.assertEqual(normalized.getpixel((0, 0))[3], 0)
                self.assertEqual(normalized.getpixel((48, 48))[3], 255)

    def test_baked_checkerboard_is_rejected_for_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            output = root / "static-sheet.png"
            array = np.zeros((128, 128, 3), dtype=np.uint8)
            size = 16
            for row in range(8):
                for column in range(8):
                    array[row * size:(row + 1) * size, column * size:(column + 1) * size] = (
                        (255, 255, 255) if (row + column) % 2 == 0 else (239, 239, 239)
                    )
            image = Image.fromarray(array, mode="RGB")
            ImageDraw.Draw(image).ellipse((32, 24, 96, 104), fill=(220, 45, 65))
            image.save(source)
            with self.assertRaisesRegex(StaticSheetAlphaError, "simulated-transparency-detected"):
                normalize_static_sheet(source, output)
            self.assertFalse(output.exists())

    def test_uniform_light_plate_is_rejected_for_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            output = root / "static-sheet.png"
            image = Image.new("RGB", (128, 128), (245, 245, 245))
            ImageDraw.Draw(image).ellipse((32, 24, 96, 104), fill=(220, 45, 65))
            image.save(source)
            with self.assertRaisesRegex(StaticSheetAlphaError, "unsafe-key-background-detected"):
                normalize_static_sheet(source, output)
            self.assertFalse(output.exists())

    def test_ambiguous_gradient_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            output = root / "static-sheet.png"
            gradient = np.zeros((96, 96, 3), dtype=np.uint8)
            gradient[:, :, 0] = np.arange(96, dtype=np.uint8)[None, :] * 2
            gradient[:, :, 1] = 120
            gradient[:, :, 2] = np.arange(96, dtype=np.uint8)[:, None] * 2
            Image.fromarray(gradient, mode="RGB").save(source)
            with self.assertRaises(StaticSheetAlphaError):
                normalize_static_sheet(source, output)
            self.assertFalse(output.exists())

    def test_uniform_chroma_removes_enclosed_background_hole(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            output = root / "static-sheet.png"
            image = Image.new("RGB", (128, 128), (0, 255, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse((20, 16, 108, 112), fill=(235, 198, 150))
            draw.ellipse((52, 48, 76, 76), fill=(0, 255, 0))
            image.save(source)

            report = normalize_static_sheet(source, output)

            self.assertEqual(report["background"]["kind"], "uniform")
            with Image.open(output) as normalized:
                rgba = normalized.convert("RGBA")
                self.assertEqual(rgba.getpixel((64, 64))[3], 0)
                self.assertEqual(rgba.getpixel((40, 64))[3], 255)

    def test_uniform_green_plate_removes_weak_green_edge_spill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            output = root / "static-sheet.png"
            image = Image.new("RGB", (128, 128), (0, 255, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse((20, 16, 108, 112), fill=(235, 198, 150))
            # Simulate a low-strength baked green fringe that is visually
            # noticeable but much closer to fur than to the original key.
            draw.arc((18, 14, 110, 114), 90, 270, fill=(176, 184, 158), width=2)
            image.save(source)

            normalize_static_sheet(source, output)

            with Image.open(output) as normalized:
                rgba = np.asarray(normalized.convert("RGBA"), dtype=np.uint8)
            visible = rgba[:, :, 3] > 0
            green_excess = rgba[:, :, 1].astype(np.int16) - np.maximum(
                rgba[:, :, 0], rgba[:, :, 2]
            ).astype(np.int16)
            self.assertFalse(np.any(visible & (green_excess > 3)))
            fringe = rgba[15:116, 17:112]
            partial = (fringe[:, :, 3] > 0) & (fringe[:, :, 3] < 255)
            balanced_green = (
                fringe[:, :, 0].astype(np.int16) + fringe[:, :, 2].astype(np.int16)
            ) // 2
            self.assertFalse(np.any(partial & (fringe[:, :, 1] > balanced_green + 1)))


if __name__ == "__main__":
    unittest.main()
