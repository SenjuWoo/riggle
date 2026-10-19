"""Checks for the release packaging helper."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("build_portable", ROOT / "packaging" / "build_portable.py")
build_portable = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_portable)


class PackagingTests(unittest.TestCase):
    def test_checksums_are_lf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SHA256SUMS.txt"
            build_portable.write_checksums(path, ["a" * 64 + "  one.zip", "b" * 64 + "  two.zip"])
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertTrue(raw.endswith(b"\n"))
            self.assertEqual(raw.count(b"\n"), 2)


if __name__ == "__main__":
    unittest.main()
