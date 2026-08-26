from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from keyframe_fallback import transformed  # noqa: E402
from render_keypose_pack import loop_indices  # noqa: E402


class KeyframeFallbackTests(unittest.TestCase):
    def test_keypose_sequence_ping_pongs_without_duplicate_endpoints(self) -> None:
        self.assertEqual(loop_indices(4), [0, 1, 2, 3, 2, 1])

    def test_periodic_transform_returns_to_start(self) -> None:
        base = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        pixels = np.asarray(base).copy()
        pixels[20:60, 20:60] = (255, 0, 0, 255)
        base = Image.fromarray(pixels, mode="RGBA")
        for recipe in ("bounce", "sway", "pulse", "shake", "float"):
            first = np.asarray(transformed(base, recipe, 0.0))
            end = np.asarray(transformed(base, recipe, 2.0 * np.pi))
            self.assertTrue(np.array_equal(first, end), recipe)


if __name__ == "__main__":
    unittest.main()
