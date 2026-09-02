from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
sys.path.insert(0, str(ROOT / "scripts"))

from character_workspace import character_slug, character_workspace  # noqa: E402


class CharacterWorkspaceTests(unittest.TestCase):
    def test_slug_keeps_cjk_and_strips_path_parts(self) -> None:
        self.assertEqual(character_slug("小黑猫"), "小黑猫")
        self.assertEqual(character_slug("Elon Musk"), "Elon-Musk")
        self.assertEqual(character_slug("../etc/passwd"), "etc-passwd")
        self.assertEqual(character_slug("CON"), "_CON")
        self.assertEqual(character_slug("   "), "unnamed")

    def test_workspace_stays_under_works(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = character_workspace(root, "小黑猫")
            self.assertEqual(path, (root / "works" / "小黑猫").resolve())
            self.assertEqual(path.relative_to((root / "works").resolve()), Path("小黑猫"))

    def test_cli_and_prepare_workflow_create_character_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            created = json.loads(
                subprocess.check_output(
                    [PYTHON, str(ROOT / "scripts" / "character_workspace.py"), "--name", "小黑猫", "--skill-root", str(root)],
                    text=True,
                )
            )
            work = Path(created["work_dir"])
            self.assertEqual(created["slug"], "小黑猫")
            self.assertTrue((work / "character.json").is_file())
            (work / "character.json").write_text(
                json.dumps({
                    "name": "小黑猫",
                    "slug": "小黑猫",
                    "identity_version": "v2",
                    "anchor": "anchors/approved.png",
                }),
                encoding="utf-8",
            )
            image = work / "static-sheet.png"
            Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(image)
            layout = work / "layout.json"
            layout.write_text(json.dumps({"detected_layout": {"columns": 1, "rows": 1, "count": 1, "confidence": 0.99}}))
            prompts = work / "prompts.json"
            prompts.write_text(json.dumps({"detected_layout": {"columns": 1, "rows": 1, "count": 1}, "grid_video_prompt": "move"}))
            state = work / "job-state.json"
            state.write_text("{}")
            tile_plan = work / "tile-plan.json"
            tile_plan.write_text(json.dumps({"tiles": [{"id": "01", "motion": "move"}]}))
            subprocess.run(
                [
                    PYTHON,
                    str(ROOT / "scripts" / "prepare_workflow.py"),
                    "--skill-root",
                    str(ROOT),
                    "--character",
                    "小黑猫",
                    "--work-dir",
                    str(work),
                    "--image",
                    str(image),
                    "--layout",
                    str(layout),
                    "--prompts",
                    str(prompts),
                    "--state",
                    str(state),
                    "--tile-plan",
                    str(tile_plan),
                    "--overwrite",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            self.assertTrue((work / "video-task.json").is_file())
            self.assertTrue((work / "video-providers.json").is_file())
            character = json.loads((work / "character.json").read_text())
            self.assertEqual(character["name"], "小黑猫")
            self.assertEqual(character["identity_version"], "v2")
            self.assertEqual(character["anchor"], "anchors/approved.png")


if __name__ == "__main__":
    unittest.main()
