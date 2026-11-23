#!/usr/bin/env python3
"""Tests for flash-firmware.sh's non-interactive flags, with a fake edl.

The script and radio_backup.py are copied into a temp flasher dir so backups
land there, never in the real backup/units/. A fake `ioreg`/`lsusb` reports
the dongle in EDL mode; a fake `edl` logs every call.

Run:  python3 -I flasher/tests/test_flash_noninteractive.py
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FLASHER = os.path.dirname(HERE)
RADIO = ["fsc", "fsg", "modemst1", "modemst2", "modem", "persist", "sec"]

FAKE_EDL = r'''#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_DIR"]
with open(os.path.join(d, "edl.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
state = os.path.join(d, "session")
if not os.path.exists(state):
    open(state, "w").close()
    print("main - Mode detected: sahara")
    print("HWID:              0x007050e100000000 (MSM_ID:0x007050e1)")
    print("Serial:            0x0bc56cbf")
print("main - Mode detected: firehose")
if len(sys.argv) > 3 and sys.argv[1] == "r":
    with open(sys.argv[3], "wb") as f:
        f.write(("%s:live" % sys.argv[2]).encode() * 64)
'''

# The EDL device first, then plenty more output (like the real ioreg): grep -q
# exits at the first match and the writer gets SIGPIPE, which must not make
# the script think the dongle is not in EDL mode.
FAKE_USB = ("#!/bin/sh\necho '+-o QHSUSB__BULK@01120000'\n"
            "echo 'Bus 001 Device 002: ID 05c6:9008 Qualcomm QDLoader'\n"
            "i=0; while [ $i -lt 20000 ]; do echo \"  | filler line $i\"; i=$((i+1)); done\n")

# Anything that would reach a real dongle on the network or ADB fails and is
# logged, so a test can never reboot or touch real hardware.
FAKE_NET = "#!/bin/sh\necho \"$(basename \"$0\") $*\" >> \"$FAKE_DIR/net.log\"\nexit 1\n"


class FlashNonInteractiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.dir = os.path.join(t, "flasher")
        os.makedirs(self.dir)
        for f in ("flash-firmware.sh", "radio_backup.py"):
            shutil.copy(os.path.join(FLASHER, f), self.dir)
        self.bin = os.path.join(t, "bin")
        os.makedirs(self.bin)
        self._exe("edl", FAKE_EDL)
        for tool in ("ioreg", "lsusb"):
            self._exe(tool, FAKE_USB)
        for tool in ("ping", "ssh", "adb"):
            self._exe(tool, FAKE_NET)
        self.fw = os.path.join(self.dir, "firmware", "openwrt-stable")
        os.makedirs(self.fw)
        gpt = bytearray(67 * 512)
        struct.pack_into("<Q", gpt, 544, 7569407)        # alternate LBA
        self._write(os.path.join(self.fw, "x-gpt_both0.bin"), bytes(gpt))
        self._write(os.path.join(self.fw, "x-boot.img"), b"boot" * 100)
        self._write(os.path.join(self.fw, "x-system.img"), b"root" * 100)
        with zipfile.ZipFile(os.path.join(self.fw, "x-firmware.zip"), "w") as z:
            for p in ("aboot", "hyp", "rpm", "sbl1", "tz"):
                z.writestr("fw/%s.mbn" % p, p)
        self.radio = os.path.join(t, "radio")
        os.makedirs(self.radio)
        for p in RADIO:
            self._write(os.path.join(self.radio, p + ".bin"), p.encode())
        self.env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"],
                        EDL_BIN=os.path.join(self.bin, "edl"), FAKE_DIR=t)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, path, data):
        with open(path, "wb") as f:
            f.write(data)

    def _exe(self, name, text):
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write(text)
        os.chmod(p, 0o755)

    def run_flash(self, *args, stdin=None):
        return subprocess.run(["bash", os.path.join(self.dir, "flash-firmware.sh")] + list(args),
                              input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, env=self.env, timeout=120)

    def edl_calls(self):
        log = os.path.join(self.tmp.name, "edl.log")
        return open(log).read().splitlines() if os.path.exists(log) else []

    def test_noninteractive_runs_without_stdin(self):
        r = self.run_flash("--firmware", self.fw, "--radio-from", self.radio,
                           "--skip-live-backup", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        calls = self.edl_calls()
        self.assertFalse([c for c in calls if c.startswith("r ")], "no live backup reads")
        self.assertTrue(any(c.startswith("w boot ") for c in calls))
        self.assertTrue(any(c.startswith("w rootfs ") for c in calls))
        for p in RADIO:
            self.assertIn("w %s %s" % (p, os.path.join(self.radio, p + ".bin")), calls)

    def test_edl_detected_without_touching_network(self):
        r = self.run_flash("--firmware", self.fw, "--radio-from", self.radio,
                           "--skip-live-backup", "--yes")
        self.assertIn("already in EDL mode", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "net.log")),
                         "ping/ssh/adb must not be used when the dongle is in EDL")

    def test_skip_live_backup_requires_radio_from(self):
        r = self.run_flash("--firmware", self.fw, "--skip-live-backup", "--yes")
        self.assertEqual(r.returncode, 2, r.stdout[-2000:])
        self.assertEqual(self.edl_calls(), [])

    def test_interactive_default_unchanged(self):
        r = self.run_flash(stdin="1\n1\ny\n")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        calls = self.edl_calls()
        self.assertTrue([c for c in calls if c.startswith("r ")], "live backup still runs")
        self.assertTrue(any(c.startswith("w rootfs ") for c in calls))
        self.assertTrue(any(c.startswith("w modemst1 ") for c in calls))


if __name__ == "__main__":
    unittest.main(verbosity=1)
