# Flasher "convert" mode: stock UZ801 → OpenWrt in one step per dongle

Date: 2026-11-23 · Status: approved

## Why

Ten stock UZ801 dongles have to be moved to OpenWrt. Today each one needs two
separate interactive runs (full stock dump ~4 min, then flash ~3 min with a
second radio backup), and a full dump per unit costs ~950 MB (≈9.5 GB for ten).

## Goals

- One command, `python3 flasher.py --convert` (and a menu entry), that loops:
  plug a stock dongle → back up what is unique to it → flash OpenWrt →
  verify → "unplug, plug the next one". No typing between dongles.
- Keep every unit restorable to its own stock state (own IMEI, calibration,
  MACs) without storing a full dump per unit.
- ~4 min per dongle; ~40 MB of backup per dongle.

## Non-goals / anti-goals

- No parallel flashing of several dongles (edl talks to one device at a time).
- The single-session edl optimisation (all reads/writes in one firehose
  session) is deferred.
- Never write another unit's radio data in this mode; never skip the unit's
  own backup.

## Approach (picked: B)

- A. Full dump per unit — safest, ~7 min and ~950 MB per unit.
- **B. Unique partitions per unit + one "golden" full dump per stock firmware
  version** — ~4 min, ~40 MB per unit. Picked. A unit whose stock version has
  no golden dump yet gets a full dump, which becomes the golden for that
  version (so B falls back to A automatically).

## Design

Unique partitions per unit: `fsc fsg modemst1 modemst2 persist sec DDR ssd`
(~37 MB). Stored with radio_backup.py under `backup/units/<chip serial>/`,
plus an identity record (IMEI, Wi-Fi MAC, Android serial, chip serial, stock
version, baseband) read over ADB before leaving Android.

Golden dumps: `backup/stock-*` folders that carry the stock version they came
from. `stock-20261123-122955` (V2.3.15.1) is the first golden.

Convert loop, per dongle:
1. Wait for a dongle on ADB (stock Android). Read model, stock version
   (`ro.build.display.id`), IMEI (iphonesubinfo), Wi-Fi MAC, serial.
2. If no golden dump exists for that version: run the full stock dump instead
   of the unique backup, mark it golden for that version.
3. `adb reboot edl`, back up the unique partitions (filed by chip serial),
   write the identity record next to it.
4. Flash OpenWrt non-interactively: firmware `firmware/openwrt-stable`,
   radio partitions from the unit's own backup (no second radio backup).
5. After boot (192.168.8.1): check IMEI == stock IMEI, Wi-Fi AP up, modem
   present; print a one-line verdict.
6. "Done — unplug it and plug the next dongle"; wait for the next one.
   A failure stops the loop with the reason and the unit's backup path.

Restore to stock (existing menu entry): for a unit without a full dump,
combine the golden dump of its stock version with the unit's unique
partitions (sector-addressed writes, as restore-stock.sh does today).

flash-firmware.sh gets non-interactive inputs (firmware dir, radio source
folder, assume-yes) so convert mode can drive it.

## Left to the builder

Exact flag names, how the stock version is attached to a golden dump
(e.g. a `STOCK_VERSION` file), timeouts, and output wording.
