#!/usr/bin/env python3
"""
All-in-One MSM8916 4G Dongle Flasher & Recovery Toolkit.
Unified Python 3 launcher for OpenWrt flashing, stock/full dump backup & restore,
per-unit radio backup/verify, and EDL/SSH utilities.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

FLASHER_DIR = Path(__file__).resolve().parent

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_CYAN = "\033[1;36m"
C_GREEN = "\033[1;32m"
C_YELLOW = "\033[1;33m"
C_RED = "\033[1;31m"
C_DIM = "\033[2m"

RADIO_ONLY_PARTS = ["fsc", "fsg", "modemst1", "modemst2", "modem", "persist", "sec"]


def run_cmd(cmd: list[str], cwd: Path = FLASHER_DIR) -> int:
    """Run a command interactively, streaming stdio directly to the terminal."""
    try:
        proc = subprocess.run(cmd, cwd=str(cwd))
        return proc.returncode
    except KeyboardInterrupt:
        print(f"\n{C_YELLOW}[!] Interrupted by user.{C_RESET}")
        return 130


def check_tcp(host: str, port: int, timeout: float = 0.6) -> bool:
    """Check if a TCP port is reachable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_adb() -> str | None:
    """Locate adb binary from PATH or ./temp/platform-tools/adb."""
    adb = shutil.which("adb")
    if adb:
        return adb
    local_adb = FLASHER_DIR / "temp" / "platform-tools" / "adb"
    return str(local_adb) if local_adb.is_file() else None


def find_edl() -> str | None:
    """Locate edl binary from EDL_BIN, PATH, or ./temp/edl/venv/bin/edl."""
    env_edl = os.environ.get("EDL_BIN")
    if env_edl and Path(env_edl).is_file():
        return env_edl
    sys_edl = shutil.which("edl")
    if sys_edl:
        return sys_edl
    local_edl = FLASHER_DIR / "temp" / "edl" / "venv" / "bin" / "edl"
    return str(local_edl) if local_edl.is_file() else None


def detect_edl() -> bool:
    """Detect Qualcomm EDL 9008 mode on macOS or Linux."""
    system = platform.system()
    if system == "Darwin":
        res = subprocess.run(
            ["ioreg", "-p", "IOUSB", "-l", "-w0"],
            capture_output=True,
            text=True,
        )
        out = res.stdout
        # ioreg prints USB ids in decimal: 05c6:9008 is idVendor 1478 / idProduct 36872
        if "QHSUSB" in out or "QDLoader" in out:
            return True
        return '"idVendor" = 1478' in out and '"idProduct" = 36872' in out
    if system == "Linux" and shutil.which("lsusb"):
        res = subprocess.run(["lsusb"], capture_output=True, text=True)
        lower = res.stdout.lower()
        return "05c6:9008" in lower or "qdloader" in lower or "qhsusb" in lower
    return False


def detect_adb() -> bool:
    """Check if an ADB device is attached."""
    adb = find_adb()
    if not adb:
        return False
    res = subprocess.run([adb, "devices"], capture_output=True, text=True)
    for line in res.stdout.splitlines()[1:]:
        if line.strip().endswith("\tdevice"):
            return True
    return False


def get_device_status() -> str:
    """Return a concise human-readable connection status string."""
    if detect_edl():
        return f"{C_YELLOW}EDL Mode (05c6:9008){C_RESET}"
    if check_tcp("192.168.8.1", 22):
        return f"{C_GREEN}OpenWrt Online (192.168.8.1){C_RESET}"
    if check_tcp("192.168.1.1", 22):
        return f"{C_GREEN}OpenWrt Online (192.168.1.1){C_RESET}"
    if detect_adb():
        return f"{C_CYAN}Android ADB Connected{C_RESET}"
    return f"{C_DIM}No device detected (plug dongle or check USB){C_RESET}"


