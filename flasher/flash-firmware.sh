#!/usr/bin/env bash
# ===========================================================================
# flash-firmware.sh — Flash the custom OpenWrt-Liberty image via EDL.
#
# Mirrors the upstream MSM8916 flash.sh, self-contained. Flashes a CLEAN image:
# no user settings are baked in (WiFi/network config, passwords etc. live in the
# overlay, which is erased -> the dongle boots with OpenWrt defaults). The unit's
# own radio partitions (IMEI/calibration) are backed up and restored, so its real
# IMEI is preserved.
#
# Reads images from ./firmware/. Run backup-stock.sh FIRST on a new dongle.
# Any missing tool (edl, python, adb) is installed into ./temp automatically.
#
# Output: one line per step. edl's own output (banners, progress bars) is kept
# off the screen and only shown -- its last lines -- when a step fails. No log
# file is written.
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"
trap '' HUP

# ===== self-contained tooling — everything installs into ./temp (gitignored) =====
TEMP_DIR="$PWD/temp"
c_g=$'\033[1;32m'; c_y=$'\033[1;33m'; c_r=$'\033[1;31m'; c_0=$'\033[0m'
log()  { printf '%s[*]%s %s\n' "$c_g" "$c_0" "$*"; }
warn() { printf '%s[!]%s %s\n' "$c_y" "$c_0" "$*"; }
die()  { printf '%s[-]%s %s\n' "$c_r" "$c_0" "$*" >&2; exit 1; }
hms()  { printf '%dh %02dm %02ds' $(($1/3600)) $(($1%3600/60)) $(($1%60)); }   # seconds -> H M S
PY=""; ADB=""; EDL="${EDL_BIN:-}"

# Run one edl command as a single tidy step: "[*] <label> … 12s" with a spinner
# on a terminal, then "✓" or, on failure, edl's last 20 lines. Returns edl's
# exit status (callers decide whether it is fatal).
edl_step() {
    local label="$1"; shift
    local out rc=0 start=$SECONDS pid spin='|/-\' i=0
    out="$(mktemp)"
    if [[ -t 1 ]]; then
        "$EDL" "$@" >"$out" 2>&1 &
        pid=$!
        while kill -0 "$pid" 2>/dev/null; do
            printf '\r%s[*]%s %s %s %ds ' "$c_g" "$c_0" "$label" "${spin:i++%4:1}" $((SECONDS - start))
            sleep 0.2
        done
        wait "$pid" || rc=$?
        printf '\r\033[K'
    else
        "$EDL" "$@" >"$out" 2>&1 || rc=$?
    fi
    if [[ $rc -eq 0 ]]; then
        printf '%s[*]%s %s %s✓%s %ds\n' "$c_g" "$c_0" "$label" "$c_g" "$c_0" $((SECONDS - start))
    else
        printf '%s[-]%s %s failed (edl exit %d). Last edl output:\n' "$c_r" "$c_0" "$label" "$rc"
        { tr '\r' '\n' <"$out" | grep -vE '^[[:space:]]*$|Progress:|^Done \|' | tail -n 20 | sed 's/^/      /'; } || true
    fi
    rm -f "$out"
    return "$rc"
}

# python3: system one, else a standalone build downloaded into ./temp/python
ensure_python() {
    command -v python3 >/dev/null 2>&1 && { PY=python3; return; }
    [[ -x "$TEMP_DIR/python/bin/python3" ]] && { PY="$TEMP_DIR/python/bin/python3"; return; }
    local t=20241016 v=3.12.7 triple
    case "$(uname -s)-$(uname -m)" in
        Darwin-arm64)  triple=aarch64-apple-darwin ;;
        Darwin-x86_64) triple=x86_64-apple-darwin ;;
        Linux-x86_64)  triple=x86_64-unknown-linux-gnu ;;
        Linux-aarch64) triple=aarch64-unknown-linux-gnu ;;
        *) die "no python3 and no standalone build for this platform — install python3" ;;
    esac
    log "python3 not found — downloading a standalone Python into ./temp/python"
    mkdir -p "$TEMP_DIR/python"
    curl -fsSL "https://github.com/astral-sh/python-build-standalone/releases/download/$t/cpython-$v+$t-$triple-install_only.tar.gz" \
        | tar -xz -C "$TEMP_DIR/python" --strip-components=1 || die "python download failed"
    PY="$TEMP_DIR/python/bin/python3"
}

