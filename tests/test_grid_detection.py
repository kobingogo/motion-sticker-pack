from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from inspect_sticker_sheet import detect_layout  # noqa: E402
from process_emoji_grid import remove_edge_background, tile_bounds  # noqa: E402


class GridDetectionTests(unittest.TestCase):
    def test_detects_actual_three_by_three(self) -> None:
        image = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for row in range(3):
            for column in range(3):
                x0 = column * 200 + 45
                y0 = row * 200 + 40
                draw.ellipse((x0, y0, x0 + 110, y0 + 120), fill=(80, 170, 240, 255))
        report = detect_layout(image, [(3, 3), (4, 3), (3, 4), (4, 4)])
        self.assertEqual(report["detected_layout"]["columns"], 3)
        self.assertEqual(report["detected_layout"]["rows"], 3)
        self.assertEqual(report["detected_layout"]["count"], 9)

    def test_detects_actual_four_by_three_instead_of_requested_three_by_three(self) -> None:
        image = Image.new("RGBA", (800, 600), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for row in range(3):
            for column in range(4):
                x0 = column * 200 + 45
                y0 = row * 200 + 35
                draw.rounded_rectangle((x0, y0, x0 + 110, y0 + 130), radius=25, fill=(240, 80, 120, 255))
        report = detect_layout(image, [(3, 3), (4, 3), (3, 4), (4, 4)], requested=(3, 3))
        self.assertEqual(report["detected_layout"]["columns"], 4)
        self.assertEqual(report["detected_layout"]["rows"], 3)
        self.assertEqual(report["detected_layout"]["count"], 12)

    def test_odd_dimension_bounds_cover_every_pixel_once(self) -> None:
        bounds = [tile_bounds(101, index, 4) for index in range(4)]
        self.assertEqual(bounds[0][0], 0)
        self.assertEqual(bounds[-1][1], 101)
        self.assertEqual([left for left, _ in bounds[1:]], [right for _, right in bounds[:-1]])

    def test_edge_connected_matting_preserves_enclosed_similar_color(self) -> None:
        rgb = np.zeros((50, 50, 3), dtype=np.uint8)
        rgb[:, :] = (0, 255, 0)
        rgb[10:40, 10:40] = (220, 30, 50)
        rgb[20:30, 20:30] = (0, 255, 0)
        rgba = np.asarray(remove_edge_background(rgb, hard_tolerance=20, soft_tolerance=40))
        self.assertEqual(int(rgba[0, 0, 3]), 0)
        self.assertEqual(int(rgba[25, 25, 3]), 255)

    def test_black_subject_on_black_plate_keeps_interior_opaque(self) -> None:
        rgb = np.zeros((80, 80, 3), dtype=np.uint8)
        rgb[18:62, 18:62] = (32, 24, 28)
        rgb[28:52, 28:52] = (48, 36, 40)
        rgba = np.asarray(remove_edge_background(rgb))
        interior = rgba[30:50, 30:50, 3]
        self.assertEqual(int(rgba[0, 0, 3]), 0)
        self.assertGreaterEqual(int(interior.min()), 250)
        self.assertGreaterEqual(float(np.mean(interior == 255)), 0.99)

    def test_blank_sheet_never_reports_high_confidence(self) -> None:
        image = Image.new("RGB", (400, 400), (255, 255, 255))
        report = detect_layout(image, [(2, 2), (3, 3), (4, 3)])
        self.assertLess(report["detected_layout"]["confidence"], 0.75)
        self.assertIn("one-or-more-detected-cells-appear-empty", report["warnings"])


if __name__ == "__main__":
    unittest.main()
