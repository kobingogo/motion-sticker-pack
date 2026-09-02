from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_gallery_provenance import build_record, read_json  # noqa: E402


class GalleryProvenanceTests(unittest.TestCase):
    def test_every_public_case_has_current_machine_readable_provenance(self) -> None:
        index_path = ROOT / "gallery" / "index.json"
        index = read_json(index_path)
        for entry in index["styles"]:
            with self.subTest(style=entry["id"]):
                target = index_path.parent / "styles" / entry["gallery"] / "provenance.json"
                self.assertTrue(target.is_file())
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), build_record(index_path, entry))

    def test_legacy_provenance_does_not_forge_approval(self) -> None:
        index_path = ROOT / "gallery" / "index.json"
        entry = read_json(index_path)["styles"][0]
        record = build_record(index_path, entry)
        self.assertIsNone(record["approval"]["sha256"])
        self.assertIn("approval_hash", record["missing_historical_fields"])


if __name__ == "__main__":
    unittest.main()
