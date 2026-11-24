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
             mock.patch.object(flasher.subprocess, "run", return_value=fake_res) as run_mock, \
             redirect_stdout(buf):
            rc = flasher.verify_openwrt_dongle(wait_for_boot=False)
        self.assertEqual(rc, 0)
        self.assertIn("sync", run_mock.call_args[0][0][-1])
        out = buf.getvalue()
        self.assertIn("860018046041589", out)
        self.assertIn("OpenWrt 24.10.0", out)
        self.assertIn("verification passed", out)

    def test_verify_openwrt_dongle_accepts_intentionally_disabled_wifi(self):
        ssh_out = (
            "IMEI=860018046041589\n"
            "WIFI=0\n"
            "WIFI_DISABLED=1\n"
            "MODEM=/org/freedesktop/ModemManager1/Modem/0\n"
            "RELEASE=OpenWrt 24.10.0\n"
        )
        fake_res = subprocess.CompletedProcess(["ssh"], 0, stdout=ssh_out, stderr="")
        buf = io.StringIO()
        with mock.patch.object(flasher, "check_tcp", side_effect=lambda h, p: h == "192.168.8.1" and p in (22, 80)), \
             mock.patch.object(flasher.subprocess, "run", return_value=fake_res), \
             redirect_stdout(buf):
            rc = flasher.verify_openwrt_dongle(wait_for_boot=False, disable_wifi=True)
        self.assertEqual(rc, 0)
        self.assertIn("disabled (power-saving)", buf.getvalue())

    def test_ask_disable_wifi_defaults_to_true(self):
        with mock.patch("builtins.input", return_value=""):
            self.assertTrue(flasher.ask_disable_wifi())
        with mock.patch("builtins.input", return_value="1"):
            self.assertTrue(flasher.ask_disable_wifi())
        with mock.patch("builtins.input", return_value="2"):
            self.assertFalse(flasher.ask_disable_wifi())

    def test_fix_and_verify_dongle_applies_fixes_and_verifies_after_reboot(self):
        calls = []
        inspect_out = (
            "USB_STATE=legacy\nUSB_HASH=deadbeef\n"
            "WLED_STATE=forces-on\nWLED_HASH=deadbeef\n"
            "PERM_BAD=2\nUMOUNT_EN=0\n"
            "WIFI_DIS=0\nWIFI_DEF_DIS=0\n"
        )

        def fake_run(cmd, **kw):
            calls.append(cmd[-1])
            out = inspect_out if "USB_STATE=" in cmd[-1] else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

        buf = io.StringIO()
        with mock.patch.object(flasher, "_wait_openwrt_host", return_value="192.168.8.1"), \
             mock.patch.object(flasher.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(flasher.time, "sleep"), \
             mock.patch.object(flasher, "verify_openwrt_dongle", return_value=0) as verify_mock, \
             redirect_stdout(buf):
            rc = flasher.fix_and_verify_dongle(wait_for_boot=False, disable_wifi=True)

        self.assertEqual(rc, 0)
        verify_mock.assert_called_once_with(wait_for_boot=True, timeout_s=120)
        joined = "\n".join(calls)
        self.assertIn("/usr/sbin/usb-gadget", joined)
        self.assertIn("/etc/hotplug.d/ieee80211/99-wifi-led", joined)
        self.assertIn("wireless.radio0.disabled='1'", joined)
        self.assertIn("sync && reboot", joined)
        out = buf.getvalue()
        self.assertIn("legacy", out)
        self.assertIn("forces-on", out)
        self.assertIn("2 non-exec, umount-overlay=disabled", out)
        self.assertIn("CHANGED", out)

    def test_fix_and_verify_dongle_skips_unchanged_and_reboot(self):
        repo_root = FLASHER_DIR.parent
        usb_sha, _ = flasher._file_hashes(repo_root / "packages" / "uci-usb-gadget" / "files" / "usb-gadget.sh")
        wled_sha, _ = flasher._file_hashes(repo_root / "msm89xx" / "base-files" / "etc" / "hotplug.d" / "ieee80211" / "99-wifi-led")
        inspect_out = (
            f"USB_STATE=hardened\nUSB_HASH={usb_sha}\n"
            f"WLED_STATE=fixed\nWLED_HASH={wled_sha}\n"
            "PERM_BAD=0\nUMOUNT_EN=1\n"
            "WIFI_DIS=1\nWIFI_DEF_DIS=1\n"
        )
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, stdout=inspect_out, stderr="")

        buf = io.StringIO()
        with mock.patch.object(flasher, "_wait_openwrt_host", return_value="192.168.8.1"), \
             mock.patch.object(flasher.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(flasher.time, "sleep"), \
             mock.patch.object(flasher, "verify_openwrt_dongle", return_value=0) as verify_mock, \
             redirect_stdout(buf):
            rc = flasher.fix_and_verify_dongle(wait_for_boot=False, disable_wifi=True)

        self.assertEqual(rc, 0)
        verify_mock.assert_called_once_with(wait_for_boot=True, timeout_s=120)
        self.assertEqual(len(calls), 1)
        out = buf.getvalue()
        self.assertIn("unchanged", out)
        self.assertNotIn("CHANGED", out)
        self.assertIn("skipping reboot", out)

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
