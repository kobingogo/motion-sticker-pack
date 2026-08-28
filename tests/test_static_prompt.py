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
from prepare_image_gen_call import prepare_call  # noqa: E402


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
        self.assertIn("直接根据角色定义“所附图像” 创建一套 3D 卡通风 贴纸包", prompt)
        self.assertIn("🎸😍🥹😘🥰", prompt)
        self.assertIn("九个", prompt)
        self.assertIn("3×3", prompt)
        self.assertIn("Use polished 3D cartoon rendering", prompt)
        self.assertIn("装饰性反应元素", prompt)
        self.assertIn("真实 alpha 通道的 RGBA PNG", prompt)
        self.assertIn("透明区域", prompt)
        self.assertIn("alpha 必须为 0", prompt)
        self.assertIn("严禁绘制棋盘格", prompt)
        self.assertIn("不要将图像扁平化成 RGB/JPEG", prompt)
        self.assertEqual(
            result["image_generation_request"]["arguments"],
            {"background": "transparent", "output_format": "png"},
        )
        self.assertEqual(
            result["image_generation_request"]["opaque_fallback"]["key_color"],
            "#00FF00",
        )
        self.assertTrue(result["requires_user_approval_before_video"])
        self.assertEqual(result["next_phase"], "static-review")

    def test_text_defined_character_goes_directly_to_the_sheet(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        result = compile_prompt(
            "用户描述的角色",
            style_id,
            label,
            style_prompt,
            ["开心", "点赞"],
            3,
            3,
            character_description="金发、深色西装、红领带的公众人物漫画形象",
        )
        self.assertEqual(result["source_mode"], "text-defined-character")
        self.assertIsNone(result["reference_image"])
        self.assertIn("不要先生成单张角色图", result["static_sheet_prompt"])
        self.assertIn("直接输出完整贴纸页", result["static_sheet_prompt"])

    def test_transparent_jpeg_is_rejected(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        with self.assertRaisesRegex(ValueError, "requires png or webp"):
            compile_prompt(
                "角色",
                style_id,
                label,
                style_prompt,
                ["开心"],
                1,
                1,
                background="transparent",
                output_format="jpeg",
            )

    def test_current_image_gen_schema_omits_future_arguments_and_records_them(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        contract = compile_prompt("角色", style_id, label, style_prompt, ["开心"], 1, 1)
        result = prepare_call(
            contract,
            {"prompt", "referenced_image_paths", "num_last_images_to_include"},
        )
        self.assertNotIn("background", result["call_arguments"])
        self.assertIn("真实 alpha 通道", result["call_arguments"]["prompt"])
        self.assertNotIn("备用调用（首次真实透明输出未通过本地检查）", result["call_arguments"]["prompt"])
        self.assertEqual(
            result["omitted_unsupported_arguments"],
            {"background": "transparent", "output_format": "png"},
        )
        self.assertEqual(
            result["generation_policy"]["on_omitted_transparency_arguments"],
            "continue-transparent-first-via-prompt",
        )
        self.assertFalse(
            result["generation_policy"]["schema_omission_implies_no_transparency"]
        )
        self.assertFalse(
            result["generation_policy"]["reference_image_changes_background_policy"]
        )
        self.assertEqual(
            result["generation_policy"]["fallback_trigger"],
            "local-alpha-normalization-failure-only",
        )
        self.assertEqual(
            result["generation_policy"]["on_missing_real_alpha_or_simulated_transparency"],
            "use-opaque-fallback-call",
        )
        self.assertEqual(
            result["opaque_fallback_call"]["call_arguments"]["prompt"].splitlines()[-1],
            "备用调用（首次真实透明输出未通过本地检查）：不要尝试透明输出，将所有空白区域渲染为完全一致的 #00FF00 纯绿色，不要棋盘格、纹理、渐变、阴影、地面或环境背景。",
        )

    def test_future_image_gen_schema_receives_background_and_output_format(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        contract = compile_prompt("角色", style_id, label, style_prompt, ["开心"], 1, 1)
        result = prepare_call(
            contract,
            {"prompt", "referenced_image_paths", "background", "output_format"},
        )
        self.assertEqual(result["call_arguments"]["background"], "transparent")
        self.assertEqual(result["call_arguments"]["output_format"], "png")
        self.assertEqual(result["omitted_unsupported_arguments"], {})
        self.assertEqual(result["opaque_fallback_call"]["call_arguments"]["background"], "opaque")
        self.assertEqual(result["opaque_fallback_call"]["call_arguments"]["output_format"], "png")

    def test_reference_image_does_not_change_transparent_first_policy(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        contract = compile_prompt(
            "所附图像",
            style_id,
            label,
            style_prompt,
            ["开心"],
            1,
            1,
            str(PRESETS),
        )
        result = prepare_call(contract, {"prompt", "referenced_image_paths"})
        self.assertEqual(
            result["call_arguments"]["referenced_image_paths"],
            [str(PRESETS.resolve())],
        )
        self.assertIn("真实 alpha 通道", result["call_arguments"]["prompt"])
        self.assertNotIn("#00FF00", result["call_arguments"]["prompt"])
        self.assertEqual(result["generation_policy"]["mode"], "transparent-first")

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
