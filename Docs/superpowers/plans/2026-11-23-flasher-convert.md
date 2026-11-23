# Flasher Convert Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python3 flasher.py --convert` turns stock UZ801 dongles into OpenWrt one after another: back up only what is unique to each unit, flash, verify, wait for the next dongle.

**Architecture:** A new `flasher/convert.py` (imported by `flasher.py`) orchestrates existing tools: `radio_backup.py` (unique-partition backup + identity), `backup-stock.sh` (full dump, only when no golden dump exists for the unit's stock version), and `flash-firmware.sh` (gains non-interactive flags). Restore-to-stock combines a golden dump with a unit's unique partitions.

**Tech Stack:** Python 3 (stdlib only, must run on macOS's Python 3.9), bash, bkerler/edl, adb, ssh.

**Spec:** `docs/superpowers/specs/2026-11-23-flasher-convert-design.md`

## Global Constraints

- Unique partitions: `fsc fsg modemst1 modemst2 persist sec DDR ssd`.
- Partitions written as "radio" during an OpenWrt flash stay `fsc fsg modemst1 modemst2 modem persist sec` (`modem` comes from the golden dump; it is identical across units of one stock version).
- First golden dump: `flasher/backup/stock-20261123-122955`, stock version `V2.3.15.1`.
- Firmware for convert mode: `flasher/firmware/openwrt-stable`.
- Python code must run on Python 3.9 (`from __future__ import annotations`, no `match`); no third-party modules.
- All UI text in English. Tests: `unittest`, runnable as `python3 -I flasher/tests/<file>.py`, no hardware (fake `adb`/`edl`/`ssh` on PATH or injected callables).
- Never write another unit's unique partitions in convert mode.

## Review Focus

- A dongle already running OpenWrt (192.168.8.1 answers) plugged in during convert: must be refused, not flashed. → Task 5 test `test_refuses_openwrt_dongle`.
- The dongle that was just converted is still plugged in when the loop restarts: it must not be converted again; wait until USB/ADB shows it gone. → Task 5 test `test_waits_for_unplug_before_next`.
- `ro.build.display.id` empty or IMEI unreadable over ADB: version empty → treated as "no golden" (full dump); IMEI empty → verdict says "IMEI not verified", never "OK". → Task 3 test `test_identity_missing_fields`, Task 5 test `test_verdict_without_imei`.
- A failure in any step (backup, flash, verify) stops the loop with the unit's backup path printed; it never continues to the next dongle. → Task 5 test `test_stops_on_flash_failure`.
- Restore-to-stock for a unit whose golden dump is missing must refuse before touching the device. → Task 6 test `test_combined_restore_without_golden_refuses`.

---

### Task 1: Unique-partition backups with an identity record

**Files:**
- Modify: `flasher/radio_backup.py`
- Test: `flasher/tests/test_radio_backup.py`

**Interfaces:**
- Produces: `UNIQUE_PARTS = ["fsc", "fsg", "modemst1", "modemst2", "persist", "sec", "DDR", "ssd"]`; `backup --parts unique` expands to it; `backup --identity <json-file>` copies that JSON as `identity.json` into the new backup folder (not part of `manifest.json` "files").

- [ ] **Step 1: Write failing tests** `test_parts_unique_alias` (fake edl reads exactly the 8 unique parts, backup folder holds those 8 `.bin` files) and `test_identity_json_saved` (`--identity` file content equals `identity.json` in the printed backup path; `verify` on that folder still passes).
- [ ] **Step 2: Run** `python3 -I flasher/tests/test_radio_backup.py` → the two new tests FAIL.
- [ ] **Step 3: Implement** the constant, the `unique` alias in `cmd_backup`, and `--identity` (copy after `store()` returns, also when the backup was identical to the latest one).
- [ ] **Step 4: Run** the test file → all PASS.
- [ ] **Step 5: Commit** `feat(flasher): unique-partition backup and identity record`.

### Task 2: Golden dumps tagged with their stock version

**Files:**
- Create: `flasher/convert.py` (golden helpers only in this task)
- Create: `flasher/backup/stock-20261123-122955/STOCK_VERSION` (content `V2.3.15.1\n`; backup/ is git-ignored, so this is a data step, not committed)
- Test: `flasher/tests/test_convert.py`

**Interfaces:**
- Produces: `find_golden(backup_root: Path, version: str) -> Path | None` (a `stock-*` dir whose `STOCK_VERSION` equals `version` and that has `restore_sectors.txt`; newest wins; empty version → None); `mark_golden(stock_dir: Path, version: str) -> None`.

- [ ] **Step 1: Write failing tests** `test_find_golden_matches_version`, `test_find_golden_ignores_incomplete_dump` (no `restore_sectors.txt`), `test_find_golden_empty_version_is_none`.
- [ ] **Step 2: Run** `python3 -I flasher/tests/test_convert.py` → FAIL (module missing).
- [ ] **Step 3: Implement** both functions in `flasher/convert.py`.
- [ ] **Step 4: Run** → PASS. Write `STOCK_VERSION` into the existing dump.
- [ ] **Step 5: Commit** `feat(flasher): golden stock dumps tagged by stock version`.

### Task 3: Read a stock dongle's identity over ADB

**Files:**
- Modify: `flasher/convert.py`
- Test: `flasher/tests/test_convert.py`

**Interfaces:**
- Produces: `read_stock_identity(adb: str) -> dict` with keys `model, version, imei, wifi_mac, android_serial, baseband` (strings, "" when unreadable). Sources: `getprop ro.product.model / ro.build.display.id / ro.serialno / gsm.version.baseband`, `cat /sys/class/net/wlan0/address`, IMEI from `service call iphonesubinfo 1` (UTF-16 digits in the Parcel dump, e.g. the sample below must give `860018046041589`):
  ```
  Result: Parcel(
    0x00000000: 00000000 0000000f 00360038 00300030 '........8.6.0.0.'
    0x00000010: 00380031 00340030 00300036 00310034 '1.8.0.4.6.0.4.1.'
    0x00000020: 00380035 00000039                   '5.8.9...        ')
  ```
- Produces: `parse_iphonesubinfo(text: str) -> str` (15 digits or "").

- [ ] **Step 1: Write failing tests** `test_parse_iphonesubinfo` (sample → `860018046041589`), `test_read_stock_identity` (fake `adb` script answering each `shell` command), `test_identity_missing_fields` (empty getprop / no Parcel → those keys "").
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** (one `adb shell` call per value; the stock shell lacks `tr`/`head`, so parse on the host).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(flasher): read stock dongle identity over adb`.

### Task 4: Non-interactive flash-firmware.sh

**Files:**
- Modify: `flasher/flash-firmware.sh`
- Test: `flasher/tests/test_flash_noninteractive.py`

**Interfaces:**
- Produces flags: `--firmware DIR`, `--radio-from DIR` (folder with the 7 radio `.bin`), `--skip-live-backup` (only valid together with `--radio-from`; otherwise exit 2 with a message), `--yes`. Without flags the script behaves exactly as today. Existing `--debug` keeps working in any position.

- [ ] **Step 1: Write failing tests** with fake `edl`, `ioreg`/`lsusb` reporting 9008 and a temp firmware dir: `test_noninteractive_runs_without_stdin` (stdin closed, exit 0, edl got `w boot`, `w rootfs` and `w <part>` for all 7 radio parts from `--radio-from`), `test_skip_live_backup_requires_radio_from` (exit 2, edl never called), `test_interactive_default_unchanged` (no flags, answers `1\n1\ny\n` on stdin → same edl call sequence as before the change, live backup included).
- [ ] **Step 2: Run** `python3 -I flasher/tests/test_flash_noninteractive.py` → FAIL.
- [ ] **Step 3: Implement** argument parsing at the top of the `{ ... }` block; replace each `read` with the flag value when given; with `--skip-live-backup` skip `radio_backup.py backup` and the ownership check and use `--radio-from` as `SAVED`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(flasher): non-interactive flash-firmware.sh flags for convert mode`.

### Task 5: Convert loop

**Files:**
- Modify: `flasher/convert.py`
- Test: `flasher/tests/test_convert.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `convert_one(env: ConvertEnv) -> UnitResult` and `convert_loop(env: ConvertEnv) -> int`. `ConvertEnv` is a dataclass of injectable callables + paths: `adb_devices() -> list[str]`, `openwrt_online() -> bool`, `read_identity() -> dict`, `run(cmd: list[str]) -> int`, `run_capture(cmd) -> tuple[int, str]`, `verify_openwrt(expected_imei: str) -> dict` (keys `imei, wifi_up, modem_present`), `sleep(seconds)`, `backup_root: Path`, `firmware_dir: Path`. `UnitResult` dataclass: `ok: bool, serial: str, imei: str, backup_dir: str, verdict: str`.

Per unit (spec "Convert loop"): wait for exactly one ADB stock dongle → `read_identity` → if `find_golden(version)` is None: `run(["bash","backup-stock.sh"])`, `mark_golden(newest stock-*, version)`, wait for ADB again → `adb reboot edl` → `radio_backup.py backup --parts unique --identity <tmp json> --print-path` → temp radio set = unit's 6 radio files + golden `modem.bin` (symlinks) → `flash-firmware.sh --firmware <dir> --radio-from <set> --skip-live-backup --yes` → wait for SSH on 192.168.8.1 (max 180 s) → `verify_openwrt(imei)` → verdict line → wait until ADB and 192.168.8.1 are both gone → next.

- [ ] **Step 1: Write failing tests** (all with fakes): `test_converts_one_unit_with_golden` (call order: identity, reboot edl, unique backup, flash with the 4 flags, verify; result ok, verdict contains IMEI and "Wi-Fi up"), `test_full_dump_when_no_golden` (backup-stock.sh run and marked golden before unique backup), `test_refuses_openwrt_dongle`, `test_waits_for_unplug_before_next`, `test_verdict_without_imei` (verdict "IMEI not verified", ok False), `test_stops_on_flash_failure` (loop returns non-zero, message contains backup dir, no second unit started).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; real `verify_openwrt` uses ssh (BatchMode, root@192.168.8.1): IMEI from `qmicli --dms-get-ids`, Wi-Fi from `wifi status` `radio0.up`, modem from `mmcli -L`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(flasher): convert loop for stock dongles`.

### Task 6: Restore to stock from golden + unit backup

**Files:**
- Modify: `flasher/convert.py` (assembly), `flasher/flasher.py` (menu 7 flow)
- Test: `flasher/tests/test_convert.py`

**Interfaces:**
- Produces: `assemble_stock_restore(unit_backup: Path, backup_root: Path, out_dir: Path) -> Path` — reads `identity.json` → version → golden; out_dir gets symlinks to every golden file, the unique parts replaced by the unit's files, golden `restore_sectors.txt`; raises `ValueError` when identity/version/golden is missing. Menu 7 lists full dumps and unit backups that have `identity.json`; for the latter it assembles into a temp dir and runs `restore-stock.sh <dir>`.

- [ ] **Step 1: Write failing tests** `test_combined_restore_uses_unit_unique_parts` (modemst1 link points at unit file, system link at golden), `test_combined_restore_without_golden_refuses`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** all flasher tests (`for t in flasher/tests/test_*.py; do python3 -I $t; done`) → PASS.
- [ ] **Step 5: Commit** `feat(flasher): restore stock from golden dump + unit backup`.

### Task 7: Menu, CLI and docs; hardware check

**Files:**
- Modify: `flasher/flasher.py` (menu entry "Convert stock dongles → OpenWrt", `--convert`), `flasher/README.md` (short "Converting many dongles" section)

- [ ] **Step 1:** Add the menu entry and flag calling `convert.convert_loop(real_env())`.
- [ ] **Step 2:** `python3 -I -m py_compile flasher/flasher.py flasher/convert.py` with `/usr/bin/python3` (3.9) → no error; `python3 flasher/flasher.py --help` lists `--convert`.
- [ ] **Step 3:** Hardware check on one real stock dongle: run `python3 flasher/flasher.py --convert`, convert one unit, confirm verdict OK (IMEI = stock IMEI, Wi-Fi up), unit backup ≈ 40 MB with `identity.json`.
- [ ] **Step 4: Commit** `feat(flasher): convert mode in the menu and docs`.
