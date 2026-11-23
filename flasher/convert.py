"""Convert stock UZ801 dongles to OpenWrt, one after another.

Per unit only what is unique to it is backed up (see radio_backup.UNIQUE_PARTS);
everything else comes from one "golden" full stock dump per stock firmware
version. Design: docs/superpowers/specs/2026-11-23-flasher-convert-design.md
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

STOCK_VERSION_FILE = "STOCK_VERSION"


def mark_golden(stock_dir: Path, version: str) -> None:
    """Tag a full stock dump with the stock firmware version it came from."""
    (Path(stock_dir) / STOCK_VERSION_FILE).write_text(version.strip() + "\n")


def find_golden(backup_root: Path, version: str) -> Optional[Path]:
    """The newest complete full stock dump (backup/stock-*) for `version`."""
    version = (version or "").strip()
    if not version:
        return None
    found = []
    for d in Path(backup_root).glob("stock-*"):
        tag = d / STOCK_VERSION_FILE
        if (d / "restore_sectors.txt").is_file() and tag.is_file() \
                and tag.read_text().strip() == version:
            found.append(d)
    return sorted(found, key=lambda p: p.name)[-1] if found else None


def parse_iphonesubinfo(text: str) -> str:
    """IMEI from `service call iphonesubinfo 1` (Android 4.4): the digits of
    the UTF-16 string shown in the Parcel dump's quoted text columns."""
    quoted = "".join(re.findall(r"'([^']*)'", text or ""))
    digits = re.sub(r"[^0-9]", "", quoted)
    return digits if len(digits) == 15 else ""


