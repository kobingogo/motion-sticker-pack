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
sys.path.insert(0, str(ROOT / "scripts"))
from compile_keypose_plan import build_prompt, suggested_motion
from interpolate_keypose_frames import cycle_with_inbetweens
from render_keypose_pack import normalize_poses


class KeyposePipelineTests(unittest.TestCase):
    def test_optical_flow_cycle_expands_to_24_frames(self) -> None:
        images = []
        for offset in (0, 4, 8, 12):
            image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rectangle((8, 8, 26, 26), fill=(220, 60, 60, 255))
            draw.ellipse((35 + offset, 42, 55 + offset, 62), fill=(60, 120, 240, 255))
            images.append(image)
        normalized, _ = normalize_poses(images, (96, 96))
        frames, report = cycle_with_inbetweens(normalized, transition_frames=3)
        self.assertEqual(len(frames), 24)
        self.assertEqual(report["method"], "opencv-farneback-optical-flow")
        self.assertEqual(report["transition_frames"], 3)
        self.assertTrue(report["guardrails"]["mask_aware_flow"])
        self.assertTrue(report["guardrails"]["bidirectional_consistency"])
        self.assertGreater(report["guardrails"]["max_displacement_px"], 0)
        for image in images + normalized + frames:
            image.close()

    def test_renderer_keeps_pose_scale_fixed_to_start_anchor(self) -> None:
        images = []
        for size in (48, 42, 52, 45):
            image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
            ImageDraw.Draw(image).ellipse((48 - size // 2, 48 - size // 2, 48 + size // 2, 48 + size // 2), fill=(240, 80, 80, 255))
            images.append(image)
        normalized, transform = normalize_poses(images, (96, 96))
        widths = []
        for image in normalized:
            alpha = image.getchannel("A")
            bbox = alpha.getbbox()
            widths.append(bbox[2] - bbox[0])
            image.close()
        for image in images:
            image.close()
        self.assertLess(max(widths) - min(widths), 3)
        self.assertEqual(transform["method"], "start-anchor-scale-and-centroid")

    def test_reaction_suggestions_are_specific_and_pose_prompt_forbids_labels(self) -> None:
        self.assertIn("泪", suggested_motion("委屈"))
        self.assertIn("红心", suggested_motion("喜欢你"))
        self.assertIn("飞吻", suggested_motion("亲亲"))
        prompt = build_prompt(1, "委屈", suggested_motion("委屈"))
        self.assertIn("禁止渲染", prompt)
        self.assertIn("没有任何标签", prompt)

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
            approved = root / "approved.png"
            sheet = Image.new("RGBA", (96, 48), (0, 0, 0, 0))
            for index, source in enumerate(sorted(cells.glob("*.png"))):
                with Image.open(source) as image:
                    sheet.alpha_composite(image, (index * 48, 0))
            sheet.save(approved)
            layout = root / "layout.json"
            layout.write_text(json.dumps({"detected_layout": {"columns": 2, "rows": 1, "count": 2, "confidence": 0.95}}), encoding="utf-8")
            state = root / "job-state.json"
            subprocess.run([PYTHON, str(ROOT / "scripts/manage_job_state.py"), "create", "--image", str(approved), "--layout", str(layout), "--source-type", "user-supplied", "--output", str(state)], check=True, stdout=subprocess.DEVNULL)
            approved_cells = root / "approved-cells"
            manifest = root / "artifact-manifest.json"
            subprocess.run(
                [PYTHON, str(ROOT / "scripts/split_approved_static.py"), "--image", str(approved), "--layout", str(layout), "--state", str(state), "--output-dir", str(approved_cells), "--manifest", str(manifest)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            source_report = approved_cells / "static-cells.json"
            plan_output = root / "plan"
            subprocess.run(
                [PYTHON, str(ROOT / "scripts/compile_keypose_plan.py"), "--input-dir", str(approved_cells), "--source-report", str(source_report), "--output-dir", str(plan_output), "--reactions", "开心,惊讶", "--image", str(approved), "--layout", str(layout), "--state", str(state), "--manifest", str(manifest), "--workspace", str(root)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            plan = json.loads((plan_output / "keypose-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["source_count"], 2)
            self.assertEqual([tile["id"] for tile in plan["tiles"]], ["01", "02"])
            self.assertTrue((plan_output / "prompts" / "01.txt").is_file())
            prompt_text = (plan_output / "prompts" / "01.txt").read_text(encoding="utf-8")
            self.assertIn("禁止渲染", prompt_text)
            self.assertIn("START", prompt_text)
            self.assertIn("没有任何标签", prompt_text)

            prepared = root / "prepared"
            subprocess.run(
                [PYTHON, str(ROOT / "scripts/prepare_keyposes.py"), "--source-cells", str(approved_cells), "--pose-sheets", str(self._pose_sheets(root)), "--output-dir", str(prepared), "--size", "64", "--plan", str(plan_output / "keypose-plan.json"), "--image", str(approved), "--layout", str(layout), "--state", str(state), "--manifest", str(manifest), "--workspace", str(root)],
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
            rendered = root / "rendered"
            subprocess.run(
                [PYTHON, str(ROOT / "scripts/render_keypose_pack.py"), str(prepared), str(rendered), "--layout", str(layout), "--image", str(approved), "--state", str(state), "--plan", str(plan_output / "keypose-plan.json"), "--preparation-report", str(prepared / "keypose-preparation.json"), "--manifest", str(manifest)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            processing = json.loads((rendered / "processing.json").read_text(encoding="utf-8"))
            self.assertEqual(processing["cells"][0]["keyposes"], 4)
            self.assertEqual(processing["cells"][0]["output_frames"], 24)
            self.assertEqual(processing["cells"][0]["duration_seconds"], 3.0)
            self.assertIn("encoded_qc", processing["cells"][0])
            self.assertTrue((rendered / "preview.png").is_file())
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

    def test_keypose_renderer_rejects_non_contract_pose_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._source_cells(root)
            # A 2x1 approved sheet is enough to exercise the renderer contract.
            approved = root / "approved.png"
            sheet = Image.new("RGBA", (96, 48), (0, 0, 0, 0))
            for index, source in enumerate(sorted(cells.glob("*.png"))):
                with Image.open(source) as image:
                    sheet.alpha_composite(image, (index * 48, 0))
            sheet.save(approved)
            layout = root / "layout.json"
            layout.write_text(json.dumps({"detected_layout": {"columns": 2, "rows": 1, "count": 2, "confidence": 0.95}}), encoding="utf-8")
            state = root / "job-state.json"
            subprocess.run([PYTHON, str(ROOT / "scripts/manage_job_state.py"), "create", "--image", str(approved), "--layout", str(layout), "--source-type", "user-supplied", "--output", str(state)], check=True, stdout=subprocess.DEVNULL)
            keyposes = root / "keyposes"
            for index in (1, 2):
                target = keyposes / f"{index:02d}"
                target.mkdir(parents=True)
                for name in ("01-start.png", "02-anticipation.png"):
                    Image.new("RGBA", (64, 64), (240, 80, 80, 255)).save(target / name)
            result = subprocess.run([PYTHON, str(ROOT / "scripts/render_keypose_pack.py"), str(keyposes), str(root / "output"), "--layout", str(layout), "--image", str(approved), "--state", str(state)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly the four contract pose files", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