# adb: system one, else Google platform-tools downloaded into ./temp
ensure_adb() {
    command -v adb >/dev/null 2>&1 && { ADB=adb; return; }
    [[ -x "$TEMP_DIR/platform-tools/adb" ]] && { ADB="$TEMP_DIR/platform-tools/adb"; return; }
    local os; case "$(uname -s)" in Darwin) os=darwin;; Linux) os=linux;; *) os=windows;; esac
    log "adb not found — downloading Android platform-tools into ./temp"
    mkdir -p "$TEMP_DIR"
    curl -fsSL "https://dl.google.com/android/repository/platform-tools-latest-$os.zip" -o "$TEMP_DIR/pt.zip" \
        && unzip -qo "$TEMP_DIR/pt.zip" -d "$TEMP_DIR" && rm -f "$TEMP_DIR/pt.zip" || die "adb download failed"
    ADB="$TEMP_DIR/platform-tools/adb"
}

# edl: EDL_BIN or a system edl, else install it into ./temp/edl
ensure_edl() {
    [[ -n "$EDL" && -x "$EDL" ]] && return
    command -v edl >/dev/null 2>&1 && { EDL=edl; return; }
    [[ -x "$TEMP_DIR/edl/venv/bin/edl" ]] && { EDL="$TEMP_DIR/edl/venv/bin/edl"; return; }
    ensure_python
    command -v git >/dev/null || die "git required to fetch edl"
    log "edl not found — installing it once into ./temp/edl"
    if [[ ! -d "$TEMP_DIR/edl/src" ]]; then
        git clone -q --depth 1 https://github.com/bkerler/edl "$TEMP_DIR/edl/src" || die "edl clone failed"
        git -C "$TEMP_DIR/edl/src" submodule update -q --init --recursive || true
    fi
    "$PY" -m venv "$TEMP_DIR/edl/venv"
    "$TEMP_DIR/edl/venv/bin/pip" install -q --upgrade pip
    "$TEMP_DIR/edl/venv/bin/pip" install -q "$TEMP_DIR/edl/src"
    [[ -x "$TEMP_DIR/edl/venv/bin/edl" ]] || die "edl install failed"
    EDL="$TEMP_DIR/edl/venv/bin/edl"
}

# put the dongle into EDL (9008): already in EDL, ssh reboot-edl, adb reboot, else manual D+/GND bridge
enter_edl() {
    if is_edl; then
        log "Dongle is already in EDL mode (05c6:9008)"
        return 0
    fi

    # Check if reachable via SSH on OpenWrt
    for ip in 192.168.8.1 192.168.1.1; do
        if ping -c 1 -W 1000 "$ip" >/dev/null 2>&1; then
            log "Dongle reachable via IP $ip — sending reboot-edl over SSH…"
            # reboot-edl tears down networking mid-session, so a foreground ssh
            # never gets a clean close and hangs until TCP times out (the
            # "stuck at Bringing down interfaces, had to Ctrl-C" bug). Detach
            # the command so the remote shell returns at once, and add
            # keepalives so ssh gives up within ~2s if the link drops first.
            ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                -o BatchMode=yes -o ConnectTimeout=5 \
                -o ServerAliveInterval=1 -o ServerAliveCountMax=2 \
                "root@$ip" "reboot-edl >/dev/null 2>&1 &" </dev/null >/dev/null 2>&1 || true
            wait_edl
            return
        fi
    done

    ensure_adb
    if [[ -n "$("$ADB" devices 2>/dev/null | awk 'NR>1 && $2=="device"{print $1}')" ]]; then
        log "Dongle on adb — rebooting into EDL"
        "$ADB" reboot edl 2>/dev/null || warn "adb reboot edl returned nonzero"
    else
        warn "Enter EDL manually: short the USB plug's D+ to GND while inserting the dongle"
        warn "(it enumerates as USB 05c6:9008 / QHSUSB__BULK)."
        read -r -p "    Press Enter once the dongle is in EDL mode... " _
    fi
    wait_edl
}
is_edl() {
    local os; os="$(uname -s)"
    if [[ "$os" == Darwin ]]; then
        ioreg -p IOUSB -w0 2>/dev/null | grep -qiE "0x9008|QHSUSB|QDLoader"
    elif [[ "$os" == Linux ]]; then
        lsusb 2>/dev/null | grep -qiE "05c6:9008|QHSUSB|QDLoader"
    else
        return 1
    fi
}
wait_edl() {  # fast 9008 detection: macOS (ioreg), Linux (lsusb), else skip
    local os; os="$(uname -s)"
    [[ "$os" != Darwin && "$os" != Linux ]] && { log "Assuming the dongle is in EDL."; return 0; }
    log "Waiting for the EDL device (05c6:9008)…"; local i
    for i in $(seq 1 40); do
        if is_edl; then
            log "EDL device detected."; return 0; fi
        sleep 1
    done
    warn "Could not confirm 9008 automatically — continuing (edl waits for the device)."
}

