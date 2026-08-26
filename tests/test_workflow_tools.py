from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


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
            self.assertTrue((work / "video-providers.json").is_file())
            self.assertTrue((work / "runtime-tools.json").is_file())

    def test_independent_stickers_produce_numbered_media_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "stickers"
            inputs.mkdir()
            for name, color in (("b.png", (0, 255, 0, 255)), ("a.png", (255, 0, 0, 255))):
                Image.new("RGBA", (32, 32), color).save(inputs / name)
            output = root / "output"
            subprocess.run([
                PYTHON, str(ROOT / "scripts" / "process_independent_stickers.py"),
                str(inputs), str(output), "--fps", "2", "--duration", "0.25",
            ], check=True, stdout=subprocess.DEVNULL)
            self.assertTrue((output / "01.webp").is_file())
            self.assertTrue((output / "02.gif").is_file())
            with zipfile.ZipFile(output / "sticker-pack.zip") as bundle:
                self.assertIn("01.png", bundle.namelist())
                self.assertIn("processing.json", bundle.namelist())

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
            for name in ("job-state.json", "prompts.json", "route.json"):
                (audit / name).write_text("{}")
            output = root / "delivered"
            subprocess.run([
                PYTHON, str(ROOT / "scripts" / "assemble_delivery.py"),
                "--media-dir", str(media), "--audit-dir", str(audit), "--output", str(output),
                "--require-job-state", "--require-prompts", "--require-route",
            ], check=True, stdout=subprocess.DEVNULL)
            with zipfile.ZipFile(output / "sticker-pack.zip") as bundle:
                self.assertTrue({"job-state.json", "prompts.json", "route.json"}.issubset(bundle.namelist()))

    def test_prompt_only_delivery_is_explicitly_non_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = []
            for name in ("static-prompt.json", "tile-plan.json", "prompts.json", "route.json"):
                path = root / name
                path.write_text("{}")
                inputs.append(path)
            output = root / "prompt-only"
            subprocess.run([
                PYTHON, str(ROOT / "scripts" / "assemble_prompt_only.py"),
                "--static-prompt", str(inputs[0]), "--tile-plan", str(inputs[1]),
                "--prompts", str(inputs[2]), "--route", str(inputs[3]), "--output", str(output),
            ], check=True, stdout=subprocess.DEVNULL)
            report = json.loads((output / "prompt-only.json").read_text())
            self.assertFalse(report["generated_video"])
            with zipfile.ZipFile(output / "prompt-only.zip") as bundle:
                self.assertIn("route.json", bundle.namelist())


if __name__ == "__main__":
    unittest.main()
