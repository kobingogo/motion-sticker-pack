from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


class KeyposePipelineTests(unittest.TestCase):
    def _source_cells(self, root: Path) -> Path:
        cells = root / "cells"
        cells.mkdir()
        for index, color in enumerate(((240, 80, 80, 255), (80, 120, 240, 255)), start=1):
            image = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
            ImageDraw.Draw(image).ellipse((10, 10, 38, 38), fill=color)
            image.save(cells / f"{index:02d}.png")
        return cells

    def _pose_sheets(self, root: Path, background: tuple[int, int, int, int] = (0, 0, 0, 0)) -> Path:
        sheets = root / "pose-sheets"
        sheets.mkdir()
        for index, color in enumerate(((240, 80, 80, 255), (80, 120, 240, 255)), start=1):
            sheet = Image.new("RGBA", (256, 256), background)
            draw = ImageDraw.Draw(sheet)
            # Each pose stays away from the center seams, leaving a measurable gutter.
            for pose, box in enumerate(((12, 12, 112, 112), (144, 12, 244, 112), (12, 144, 112, 244), (144, 144, 244, 244))):
                x0, y0, x1, y1 = box
                inset = min(18, pose * 3)
                draw.ellipse((x0 + inset, y0 + 12, x1 - inset, y1 - 12), fill=color)
            sheet.save(sheets / f"{index:02d}.png")
        return sheets

    def test_compile_and_prepare_keypose_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._source_cells(root)
            plan_output = root / "plan"
            manifest = root / "artifact-manifest.json"
            subprocess.run(
                [PYTHON, str(ROOT / "scripts/compile_keypose_plan.py"), "--input-dir", str(cells), "--output-dir", str(plan_output), "--reactions", "开心,惊讶", "--manifest", str(manifest), "--workspace", str(root)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            plan = json.loads((plan_output / "keypose-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["source_count"], 2)
            self.assertEqual([tile["id"] for tile in plan["tiles"]], ["01", "02"])
            self.assertTrue((plan_output / "prompts" / "01.txt").is_file())

            prepared = root / "prepared"
            subprocess.run(
                [PYTHON, str(ROOT / "scripts/prepare_keyposes.py"), "--source-cells", str(cells), "--pose-sheets", str(self._pose_sheets(root)), "--output-dir", str(prepared), "--size", "64", "--manifest", str(manifest), "--workspace", str(root)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            report = json.loads((prepared / "keypose-preparation.json").read_text(encoding="utf-8"))
            self.assertEqual(report["count"], 2)
            self.assertEqual(report["start_frame_policy"], "exact-approved-static-cell")
            self.assertTrue((prepared / "01" / "01-start.png").is_file())
            self.assertGreater(report["cells"][0]["motion_difference_from_start"]["peak"], 0.02)
            manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
            kinds = {item["kind"] for item in manifest_value["artifacts"] if item.get("current", True)}
            self.assertTrue({"keypose-plan", "keypose-preparation-report", "keypose-frame"}.issubset(kinds))
            subprocess.run([PYTHON, str(ROOT / "scripts/artifact_manifest.py"), "verify", "--manifest", str(manifest)], check=True, stdout=subprocess.DEVNULL)

    def test_prepare_keyposes_mattes_uniform_green_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._source_cells(root)
            sheets = self._pose_sheets(root, (0, 255, 0, 255))
            output = root / "prepared"
            subprocess.run(
                [PYTHON, str(ROOT / "scripts/prepare_keyposes.py"), "--source-cells", str(cells), "--pose-sheets", str(sheets), "--output-dir", str(output), "--size", "64"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            report = json.loads((output / "keypose-preparation.json").read_text(encoding="utf-8"))
            self.assertEqual(report["cells"][0]["pose_sheet_normalization"]["method"], "uniform-key-matte")
            with Image.open(output / "01" / "03-peak.png") as peak:
                self.assertLess(peak.getpixel((0, 0))[3], 32)

    def test_keypose_plan_rejects_output_inside_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._source_cells(root)
            result = subprocess.run(
                [PYTHON, str(ROOT / "scripts/compile_keypose_plan.py"), "--input-dir", str(cells), "--output-dir", str(cells / "plan"), "--reactions", "开心,惊讶"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("disjoint", result.stderr + result.stdout)

    def test_keypose_preparation_rejects_identical_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._source_cells(root)
            sheets = self._pose_sheets(root)
            # Replace the first generated sheet with four identical poses.
            identical = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            draw = ImageDraw.Draw(identical)
            for x0, y0 in ((0, 0), (128, 0), (0, 128), (128, 128)):
                draw.ellipse((x0 + 27, y0 + 27, x0 + 101, y0 + 101), fill=(240, 80, 80, 255))
            identical.save(sheets / "01.png")
            result = subprocess.run(
                [PYTHON, str(ROOT / "scripts/prepare_keyposes.py"), "--source-cells", str(cells), "--pose-sheets", str(sheets), "--output-dir", str(root / "prepared"), "--size", "64"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("meaningful action change", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
