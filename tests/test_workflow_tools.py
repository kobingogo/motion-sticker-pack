from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from screen_selector import choose_screen  # noqa: E402
from artifact_manifest import record_artifact, verify_manifest  # noqa: E402


class WorkflowToolTests(unittest.TestCase):
    def test_prepare_workflow_creates_one_consistent_work_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "sheet.png"
            Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(image)
            layout = root / "layout.json"
            layout.write_text(json.dumps({"detected_layout": {"columns": 1, "rows": 1, "count": 1, "confidence": 0.99}}))
            prompts = root / "prompts.json"
            prompts.write_text(json.dumps({"detected_layout": {"columns": 1, "rows": 1, "count": 1}, "grid_video_prompt": "move"}))
            state = root / "job-state.json"
            state.write_text("{}")
            tile_plan = root / "tile-plan.json"
            tile_plan.write_text(json.dumps({"tiles": [{"id": "01", "motion": "move"}]}))
            work = root / "work"
            subprocess.run([
                PYTHON, str(ROOT / "scripts" / "prepare_workflow.py"),
                "--work-dir", str(work), "--image", str(image), "--layout", str(layout),
                "--prompts", str(prompts), "--state", str(state), "--tile-plan", str(tile_plan),
            ], check=True, stdout=subprocess.DEVNULL)
            task = json.loads((work / "video-task.json").read_text())
            self.assertEqual(Path(task["input_image"]), image.resolve())
            self.assertTrue(Path(task["output_directory"]).is_absolute())
            self.assertEqual(task["aspect_ratio"], "1:1")
            self.assertEqual(task["duration_seconds"], 6)
            self.assertEqual(task["provider_chain"], ["grok-build-local"])
            self.assertEqual(task["provider_key_colors"], {"grok-build-local": "#00FF00"})
            self.assertTrue(Path(task["provider_input_images"]["grok-build-local"]).is_file())
            self.assertEqual(
                task["provider_execution"],
                {"grok-build-local": {"duration_seconds": 6, "resolution": "720p"}},
            )
            self.assertEqual(
                task["provider_duration_seconds"],
                {"grok-build-local": 6, "xai-direct": 3},
            )
            self.assertTrue(Path(task["production_settings_file"]).is_file())
            self.assertEqual(Path(task["attempt_ledger_file"]), (work / "attempt-ledger.json").resolve())
            self.assertEqual(Path(task["artifact_manifest_file"]), (work / "artifact-manifest.json").resolve())
            manifest = json.loads((work / "artifact-manifest.json").read_text())
            self.assertGreaterEqual(len(manifest["artifacts"]), 8)
            self.assertEqual(task["max_retries"], 0)
            self.assertEqual(task["min_guard_fraction"], 0.10)
            self.assertEqual(task["max_foreground_bbox_fraction"], 0.75)
            self.assertTrue((work / "video-providers.json").is_file())
            self.assertTrue((work / "runtime-tools.json").is_file())
            providers = json.loads((work / "video-providers.json").read_text())["providers"]
            python_commands = [
                item["command"][0]
                for item in providers
                if item.get("id") in {"grok-build-local", "xai-direct"}
            ]
            self.assertEqual(python_commands, [PYTHON, PYTHON])
            capabilities = work / "capabilities.json"
            capabilities.write_text(
                json.dumps({"version": 1, "providers": [], "local_processing": {}}),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    PYTHON,
                    str(ROOT / "scripts" / "route_video_provider.py"),
                    "--config",
                    str(work / "video-providers.json"),
                    "--capabilities",
                    str(capabilities),
                    "--task",
                    str(work / "video-task.json"),
                    "--output",
                    str(work / "route.json"),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            self.assertTrue((work / "attempt-ledger.json").is_file())
            self.assertIn("preflight", json.loads((work / "route.json").read_text()))
            routed_manifest = json.loads((work / "artifact-manifest.json").read_text())
            routed_kinds = {
                item["kind"] for item in routed_manifest["artifacts"] if item.get("current", True)
            }
            self.assertIn("provider-route", routed_kinds)
            self.assertNotIn("attempt-ledger", routed_kinds)

    def test_prepare_workflow_can_select_xai_and_order_a_grok_fallback_per_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "sheet.png"
            Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(image)
            inputs = {}
            for name, value in {
                "layout.json": {"detected_layout": {"columns": 1, "rows": 1, "count": 1, "confidence": 0.99}},
                "prompts.json": {"detected_layout": {"columns": 1, "rows": 1, "count": 1}, "grid_video_prompt": "move"},
                "job-state.json": {},
                "tile-plan.json": {"tiles": [{"id": "01", "motion": "move"}]},
            }.items():
                path = root / name
                path.write_text(json.dumps(value), encoding="utf-8")
                inputs[name] = path
            work = root / "work"
            subprocess.run(
                [
                    PYTHON, str(ROOT / "scripts" / "prepare_workflow.py"),
                    "--work-dir", str(work), "--image", str(image),
                    "--layout", str(inputs["layout.json"]), "--prompts", str(inputs["prompts.json"]),
                    "--state", str(inputs["job-state.json"]), "--tile-plan", str(inputs["tile-plan.json"]),
                    "--provider", "xai-direct", "--fallback-provider", "grok-build-local", "--allow-fallback",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            task = json.loads((work / "video-task.json").read_text())
            settings = json.loads((work / "sticker-production.json").read_text())
            self.assertEqual(task["provider"], "xai-direct")
            self.assertEqual(task["provider_chain"], ["xai-direct", "grok-build-local"])
            self.assertEqual(task["duration_seconds"], 3)
            self.assertEqual(task["provider_execution"]["xai-direct"], {"duration_seconds": 3, "resolution": "720p"})
            self.assertEqual(task["provider_execution"]["grok-build-local"], {"duration_seconds": 6, "resolution": "720p"})
            self.assertTrue(task["allow_fallback"])
            self.assertEqual(settings["generation"]["provider"], "xai-direct")

    def test_non_grok_screen_selector_avoids_green_foreground(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "green-subject.png"
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            for x in range(12, 52):
                for y in range(12, 52):
                    image.putpixel((x, y), (0, 255, 0, 255))
            image.save(source)
            report = choose_screen(source)
            self.assertNotEqual(report["selected"]["color"], "#00FF00")

    def test_independent_stickers_produce_numbered_media_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "stickers"
            inputs.mkdir()
            for name, color in (("b.png", (0, 255, 0, 255)), ("a.png", (255, 0, 0, 255))):
                image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
                ImageDraw.Draw(image).ellipse((8, 8, 24, 24), fill=color)
                image.save(inputs / name)
            output = root / "output"
            subprocess.run([
                PYTHON, str(ROOT / "scripts" / "process_independent_stickers.py"),
                str(inputs), str(output), "--fps", "2", "--duration", "0.25",
            ], check=True, stdout=subprocess.DEVNULL)
            self.assertTrue((output / "01.webp").is_file())
            self.assertTrue((output / "02.gif").is_file())
            self.assertTrue((output / "preview.png").is_file())
            with zipfile.ZipFile(output / "sticker-pack.zip") as bundle:
                self.assertIn("01.png", bundle.namelist())
                self.assertIn("preview.png", bundle.namelist())
                self.assertIn("processing.json", bundle.namelist())

    def test_independent_opaque_checkerboard_is_rejected_before_matting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "stickers"
            inputs.mkdir()
            image = Image.new("RGB", (32, 32), (255, 255, 255))
            draw = ImageDraw.Draw(image)
            for row in range(4):
                for column in range(4):
                    if (row + column) % 2:
                        draw.rectangle((column * 8, row * 8, column * 8 + 7, row * 8 + 7), fill=(239, 239, 239))
            draw.ellipse((9, 9, 23, 23), fill=(240, 60, 80))
            image.save(inputs / "01.png")
            result = subprocess.run(
                [
                    PYTHON, str(ROOT / "scripts" / "process_independent_stickers.py"),
                    str(inputs), str(root / "output"), "--fps", "2", "--duration", "0.25",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("uniform edge background", result.stderr + result.stdout)

    def test_local_fallback_rejects_unapproved_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "sheet.png"
            Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(image)
            layout = root / "layout.json"
            layout.write_text(json.dumps({"detected_layout": {"columns": 1, "rows": 1, "count": 1, "confidence": 0.99}}))
            prompt = root / "static-prompt.json"
            prompt.write_text(json.dumps({"static_sheet_prompt": "static"}))
            state = root / "job-state.json"
            subprocess.run([
                PYTHON, str(ROOT / "scripts" / "manage_job_state.py"), "create",
                "--image", str(image), "--layout", str(layout), "--static-prompt", str(prompt), "--output", str(state),
            ], check=True, stdout=subprocess.DEVNULL)
            result = subprocess.run([
                PYTHON, str(ROOT / "scripts" / "keyframe_fallback.py"), str(image), str(root / "output"),
                "--state", str(state), "--layout", str(layout),
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("has not been approved", result.stderr + result.stdout)

    def test_delivery_assembler_includes_audit_files_in_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "media"
            audit = root / "audit"
            media.mkdir()
            audit.mkdir()
            for suffix in ("png", "webp", "gif"):
                (media / f"01.{suffix}").write_bytes(b"media")
            (media / "layout.json").write_text(json.dumps({"detected_layout": {"columns": 1, "rows": 1, "count": 1, "confidence": 1.0}}))
            (media / "processing.json").write_text("{}")
            short = media / "3s"
            short.mkdir()
            (short / "01.gif").write_bytes(b"short-media")
            (short / "processing.json").write_text("{}")
            (short / "sticker-pack.zip").write_bytes(b"redundant nested archive")
            for name in (
                "job-state.json",
                "prompts.json",
                "route.json",
                "static-prompt.json",
                "static-generation.json",
                "static-alpha.json",
                "artifact-manifest.json",
                "attempt-ledger.json",
            ):
                (audit / name).write_text("{}")
            keypose_audit = audit / "keyposes"
            keypose_audit.mkdir()
            (keypose_audit / "keypose-plan.json").write_text("{\"mode\": \"keypose-local\"}")
            (keypose_audit / "keypose-preparation.json").write_text("{\"mode\": \"keypose-local-preparation\"}")
            output = root / "delivered"
            subprocess.run([
                PYTHON, str(ROOT / "scripts" / "assemble_delivery.py"),
                "--media-dir", str(media), "--audit-dir", str(audit), "--output", str(output),
                "--require-job-state", "--require-prompts", "--require-route",
            ], check=True, stdout=subprocess.DEVNULL)
            with zipfile.ZipFile(output / "sticker-pack.zip") as bundle:
                self.assertTrue(
                    {
                        "job-state.json",
                        "prompts.json",
                        "route.json",
                        "static-prompt.json",
                        "static-generation.json",
                        "static-alpha.json",
                        "artifact-manifest.json",
                        "attempt-ledger.json",
                        "keypose-plan.json",
                        "keypose-preparation.json",
                        "3s/01.gif",
                        "3s/processing.json",
                    }.issubset(bundle.namelist())
                )
                self.assertNotIn("3s/sticker-pack.zip", bundle.namelist())
            self.assertFalse((output / "3s" / "sticker-pack.zip").exists())

    def test_delivery_assembler_can_remove_intermediate_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "media"
            audit = root / "audit"
            media.mkdir()
            audit.mkdir()
            for suffix in ("png", "webp", "gif"):
                (media / f"01.{suffix}").write_bytes(b"media")
            (media / "layout.json").write_text("{}")
            (media / "processing.json").write_text("{}")
            manifest = audit / "artifact-manifest.json"
            record_artifact(manifest, media / "01.webp", kind="sticker-output", stage="processed")
            output = root / "delivered"
            subprocess.run([
                PYTHON, str(ROOT / "scripts" / "assemble_delivery.py"),
                "--media-dir", str(media), "--audit-dir", str(audit),
                "--output", str(output), "--cleanup-media-dir",
            ], check=True, stdout=subprocess.DEVNULL)
            self.assertFalse(media.exists())
            self.assertTrue((output / "sticker-pack.zip").is_file())
            self.assertTrue(verify_manifest(output / "artifact-manifest.json")["valid"])

    def test_prompt_only_delivery_is_explicitly_non_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = []
            for name in ("static-prompt.json", "tile-plan.json", "prompts.json"):
                path = root / name
                path.write_text("{}")
                inputs.append(path)
            route = root / "route.json"
            route.write_text(json.dumps({
                "selected": {"id": "prompt-only", "driver": "none"},
                "attempts": [],
                "preflight": {"selected_provider": "prompt-only"},
            }))
            output = root / "prompt-only"
            subprocess.run([
                PYTHON, str(ROOT / "scripts" / "assemble_prompt_only.py"),
                "--static-prompt", str(inputs[0]), "--tile-plan", str(inputs[1]),
                "--prompts", str(inputs[2]), "--route", str(route), "--output", str(output),
            ], check=True, stdout=subprocess.DEVNULL)
            report = json.loads((output / "prompt-only.json").read_text())
            self.assertFalse(report["generated_video"])
            with zipfile.ZipFile(output / "prompt-only.zip") as bundle:
                self.assertIn("route.json", bundle.namelist())


if __name__ == "__main__":
    unittest.main()
