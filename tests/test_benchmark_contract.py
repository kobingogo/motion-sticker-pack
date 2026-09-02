from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_benchmark import validate  # noqa: E402


class BenchmarkContractTests(unittest.TestCase):
    def test_public_benchmark_covers_all_declared_failure_modes(self) -> None:
        result = validate()
        self.assertEqual(result["cases"], 9)
        self.assertGreater(result["real_run_required"], 0)


if __name__ == "__main__":
    unittest.main()