find_firmware_files() {
    local dir="$1"
    GPT="$(find "$dir" -maxdepth 1 -name "*gpt_both0.bin" 2>/dev/null | head -1 || true)"
    BOOT="$(find "$dir" -maxdepth 1 -name "*boot.img" 2>/dev/null | head -1 || true)"
    ROOTFS="$(find "$dir" -maxdepth 1 -name "*system.img" 2>/dev/null | head -1 || true)"
    ZIP="$(find "$dir" -maxdepth 1 -name "*firmware.zip" 2>/dev/null | head -1 || true)"
    [[ -n "$GPT" && -n "$BOOT" && -n "$ROOTFS" && -n "$ZIP" ]]
}

FW_DIRS=()
FW_LABELS=()

# 1. OpenWrt Stable build in this repo
if find_firmware_files "firmware/openwrt-stable"; then
    FW_DIRS+=("firmware/openwrt-stable")
    FW_LABELS+=("OpenWrt Stable (Clean 13MB / Native Qualcomm Tools - ./firmware/openwrt-stable)")
fi

# 2. OpenWrt-Liberty in this repo
if find_firmware_files "firmware"; then
    FW_DIRS+=("firmware")
    FW_LABELS+=("OpenWrt-Liberty (Bypass / PassWall / VPN - ./firmware)")
fi

