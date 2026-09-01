from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "import_personal_handoff.py"
SPEC = importlib.util.spec_from_file_location("import_personal_handoff", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PersonalHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.card = self.root / "character.md"
        self.anchor = self.root / "hero-anchor.png"
        self.card.write_text("approved card", encoding="utf-8")
        self.anchor.write_bytes(b"stable-anchor-bytes")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def handoff(self, **updates: object) -> dict:
        payload = {
            "type": "IP_HANDOFF",
            "protocol": "ip-handoff/v2",
            "source_skill": "personal-ip-studio",
            "target_skill": "motion-sticker-pack",
            "id": "test-character",
            "identity_status": "approved",
            "identity_version": "v3",
            "card": str(self.card),
            "anchor": str(self.anchor),
            "anchor_sha256": hashlib.sha256(self.anchor.read_bytes()).hexdigest(),
            "skin_id": "toy",
            "rendering_policy": "preserve-source-appearance",
            "original_photo_policy": "do-not-use",
            "style": {"label": "3D toy", "prompt": "preserve it"},
            "reactions": ["开心", {"label": "谢谢", "text": "谢谢"}],
        }
        payload.update(updates)
        return payload

    def run_cli(self, handoff_path: Path, work_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(handoff_path), "--work-dir", str(work_dir), *extra],
            check=False,
            capture_output=True,
            text=True,
        )

    def write_handoff(self, payload: dict) -> Path:
        path = self.root / "input.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_valid_import_writes_only_metadata_and_resolves_style_reactions(self) -> None:
        work = self.root / "job"
        result = self.run_cli(self.write_handoff(self.handoff()), work)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["ok"])
        self.assertEqual(["toy", "开心"], [
            output["resolved"]["style"]["skin_id"],
            output["resolved"]["reactions"][0]["label"],
        ])
        self.assertEqual({"handoff.json", "character.json"}, {path.name for path in work.iterdir()})
        character = json.loads((work / "character.json").read_text(encoding="utf-8"))
        self.assertEqual("approved", character["identity_status"])
        self.assertFalse(character["motion_job"]["original_photo_used"])

    def test_canonical_personal_fields_are_resolved(self) -> None:
        payload = self.handoff(
            style=None,
            reactions=None,
            motion_style_id="custom",
            motion_style_prompt="keep anchor appearance",
            reaction_overlays={"glad": "开心", "focus": "专注"},
            requested_reactions=["glad", "focus"],
        )
        work = self.root / "canonical-job"
        result = self.run_cli(self.write_handoff(payload), work)
        self.assertEqual(0, result.returncode, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual("custom", output["resolved"]["style"]["id"])
        self.assertEqual(["glad", "focus"], [item["label"] for item in output["resolved"]["reactions"]])

    def test_hash_mismatch_is_stable_nonzero_and_writes_nothing(self) -> None:
        work = self.root / "job"
        result = self.run_cli(self.write_handoff(self.handoff(anchor_sha256="0" * 64)), work)
        self.assertEqual(result.returncode, 2)
        self.assertEqual("hash-mismatch", json.loads(result.stdout)["error"]["code"])
        self.assertFalse(work.exists())

    def test_status_and_path_are_rejected(self) -> None:
        for update, code in (({"identity_status": "draft"}, "validation-error"), ({"anchor": "relative.png"}, "path-error")):
            work = self.root / code
            result = self.run_cli(self.write_handoff(self.handoff(**update)), work)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(code, json.loads(result.stdout)["error"]["code"])

    def test_credentials_are_rejected_even_in_extension_fields(self) -> None:
        work = self.root / "job"
        payload = self.handoff(**{"x_vendor": {"api_key": "sk-live-not-allowed"}})
        result = self.run_cli(self.write_handoff(payload), work)
        self.assertEqual(result.returncode, 2)
        self.assertEqual("credential-detected", json.loads(result.stdout)["error"]["code"])
        self.assertFalse(work.exists())

    def test_unknown_extension_fields_are_preserved(self) -> None:
        payload = self.handoff(**{"x_personal_extension": {"future_flag": True}})
        work = self.root / "job"
        result = self.run_cli(self.write_handoff(payload), work)
        self.assertEqual(result.returncode, 0)
        saved = json.loads((work / "handoff.json").read_text(encoding="utf-8"))
        self.assertEqual({"future_flag": True}, saved["x_personal_extension"])

    def test_existing_character_is_deep_merged_without_overwriting_fields(self) -> None:
        work = self.root / "job"
        work.mkdir()
        existing = {
            "name": "local-job-name",
            "identity_version": "v3",
            "anchor_sha256": hashlib.sha256(self.anchor.read_bytes()).hexdigest(),
            "local_extension": {"keep": "me", "existing": 1},
            "resolved_style": {"label": "local label"},
        }
        (work / "character.json").write_text(json.dumps(existing), encoding="utf-8")
        result = self.run_cli(self.write_handoff(self.handoff()), work)
        self.assertEqual(result.returncode, 0, result.stderr)
        merged = json.loads((work / "character.json").read_text(encoding="utf-8"))
        self.assertEqual("local-job-name", merged["name"])
        self.assertEqual({"keep": "me", "existing": 1}, merged["local_extension"])
        self.assertEqual("local label", merged["resolved_style"]["label"])
        self.assertTrue(merged["resolved_style"]["preserve_source_appearance"])

    def test_numeric_and_v_prefixed_versions_are_idempotent(self) -> None:
        work = self.root / "job"
        work.mkdir()
        (work / "character.json").write_text(
            json.dumps({"identity_version": "v3", "anchor_sha256": self.handoff()["anchor_sha256"]}),
            encoding="utf-8",
        )
        result = self.run_cli(self.write_handoff(self.handoff(identity_version=3)), work)
        self.assertEqual(0, result.returncode, result.stdout)

    def test_stale_existing_identity_is_rejected(self) -> None:
        work = self.root / "job"
        work.mkdir()
        (work / "character.json").write_text(json.dumps({"identity_version": "v2"}), encoding="utf-8")
        result = self.run_cli(self.write_handoff(self.handoff()), work)
        self.assertEqual(result.returncode, 2)
        self.assertEqual("stale-job", json.loads(result.stdout)["error"]["code"])


if __name__ == "__main__":
    unittest.main()
