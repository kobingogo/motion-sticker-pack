from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from prompt_compiler import compile_prompts  # noqa: E402
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
        self.assertEqual(len(result["tile_plan"]), 12)

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
        self.assertEqual(result["fallback"]["id"], "transform-local")

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

    def test_prompt_only_is_selected_when_no_video_or_local_processing_exists(self) -> None:
        result = route(
            config("transform-local"),
            capabilities([], {"transform_local": False, "keyframe_local": False}),
            task(allow_fallback=True),
        )
        self.assertEqual(result["selected"]["id"], "prompt-only")


if __name__ == "__main__":
    unittest.main()
