#!/usr/bin/env python3
"""radio_backup.py — per-unit backup of a dongle's stock radio partitions.

Every UZ801 carries data that exists nowhere else: its IMEI and RF calibration
(modemst1/2, fsg, fsc, sec, persist) plus the stock boot chain. This tool reads
those partitions over EDL and files them under the unit's own chip serial, so
a backup can never be confused with another dongle's (restoring another unit's
radio data clones its IMEI).

Layout (under flasher/backup/, which is gitignored):

    units/<serial>/unit.json                 hwid, pk_hash, first/last seen
    units/<serial>/<YYYYmmdd-HHMMSS>/*.bin   partition images
    units/<serial>/<YYYYmmdd-HHMMSS>/manifest.json

The serial is the SoC serial that edl prints in Sahara mode ("Serial: 0x...").
Sahara only runs on the first edl call after the dongle enters EDL, so
`backup` must be the first edl command of a session (or pass --serial).

Commands:
    backup   read partitions from the connected dongle (EDL) and store them
    import   file an existing folder of .bin images under a serial
    list     show stored units and backups
    verify   re-hash a backup against its manifest
    check    compare the serials of two backups (restore safety)

Standard library only; Python 3.9+.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.join(HERE, "backup", "units")

# Per-unit data: IMEI, EFS/NV, RF calibration. Must never be lost or mixed up.
RADIO_PARTS = ["fsc", "fsg", "modemst1", "modemst2", "modem", "persist", "sec"]
# Stock boot chain: replaced by the OpenWrt flash, needed to return to stock.
BOOT_PARTS = ["sbl1", "aboot", "rpm", "tz", "hyp"]
DEFAULT_PARTS = RADIO_PARTS + BOOT_PARTS

SERIAL_RE = re.compile(r"Serial:\s*(0x[0-9a-fA-F]+)")
HWID_RE = re.compile(r"HWID:\s*(0x[0-9a-fA-F]+)")
PKHASH_RE = re.compile(r"PK_HASH:\s*(0x[0-9a-fA-F]+)")

EXIT_OK, EXIT_ERR, EXIT_MISMATCH, EXIT_UNKNOWN = 0, 1, 2, 3


def log(msg):
    # Progress goes to stderr so stdout stays machine-readable (--print-path).
    print("[*] " + msg, file=sys.stderr, flush=True)


def warn(msg):
    print("[!] " + msg, file=sys.stderr, flush=True)


def now_stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def normalize_serial(serial):
    s = serial.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if not re.fullmatch(r"[0-9a-f]{1,16}", s):
        raise ValueError("invalid serial %r (expected hex, e.g. 0x242efec7)" % serial)
    return "0x" + s.zfill(8)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sahara(text):
    """Extract chip identity from edl's Sahara banner."""
    info = {}
    for key, rx in (("serial", SERIAL_RE), ("hwid", HWID_RE), ("pk_hash", PKHASH_RE)):
        m = rx.search(text)
        if m:
            info[key] = m.group(1).lower()
    return info


PCT_RE = re.compile(r"(\d{1,3}\.\d+)%")
SPEED_RE = re.compile(r"([\d.]+ [KMG]B/s)")
DEBUG = os.environ.get("FLASH_DEBUG") == "1"


