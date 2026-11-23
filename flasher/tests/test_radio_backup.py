#!/usr/bin/env python3
"""Tests for radio_backup.py, driven by a fake edl (no hardware needed).

Run:  python3 -I flasher/tests/test_radio_backup.py
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "radio_backup.py")

# Mimics bkerler/edl: the Sahara banner (with the chip serial) is printed only
# on the first call of an EDL session; later calls talk to firehose directly.
FAKE_EDL = r'''#!/usr/bin/env python3
import os, sys
state = os.environ["FAKE_EDL_STATE"]
serial = os.environ.get("FAKE_EDL_SERIAL", "0x242efec7")
fail = os.environ.get("FAKE_EDL_FAIL", "")
seed = os.environ.get("FAKE_EDL_SEED", "a")
if not os.path.exists(state):
    open(state, "w").close()
    print("main - Mode detected: sahara")
    print("HWID:              0x007050e100000000 (MSM_ID:0x007050e1)")
    print("PK_HASH:           0xcc3153a8")
    print("Serial:            " + serial)
print("firehose - TargetName=MSM8916")
cmd, part, out = sys.argv[1], sys.argv[2], sys.argv[3]
assert cmd == "r"
if part == fail:
    print("firehose - Error: couldn't find partition " + part)
    sys.exit(1)
with open(out, "wb") as f:
    f.write((part + ":" + seed).encode() * 64)
'''


class RadioBackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "units")
        self.edl = os.path.join(self.tmp.name, "edl")
        with open(self.edl, "w") as f:
            f.write(FAKE_EDL)
        os.chmod(self.edl, os.stat(self.edl).st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, FAKE_EDL_STATE=os.path.join(self.tmp.name, "session"))

    def tearDown(self):
        self.tmp.cleanup()

    def new_session(self, **env):
        """Simulate replugging the dongle into EDL."""
        try:
            os.remove(self.env["FAKE_EDL_STATE"])
        except FileNotFoundError:
            pass
        self.env.update(env)

    def run_tool(self, *args):
        return subprocess.run([sys.executable, "-I", TOOL, "--root", self.root] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, env=self.env)

    def backup(self, *extra):
        return self.run_tool("backup", "--edl", self.edl, "--print-path", *extra)

    def test_backup_files_under_chip_serial(self):
        r = self.backup()
        self.assertEqual(r.returncode, 0, r.stderr)
        dest = r.stdout.strip()
        self.assertEqual(os.path.dirname(dest), os.path.join(self.root, "0x242efec7"))
        with open(os.path.join(dest, "manifest.json")) as f:
            m = json.load(f)
        self.assertEqual(m["serial"], "0x242efec7")
        self.assertEqual(m["hwid"], "0x007050e100000000")
        for part in ("modemst1", "modemst2", "fsg", "fsc", "sec", "persist", "modem", "sbl1"):
            self.assertIn(part + ".bin", m["files"])
            self.assertTrue(os.path.isfile(os.path.join(dest, part + ".bin")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "0x242efec7", "unit.json")))
        self.assertEqual(self.run_tool("verify", dest).returncode, 0)

    def test_unchanged_backup_is_not_duplicated(self):
        first = self.backup().stdout.strip()
        self.new_session()
        r = self.backup()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), first)
        self.assertEqual(len([d for d in os.listdir(os.path.join(self.root, "0x242efec7"))
                              if d != "unit.json"]), 1)

    def test_changed_data_gets_new_backup(self):
        first = self.backup().stdout.strip()
        self.new_session(FAKE_EDL_SEED="b")
        second = self.backup().stdout.strip()
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.isdir(first) and os.path.isdir(second))

    def test_no_serial_in_firehose_mode_fails_cleanly(self):
        self.backup()                      # session already past Sahara
        r = self.backup("--keep-duplicate")
        self.assertEqual(r.returncode, 4)  # EXIT_NOSERIAL: caller reboots the dongle
        self.assertIn("serial", r.stderr)
        self.assertEqual(r.stdout.strip(), "")
        self.assertFalse([d for d in os.listdir(self.root) if d.startswith(".staging")])

    def test_explicit_serial_must_match_chip(self):
        r = self.backup("--serial", "0xdeadbeef")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.isdir(os.path.join(self.root, "0xdeadbeef")))

    def test_failed_read_stores_nothing(self):
        self.new_session(FAKE_EDL_FAIL="modemst2")
        r = self.backup()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "")
        self.assertFalse(os.path.isdir(os.path.join(self.root, "0x242efec7")))
        self.assertFalse([d for d in os.listdir(self.root) if d.startswith(".staging")])

    def test_check_detects_foreign_unit(self):
        mine = self.backup().stdout.strip()
        self.new_session(FAKE_EDL_SERIAL="0x11112222")
        other = self.backup().stdout.strip()
        self.assertEqual(self.run_tool("check", mine, "--against", mine).returncode, 0)
        self.assertEqual(self.run_tool("check", other, "--against", mine).returncode, 2)
        self.assertEqual(self.run_tool("check", mine, "--serial", "242EFEC7").returncode, 0)

    def test_check_unknown_origin(self):
        mine = self.backup().stdout.strip()
        legacy = os.path.join(self.tmp.name, "stock_extracted")
        os.makedirs(legacy)
        self.assertEqual(self.run_tool("check", legacy, "--against", mine).returncode, 3)

    def test_verify_detects_corruption(self):
        dest = self.backup().stdout.strip()
        with open(os.path.join(dest, "modemst1.bin"), "ab") as f:
            f.write(b"x")
        self.assertEqual(self.run_tool("verify", dest).returncode, 1)

    def test_import_legacy_folder(self):
        legacy = os.path.join(self.tmp.name, "stock_extracted")
        os.makedirs(legacy)
        for p in ("fsc", "fsg", "modemst1", "modemst2", "modem", "persist", "sec"):
            with open(os.path.join(legacy, p + ".bin"), "wb") as f:
                f.write(p.encode())
        r = self.run_tool("import", legacy, "--serial", "242efec7", "--print-path")
        self.assertEqual(r.returncode, 0, r.stderr)
        dest = r.stdout.strip()
        self.assertTrue(dest.startswith(os.path.join(self.root, "0x242efec7")))
        self.assertTrue(os.path.isfile(os.path.join(legacy, "modemst1.bin")))  # source kept
        self.assertEqual(self.run_tool("verify", dest).returncode, 0)

    def test_import_rejects_incomplete_radio_set(self):
        legacy = os.path.join(self.tmp.name, "partial")
        os.makedirs(legacy)
        with open(os.path.join(legacy, "modem.bin"), "wb") as f:
            f.write(b"m")
        self.assertEqual(self.run_tool("import", legacy, "--serial", "1").returncode, 1)

    def test_list(self):
        self.backup()
        r = self.run_tool("list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("0x242efec7", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