# 3. Any other subfolder in ./firmware/
for sub in firmware/*/; do
    sub_clean="${sub%/}"
    [[ "$sub_clean" == "firmware/openwrt-stable" ]] && continue
    if [[ -d "$sub_clean" ]] && find_firmware_files "$sub_clean"; then
        FW_DIRS+=("$sub_clean")
        FW_LABELS+=("Custom Folder: $sub_clean")
    fi
done

echo "=== Select OpenWrt Firmware to Flash ==="
SELECTED_FW_DIR=""
if [[ ${#FW_DIRS[@]} -eq 0 ]]; then
    warn "No pre-configured firmware directories found."
    read -e -r -p "Enter path to firmware folder: " SELECTED_FW_DIR
else
    for i in "${!FW_DIRS[@]}"; do
        printf "  %d) %s\n" "$((i+1))" "${FW_LABELS[$i]}"
    done
    printf "  %d) Enter custom directory path\n" "$(( ${#FW_DIRS[@]} + 1 ))"
    read -r -p "Select firmware [1-$(( ${#FW_DIRS[@]} + 1 ))] (default 1): " fw_choice
    fw_choice="${fw_choice:-1}"
    if [[ "$fw_choice" -le "${#FW_DIRS[@]}" && "$fw_choice" -ge 1 ]]; then
        SELECTED_FW_DIR="${FW_DIRS[$((fw_choice-1))]}"
    else
        read -e -r -p "Enter path to firmware folder: " SELECTED_FW_DIR
    fi
fi

SELECTED_FW_DIR="${SELECTED_FW_DIR//\"/}"
SELECTED_FW_DIR="${SELECTED_FW_DIR//\'/}"
SELECTED_FW_DIR="${SELECTED_FW_DIR%/}"

find_firmware_files "$SELECTED_FW_DIR" || die "Directory does not contain required files (*gpt_both0.bin, *boot.img, *system.img, *firmware.zip): $SELECTED_FW_DIR"

# Dynamically calculate total sectors from GPT header
alt_lba=$(od -An -tu8 -j 544 -N 8 "$GPT" 2>/dev/null | tr -d '[:space:]' || true)
if [[ "${alt_lba:-0}" =~ ^[0-9]+$ ]] && [[ "$alt_lba" -ge 67 ]]; then
    TOT_SECTORS=$((alt_lba + 1))
else
    TOT_SECTORS=7569408
fi

echo
log "Using firmware directory: $SELECTED_FW_DIR"
log "GPT:    $(basename "$GPT") (Total sectors: $TOT_SECTORS)"
log "Boot:   $(basename "$BOOT")"
log "Rootfs: $(basename "$ROOTFS")"
log "Bundle: $(basename "$ZIP")"

# Extract the Qualcomm .mbn firmware from the bundle.
# SAVED is never deleted: it is either the unit's permanent backup under
# backup/units/<serial>/ or a backup folder the user picked.
MBN="$(mktemp -d)"; trap 'rm -rf "$MBN" "$GPTTMP"' EXIT
GPTTMP=""; SAVED=""
log "Extracting .mbn firmware from $(basename "$ZIP")…"
unzip -q -j -d "$MBN" "$ZIP" "*.mbn"
for p in aboot hyp rpm sbl1 tz; do
    [[ -f "$MBN/$p.mbn" ]] || die "$p.mbn missing from firmware bundle"
done

RADIO="fsc fsg modemst1 modemst2 modem persist sec"

echo
warn "This ERASES the OpenWrt overlay / any existing config on the dongle."
echo "Choose radio/modem partitions source ($RADIO):"
echo "  1) Read live from connected dongle (default - preserves current dongle state)"
echo "  2) Restore from local radio/stock backup (./backup/units/<serial>/*, ./backup/*)"
echo "     Either way, this unit's live radio data is first saved permanently to"
echo "     ./backup/units/<chip serial>/ (see: python3 radio_backup.py list)."
read -r -p "Select source [1/2] (default 1): " RADIO_SRC
RADIO_SRC="${RADIO_SRC:-1}"

STOCK_DIR=""
if [[ "$RADIO_SRC" == "2" ]]; then
    validate_radio_dir() {
        local d="$1"
        for part in $RADIO; do
            [[ -s "$d/$part.bin" ]] || return 1
        done
        return 0
    }

    RADIO_DIRS=()
    # Per-unit backups (newest first), then legacy flat folders.
    shopt -s nullglob
    unit_dirs=(backup/units/*/*/)
    shopt -u nullglob
    for (( k=${#unit_dirs[@]}-1; k>=0; k-- )); do
        b_clean="${unit_dirs[$k]%/}"
        if validate_radio_dir "$b_clean"; then RADIO_DIRS+=("$b_clean"); fi
    done
    for b_dir in backup/*/; do
        b_clean="${b_dir%/}"
        [[ "$b_clean" == "backup/units" ]] && continue
        if [[ -d "$b_clean" ]] && validate_radio_dir "$b_clean"; then
            RADIO_DIRS+=("$b_clean")
        fi
    done

    if [[ ${#RADIO_DIRS[@]} -eq 0 ]]; then
        warn "No valid radio backup folders found in ./backup/."
        read -e -r -p "Enter path to folder containing radio .bin files: " STOCK_DIR
    else
        echo "Found valid radio backup folders:"
        for i in "${!RADIO_DIRS[@]}"; do
            printf "  %d) %s\n" "$((i+1))" "${RADIO_DIRS[$i]}"
        done
        printf "  %d) Enter custom directory path\n" "$(( ${#RADIO_DIRS[@]} + 1 ))"
        read -r -p "Select backup [1-$(( ${#RADIO_DIRS[@]} + 1 ))] (default 1): " b_choice
        b_choice="${b_choice:-1}"
        if [[ "$b_choice" -le "${#RADIO_DIRS[@]}" && "$b_choice" -ge 1 ]]; then
            STOCK_DIR="${RADIO_DIRS[$((b_choice-1))]}"
        else
            read -e -r -p "Enter path to folder containing radio .bin files: " STOCK_DIR
        fi
    fi

    STOCK_DIR="${STOCK_DIR//\"/}"
    STOCK_DIR="${STOCK_DIR//\'/}"
    STOCK_DIR="${STOCK_DIR%/}"

    validate_radio_dir "$STOCK_DIR" || die "Directory does not contain all required radio partitions ($RADIO): $STOCK_DIR"
    log "Using radio backup from: $STOCK_DIR"
fi

read -r -p "Continue flashing? (y/N): " c; [[ "$c" =~ ^[Yy]$ ]] || { log "Cancelled."; exit 0; }

ensure_edl
ensure_python
enter_edl

# Always save this unit's live radio + boot chain permanently FIRST, filed by
# chip serial (backup/units/<serial>/<timestamp>/). It must be the first edl
# call of the session: only then does edl report the serial.
LIVE="$("$PY" -I radio_backup.py backup --edl "$EDL" --print-path)" \
    || die "radio backup failed — nothing has been written to the dongle"
[[ -d "$LIVE" ]] || die "radio backup returned no folder — nothing has been written to the dongle"

SAVED=""
if [[ "$RADIO_SRC" == "2" ]]; then
    # Restoring another unit's radio data clones its IMEI: refuse by default.
    rc=0; "$PY" -I radio_backup.py check "$STOCK_DIR" --against "$LIVE" || rc=$?
    if [[ $rc -ne 0 ]]; then
        if [[ $rc -eq 2 ]]; then
            warn "That backup belongs to ANOTHER dongle — restoring it clones that unit's IMEI."
        else
            warn "That backup has no serial record — it may belong to another dongle."
        fi
        read -r -p "Type YES to restore it anyway: " c
        if [[ "$c" != "YES" ]]; then
            log "Cancelled — nothing written. This unit's own backup: $LIVE"
            edl_step "Rebooting the dongle" reset || warn "edl reset failed — power-cycle the dongle manually"
            exit 0
        fi
    fi
    SAVED="$STOCK_DIR"
    log "Will restore radio partitions from $STOCK_DIR"
else
    SAVED="$LIVE"
fi

GPTTMP="$(mktemp -d)"
dd if="$GPT" bs=512 count=34         of="$GPTTMP/primary.bin"        2>/dev/null
dd if="$GPT" bs=512 skip=34 count=32 of="$GPTTMP/backup_entries.bin" 2>/dev/null
dd if="$GPT" bs=512 skip=66 count=1  of="$GPTTMP/backup_header.bin"  2>/dev/null
edl_step "Writing GPT (primary)"        ws 0                   "$GPTTMP/primary.bin"        || die "GPT primary write failed"
edl_step "Writing GPT (backup entries)" ws $((TOT_SECTORS-33)) "$GPTTMP/backup_entries.bin" || die "GPT backup-entries write failed"
edl_step "Writing GPT (backup header)"  ws $((TOT_SECTORS-1))  "$GPTTMP/backup_header.bin"  || die "GPT backup-header write failed"

mb() { echo "$(( ($(wc -c <"$1") + 524288) / 1048576 )) MB"; }
edl_step "Writing aboot"                  w aboot  "$MBN/aboot.mbn" || die "aboot write failed"
edl_step "Writing hyp"                    w hyp    "$MBN/hyp.mbn"   || die "hyp write failed"
edl_step "Writing rpm"                    w rpm    "$MBN/rpm.mbn"   || die "rpm write failed"
edl_step "Writing sbl1"                   w sbl1   "$MBN/sbl1.mbn"  || die "sbl1 write failed"
edl_step "Writing tz"                     w tz     "$MBN/tz.mbn"    || die "tz write failed"
edl_step "Writing boot ($(mb "$BOOT"))"     w boot   "$BOOT"          || die "boot write failed"
edl_step "Writing rootfs ($(mb "$ROOTFS"))" w rootfs "$ROOTFS"        || die "rootfs write failed"
ZEROS="$(mktemp)"
dd if=/dev/zero of="$ZEROS" bs=1M count=32 status=none 2>/dev/null || dd if=/dev/zero of="$ZEROS" bs=512 count=65536 status=none 2>/dev/null || true
edl_step "Clearing the settings overlay" ws 610338 "$ZEROS" || warn "clearing the overlay header returned non-zero"
rm -f "$ZEROS"

for n in $RADIO; do
    edl_step "Restoring radio partition $n" w "$n" "$SAVED/$n.bin" || die "restore of $n failed"
done

edl_step "Rebooting the dongle" reset || warn "edl reset failed — power-cycle the dongle manually"

echo
log "Flash complete in $(hms $SECONDS). The dongle boots OpenWrt with default config."
log "Access: http://192.168.8.1 over the USB gadget (DHCP hands out 192.168.8.x)."
