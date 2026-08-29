from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sticker_production_config import (  # noqa: E402
    default_settings_path,
    load_production_settings,
    match_duration_profile,
)
from video_adapter_common import duration_for_provider  # noqa: E402
from config_contract import ContractError  # noqa: E402


class StickerProductionConfigTests(unittest.TestCase):
    def test_default_settings_route_three_and_six_second_outputs(self) -> None:
        settings = load_production_settings(default_settings_path())
        three = match_duration_profile(settings, 3.0)
        six = match_duration_profile(settings, 6.0)
        self.assertEqual(
            settings["generation"]["provider_duration_seconds"],
            {"grok-build-local": 6, "xai-direct": 3},
        )
        self.assertNotIn("request_duration_seconds", settings["generation"])
        self.assertEqual((three["output"]["width"], three["output"]["height"]), (240, 240))
        self.assertEqual(three["output"]["fps"], 8)
        self.assertEqual((six["output"]["width"], six["output"]["height"]), (240, 240))
        self.assertEqual(six["output"]["fps"], 8)
        self.assertEqual(six["output"]["gif"]["max_colors"], 192)
        self.assertEqual(settings["budget"]["gif_max_bytes"], 1_048_576)
        self.assertEqual(settings["trial"]["cell_id"], "01")
        self.assertEqual(settings["budget"]["on_exceeded"], "stop-trial-and-warn-pack")
        self.assertEqual(
            settings["delivery_variants"]["grok-build-local"]["short_duration_seconds"], 3
        )

    def test_legacy_request_duration_becomes_selected_provider_map(self) -> None:
        settings = json.loads(default_settings_path().read_text(encoding="utf-8"))
        settings["generation"].pop("provider_duration_seconds")
        settings["generation"]["request_duration_seconds"] = 6
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps(settings), encoding="utf-8")
            loaded = load_production_settings(path)
        self.assertEqual(loaded["generation"]["provider_duration_seconds"], {"grok-build-local": 6})
        self.assertNotIn("request_duration_seconds", loaded["generation"])

    def test_mixed_duration_formats_are_rejected(self) -> None:
        settings = json.loads(default_settings_path().read_text(encoding="utf-8"))
        settings["generation"]["request_duration_seconds"] = 6
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps(settings), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "both duration formats"):
                load_production_settings(path)

    def test_map_must_include_selected_provider(self) -> None:
        settings = json.loads(default_settings_path().read_text(encoding="utf-8"))
        settings["generation"]["provider_duration_seconds"] = {"xai-direct": 3}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps(settings), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing selected provider"):
                load_production_settings(path)

    def test_unexpected_duration_stops(self) -> None:
        settings = load_production_settings(default_settings_path())
        with self.assertRaisesRegex(ValueError, "no unique duration profile"):
            match_duration_profile(settings, 5.0)

    def test_overlapping_profiles_are_rejected(self) -> None:
        settings = json.loads(default_settings_path().read_text(encoding="utf-8"))
        settings["duration_profiles"][1]["match_duration"]["minimum_seconds"] = 3.0
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps(settings), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overlap"):
                load_production_settings(path)

    def test_alpha_coverage_delta_is_bounded(self) -> None:
        settings = json.loads(default_settings_path().read_text(encoding="utf-8"))
        settings["gif"]["max_alpha_coverage_delta"] = 99
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps(settings), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max_alpha_coverage_delta"):
                load_production_settings(path)


class ProviderDurationLookupTests(unittest.TestCase):
    def test_adapters_read_own_provider_duration_from_the_task_map(self) -> None:
        task = {
            "duration_seconds": 6,
            "provider_duration_seconds": {"grok-build-local": 6, "xai-direct": 3},
        }
        self.assertEqual(duration_for_provider(task, "grok-build-local", default=6), 6)
        self.assertEqual(duration_for_provider(task, "xai-direct", default=6), 3)

    def test_missing_map_falls_back_to_duration_seconds(self) -> None:
        self.assertEqual(duration_for_provider({"duration_seconds": 6}, "xai-direct", default=3), 6)

    def test_invalid_map_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "must be an object"):
            duration_for_provider({"provider_duration_seconds": [3]}, "xai-direct", default=3)


if __name__ == "__main__":
    unittest.main()
