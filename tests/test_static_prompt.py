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
        self.assertIn("直接根据角色定义“所附图像” 创建一套 3D 动态表情包的静态九宫格源图", prompt)
        self.assertIn("🎸😍🥹😘🥰", prompt)
        self.assertIn("九个", prompt)
        self.assertIn("3×3", prompt)
        self.assertIn("Use one coherent 3D rendering treatment", prompt)
        self.assertIn("可自由选择一种统一的 3D 动画风或 3D 真实人物风格", prompt)
        self.assertEqual(result["style_policy"]["mode"], "free-choice")
        self.assertIn("装饰性反应元素", prompt)
        self.assertIn("无白边、无厚描边", prompt)
        self.assertIn("轻微、局部、与情绪匹配的背景点缀", prompt)
        self.assertIn("九格保持统一色调", prompt)
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
        self.assertIn("直接输出完整九宫格源图", result["static_sheet_prompt"])

    def test_explicit_3d_animation_alias_is_a_hard_constraint(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D 卡通", None)
        result = compile_prompt(
            "角色", style_id, label, style_prompt, ["开心"], 1, 1, style_input="3D 卡通"
        )
        prompt = result["static_sheet_prompt"]
        self.assertEqual(result["style"]["label"], "3D 动画风")
        self.assertEqual(result["style_policy"]["variant"], "animation")
        self.assertEqual(result["style_policy"]["mode"], "explicit")
        self.assertIn("必须严格遵守", prompt)
        self.assertIn("禁止照片质感", prompt)
        self.assertNotIn("可自由选择一种统一的 3D 动画风或 3D 真实人物风格", prompt)

    def test_white_sticker_outline_is_optional_for_3d_animation(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D 卡通", None)
        result = compile_prompt(
            "角色",
            style_id,
            label,
            style_prompt,
            ["开心"],
            1,
            1,
            style_input="3D 卡通",
            sticker_outline="white",
        )
        prompt = result["static_sheet_prompt"]
        self.assertEqual(result["outline_policy"]["resolved"], "white")
        self.assertEqual(result["style_policy"]["sticker_outline"], "white")
        self.assertIn("白边贴纸风", prompt)
        self.assertIn("窄、均匀、纯白且连续的外轮廓", prompt)
        self.assertIn("使用统一、窄且干净的白色贴纸外轮廓", prompt)
        self.assertNotIn("禁止照片质感、真人摄影式肖像和照片剪纸效果。", prompt)

    def test_white_outline_can_be_inferred_from_combined_style_input(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D 卡通白边贴纸", None)
        result = compile_prompt(
            "角色", style_id, label, style_prompt, ["开心"], 1, 1, style_input="3D 卡通白边贴纸"
        )
        self.assertEqual(result["outline_policy"]["resolved"], "white")
        self.assertEqual(result["style_policy"]["variant"], "animation")

    def test_default_outline_remains_none(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        result = compile_prompt("角色", style_id, label, style_prompt, ["开心"], 1, 1)
        self.assertEqual(result["outline_policy"]["resolved"], "none")
        self.assertIn("不主动添加白色贴纸外轮廓", result["static_sheet_prompt"])

    def test_explicit_3d_realistic_alias_is_a_hard_constraint(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D 写实", None)
        result = compile_prompt(
            "角色", style_id, label, style_prompt, ["开心"], 1, 1, style_input="3D 写实"
        )
        prompt = result["static_sheet_prompt"]
        self.assertEqual(result["style"]["label"], "3D 真实人物风")
        self.assertEqual(result["style_policy"]["variant"], "realistic")
        self.assertIn("自然的人体比例", prompt)
        self.assertIn("禁止 Q 版", prompt)
        self.assertNotIn("可自由选择一种统一的 3D 动画风或 3D 真实人物风格", prompt)

    def test_explicit_3d_style_in_character_description_is_respected(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        result = compile_prompt(
            "角色",
            style_id,
            label,
            style_prompt,
            ["开心"],
            1,
            1,
            character_description="3D 真实人物风格的宇航员",
            style_input="3D",
        )
        self.assertEqual(result["style_policy"]["variant"], "realistic")
        self.assertEqual(result["style_policy"]["source"], "character-description")

    def test_conflicting_3d_substyles_fail_closed(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            compile_prompt(
                "角色",
                style_id,
                label,
                style_prompt,
                ["开心"],
                1,
                1,
                character_description="3D 写实人物",
                style_input="3D 卡通",
            )

    def test_text_defaults_to_avoid_but_is_not_a_failure_gate(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        result = compile_prompt("角色", style_id, label, style_prompt, ["无语"], 1, 1)
        self.assertEqual(result["text_policy"]["default"], "avoid")
        self.assertTrue(result["text_policy"]["generated_text_is_not_a_failure"])
        self.assertIn("如果模型仍然生成了文字，不把它视为生成失败", result["static_sheet_prompt"])

    def test_text_can_be_explicitly_allowed(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        result = compile_prompt(
            "角色", style_id, label, style_prompt, ["收到"], 1, 1, include_text=True
        )
        self.assertTrue(result["text_policy"]["user_requested_text"])
        self.assertEqual(result["text_policy"]["default"], "allow")
        self.assertIn("允许在合适的格子加入简短、清晰的反应文字", result["static_sheet_prompt"])

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
        fallback_prompt = result["opaque_fallback_call"]["call_arguments"]["prompt"]
        self.assertIn("#00FF00", fallback_prompt)
        self.assertIn("纯色抠像硬约束", fallback_prompt)
        self.assertNotIn("真实 alpha 通道", fallback_prompt)
        self.assertNotIn("透明九宫格", fallback_prompt)
        self.assertNotIn("首次调用", fallback_prompt)

    def test_opaque_fallback_uses_a_standalone_prompt(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        contract = compile_prompt("角色", style_id, label, style_prompt, ["开心"], 3, 3)
        fallback = contract["image_generation_request"]["opaque_fallback"]
        self.assertEqual(fallback["prompt_mode"], "standalone-opaque")
        prompt = fallback["prompt"]
        self.assertLessEqual(len(prompt.encode("utf-8")), 3800)
        self.assertIn("#00FF00", prompt)
        self.assertIn("背景只能使用这一种颜色", prompt)
        self.assertNotIn("真实 alpha 通道", prompt)
        self.assertNotIn("透明区域", prompt)
        self.assertNotIn("首次调用", prompt)
        prepared = prepare_call(contract, {"prompt", "referenced_image_paths", "background", "output_format"})
        self.assertEqual(prepared["opaque_fallback_call"]["call_arguments"]["prompt"], prompt)

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
        self.assertNotIn("九宫格", result["static_sheet_prompt"])
        self.assertNotIn("九格保持", result["static_sheet_prompt"])

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

    def test_reference_hash_is_rechecked_before_call(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "reference.png"
            reference.write_bytes(b"revision one")
            contract = compile_prompt(
                "所附图像", style_id, label, style_prompt, ["开心"], 1, 1, str(reference)
            )
            reference.write_bytes(b"revision two")
            with self.assertRaisesRegex(ValueError, "sha256"):
                prepare_call(contract, {"prompt", "referenced_image_paths"})

    def test_reference_and_text_source_cannot_both_be_supplied(self) -> None:
        presets = load_presets(PRESETS)
        style_id, label, style_prompt = resolve_style(presets, "3D", None)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            compile_prompt(
                "角色", style_id, label, style_prompt, ["开心"], 1, 1,
                str(PRESETS), character_description="另一个角色",
            )

    def test_style_presets_file_is_valid_json(self) -> None:
        data = json.loads(PRESETS.read_text(encoding="utf-8"))
        self.assertEqual(
            set(data["presets"]),
            {
                "realistic", "3d", "hand-drawn", "chibi", "manga", "pixel-art", "cute", "retro",
                "caricature-3d", "fashion-realistic", "mascot-toy", "clay-cute",
                "fantasy-plush", "kawaii-anime",
            },
        )


if __name__ == "__main__":
    unittest.main()
