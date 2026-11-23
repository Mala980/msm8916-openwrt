#!/usr/bin/env python3
"""
All-in-One MSM8916 4G Dongle Flasher & Recovery Toolkit.
Unified Python 3 launcher for OpenWrt flashing, stock backup/restore,
per-unit radio backup inspection, and EDL/SSH utilities.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

FLASHER_DIR = Path(__file__).resolve().parent

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_CYAN = "\033[1;36m"
C_GREEN = "\033[1;32m"
C_YELLOW = "\033[1;33m"
C_RED = "\033[1;31m"
C_DIM = "\033[2m"


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


def detect_edl() -> bool:
    """Detect Qualcomm EDL 9008 mode on macOS or Linux."""
    system = platform.system()
    if system == "Darwin":
        res = subprocess.run(
            ["ioreg", "-p", "IOUSB", "-l"],
            capture_output=True,
            text=True,
        )
        return "0x5c6" in res.stdout and "0x9008" in res.stdout
    if system == "Linux" and shutil.which("lsusb"):
        res = subprocess.run(["lsusb"], capture_output=True, text=True)
        lower = res.stdout.lower()
        return "05c6:9008" in lower or "qdloader" in lower or "qhsusb" in lower
    return False


def detect_adb() -> bool:
    """Check if an ADB device is attached."""
    adb = shutil.which("adb")
    if not adb:
        local_adb = FLASHER_DIR / "temp" / "platform-tools" / "adb"
        if local_adb.is_file():
            adb = str(local_adb)
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


def reboot_to_edl() -> int:
    """Attempt to reboot an attached OpenWrt or ADB dongle into EDL 9008."""
    if detect_edl():
        print(f"{C_YELLOW}[!] Dongle is already in EDL mode (05c6:9008).{C_RESET}")
        return 0
    for host in ("192.168.8.1", "192.168.1.1"):
        if check_tcp(host, 22):
            print(f"{C_GREEN}[*] Sending reboot-edl via SSH ({host})...{C_RESET}")
            return run_cmd([
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=5",
                f"root@{host}",
                "reboot-edl",
            ])
    if detect_adb():
        adb = shutil.which("adb") or str(FLASHER_DIR / "temp" / "platform-tools" / "adb")
        print(f"{C_GREEN}[*] Rebooting to EDL via ADB...{C_RESET}")
        return run_cmd([adb, "reboot", "edl"])
    print(f"{C_RED}[-] No SSH or ADB device found. Hold EDL testpoint/button while plugging USB.{C_RESET}")
    return 1


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
        print("  1) Flash OpenWrt Firmware (Standard)")
        print("  2) Flash OpenWrt Firmware (Debug / Verbose EDL)")
        print("  3) Backup Stock / Full Partitions")
        print("  4) Restore Stock Firmware")
        print("  5) List Per-Unit Radio Backups")
        print("  6) Reboot Dongle to EDL Mode (9008)")
        print("  7) Open SSH Console (root@192.168.8.1)")
        print("  0) Exit")
        print()

        try:
            choice = input("Select action [0-7]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice == "1":
            run_script("flash-firmware.sh")
        elif choice == "2":
            run_script("flash-firmware.sh", ["--debug"])
        elif choice == "3":
            run_script("backup-stock.sh")
        elif choice == "4":
            run_script("restore-stock.sh")
        elif choice == "5":
            run_cmd([sys.executable, str(FLASHER_DIR / "radio_backup.py"), "list"])
        elif choice == "6":
            reboot_to_edl()
        elif choice == "7":
            open_ssh()
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
    parser.add_argument("--debug", action="store_true", help="Enable verbose EDL output when flashing")
    parser.add_argument("--backup-stock", action="store_true", help="Run full stock partition backup")
    parser.add_argument("--restore-stock", action="store_true", help="Restore stock firmware")
    parser.add_argument("--list-backups", action="store_true", help="List saved per-unit radio backups")
    parser.add_argument("--edl", action="store_true", help="Reboot connected dongle into EDL mode")
    parser.add_argument("--ssh", action="store_true", help="Open SSH session to modem")
    return parser.parse_args()


def main() -> int:
    os.chdir(FLASHER_DIR)
    args = parse_args()

    if args.flash:
        return run_script("flash-firmware.sh", ["--debug"] if args.debug else None)
    if args.backup_stock:
        return run_script("backup-stock.sh")
    if args.restore_stock:
        return run_script("restore-stock.sh")
    if args.list_backups:
        return run_cmd([sys.executable, str(FLASHER_DIR / "radio_backup.py"), "list"])
    if args.edl:
        return reboot_to_edl()
    if args.ssh:
        return open_ssh()

    return interactive_menu()


if __name__ == "__main__":
    sys.exit(main())
