#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Build firmware bundle: compiles qhypstub (AArch64 asm) and lk2nd (ARM),
# signs them with qtestsign, and packs a ZIP.

set -euo pipefail

OUT_FILE="${1:-firmware.zip}"

# Per-device lk2nd configuration, passed by the image Makefile. An empty
# LK2ND_COMPATIBLE lets lk2nd match dynamically from the boot cmdline instead
# of forcing one board's device node; the bundle DTB only needs to carry the
# candidate nodes.
LK2ND_BUNDLE_DTB="${LK2ND_BUNDLE_DTB:-msm8916-512mb-mtp.dtb}"
LK2ND_COMPATIBLE="${LK2ND_COMPATIBLE:-}"

# Keep one build directory per lk2nd configuration: the generic boards share
# a binary, UZ801 (and any future explicit compatible) gets its own.
LK2ND_KEY="$(printf '%s' "${LK2ND_COMPATIBLE:-generic}-${LK2ND_BUNDLE_DTB%.dtb}" |
    tr -c 'A-Za-z0-9_.-' '_')"

# Upstream revisions, pinned so that the bootloader in firmware.zip does not
# change silently between builds. Bump these together with any lk2nd patch in
# target/linux/msm89xx/lk2nd/.
QHYPSSTUB_REV="fca3c513b6fb5e5b8fabae21dac1f4a5c0b51bc6"
LK2ND_REV="bef71355c7e8742d21d6eaffc6a2d0065580cf07"
QTESTSIGN_REV="0eef3b552b1ada848f22bad38ab2e40407307c5b"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

###############################################################################
# Temporary working directory
###############################################################################

# Respect OpenWrt-provided TMPDIR if present; otherwise create a private one.
CLEANUP_DIR=""
if [ -z "${TMPDIR:-}" ]; then
    TMPDIR="$(mktemp -d)"
    CLEANUP_DIR="$TMPDIR"
fi

trap 'if [ -n "$CLEANUP_DIR" ]; then rm -rf "$CLEANUP_DIR"; fi' EXIT

BUILDDIR="$TMPDIR/msm8916-firmware-build-$LK2ND_KEY"
mkdir -p "$BUILDDIR"

###############################################################################
# Persistent download cache
###############################################################################

: "${TOPDIR:?TOPDIR not set}"

DL_DIR="$TOPDIR/dl/msm8916-firmware"

mkdir -p "$DL_DIR"

###############################################################################
# Toolchain detection
###############################################################################

if [ -n "${STAGING_DIR:-}" ]; then

    TOOLCHAIN_DIR="$(find "$STAGING_DIR/../toolchain-"* \
        -maxdepth 0 -type d 2>/dev/null | head -1 || true)"

    if [ -n "$TOOLCHAIN_DIR" ] && [ -d "$TOOLCHAIN_DIR/bin" ]; then

        export PATH="$TOOLCHAIN_DIR/bin:$PATH"

        AARCH64_PREFIX="$(
            ls \
                "$TOOLCHAIN_DIR/bin/"aarch64-openwrt-linux-musl-gcc \
                "$TOOLCHAIN_DIR/bin/"aarch64-openwrt-linux-gcc \
                2>/dev/null |
            head -1 |
            xargs -r basename |
            sed 's/-gcc$//'
        )"

        if [ -n "$AARCH64_PREFIX" ]; then

            AARCH64_CROSS="${AARCH64_PREFIX}-"
            AARCH64_CC="${AARCH64_PREFIX}-gcc"
            AARCH64_AS="${AARCH64_PREFIX}-as"
            AARCH64_LD="${AARCH64_PREFIX}-ld"
            AARCH64_AR="${AARCH64_PREFIX}-ar"
            AARCH64_OBJCOPY="${AARCH64_PREFIX}-objcopy"

        fi
    fi

    if [ -d "$STAGING_DIR/../host/bin" ]; then
        export PATH="$STAGING_DIR/../host/bin:$PATH"
    fi

fi

###############################################################################
# Default toolchain names
###############################################################################

