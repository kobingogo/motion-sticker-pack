from __future__ import annotations

import json
import hashlib
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PRESETS = Path(__file__).resolve().parents[1] / "references" / "style-presets.json"
sys.path.insert(0, str(SCRIPTS))

from compile_static_prompt import compile_prompt, load_presets, resolve_style  # noqa: E402


class StaticPromptTests(unittest.TestCase):
    def test_compiles_mobile_style_input_into_static_sheet_prompt(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        result = compile_prompt(
            "所附图像",
            style_id,
            label,
            style_prompt,
            ["🎸😍🥹😘🥰"],
            3,
            3,
        )
        prompt = result["static_sheet_prompt"]
        self.assertIn("基于 所附图像 创建一套 3D 卡通风 贴纸包", prompt)
        self.assertIn("🎸😍🥹😘🥰", prompt)
        self.assertIn("九个", prompt)
        self.assertIn("3×3", prompt)
        self.assertIn("Use polished 3D cartoon rendering", prompt)
        self.assertIn("装饰性反应元素", prompt)
        self.assertTrue(result["requires_user_approval_before_video"])
        self.assertEqual(result["next_phase"], "static-review")

    def test_accepts_short_text_reactions(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "手绘", None)
        result = compile_prompt(
            "上传的宠物照片",
            style_id,
            label,
            style_prompt,
            ["开心", "委屈", "亲亲"],
            4,
            3,
        )
        self.assertIn("开心、委屈、亲亲", result["static_sheet_prompt"])
        self.assertEqual(result["requested_layout"]["count"], 12)

    def test_binds_reference_image_hash_when_provided(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        reference = PRESETS
        result = compile_prompt(
            "所附图像", style_id, label, style_prompt, ["开心"], 1, 1, str(reference)
        )
        self.assertEqual(result["reference_image"]["path"], str(reference.resolve()))
        self.assertEqual(
            result["reference_image"]["sha256"], hashlib.sha256(reference.read_bytes()).hexdigest()
        )

    def test_style_presets_file_is_valid_json(self) -> None:
        data = json.loads(PRESETS.read_text(encoding="utf-8"))
        self.assertEqual(
            set(data["presets"]),
            {"realistic", "3d", "hand-drawn", "chibi", "manga", "pixel-art", "cute", "retro"},
        )


if __name__ == "__main__":
    unittest.main()