def run_edl(edl, args, on_progress=None):
    """Run edl, return (rc, text). Its output is read as it arrives so the
    caller can show edl's own progress (on_progress(pct, speed)); with
    FLASH_DEBUG=1 the raw output is also echoed. Callers show edl_tail() on
    failure."""
    proc = subprocess.Popen([edl] + args, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    chunks = []
    while True:
        data = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not data:
            break
        text = data.decode("utf-8", "replace")
        chunks.append(text)
        if DEBUG:
            sys.stderr.write(text)
            sys.stderr.flush()
        elif on_progress:
            frames = [f for f in text.replace("\r", "\n").split("\n") if "Progress:" in f]
            if frames:
                m, sp = PCT_RE.search(frames[-1]), SPEED_RE.search(frames[-1])
                if m:
                    on_progress(m.group(1) + "%", sp.group(1) if sp else "")
    proc.wait()
    return proc.returncode, "".join(chunks)


def edl_tail(text, n=20):
    """Print edl's last meaningful lines (no progress-bar frames)."""
    lines = [l for l in text.replace("\r", "\n").splitlines()
             if l.strip() and "Progress:" not in l and not l.startswith("Done |")]
    for line in lines[-n:]:
        print("      " + line, file=sys.stderr)
    sys.stderr.flush()


def progress(msg, done=False):
    """One self-updating line on a terminal, plain lines otherwise."""
    if DEBUG:
        if done:
            log(msg)
        return
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K[*] " + msg + ("\n" if done else ""))
        sys.stderr.flush()
    elif done:
        log(msg)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def list_backups(unit_dir):
    """Backup folders of one unit, oldest first."""
    if not os.path.isdir(unit_dir):
        return []
    out = []
    for name in sorted(os.listdir(unit_dir)):
        d = os.path.join(unit_dir, name)
        if os.path.isfile(os.path.join(d, "manifest.json")):
            out.append(d)
    return out


def store(root, serial, src_files, identity, source, keep_duplicate=False):
    """File already-read images under units/<serial>/<stamp>/.

    src_files: {part: path}. Files are moved (same filesystem) or copied.
    Returns (backup_dir, created). When every image matches the unit's latest
    backup the new copy is dropped and the latest one is returned instead.
    """
    serial = normalize_serial(serial)
    unit_dir = os.path.join(root, serial)
    os.makedirs(unit_dir, exist_ok=True)

    stamp = now_stamp()
    dest = os.path.join(unit_dir, stamp)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(unit_dir, "%s-%d" % (stamp, n))
    os.makedirs(dest)

    files = {}
    for part, src in sorted(src_files.items()):
        dst = os.path.join(dest, part + ".bin")
        shutil.copy2(src, dst)
        files[part + ".bin"] = {"size": os.path.getsize(dst), "sha256": sha256_file(dst)}

    manifest = {
        "serial": serial,
        "hwid": identity.get("hwid", ""),
        "pk_hash": identity.get("pk_hash", ""),
        "created": stamp,
        "source": source,
        "files": files,
    }

    previous = list_backups(unit_dir)
    previous = [p for p in previous if os.path.abspath(p) != os.path.abspath(dest)]
    if previous and not keep_duplicate:
        last = load_json(os.path.join(previous[-1], "manifest.json"))
        if last.get("files") == files:
            shutil.rmtree(dest)
            log("Identical to latest backup %s — not stored again." % previous[-1])
            touch_unit(unit_dir, serial, identity)
            return previous[-1], False

    write_json(os.path.join(dest, "manifest.json"), manifest)
    touch_unit(unit_dir, serial, identity)
    return dest, True


def touch_unit(unit_dir, serial, identity):
    path = os.path.join(unit_dir, "unit.json")
    unit = load_json(path) if os.path.isfile(path) else {"serial": serial, "first_seen": now_stamp()}
    for key in ("hwid", "pk_hash"):
        if identity.get(key):
            unit[key] = identity[key]
    unit["last_seen"] = now_stamp()
    write_json(path, unit)


# --------------------------------------------------------------------------
# commands


def cmd_backup(a):
    edl = a.edl or os.environ.get("EDL_BIN") or shutil.which("edl")
    if not edl:
        warn("edl not found — pass --edl or set EDL_BIN")
        return EXIT_ERR
    parts = a.parts.split(",") if a.parts else DEFAULT_PARTS

    staging = os.path.join(a.root, ".staging-" + now_stamp())
    os.makedirs(staging)
    identity = {}
    src = {}
    try:
        started = time.time()
        for i, part in enumerate(parts):
            out = os.path.join(staging, part + ".bin")
            label = "Backing up this unit's radio partitions… %s (%d/%d)" % (part, i + 1, len(parts))
            progress(label)
            if DEBUG:
                log(label)
            rc, text = run_edl(edl, ["r", part, out],
                               lambda pct, speed, label=label: progress(
                                   "%s  %s%s" % (label, pct, ("  " + speed) if speed else "")))
            if i == 0:
                identity = parse_sahara(text)
            if rc != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
                progress("Backing up this unit's radio partitions… %s failed" % part, done=True)
                edl_tail(text)
                warn("read of %s failed (edl rc=%d)" % (part, rc))
                return EXIT_ERR
            src[part] = out
        progress("Backing up this unit's radio partitions… %d/%d ✓ %ds"
                 % (len(parts), len(parts), time.time() - started), done=True)

        serial = a.serial or identity.get("serial")
        if not serial:
            warn("edl did not report the chip serial (the dongle was already in firehose "
                 "mode). Replug it into EDL and retry, or pass --serial 0x....")
            return EXIT_ERR
        if a.serial and identity.get("serial") and \
                normalize_serial(a.serial) != normalize_serial(identity["serial"]):
            warn("--serial %s does not match the connected chip %s" % (a.serial, identity["serial"]))
            return EXIT_MISMATCH

        dest, created = store(a.root, serial, src, identity, "edl-live", a.keep_duplicate)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    log("%s backup for unit %s: %s" % ("New" if created else "Existing",
                                       normalize_serial(serial), dest))
    if a.print_path:
        print(dest)
    return EXIT_OK


def cmd_import(a):
    src = {}
    for name in sorted(os.listdir(a.dir)):
        if name.endswith(".bin"):
            src[name[:-4]] = os.path.join(a.dir, name)
    missing = [p for p in RADIO_PARTS if p not in src]
    if missing:
        warn("%s lacks radio partitions: %s" % (a.dir, ", ".join(missing)))
        if not a.force:
            return EXIT_ERR
    dest, created = store(a.root, a.serial, src, {}, "import:" + os.path.abspath(a.dir),
                          a.keep_duplicate)
    log("%s: %s" % ("Imported" if created else "Already stored", dest))
    if a.print_path:
        print(dest)
    return EXIT_OK


def cmd_list(a):
    if not os.path.isdir(a.root):
        print("No backups yet (%s)." % a.root)
        return EXIT_OK
    units = sorted(d for d in os.listdir(a.root)
                   if os.path.isdir(os.path.join(a.root, d)) and not d.startswith("."))
    if not units:
        print("No backups yet (%s)." % a.root)
    for u in units:
        unit_dir = os.path.join(a.root, u)
        meta_path = os.path.join(unit_dir, "unit.json")
        meta = load_json(meta_path) if os.path.isfile(meta_path) else {}
        print("Unit %s  hwid=%s  last seen %s" % (u, meta.get("hwid", "?"), meta.get("last_seen", "?")))
        for b in list_backups(unit_dir):
            m = load_json(os.path.join(b, "manifest.json"))
            size = sum(f["size"] for f in m["files"].values())
            print("   %s  %3d files  %6.1f MB  %s" % (os.path.basename(b), len(m["files"]),
                                                   size / 1048576.0, m.get("source", "")))
    return EXIT_OK


def cmd_verify(a):
    mpath = os.path.join(a.dir, "manifest.json")
    if not os.path.isfile(mpath):
        warn("no manifest.json in %s" % a.dir)
        return EXIT_UNKNOWN
    m = load_json(mpath)
    bad = 0
    for name, meta in sorted(m["files"].items()):
        path = os.path.join(a.dir, name)
        if not os.path.isfile(path):
            warn("MISSING  %s" % name)
            bad += 1
        elif sha256_file(path) != meta["sha256"]:
            warn("CORRUPT  %s" % name)
            bad += 1
    if bad:
        warn("%d file(s) failed verification" % bad)
        return EXIT_ERR
    log("OK: %d files match the manifest (unit %s)" % (len(m["files"]), m["serial"]))
    return EXIT_OK


def manifest_serial(path):
    mpath = os.path.join(path, "manifest.json")
    if not os.path.isfile(mpath):
        return None
    return normalize_serial(load_json(mpath)["serial"])


def cmd_check(a):
    """Exit 0 when DIR belongs to the same unit as AGAINST, 2 if not, 3 unknown."""
    want = normalize_serial(a.serial) if a.serial else manifest_serial(a.against)
    have = manifest_serial(a.dir)
    if want is None:
        warn("cannot tell which unit is connected")
        return EXIT_UNKNOWN
    if have is None:
        warn("%s has no manifest — its origin unit is unknown" % a.dir)
        return EXIT_UNKNOWN
    if have != want:
        warn("%s belongs to unit %s, but the connected unit is %s" % (a.dir, have, want))
        return EXIT_MISMATCH
    log("Backup matches the connected unit %s" % want)
    return EXIT_OK


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", default=DEFAULT_ROOT, help="backup store (default: %(default)s)")
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    b = sub.add_parser("backup", help="read partitions over EDL and store them")
    b.add_argument("--edl", help="edl executable (default: $EDL_BIN or edl on PATH)")
    b.add_argument("--serial", help="chip serial, if edl cannot report it")
    b.add_argument("--parts", help="comma-separated partitions (default: radio + boot chain)")
    b.add_argument("--keep-duplicate", action="store_true", help="store even if unchanged")
    b.add_argument("--print-path", action="store_true", help="print the backup folder on stdout")
    b.set_defaults(fn=cmd_backup)

    i = sub.add_parser("import", help="file an existing folder of .bin images")
    i.add_argument("dir")
    i.add_argument("--serial", required=True)
    i.add_argument("--force", action="store_true", help="import even without all radio parts")
    i.add_argument("--keep-duplicate", action="store_true")
    i.add_argument("--print-path", action="store_true")
    i.set_defaults(fn=cmd_import)

    sub.add_parser("list", help="show stored units and backups").set_defaults(fn=cmd_list)

    v = sub.add_parser("verify", help="re-hash a backup against its manifest")
    v.add_argument("dir")
    v.set_defaults(fn=cmd_verify)

    c = sub.add_parser("check", help="does a backup belong to the connected unit?")
    c.add_argument("dir", help="backup to be restored")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--against", help="a backup taken from the connected unit")
    g.add_argument("--serial", help="the connected unit's serial")
    c.set_defaults(fn=cmd_check)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
        warn(str(e))
        return EXIT_ERR


if __name__ == "__main__":
    sys.exit(main())
