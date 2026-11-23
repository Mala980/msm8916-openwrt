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


UNIQUE = ["fsc", "fsg", "modemst1", "modemst2", "persist", "sec", "DDR", "ssd"]
IDENT = {"model": "UZ801", "version": "V2.3.15.1", "imei": "860018046041589",
         "wifi_mac": "a0:7c:48:ed:90:8d", "android_serial": "c7aa55ec", "baseband": "x"}


class FakeWorld:
    """Scripted dongle + tools for convert_one/convert_loop."""

    def __init__(self, root: Path, golden=True, flash_rc=0, verify=None, ident=None,
                 units=1, linger_polls=0):
        self.root = root
        self.calls = []
        self.flash_rc = flash_rc
        self.verify = verify or {"imei": IDENT["imei"], "wifi_up": True, "modem_present": True}
        self.ident = dict(ident or IDENT)
        self.units_left = units          # stock dongles that will be plugged in
        self.state = "empty"             # empty | stock | edl | openwrt
        self.linger = 0
        self.linger_polls = linger_polls  # polls the converted dongle stays plugged
        if golden:
            make_dump(root, "stock-20261123-122955", "V2.3.15.1")
            (root / "stock-20261123-122955" / "modem.bin").write_bytes(b"modem")

    # --- env callables -------------------------------------------------
    def adb_devices(self):
        if self.state == "empty" and self.units_left > 0:
            self.state = "stock"
            self.units_left -= 1
        return ["0123456789ABCDEF"] if self.state == "stock" else []

    def openwrt_online(self):
        if self.state == "openwrt":
            if self.linger >= self.linger_polls and self.calls and self.calls[-1][0] == "verify":
                pass
            return True
        return False

    def read_identity(self):
        self.calls.append(("identity",))
        return dict(self.ident)

    def run(self, cmd):
        self.calls.append(("run", cmd))
        if cmd[-2:] == ["reboot", "edl"]:
            self.state = "edl"
        elif cmd[-1].endswith("backup-stock.sh"):
            d = make_dump(self.root, "stock-20261123-130000")
            (d / "modem.bin").write_bytes(b"modem")
            self.state = "stock"
        elif "flash-firmware.sh" in " ".join(cmd):
            if self.flash_rc == 0:
                self.state = "openwrt"
            return self.flash_rc
        return 0

    def run_capture(self, cmd):
        self.calls.append(("capture", cmd))
        dest = self.root / "units" / "0x0bc56cbf" / "20261123-130500"
        dest.mkdir(parents=True, exist_ok=True)
        for p in UNIQUE:
            (dest / (p + ".bin")).write_bytes(p.encode())
        return 0, str(dest) + "\n"

    def verify_openwrt(self, imei):
        self.calls.append(("verify", imei))
        return dict(self.verify)

    def sleep(self, _s):
        if self.state == "openwrt" and self.calls and self.calls[-1][0] in ("verify", "unplug-wait"):
            self.linger += 1
            self.calls.append(("unplug-wait",))
            if self.linger > self.linger_polls:
                self.state = "empty"
                self.linger = 0

    def env(self, **kw):
        return convert.ConvertEnv(
            adb="adb", edl="edl", python="python3", flasher_dir=Path("/flasher"),
            backup_root=self.root, firmware_dir=Path("/flasher/firmware/openwrt-stable"),
            adb_devices=self.adb_devices, openwrt_online=self.openwrt_online,
            read_identity=self.read_identity, run=self.run, run_capture=self.run_capture,
            verify_openwrt=self.verify_openwrt, sleep=self.sleep, out=lambda *a: None, **kw)

    def kinds(self):
        out = []
        for c in self.calls:
            if c[0] == "run":
                cmd = " ".join(c[1])
                out.append("flash" if "flash-firmware.sh" in cmd else
                           "stockdump" if "backup-stock.sh" in cmd else
                           "reboot-edl" if cmd.endswith("reboot edl") else "run")
            elif c[0] == "capture":
                out.append("unique-backup")
            elif c[0] != "unplug-wait":
                out.append(c[0])
        return out


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def flash_cmd(self, w):
        return [c[1] for c in w.calls if c[0] == "run" and "flash-firmware.sh" in " ".join(c[1])][0]

    def test_converts_one_unit_with_golden(self):
        w = FakeWorld(self.root)
        r = convert.convert_one(w.env())
        self.assertTrue(r.ok, r.verdict)
        self.assertEqual(w.kinds(), ["identity", "reboot-edl", "unique-backup", "flash", "verify"])
        cmd = self.flash_cmd(w)
        for flag in ("--firmware", "--radio-from", "--skip-live-backup", "--yes"):
            self.assertIn(flag, cmd)
        radio = Path(cmd[cmd.index("--radio-from") + 1])
        self.assertEqual(sorted(p.name for p in radio.iterdir()),
                         sorted(p + ".bin" for p in ["fsc", "fsg", "modemst1", "modemst2", "modem", "persist", "sec"]))
        self.assertEqual((radio / "modem.bin").resolve(),
                         (self.root / "stock-20261123-122955" / "modem.bin").resolve())
        self.assertEqual((radio / "modemst1.bin").resolve().parent.name, "20261123-130500")
        backup_cmd = [c[1] for c in w.calls if c[0] == "capture"][0]
        self.assertIn("unique", backup_cmd)
        self.assertIn("--identity", backup_cmd)
        self.assertIn("860018046041589", r.verdict)
        self.assertIn("Wi-Fi up", r.verdict)

    def test_full_dump_when_no_golden(self):
        w = FakeWorld(self.root, golden=False)
        r = convert.convert_one(w.env())
        self.assertTrue(r.ok, r.verdict)
        self.assertEqual(w.kinds()[:3], ["identity", "stockdump", "reboot-edl"])
        self.assertEqual(convert.find_golden(self.root, "V2.3.15.1"),
                         self.root / "stock-20261123-130000")

    def test_refuses_openwrt_dongle(self):
        w = FakeWorld(self.root, units=0)
        w.state = "openwrt"
        r = convert.convert_one(w.env())
        self.assertFalse(r.ok)
        self.assertIn("already runs OpenWrt", r.verdict)
        self.assertNotIn("flash", w.kinds())

    def test_waits_for_unplug_before_next(self):
        w = FakeWorld(self.root, units=2, linger_polls=3)
        rc = convert.convert_loop(w.env(max_units=2))
        self.assertEqual(rc, 0)
        kinds = [c[0] for c in w.calls]
        first_verify = kinds.index("verify")
        second_identity = kinds.index("identity", first_verify)
        self.assertGreaterEqual(kinds[first_verify:second_identity].count("unplug-wait"), 3)

    def test_verdict_without_imei(self):
        ident = dict(IDENT, imei="")
        w = FakeWorld(self.root, ident=ident)
        r = convert.convert_one(w.env())
        self.assertFalse(r.ok)
        self.assertIn("IMEI not verified", r.verdict)

    def test_stops_on_flash_failure(self):
        w = FakeWorld(self.root, units=2, flash_rc=1)
        rc = convert.convert_loop(w.env(max_units=2))
        self.assertNotEqual(rc, 0)
        self.assertEqual([c[0] for c in w.calls].count("identity"), 1)


class RealEnvParsingTest(unittest.TestCase):
    def test_parse_adb_devices(self):
        text = "List of devices attached\n0123456789ABCDEF\tdevice usb:1 product:x\nZZZ\toffline\n\n"
        self.assertEqual(convert.parse_adb_devices(text), ["0123456789ABCDEF"])

    def test_parse_verify(self):
        text = ("IMEI=860018046041589\nWIFI=1\nMODEM=/org/freedesktop/ModemManager1/Modem/0\n")
        self.assertEqual(convert.parse_verify(text),
                         {"imei": "860018046041589", "wifi_up": True, "modem_present": True})
        self.assertEqual(convert.parse_verify("IMEI=\nWIFI=0\nMODEM=\n"),
                         {"imei": "", "wifi_up": False, "modem_present": False})


if __name__ == "__main__":
    unittest.main(verbosity=1)
