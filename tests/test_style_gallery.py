from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from style_selector import verify_gallery  # noqa: E402


class StyleGalleryTests(unittest.TestCase):
    def test_gallery_contains_only_real_verified_styles(self) -> None:
        result = verify_gallery()
        self.assertGreaterEqual(result["verified_count"], 12)
        self.assertLessEqual(result["verified_count"], 16)
        self.assertEqual(result["verified_count"], len({item["id"] for item in result["styles"]}))
        for style in result["styles"]:
            self.assertEqual(style["source_route"], "grok-build-local")
            self.assertIn("motion.gif", style["files"])
            self.assertIn("processing.json", style["files"])


if __name__ == "__main__":
    unittest.main()
