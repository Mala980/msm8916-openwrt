"""Convert stock UZ801 dongles to OpenWrt, one after another.

Per unit only what is unique to it is backed up (see radio_backup.UNIQUE_PARTS);
everything else comes from one "golden" full stock dump per stock firmware
version. Design: docs/superpowers/specs/2026-11-23-flasher-convert-design.md
"""

from __future__ import annotations

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
