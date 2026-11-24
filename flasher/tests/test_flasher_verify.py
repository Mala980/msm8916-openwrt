#!/usr/bin/env python3
"""Tests for flasher.py's OpenWrt and backup verification helpers."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

FLASHER_DIR = Path(__file__).resolve().parents[1]
if str(FLASHER_DIR) not in sys.path:
    sys.path.insert(0, str(FLASHER_DIR))

import flasher  # noqa: E402


class FlasherVerifyTest(unittest.TestCase):
    def test_verify_openwrt_dongle_passes_when_all_checks_ok(self):
        ssh_out = (
            "IMEI=860018046041589\n"
            "WIFI=1\n"
            "MODEM=/org/freedesktop/ModemManager1/Modem/0\n"
            "RELEASE=OpenWrt 24.10.0\n"
        )
        fake_res = subprocess.CompletedProcess(["ssh"], 0, stdout=ssh_out, stderr="")
        buf = io.StringIO()
        with mock.patch.object(flasher, "check_tcp", side_effect=lambda h, p: h == "192.168.8.1" and p in (22, 80)), \
             mock.patch.object(flasher.subprocess, "run", return_value=fake_res), \
             redirect_stdout(buf):
            rc = flasher.verify_openwrt_dongle(wait_for_boot=False)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("860018046041589", out)
        self.assertIn("OpenWrt 24.10.0", out)
        self.assertIn("verification passed", out)

    def test_verify_openwrt_dongle_fails_when_imei_missing(self):
        ssh_out = "IMEI=\nWIFI=1\nMODEM=/org/freedesktop/ModemManager1/Modem/0\n"
        fake_res = subprocess.CompletedProcess(["ssh"], 0, stdout=ssh_out, stderr="")
        buf = io.StringIO()
        with mock.patch.object(flasher, "check_tcp", side_effect=lambda h, p: h == "192.168.8.1" and p == 22), \
             mock.patch.object(flasher.subprocess, "run", return_value=fake_res), \
             redirect_stdout(buf):
            rc = flasher.verify_openwrt_dongle(wait_for_boot=False)
        self.assertEqual(rc, 1)
        self.assertIn("verification failed", buf.getvalue())

    def test_verify_all_backups_checks_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bdir = root / "backup" / "units" / "0x242efec7" / "20261124-182929"
            bdir.mkdir(parents=True)
            data = b"radio-data"
            (bdir / "fsc.bin").write_bytes(data)
            manifest = {
                "serial": "0x242efec7",
                "files": {"fsc.bin": {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}},
            }
            (bdir / "manifest.json").write_text(json.dumps(manifest))

            buf = io.StringIO()
            with mock.patch.object(flasher, "FLASHER_DIR", root), \
                 mock.patch.object(flasher, "UNITS_DIR", root / "backup" / "units"), \
                 redirect_stdout(buf):
                self.assertEqual(flasher.verify_all_backups(), 0)
                (bdir / "fsc.bin").write_bytes(b"corrupted")
                self.assertEqual(flasher.verify_all_backups(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
