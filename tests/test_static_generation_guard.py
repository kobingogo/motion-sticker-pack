from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from static_generation_guard import (  # noqa: E402
    StaticGenerationGuardError,
    claim_attempt,
    update_attempt,
)


class StaticGenerationGuardTests(unittest.TestCase):
    def make_generation(self, root: Path) -> Path:
        generation = root / "static-generation.json"
        generation.write_text(
            json.dumps(
                {
                    "version": 1,
                    "call_arguments": {"prompt": "first"},
                    "opaque_fallback_call": {"call_arguments": {"prompt": "fallback"}},
                    "generation_policy": {"max_static_generation_attempts": 2},
                }
            ),
            encoding="utf-8",
        )
        return generation

    def test_missing_content_does_not_unlock_a_second_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generation = self.make_generation(root)
            ledger = root / "static-generation-attempts.json"
            claim_attempt(generation, ledger, 1, root / "static-sheet-source.png")
            with self.assertRaisesRegex(StaticGenerationGuardError, "unresolved"):
                claim_attempt(generation, ledger, 2, root / "static-sheet-source.png")

    def test_accept_records_source_and_selected_attempt_without_breaking_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generation = self.make_generation(root)
            ledger = root / "static-generation-attempts.json"
            source = root / "static-sheet-source.png"
            claim_attempt(generation, ledger, 1, source)
            update_attempt(generation, ledger, 1, "invoked")
            source.write_bytes(b"generated image")
            accepted = update_attempt(generation, ledger, 1, "accept", source=source)
            self.assertEqual(accepted["status"], "accepted")
            self.assertEqual(json.loads(generation.read_text())["selected_attempt"]["attempt"], 1)
            with self.assertRaisesRegex(StaticGenerationGuardError, "already recorded"):
                claim_attempt(generation, ledger, 1, source)
            with self.assertRaisesRegex(StaticGenerationGuardError, "unresolved"):
                claim_attempt(generation, ledger, 2, source)

    def test_fallback_requires_explicit_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generation = self.make_generation(root)
            ledger = root / "static-generation-attempts.json"
            source = root / "source.png"
            claim_attempt(generation, ledger, 1, source)
            update_attempt(generation, ledger, 1, "invoked")
            update_attempt(generation, ledger, 1, "reject", reason="ambiguous background")
            claim_attempt(generation, ledger, 2, source)
            update_attempt(generation, ledger, 2, "invoked")
            update_attempt(generation, ledger, 2, "reject", reason="still ambiguous")
            with self.assertRaisesRegex(StaticGenerationGuardError, "between 1 and 2"):
                claim_attempt(generation, ledger, 3, source)


if __name__ == "__main__":
    unittest.main()