def _adb_shell(adb: str, command: str) -> str:
    try:
        res = subprocess.run([adb, "shell", command], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout if res.returncode == 0 else ""


def read_stock_identity(adb: str) -> dict:
    """Identity of a stock dongle on ADB; fields that cannot be read are ""."""
    def prop(name: str) -> str:
        return _adb_shell(adb, "getprop " + name).strip()

    return {
        "model": prop("ro.product.model"),
        "version": prop("ro.build.display.id"),
        "imei": parse_iphonesubinfo(_adb_shell(adb, "service call iphonesubinfo 1")),
        "wifi_mac": _adb_shell(adb, "cat /sys/class/net/wlan0/address").strip(),
        "android_serial": prop("ro.serialno"),
        "baseband": prop("gsm.version.baseband"),
    }


# ---------------------------------------------------------------------------
# Convert loop

import json  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402
import tempfile  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Callable, List, Tuple  # noqa: E402

# Written as "radio" during an OpenWrt flash; `modem` comes from the golden dump.
FLASH_RADIO_PARTS = ["fsc", "fsg", "modemst1", "modemst2", "modem", "persist", "sec"]
OPENWRT_BOOT_TIMEOUT = 180


@dataclass
class ConvertEnv:
    adb: str
    edl: str
    python: str
    flasher_dir: Path
    backup_root: Path
    firmware_dir: Path
    adb_devices: Callable[[], List[str]]
    openwrt_online: Callable[[], bool]
    read_identity: Callable[[], dict]
    run: Callable[[List[str]], int]
    run_capture: Callable[[List[str]], Tuple[int, str]]
    verify_openwrt: Callable[[str], dict]
    sleep: Callable[[float], None]
    out: Callable[..., None] = print
    max_units: Optional[int] = None


@dataclass
class UnitResult:
    ok: bool
    verdict: str
    serial: str = ""
    imei: str = ""
    backup_dir: str = ""


def _fail(verdict: str, backup_dir: str = "", imei: str = "") -> UnitResult:
    serial = Path(backup_dir).parent.name if backup_dir else ""
    return UnitResult(False, verdict, serial, imei, backup_dir)


def _wait_for_stock_dongle(env: ConvertEnv) -> Optional[UnitResult]:
    """Block until one stock dongle is on ADB. A dongle that already runs
    OpenWrt is refused (returned as a failed UnitResult)."""
    while True:
        if env.adb_devices():
            return None
        if env.openwrt_online():
            return _fail("refused: the plugged dongle already runs OpenWrt (192.168.8.1); "
                         "unplug it — convert mode only takes stock dongles")
        env.sleep(2)


def _radio_set(unit_dir: Path, golden: Path) -> Path:
    """Folder with the 7 partitions an OpenWrt flash writes as radio data:
    the unit's own files plus the golden dump's (generic) modem.bin."""
    out = Path(tempfile.mkdtemp(prefix="convert-radio-"))
    for p in FLASH_RADIO_PARTS:
        src = (golden if p == "modem" else unit_dir) / (p + ".bin")
        os.symlink(str(src.resolve()), str(out / (p + ".bin")))
    return out


def convert_one(env: ConvertEnv) -> UnitResult:
    refused = _wait_for_stock_dongle(env)
    if refused:
        return refused

    ident = env.read_identity()
    version, imei = ident.get("version", ""), ident.get("imei", "")
    env.out("[*] Stock dongle: %s %s, IMEI %s" % (ident.get("model", "?"), version or "(unknown version)",
                                                  imei or "(unreadable)"))

    golden = find_golden(env.backup_root, version)
    if golden is None:
        env.out("[*] No golden dump for stock %s yet: taking a full stock dump of this unit"
                % (version or "(unknown version)"))
        if env.run(["bash", str(env.flasher_dir / "backup-stock.sh")]) != 0:
            return _fail("full stock dump failed — nothing was written", imei=imei)
        dumps = sorted(Path(env.backup_root).glob("stock-*"), key=lambda p: p.name)
        if not dumps:
            return _fail("full stock dump produced no folder — nothing was written", imei=imei)
        golden = dumps[-1]
        if version:
            mark_golden(golden, version)
        refused = _wait_for_stock_dongle(env)  # backup-stock.sh reboots it to stock
        if refused:
            return refused

    env.run([env.adb, "reboot", "edl"])

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(ident, f, indent=2, sort_keys=True)
        ident_file = f.name
    rc, outp = env.run_capture([env.python, "-I", str(env.flasher_dir / "radio_backup.py"),
                                "--root", str(Path(env.backup_root) / "units"), "backup",
                                "--edl", env.edl, "--parts", "unique", "--identity", ident_file,
                                "--print-path"])
    os.unlink(ident_file)
    lines = [l for l in outp.splitlines() if l.strip()]
    unit_dir = lines[-1].strip() if lines else ""
    if rc != 0 or not unit_dir or not Path(unit_dir).is_dir():
        return _fail("unique-partition backup failed (radio_backup exit %d) — nothing was written" % rc,
                     imei=imei)
    env.out("[*] Unit backup: %s" % unit_dir)

    radio = _radio_set(Path(unit_dir), golden)
    rc = env.run(["bash", str(env.flasher_dir / "flash-firmware.sh"), "--firmware", str(env.firmware_dir),
                  "--radio-from", str(radio), "--skip-live-backup", "--yes"])
    if rc != 0:
        return _fail("flash failed (exit %d); this unit's backup: %s" % (rc, unit_dir), unit_dir, imei)

    waited = 0
    while not env.openwrt_online():
        if waited >= OPENWRT_BOOT_TIMEOUT:
            return _fail("flashed, but OpenWrt did not come up on 192.168.8.1 within %ds; backup: %s"
                         % (OPENWRT_BOOT_TIMEOUT, unit_dir), unit_dir, imei)
        env.sleep(3)
        waited += 3

    v = env.verify_openwrt(imei)
    problems = []
    if not imei:
        problems.append("IMEI not verified (unreadable on stock)")
    elif v.get("imei") != imei:
        problems.append("IMEI mismatch: stock %s, OpenWrt %s" % (imei, v.get("imei") or "none"))
    if not v.get("wifi_up"):
        problems.append("Wi-Fi down")
    if not v.get("modem_present"):
        problems.append("modem missing")
    serial = Path(unit_dir).parent.name
    if problems:
        return UnitResult(False, "; ".join(problems) + "; backup: " + unit_dir, serial, imei, unit_dir)
    return UnitResult(True, "IMEI %s ✓ · Wi-Fi up · modem present" % imei, serial, imei, unit_dir)


def convert_loop(env: ConvertEnv) -> int:
    done = 0
    env.out("[*] Convert mode: plug in a stock dongle (Ctrl-C to stop).")
    while env.max_units is None or done < env.max_units:
        r = convert_one(env)
        if not r.ok and r.verdict.startswith("refused"):
            env.out("[!] " + r.verdict)
            while env.openwrt_online():
                env.sleep(2)
            continue
        if not r.ok:
            env.out("[-] %s" % r.verdict)
            env.out("[-] Stopped after %d converted dongle(s)." % done)
            return 1
        done += 1
        env.out("[✓] #%d unit %s: %s" % (done, r.serial, r.verdict))
        env.out("[*] Done — unplug it and plug in the next stock dongle.")
        while env.openwrt_online():
            env.sleep(2)
    return 0


# ---------------------------------------------------------------------------
# Real environment (adb, edl, ssh on this computer)

OPENWRT_HOST = "192.168.8.1"
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "LogLevel=ERROR"]