: "${AARCH64_CROSS:=aarch64-openwrt-linux-}"
: "${AARCH64_CC:=aarch64-openwrt-linux-gcc}"
: "${AARCH64_AS:=aarch64-openwrt-linux-as}"
: "${AARCH64_LD:=aarch64-openwrt-linux-ld}"
: "${AARCH64_AR:=aarch64-openwrt-linux-ar}"
: "${AARCH64_OBJCOPY:=aarch64-openwrt-linux-objcopy}"
: "${ARM_CROSS:=arm-none-eabi-}"

###############################################################################
# Sanity checks
###############################################################################

echo "[+] Checking toolchains..."

command -v "${AARCH64_CC}" >/dev/null ||
{
    echo "[!] Error: ${AARCH64_CC} not found"
    exit 1
}

command -v "${AARCH64_AS}" >/dev/null ||
{
    echo "[!] Error: ${AARCH64_AS} not found"
    exit 1
}

command -v "${ARM_CROSS}gcc" >/dev/null ||
{
    echo "[!] Error: ${ARM_CROSS}gcc not found"
    exit 1
}

echo "[+] Found aarch64 toolchain: $(command -v ${AARCH64_CC})"
echo "[+] Found aarch64 assembler: $(command -v ${AARCH64_AS})"
echo "[+] Found arm toolchain: $(command -v ${ARM_CROSS}gcc)"

###############################################################################
# Repository cache
###############################################################################

for repo in qhypstub:qhypstub lk2nd:lk2nd qtestsign:qtestsign; do

    name="${repo%:*}"
    dir="${repo#*:}"

    CACHE_DIR="$DL_DIR/$dir"
    BUILD_DIR="$BUILDDIR/$dir"

    if [ ! -d "$CACHE_DIR/.git" ] || \
       ! git -C "$CACHE_DIR" rev-parse \
            --is-inside-work-tree >/dev/null 2>&1; then

        rm -rf "$CACHE_DIR"

        echo "[+] Downloading $name..."

        git clone \
            "https://github.com/msm8916-mainline/$name.git" \
            "$CACHE_DIR"

    else

        echo "[+] Using cached $name"

    fi

    rm -rf "$BUILD_DIR"

    cp -a "$CACHE_DIR" "$BUILD_DIR"

    case "$name" in
    qhypstub)  rev="$QHYPSSTUB_REV" ;;
    lk2nd)     rev="$LK2ND_REV" ;;
    qtestsign) rev="$QTESTSIGN_REV" ;;
    esac

    if [ -n "$rev" ]; then

        if ! git -C "$BUILD_DIR" rev-parse --verify --quiet "$rev^{commit}" >/dev/null; then
            echo "[+] Fetching $name for pinned revision $rev..."
            git -C "$BUILD_DIR" fetch --quiet origin || true
        fi

        git -C "$BUILD_DIR" checkout --quiet "$rev"

    fi

done

###############################################################################
# lk2nd patches (device nodes, key mapping, ...)
###############################################################################

