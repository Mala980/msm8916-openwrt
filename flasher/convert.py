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