def wait_for_edl(timeout_s: int = 40) -> bool:
    """Wait until EDL 05c6:9008 enumerates on USB."""
    print(f"{C_GREEN}[*] Waiting for EDL device (05c6:9008)...{C_RESET}")
    for _ in range(timeout_s):
        if detect_edl():
            print(f"{C_GREEN}[*] EDL device detected.{C_RESET}")
            return True
        time.sleep(1)
    print(f"{C_RED}[-] Timed out waiting for EDL 05c6:9008.{C_RESET}")
    return False


def reboot_to_edl() -> int:
    """Attempt to reboot an attached OpenWrt or ADB dongle into EDL 9008."""
    if detect_edl():
        print(f"{C_YELLOW}[!] Dongle is already in EDL mode (05c6:9008).{C_RESET}")
        return 0
    for host in ("192.168.8.1", "192.168.1.1"):
        if check_tcp(host, 22):
            print(f"{C_GREEN}[*] Sending reboot-edl via SSH ({host})...{C_RESET}")
            # reboot-edl tears the network down mid-session, so a foreground
            # ssh never gets a clean close and hangs until TCP times out.
            # Detach the remote command and let keepalives end the session.
            try:
                subprocess.run(
                    [
                        "ssh",
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null",
                        "-o", "BatchMode=yes",
                        "-o", "ConnectTimeout=5",
                        "-o", "ServerAliveInterval=1",
                        "-o", "ServerAliveCountMax=2",
                        f"root@{host}",
                        "reboot-edl >/dev/null 2>&1 &",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                )
            except subprocess.TimeoutExpired:
                pass  # the link dropped as the dongle rebooted; wait for 9008 below
            return 0 if wait_for_edl() else 1
    if detect_adb():
        adb = find_adb()
        if adb:
            print(f"{C_GREEN}[*] Rebooting to EDL via ADB...{C_RESET}")
            subprocess.run([adb, "reboot", "edl"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return 0 if wait_for_edl() else 1
    print(f"{C_RED}[-] No SSH or ADB device found. Short D+ to GND while plugging USB.{C_RESET}")
    return 1


def wait_for_dongle(timeout_s: int = 90) -> bool:
    """Wait for the dongle to show up in any mode (it may still be booting)."""
    def present() -> bool:
        return (detect_edl() or check_tcp("192.168.8.1", 22) or check_tcp("192.168.1.1", 22)
                or detect_adb())
    if present():
        return True
    print(f"{C_GREEN}[*] Waiting for the dongle (booting?) — up to {timeout_s}s...{C_RESET}")
    end = time.time() + timeout_s
    while time.time() < end:
        time.sleep(3)
        if present():
            time.sleep(2)  # let SSH finish starting
            return True
    return False


RADIO_BACKUP = FLASHER_DIR / "radio_backup.py"
UNITS_DIR = FLASHER_DIR / "backup" / "units"
EXIT_NOSERIAL = 4  # radio_backup.py: dongle still in firehose, chip serial unknown


def debug_mode() -> bool:
    return os.environ.get("FLASH_DEBUG") == "1"


def edl_step(edl: str, label: str, *args: str) -> int:
    """Run one edl command as a step, minimal-verbose (same renderer as
    flash-firmware.sh: radio_backup.py view). Raw output in debug mode."""
    start = time.time()
    print(f"{C_GREEN}[*]{C_RESET} {label}")
    try:
        if debug_mode():
            rc = subprocess.run([edl, *args]).returncode
        else:
            edl_proc = subprocess.Popen([edl, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            subprocess.run([sys.executable, "-I", str(RADIO_BACKUP), "view"], stdin=edl_proc.stdout)
            edl_proc.stdout.close()
            rc = edl_proc.wait()
    except KeyboardInterrupt:
        print(f"\n{C_YELLOW}[!] Interrupted by user.{C_RESET}")
        return 130
    if rc == 0:
        print(f"      {C_GREEN}✓{C_RESET} {time.time() - start:.0f}s")
    else:
        print(f"      {C_RED}✗ failed (edl exit {rc}){C_RESET} — run in debug mode for edl's full output")
    return rc


def ensure_edl_session() -> str | None:
    """edl binary + dongle in EDL mode, or None (with the reason printed)."""
    edl = find_edl()
    if not edl:
        print(f"{C_RED}[-] edl not found. Run 'Flash OpenWrt' once to install it into ./temp/edl.{C_RESET}")
        return None
    if not detect_edl():
        if not wait_for_dongle():
            print(f"{C_RED}[-] No dongle found. Plug it in, or short D+ to GND while plugging it "
                  f"to force EDL.{C_RESET}")
            return None
        if reboot_to_edl() != 0:
            return None
    return edl


def live_backup(edl: str, parts: list[str] | None = None) -> tuple[int, str]:
    """Back up the connected unit (filed by chip serial). Returns (rc, folder)."""
    cmd = [sys.executable, "-I", str(RADIO_BACKUP), "backup", "--edl", edl, "--print-path"]
    if parts:
        cmd.extend(["--parts", ",".join(parts)])
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    except KeyboardInterrupt:
        return 130, ""
    return res.returncode, res.stdout.strip()


def handle_firehose_leftover(edl: str) -> None:
    print(f"{C_YELLOW}[!] The dongle is still in firehose mode from an earlier, interrupted run, so it no "
          f"longer reports its chip serial. Nothing was written.{C_RESET}")
    edl_step(edl, "Rebooting the dongle back to its OS", "reset")
    print(f"{C_YELLOW}[!] Wait ~1 minute for it to boot, then run this again.{C_RESET}")


def run_radio_backup(radio_only: bool = False) -> int:
    """Back up the connected unit: radio + boot chain (12) or radio only (7)."""
    edl = ensure_edl_session()
    if not edl:
        return 1
    rc, folder = live_backup(edl, RADIO_ONLY_PARTS if radio_only else None)
    if rc == EXIT_NOSERIAL:
        handle_firehose_leftover(edl)
        return rc
    if rc != 0:
        print(f"{C_RED}[-] Backup failed.{C_RESET}")
        return rc
    try:
        ans = input("\nReboot the dongle back to its OS now? (Y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    if ans in ("", "y", "yes"):
        edl_step(edl, "Rebooting the dongle", "reset")
    return 0


def radio_backups() -> list[Path]:
    """Per-unit backup folders holding all radio partitions, newest first."""
    found = []
    if UNITS_DIR.is_dir():
        for manifest in UNITS_DIR.glob("*/*/manifest.json"):
            d = manifest.parent
            if all((d / f"{p}.bin").is_file() for p in RADIO_ONLY_PARTS):
                found.append(d)
    return sorted(found, key=lambda d: d.name, reverse=True)


def restore_radio_only() -> int:
    """Write the 7 radio/IMEI partitions from a per-unit backup to the
    connected dongle. The rest of the flash (firmware, settings) is untouched.

    The connected unit is backed up first (that also reads its chip serial);
    a backup from another unit clones that unit's IMEI and MAC addresses and
    needs an explicit YES."""
    if not radio_backups():
        print(f"{C_RED}[-] No per-unit radio backups found in {UNITS_DIR.relative_to(FLASHER_DIR)}.{C_RESET}")
        return 1

    print(f"\n{C_CYAN}=== Restore Radio Only ({', '.join(RADIO_ONLY_PARTS)}) ==={C_RESET}")
    print("  Writes IMEI / RF data from a backup. Firmware and settings are not touched.")
    print("  The connected dongle is backed up first, which also tells which unit it is.\n")

    edl = ensure_edl_session()
    if not edl:
        return 1

    # Safety copy of what is on the dongle now; also tells us which unit it is.
    rc, live = live_backup(edl)
    if rc == EXIT_NOSERIAL:
        handle_firehose_leftover(edl)
        return rc
    if rc != 0 or not live:
        print(f"{C_RED}[-] Could not back up the connected unit — nothing written.{C_RESET}")
        return 1
    this_unit = Path(live).parent.name

    backups = radio_backups()
    print(f"\n  Connected dongle: unit {C_BOLD}{this_unit}{C_RESET}\n")
    for i, d in enumerate(backups, 1):
        mark = ""
        if d.parent.name == this_unit:
            mark = f"  {C_GREEN}← this dongle{C_RESET}" + ("  (just taken)" if str(d) == live else "")
        print(f"  {i:2d}) unit {d.parent.name}   {d.name}{mark}")
    try:
        sel = input(f"\nSelect backup to restore [1-{len(backups)}] (Enter = cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        sel = ""
    if not sel.isdigit() or not 1 <= int(sel) <= len(backups):
        print("Cancelled — nothing written.")
        edl_step(edl, "Rebooting the dongle", "reset")
        return 0
    chosen = backups[int(sel) - 1]

    check = subprocess.run([sys.executable, "-I", str(RADIO_BACKUP), "check", str(chosen), "--against", live])
    if check.returncode != 0:
        print(f"\n{C_YELLOW}[!] {chosen.parent.name} is NOT this dongle. Its radio data is tied to that unit's "
              f"chip: on this dongle the IMEI reads back EMPTY and the modem cannot register until an IMEI "
              f"is written (Modem > Change IMEI, or change-imei <IMEI>).{C_RESET}")
        print(f"{C_YELLOW}    Its RF calibration and Wi-Fi/BT MAC also come from {chosen.parent.name}.{C_RESET}")
        print(f"    To undo later, restore this dongle's own backup: {Path(live).relative_to(FLASHER_DIR)}")
        try:
            ans = input("Type YES (capital letters) to restore it anyway: ").strip()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans != "YES":
            print("Cancelled — nothing written.")
            edl_step(edl, "Rebooting the dongle", "reset")
            return 0
    else:
        print(f"{C_GREEN}[*]{C_RESET} Backup belongs to this dongle.")

    for part in RADIO_ONLY_PARTS:
        if edl_step(edl, f"Restoring {part}", "w", part, str(chosen / f"{part}.bin")) != 0:
            print(f"{C_RED}[-] Restore of {part} failed. The dongle's previous radio data is in "
                  f"{Path(live).relative_to(FLASHER_DIR)}.{C_RESET}")
            return 1
    edl_step(edl, "Rebooting the dongle", "reset")
    print(f"\n{C_GREEN}[*] Radio data restored from {chosen.relative_to(FLASHER_DIR)}.{C_RESET}")
    if chosen.parent.name != this_unit:
        print(f"{C_YELLOW}[!] Another unit's radio data: write this dongle's IMEI (from its label) once it has "
              f"booted, e.g. ssh root@192.168.8.1 change-imei <15-digit IMEI>.{C_RESET}")
    return 0


def list_all_backups() -> int:
    """List both per-unit radio backups and full stock dumps."""
    print(f"\n{C_CYAN}=== Per-Unit Radio Backups (backup/units/) ==={C_RESET}")
    rc = run_cmd([sys.executable, str(FLASHER_DIR / "radio_backup.py"), "list"])

    print(f"\n{C_CYAN}=== Full Stock Dumps (backup/stock-*) ==={C_RESET}")
    stock_dirs = sorted((FLASHER_DIR / "backup").glob("stock-*")) if (FLASHER_DIR / "backup").is_dir() else []
    if not stock_dirs:
        print(f"  {C_DIM}(none){C_RESET}")
    else:
        for d in stock_dirs:
            if d.is_dir():
                bins = len(list(d.glob("*.bin")))
                has_sectors = (d / "restore_sectors.txt").is_file()
                tag = "valid" if has_sectors else "incomplete"
                print(f"  - {d.relative_to(FLASHER_DIR)}  ({bins} .bin files, {tag})")
    return rc


def run_convert() -> int:
    """Convert stock dongles to OpenWrt one after another (see convert.py)."""
    import convert

    adb, edl = find_adb(), find_edl()
    if not adb:
        print(f"{C_RED}[-] adb not found (needed to read stock dongles). Install it or run 'Flash OpenWrt' once.{C_RESET}")
        return 1
    if not edl:
        print(f"{C_RED}[-] edl not found. Run 'Flash OpenWrt' once to install it into ./temp/edl.{C_RESET}")
        return 1
    print(f"{C_CYAN}=== Convert stock dongles → OpenWrt ==={C_RESET}")
    print("  Per dongle: back up its unique partitions (IMEI, calibration, MACs), flash OpenWrt,")
    print("  verify IMEI + Wi-Fi, then wait for the next one. Plug in ONE dongle at a time.")
    try:
        return convert.convert_loop(convert.real_env(FLASHER_DIR, adb, edl))
    except KeyboardInterrupt:
        print(f"\n{C_YELLOW}[!] Convert mode stopped.{C_RESET}")
        return 130


def restore_stock() -> int:
    """Restore to stock from a full dump, or from a golden dump + a unit's
    unique-partition backup (convert mode keeps only the latter per unit)."""
    import tempfile
    import convert

    backup_root = FLASHER_DIR / "backup"
    choices = []
    for d in sorted(backup_root.glob("stock-*"), reverse=True):
        if (d / "restore_sectors.txt").is_file():
            tag = (d / "STOCK_VERSION").read_text().strip() if (d / "STOCK_VERSION").is_file() else "?"
            choices.append(("full", d, "full stock dump %s (stock %s)" % (d.name, tag)))
    for ident in sorted(UNITS_DIR.glob("*/*/identity.json"), reverse=True):
        d = ident.parent
        choices.append(("unit", d, "unit %s %s (unique partitions + golden dump)" % (d.parent.name, d.name)))
    if not choices:
        print(f"{C_RED}[-] No stock dumps or convert-mode unit backups found.{C_RESET}")
        return 1
    print(f"\n{C_CYAN}=== Restore stock firmware ==={C_RESET}")
    for i, (_, _, label) in enumerate(choices, 1):
        print(f"  {i:2d}) {label}")
    try:
        sel = input(f"\nSelect [1-{len(choices)}] (Enter = cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return 0
    if not sel.isdigit() or not 1 <= int(sel) <= len(choices):
        print("Cancelled.")
        return 0
    kind, d, _ = choices[int(sel) - 1]
    if kind == "full":
        return run_script("restore-stock.sh", [str(d)])
    try:
        combined = convert.assemble_stock_restore(d, backup_root, Path(tempfile.mkdtemp(prefix="stock-restore-")))
    except ValueError as e:
        print(f"{C_RED}[-] {e}{C_RESET}")
        return 1
    print(f"{C_GREEN}[*]{C_RESET} Golden dump + unit {d.parent.name}'s own partitions assembled in {combined}")
    return run_script("restore-stock.sh", [str(combined)])


def open_ssh() -> int:
    """Open an interactive SSH session to the modem."""
    for host in ("192.168.8.1", "192.168.1.1"):
        if check_tcp(host, 22):
            print(f"{C_GREEN}[*] Connecting to root@{host}...{C_RESET}")
            return run_cmd(["ssh", f"root@{host}"])
    print(f"{C_RED}[-] Modem SSH not reachable on 192.168.8.1 or 192.168.1.1.{C_RESET}")
    return 1


def run_script(script_name: str, extra_args: list[str] | None = None) -> int:
    """Execute one of the flasher shell scripts."""
    script = FLASHER_DIR / script_name
    if not script.is_file():
        print(f"{C_RED}[-] Missing script: {script}{C_RESET}")
        return 1
    cmd = ["bash", str(script)]
    if extra_args:
        cmd.extend(extra_args)
    return run_cmd(cmd)


def print_banner() -> None:
    print()
    print(f"{C_CYAN}============================================================{C_RESET}")
    print(f"{C_BOLD}   MSM8916 4G Dongle — All-in-One Flasher & Recovery{C_RESET}")
    print(f"{C_CYAN}============================================================{C_RESET}")
    print(f"  Status : {get_device_status()}")
    print(f"  Workdir: {C_DIM}{FLASHER_DIR}{C_RESET}")
    print(f"{C_CYAN}------------------------------------------------------------{C_RESET}")


def interactive_menu() -> int:
    while True:
        print_banner()
        dbg = f"{C_YELLOW}ON{C_RESET}" if debug_mode() else f"{C_DIM}off{C_RESET}"
        print(f"  {C_BOLD}Flash{C_RESET}")
        print("    1) Flash OpenWrt firmware")
        print("    c) Convert stock dongles → OpenWrt (batch: backup + flash + verify, one after another)")
        print(f"  {C_BOLD}Backup{C_RESET}")
        print("    2) Back up radio + boot chain    (12 partitions, per unit)")
        print("    3) Back up radio only            (7 IMEI/RF partitions, per unit)")
        print("    4) Full stock dump               (all partitions + GPT)")
        print("    5) List backups")
        print(f"  {C_BOLD}Restore{C_RESET}")
        print("    6) Restore radio only            (IMEI/RF from a backup; firmware untouched)")
        print("    7) Restore stock firmware        (from a full stock dump)")
        print(f"  {C_BOLD}Tools{C_RESET}")
        print("    8) Reboot dongle to EDL mode (9008)")
        print("    9) SSH console (root@192.168.8.1)")
        print(f"    d) Debug output: {dbg}   (show edl's raw output)")
        print("    0) Exit")
        print()

        try:
            choice = input("Select [0-9, c, d]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice == "1":
            run_script("flash-firmware.sh", ["--debug"] if debug_mode() else None)
        elif choice == "c":
            run_convert()
        elif choice == "2":
            run_radio_backup(radio_only=False)
        elif choice == "3":
            run_radio_backup(radio_only=True)
        elif choice == "4":
            run_script("backup-stock.sh")
        elif choice == "5":
            list_all_backups()
        elif choice == "6":
            restore_radio_only()
        elif choice == "7":
            restore_stock()
        elif choice == "8":
            reboot_to_edl()
        elif choice == "9":
            open_ssh()
        elif choice == "d":
            os.environ["FLASH_DEBUG"] = "0" if debug_mode() else "1"
            continue
        elif choice == "0":
            return 0
        else:
            print(f"{C_YELLOW}[!] Invalid choice: {choice}{C_RESET}")

        try:
            input(f"\n{C_DIM}Press Enter to return to menu...{C_RESET}")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="All-in-One MSM8916 4G Dongle Flasher & Recovery Toolkit"
    )
    parser.add_argument("--flash", action="store_true", help="Run OpenWrt firmware flasher")
    parser.add_argument("--convert", action="store_true",
                        help="Convert stock dongles to OpenWrt one after another (backup + flash + verify)")
    parser.add_argument("--debug", action="store_true", help="Show edl's raw output (any action)")
    parser.add_argument("--backup-radio", action="store_true", help="Backup radio + boot chain (12 partitions)")
    parser.add_argument("--backup-radio-only", action="store_true", help="Backup radio/IMEI partitions only (7 partitions)")
    parser.add_argument("--backup-stock", action="store_true", help="Run full stock partition dump")
    parser.add_argument("--restore-stock", action="store_true", help="Restore stock firmware")
    parser.add_argument("--restore-radio", action="store_true", help="Restore radio/IMEI partitions from a per-unit backup")
    parser.add_argument("--list-backups", action="store_true", help="List saved radio & full stock backups")
    parser.add_argument("--edl", action="store_true", help="Reboot connected dongle into EDL mode")
    parser.add_argument("--ssh", action="store_true", help="Open SSH session to modem")
    return parser.parse_args()


def main() -> int:
    os.chdir(FLASHER_DIR)
    args = parse_args()
    if args.debug:
        os.environ["FLASH_DEBUG"] = "1"

    if args.convert:
        return run_convert()
    if args.flash:
        return run_script("flash-firmware.sh", ["--debug"] if args.debug else None)
    if args.backup_radio:
        return run_radio_backup(radio_only=False)
    if args.backup_radio_only:
        return run_radio_backup(radio_only=True)
    if args.backup_stock:
        return run_script("backup-stock.sh")
    if args.restore_stock:
        return restore_stock()
    if args.restore_radio:
        return restore_radio_only()
    if args.list_backups:
        return list_all_backups()
    if args.edl:
        return reboot_to_edl()
    if args.ssh:
        return open_ssh()

    return interactive_menu()


if __name__ == "__main__":
    sys.exit(main())
