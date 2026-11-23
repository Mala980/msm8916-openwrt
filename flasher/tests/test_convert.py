#!/usr/bin/env python3
"""Tests for convert.py (convert stock dongles to OpenWrt), no hardware needed.

Run:  python3 -I flasher/tests/test_convert.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import convert  # noqa: E402


def make_dump(root: Path, name: str, version: str = None, complete: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "system.bin").write_bytes(b"sys")
    if complete:
        (d / "restore_sectors.txt").write_text("0 gpt_main0.bin PrimaryGPT\n")
    if version is not None:
        (d / "STOCK_VERSION").write_text(version + "\n")
    return d


class GoldenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_golden_matches_version(self):
        make_dump(self.root, "stock-20261101-000000", "V2.3.10")
        old = make_dump(self.root, "stock-20261120-000000", "V2.3.15.1")
        new = make_dump(self.root, "stock-20261123-122955", "V2.3.15.1")
        self.assertEqual(convert.find_golden(self.root, "V2.3.15.1"), new)
        self.assertNotEqual(convert.find_golden(self.root, "V2.3.15.1"), old)
        self.assertIsNone(convert.find_golden(self.root, "V9.9"))

    def test_find_golden_ignores_incomplete_dump(self):
        make_dump(self.root, "stock-20261123-122955", "V2.3.15.1", complete=False)
        self.assertIsNone(convert.find_golden(self.root, "V2.3.15.1"))

    def test_find_golden_empty_version_is_none(self):
        make_dump(self.root, "stock-20261123-122955", "")
        self.assertIsNone(convert.find_golden(self.root, ""))

    def test_mark_golden_roundtrip(self):
        d = make_dump(self.root, "stock-20261123-122955")
        convert.mark_golden(d, "V2.3.15.1")
        self.assertEqual(convert.find_golden(self.root, "V2.3.15.1"), d)


if __name__ == "__main__":
    unittest.main(verbosity=1)
