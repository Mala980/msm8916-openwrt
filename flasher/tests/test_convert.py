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


PARCEL = """Result: Parcel(
  0x00000000: 00000000 0000000f 00360038 00300030 '........8.6.0.0.'
  0x00000010: 00380031 00340030 00300036 00310034 '1.8.0.4.6.0.4.1.'
  0x00000020: 00380035 00000039                   '5.8.9...        ')
"""

FAKE_ADB = r'''#!/bin/sh
# fake adb: answers "adb shell <cmd>" from FAKE_ADB_<key> env vars
[ "$1" = "shell" ] || exit 1
shift
case "$*" in
  "getprop ro.product.model") printf '%s\n' "${FAKE_MODEL-UZ801}" ;;
  "getprop ro.build.display.id") printf '%s\n' "${FAKE_VERSION-V2.3.15.1}" ;;
  "getprop ro.serialno") printf '%s\n' "${FAKE_SERIAL-c7aa55ec}" ;;
  "getprop gsm.version.baseband") printf '%s\n' "${FAKE_BASEBAND-N958St_Z85}" ;;
  "cat /sys/class/net/wlan0/address") printf '%s\n' "${FAKE_MAC-a0:7c:48:ed:90:8d}" ;;
  "service call iphonesubinfo 1") printf '%s' "${FAKE_PARCEL}" ;;
  *) exit 1 ;;
esac
'''


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.adb = os.path.join(self.tmp.name, "adb")
        with open(self.adb, "w") as f:
            f.write(FAKE_ADB)
        os.chmod(self.adb, 0o755)
        self.saved = dict(os.environ)
        os.environ["FAKE_PARCEL"] = PARCEL

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        self.tmp.cleanup()

    def test_parse_iphonesubinfo(self):
        self.assertEqual(convert.parse_iphonesubinfo(PARCEL), "860018046041589")
        self.assertEqual(convert.parse_iphonesubinfo("Result: Parcel(00000000 00000000)"), "")

    def test_read_stock_identity(self):
        ident = convert.read_stock_identity(self.adb)
        self.assertEqual(ident, {
            "model": "UZ801", "version": "V2.3.15.1", "imei": "860018046041589",
            "wifi_mac": "a0:7c:48:ed:90:8d", "android_serial": "c7aa55ec",
            "baseband": "N958St_Z85"})

    def test_identity_missing_fields(self):
        os.environ["FAKE_VERSION"] = ""
        os.environ["FAKE_PARCEL"] = "Result: Parcel(00000000 ffffffff)"
        ident = convert.read_stock_identity(self.adb)
        self.assertEqual(ident["version"], "")
        self.assertEqual(ident["imei"], "")
        self.assertEqual(ident["model"], "UZ801")


if __name__ == "__main__":
    unittest.main(verbosity=1)
