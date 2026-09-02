from __future__ import annotations

import sys
import json
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from prompt_compiler import compile_prompts, validate_key_color  # noqa: E402
from probe_video_capabilities import command_available  # noqa: E402
from route_video_provider import route  # noqa: E402


def config(fallback: str = "keyframe-local") -> dict:
    return {
        "version": 1,
        "routing": {"policy": "local-first", "max_attempts": 3, "fallback": fallback},
        "providers": [],
    }


def task(**values) -> dict:
    return {"version": 1, "operation": "image-to-video", **values}


def capabilities(providers: list[dict], local: dict) -> dict:
    return {"version": 1, "providers": providers, "local_processing": local}


def add_xai(provider_config: dict, provider_id: str, priority: int = 50) -> None:
    provider_config["providers"].append(
        {
            "id": provider_id,
            "driver": "ai-sdk",
            "provider": "xai",
            "package": "@ai-sdk/xai",
            "model": "grok-imagine-video",
            "enabled": True,
            "priority": priority,
            "credentials": {"env": ["XAI_API_KEY"]},
            "capabilities": ["image-to-video"],
        }
    )


class PromptAndRouterTests(unittest.TestCase):
    def test_shipped_video_task_requires_grok_and_disables_local_fallback(self) -> None:
        task = json.loads((Path(__file__).resolve().parents[1] / "assets" / "video-task.example.json").read_text())
        self.assertEqual(task["provider"], "grok-build-local")
        self.assertFalse(task["allow_fallback"])
        self.assertEqual(task["key_color"], "#00FF00")
        self.assertEqual(task["duration_seconds"], 6)
        self.assertEqual(
            task["provider_duration_seconds"],
            {"grok-build-local": 6, "xai-direct": 3},
        )
        self.assertIn("production_settings_file", task)
        self.assertEqual(task["max_retries"], 0)
        self.assertEqual(task["min_guard_fraction"], 0.10)
        self.assertEqual(task["max_foreground_bbox_fraction"], 0.75)

    def test_command_adapter_requires_absolute_entrypoint_to_exist(self) -> None:
        self.assertFalse(command_available(["node", "/definitely/missing/video-adapter.mjs"]))

    def test_prompt_uses_detected_layout_consistently(self) -> None:
        layout = {"columns": 4, "rows": 3, "count": 12, "confidence": 0.9}
        tiles = [
            {"id": f"{index:02d}", "motion": f"动作 {index}", "loop": "return-to-start"}
            for index in range(1, 13)
        ]
        result = compile_prompts(layout, tiles, "#00FF00")
        prompt = result["grid_video_prompt"]
        self.assertIn("4 列 × 3 行", prompt)
        self.assertIn("共 12 格", prompt)
        self.assertNotIn("共 9 格", prompt)
        self.assertIn("前 2 秒内完成", prompt)
        self.assertIn("脚底基线固定", prompt)
        self.assertEqual(len(result["tile_plan"]), 12)

    def test_video_prompt_preserves_approved_text_without_blocking_it(self) -> None:
        layout = {"columns": 1, "rows": 1, "count": 1, "confidence": 0.9}
        tiles = [{"id": "01", "motion": "轻轻点头", "loop": "return-to-start"}]
        result = compile_prompts(
            layout,
            tiles,
            "#00FF00",
            {
                "user_requested_text": False,
                "default": "avoid",
                "post_generation": "record-only",
                "generated_text_is_not_a_failure": True,
            },
        )
        self.assertIn("保留输入图像中已经存在的文字或符号", result["grid_video_prompt"])
        self.assertNotIn(", text, caption,", result["negative_prompt"])

    def test_key_color_must_contrast_with_the_subject(self) -> None:
        self.assertEqual(validate_key_color("#00FF00"), "#00FF00")
        self.assertEqual(validate_key_color("#FF00FF"), "#FF00FF")
        for unsafe in ("#000000", "#FFFFFF", "#111111", "#FAFAFA"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaisesRegex(ValueError, "near-black or near-white"):
                    validate_key_color(unsafe)
        layout = {"columns": 3, "rows": 3, "count": 9, "confidence": 0.9}
        tiles = [{"id": f"{index:02d}", "motion": "点头"} for index in range(1, 10)]
        fallback = compile_prompts(layout, tiles, None)["grid_video_prompt"]
        self.assertIn("绿或品红", fallback)
        self.assertIn("黑、白或灰底板", fallback)

    def test_native_route_precedes_higher_priority_external_route(self) -> None:
        provider_config = config()
        add_xai(provider_config, "external", 100)
        report = capabilities(
            [
                {
                    "id": "external",
                    "driver": "ai-sdk",
                    "priority": 100,
                    "capabilities": ["image-to-video"],
                    "available": True,
                },
                {
                    "id": "native",
                    "driver": "native-tool",
                    "priority": 1,
                    "capabilities": ["image-to-video"],
                    "available": True,
                },
            ],
            {"video_postprocess": True, "keyframe_local": True},
        )
        video_task = task(
            required_capabilities=["image-to-video"],
            provider="auto",
            allow_fallback=True,
        )
        result = route(provider_config, report, video_task)
        self.assertEqual(result["selected"]["id"], "native")
        self.assertEqual(result["fallback"]["id"], "light-motion-local")

    def test_runtime_native_route_keeps_tool_binding_for_execution(self) -> None:
        result = route(
            config("none"),
            capabilities(
                [{
                    "id": "host-native-video",
                    "driver": "native-tool",
                    "available": True,
                    "capabilities": ["image-to-video"],
                    "tool": "host-native-video",
                }],
                {},
            ),
            task(required_capabilities=["image-to-video"], allow_fallback=False),
        )
        self.assertEqual(result["selected"]["id"], "host-native-video")
        self.assertEqual(result["selected"]["tool"], "host-native-video")

    def test_task_provider_chain_is_an_ordered_allow_list(self) -> None:
        provider_config = config()
        add_xai(provider_config, "low-priority-first", 1)
        add_xai(provider_config, "high-priority-fallback", 100)
        report = capabilities(
            [
                {"id": "high-priority-fallback", "driver": "ai-sdk", "available": True, "capabilities": ["image-to-video"]},
                {"id": "low-priority-first", "driver": "ai-sdk", "available": True, "capabilities": ["image-to-video"]},
            ],
            {"video_postprocess": True},
        )
        result = route(
            provider_config,
            report,
            task(
                provider="low-priority-first",
                provider_chain=["low-priority-first", "high-priority-fallback"],
                provider_execution={
                    "low-priority-first": {"duration_seconds": 3, "resolution": "720p"},
                    "high-priority-fallback": {"duration_seconds": 6, "resolution": "480p"},
                },
                allow_fallback=True,
            ),
        )
        self.assertEqual([item["id"] for item in result["attempts"]], ["low-priority-first", "high-priority-fallback"])
        self.assertEqual(result["attempts"][1]["execution"]["duration_seconds"], 6)
        self.assertEqual(result["selection_reason"], "task-provider-chain")
        self.assertTrue(result["preflight"]["charge_authorization_required"])
        self.assertEqual(result["preflight"]["billable_external_attempts"], [1, 2])
        self.assertFalse(result["preflight"]["resume_support"]["1"])

    def test_missing_alpha_can_use_local_matting(self) -> None:
        provider_config = config()
        add_xai(provider_config, "video")
        provider_config["routing"]["max_attempts"] = 1
        report = capabilities(
            [
                {
                    "id": "video",
                    "driver": "ai-sdk",
                    "priority": 50,
                    "capabilities": ["image-to-video"],
                    "available": True,
                }
            ],
            {"video_postprocess": True, "keyframe_local": True},
        )
        result = route(
            provider_config,
            report,
            task(
                required_capabilities=["image-to-video"],
                require_alpha=True,
                allow_key_background=True,
                allow_fallback=True,
            ),
        )
        self.assertTrue(result["selected"]["postprocess_alpha"])

    def test_unavailable_provider_is_audited(self) -> None:
        provider_config = config()
        add_xai(provider_config, "missing-provider")
        provider_config["routing"]["max_attempts"] = 1
        report = capabilities(
            [
                {
                    "id": "missing-provider",
                    "driver": "ai-sdk",
                    "available": False,
                    "reasons": ["missing-credential-env"],
                }
            ],
            {"keyframe_local": True, "video_postprocess": True},
        )
        result = route(provider_config, report, task(allow_fallback=True))
        self.assertEqual(result["rejected"][0]["id"], "missing-provider")
        self.assertIn("missing-credential-env", result["rejected"][0]["details"])

    def test_keypose_fallback_precedes_transform_only_fallback(self) -> None:
        result = route(
            config("keypose-local"),
            capabilities(
                [],
                {
                    "keypose_local": True,
                    "transform_local": True,
                    "video_postprocess": True,
                },
            ),
            task(allow_fallback=True),
        )
        self.assertEqual(result["selected"]["id"], "keypose-local")

    def test_explicit_light_motion_fallback_is_not_upgraded_to_keypose(self) -> None:
        result = route(
            config("light-motion-local"),
            capabilities([], {"keypose_local": True, "transform_local": True}),
            task(allow_fallback=True),
        )
        self.assertEqual(result["selected"]["id"], "light-motion-local")

    def test_fallback_is_disabled_when_task_omits_allow_fallback(self) -> None:
        result = route(
            config("prompt-only"),
            capabilities([], {"transform_local": True}),
            task(),
        )
        self.assertIsNone(result["selected"])
        self.assertFalse(result["preflight"]["ready"])

    def test_prompt_only_is_selected_when_no_video_or_local_processing_exists(self) -> None:
        result = route(
            config("transform-local"),
            capabilities([], {"transform_local": False, "keyframe_local": False}),
            task(allow_fallback=True),
        )
        self.assertEqual(result["selected"]["id"], "prompt-only")


if __name__ == "__main__":
    unittest.main()