for p in "$SCRIPT_DIR"/../lk2nd/*.patch; do

    [ -e "$p" ] || continue

    echo "[+] Applying lk2nd patch: $(basename "$p")..."

    (
        cd "$BUILDDIR/lk2nd"

        patch \
            -p1 \
            --batch \
            --silent \
            < "$p"
    )

done

###############################################################################
# Build qhypstub (pure AArch64 assembly)
###############################################################################

echo "[+] Compiling qhypstub..."

if [ ! -f "$BUILDDIR/qhypstub/qhypstub.elf" ]; then

    env -u MAKEFLAGS MAKEFLAGS= \
    make -C "$BUILDDIR/qhypstub" clean \
        CROSS_COMPILE="$AARCH64_CROSS" \
        CC="$AARCH64_CC" \
        AS="$AARCH64_AS" \
        LD="$AARCH64_LD" \
        AR="$AARCH64_AR" \
        OBJCOPY="$AARCH64_OBJCOPY" || true

    env -u MAKEFLAGS MAKEFLAGS= \
    make -C "$BUILDDIR/qhypstub" \
        CROSS_COMPILE="$AARCH64_CROSS" \
        CC="$AARCH64_CC" \
        AS="$AARCH64_AS" \
        LD="$AARCH64_LD" \
        AR="$AARCH64_AR" \
        OBJCOPY="$AARCH64_OBJCOPY"

else

    echo "[+] qhypstub already compiled"

fi

###############################################################################
# Build lk2nd
###############################################################################

echo "[+] Compiling lk2nd..."
echo "[+] lk2nd config: bundle=$LK2ND_BUNDLE_DTB compatible=${LK2ND_COMPATIBLE:-<dynamic>}"

if [ ! -f "$BUILDDIR/lk2nd/build-lk1st-msm8916/emmc_appsboot.mbn" ]; then

(
    cd "$BUILDDIR/lk2nd"

    grep -qxF 'DEFINES += USE_TARGET_HS200_CAPS=1' \
        project/lk1st-msm8916.mk || \
        echo 'DEFINES += USE_TARGET_HS200_CAPS=1' \
        >> project/lk1st-msm8916.mk

    make clean || true

    make_args=(
        "TOOLCHAIN_PREFIX=$ARM_CROSS"
        "lk1st-msm8916"
    )

    if [ -n "$LK2ND_COMPATIBLE" ]; then
        make_args=("LK2ND_COMPATIBLE=$LK2ND_COMPATIBLE" "${make_args[@]}")
    fi

    make \
        "LK2ND_BUNDLE_DTB=$LK2ND_BUNDLE_DTB" \
        "${make_args[@]}"

)

else

    echo "[+] lk2nd already compiled"

fi

###############################################################################
# Output directory
###############################################################################

OUTDIR="$BUILDDIR/output"

mkdir -p "$OUTDIR"

###############################################################################
# Qualcomm firmware
###############################################################################

FWZIP="$DL_DIR/dragonboard-410c-bootloader-emmc-linux-176.zip"

if [ ! -f "$FWZIP" ]; then

    echo "[+] Downloading Qualcomm firmware..."

    wget \
        -q \
        --show-progress \
        -O "$FWZIP" \
        "https://github.com/Mio-sha512/openstick-stuff/raw/refs/heads/main/builder-stuff/dragonboard-410c-bootloader-emmc-linux-176.zip"

else

    echo "[+] Using cached Qualcomm firmware"

fi

###############################################################################
# Extract required firmware files
###############################################################################

unzip -o -j \
    -d "$OUTDIR" \
    "$FWZIP" \
    dragonboard-410c-bootloader-emmc-linux-176/rpm.mbn \
    dragonboard-410c-bootloader-emmc-linux-176/sbl1.mbn \
    dragonboard-410c-bootloader-emmc-linux-176/tz.mbn

###############################################################################
# Sign binaries
###############################################################################

echo "[+] Signing binaries..."

python3 \
    "$BUILDDIR/qtestsign/qtestsign.py" \
    hyp \
    "$BUILDDIR/qhypstub/qhypstub.elf" \
    -o "$OUTDIR/hyp.mbn"

python3 \
    "$BUILDDIR/qtestsign/qtestsign.py" \
    aboot \
    "$BUILDDIR/lk2nd/build-lk1st-msm8916/emmc_appsboot.mbn" \
    -o "$OUTDIR/aboot.mbn"

###############################################################################
# Create firmware ZIP
###############################################################################

echo "[+] Creating firmware package..."

(
    cd "$OUTDIR"

    zip -9 \
        "$(basename "$OUT_FILE")" \
        *.mbn
)

###############################################################################
# Install final artifact
###############################################################################

DEST_DIR="$(dirname "$OUT_FILE")"

mkdir -p "$DEST_DIR"

mv -f \
    "$OUTDIR/$(basename "$OUT_FILE")" \
    "$OUT_FILE"

###############################################################################
# Done
###############################################################################

echo
echo "[+] Build completed successfully"
echo "[+] Output file: $OUT_FILE"

ls -lh "$OUT_FILE"