# One ssh call; prints KEY=value lines for parse_verify().
VERIFY_SCRIPT = (
    "for i in $(seq 1 30); do mmcli -L 2>/dev/null | grep -q Modem/ && break; sleep 3; done; "
    "for i in $(seq 1 20); do [ \"$(wifi status 2>/dev/null | jsonfilter -e @.radio0.up)\" = true ] && break; sleep 3; done; "
    "echo IMEI=$(qmicli -p -d /dev/wwan0qmi0 --dms-get-ids 2>/dev/null | sed -n \"s/.*IMEI: '\\([0-9]*\\)'.*/\\1/p\"); "
    "echo WIFI=$([ \"$(wifi status 2>/dev/null | jsonfilter -e @.radio0.up)\" = true ] && echo 1 || echo 0); "
    "echo MODEM=$(mmcli -L 2>/dev/null | grep -o '/org/freedesktop/ModemManager1/Modem/[0-9]*' | head -n 1)"
)


def parse_adb_devices(text: str) -> List[str]:
    """Serials of devices in state "device" from `adb devices`."""
    out = []
    for line in (text or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def parse_verify(text: str) -> dict:
    kv = dict(l.split("=", 1) for l in (text or "").splitlines() if "=" in l)
    return {"imei": kv.get("IMEI", "").strip(),
            "wifi_up": kv.get("WIFI", "").strip() == "1",
            "modem_present": bool(kv.get("MODEM", "").strip())}


def real_env(flasher_dir: Path, adb: str, edl: str) -> ConvertEnv:
    import sys
    import time

    def adb_devices() -> List[str]:
        try:
            res = subprocess.run([adb, "devices"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 universal_newlines=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return []
        return parse_adb_devices(res.stdout)

    def openwrt_online() -> bool:
        try:
            with socket.create_connection((OPENWRT_HOST, 22), timeout=1.5):
                return True
        except OSError:
            return False

    def run(cmd: List[str]) -> int:
        return subprocess.run(cmd, cwd=str(flasher_dir)).returncode

    def run_capture(cmd: List[str]) -> Tuple[int, str]:
        # stdout is machine-readable (the backup path); progress stays on stderr.
        res = subprocess.run(cmd, cwd=str(flasher_dir), stdout=subprocess.PIPE, universal_newlines=True)
        return res.returncode, res.stdout

    def verify_openwrt(_imei: str) -> dict:
        try:
            res = subprocess.run(["ssh"] + SSH_OPTS + ["root@" + OPENWRT_HOST, VERIFY_SCRIPT],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 universal_newlines=True, timeout=240)
        except (OSError, subprocess.TimeoutExpired):
            return parse_verify("")
        return parse_verify(res.stdout)

    return ConvertEnv(
        adb=adb, edl=edl, python=sys.executable, flasher_dir=Path(flasher_dir),
        backup_root=Path(flasher_dir) / "backup", firmware_dir=Path(flasher_dir) / "firmware" / "openwrt-stable",
        adb_devices=adb_devices, openwrt_online=openwrt_online,
        read_identity=lambda: read_stock_identity(adb), run=run, run_capture=run_capture,
        verify_openwrt=verify_openwrt, sleep=time.sleep)


# ---------------------------------------------------------------------------
# Restore to stock from a golden dump + a unit's unique partitions

from radio_backup import UNIQUE_PARTS  # noqa: E402  (single source of truth)


def assemble_stock_restore(unit_backup: Path, backup_root: Path, out_dir: Path) -> Path:
    """A folder restore-stock.sh can use: every file of the golden dump for the
    unit's stock version, with the unit's own unique partitions in place of the
    golden ones. Raises ValueError (before anything touches a device) when the
    unit has no identity.json/version or there is no golden dump for it."""
    unit_backup, out_dir = Path(unit_backup), Path(out_dir)
    ident_file = unit_backup / "identity.json"
    if not ident_file.is_file():
        raise ValueError("%s has no identity.json (not a convert-mode backup)" % unit_backup)
    version = json.loads(ident_file.read_text()).get("version", "")
    golden = find_golden(backup_root, version)
    if golden is None:
        raise ValueError("no golden stock dump for version %r — cannot rebuild the stock image"
                         % (version or "unknown"))
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in golden.iterdir():
        if f.is_file():
            os.symlink(str(f.resolve()), str(out_dir / f.name))
    for p in UNIQUE_PARTS:
        src = unit_backup / (p + ".bin")
        if src.is_file():
            dst = out_dir / (p + ".bin")
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            os.symlink(str(src.resolve()), str(dst))
    return out_dir
