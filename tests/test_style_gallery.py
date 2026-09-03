from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from compile_static_prompt import load_presets, resolve_style  # noqa: E402
from style_selector import verify_gallery  # noqa: E402


class StyleGalleryTests(unittest.TestCase):
    def test_gallery_contains_only_real_verified_styles(self) -> None:
        result = verify_gallery()
        self.assertGreaterEqual(result["verified_count"], 12)
        self.assertLessEqual(result["verified_count"], 16)
        self.assertEqual(result["verified_count"], len({item["id"] for item in result["styles"]}))
        for style in result["styles"]:
            self.assertEqual(style["source_route"], "grok-build-local")
            self.assertIn("motion-thumb.gif", style["files"])
            self.assertEqual(
                {record["path"].rsplit("/", 1)[-1] for record in style["release_media"]},
                {"static.png", "motion.gif", "motion.webp"},
            )
            self.assertIn("processing.json", style["files"])
            self.assertIn("provenance.json", style["files"])
            self.assertEqual(style["provenance"]["case_status"], "legacy-evidence-partial")
            self.assertIn("approval_hash", style["provenance"]["missing_historical_fields"])

    def test_v031_core_catalog_keeps_custom_outside_verified_selector(self) -> None:
        result = verify_gallery()
        catalog = result["core_catalog"]
        self.assertEqual(catalog["version"], "v0.3.1")
        self.assertEqual(catalog["target_count"], 16)
        self.assertEqual(catalog["verified_core_count"], 13)
        self.assertEqual(catalog["pending_core_count"], 3)
        self.assertEqual(catalog["selector_policy"], "verified-only")
        self.assertTrue(catalog["custom"]["enabled"])
        self.assertFalse(catalog["custom"]["selector_exposed"])

    def test_verified_core_display_names_resolve_to_their_legacy_ids(self) -> None:
        presets = load_presets(ROOT / "references" / "style-presets.json")
        source = json.loads((ROOT / "references" / "style-presets.json").read_text(encoding="utf-8"))
        for entry in source["core_catalog"]["styles"]:
            if entry["status"] != "route-verified":
                continue
            with self.subTest(display_id=entry["display_id"]):
                style_id, _, _ = resolve_style(presets, entry["display_id"], None)
                self.assertEqual(style_id, entry["id"])


if __name__ == "__main__":
    unittest.main()
