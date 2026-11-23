#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# fix-tree-perms.sh <openwrt-tree>
# fix-tree-perms.sh --repo <project-checkout>   (skips its openwrt/ subtree)
#
# Give files in the OpenWrt source tree their normal Unix modes again.
#
# A tree that was ever copied from an exFAT disk (which shows every file as
# rwx------) keeps 0700 modes. Packages that install files with $(CP) carry
# those modes straight into the firmware, where services running as an
# unprivileged user can no longer read their own scripts -- e.g. hostapd
# (user "network") failed to load /usr/share/hostap/common.uc, so Wi-Fi never
# came up.
#
# Only files that other users cannot read are touched. Their mode comes from
# the git index of the repository they belong to (the OpenWrt tree itself and
# each feed): 100755 -> 755, anything else -> 644. Untracked files (e.g. the
# BSP copied into target/linux/msm89xx) get 755 if they start with "#!" or are
# ELF binaries, else 644. Directories without o+rx get 755.
#
# Prints the number of files changed; exit status 0.
set -euo pipefail

mode=openwrt
if [ "${1:-}" = "--repo" ]; then mode=repo; shift; fi
tree="${1:?usage: fix-tree-perms.sh [--repo] <tree>}"
cd "$tree"

changed=0

fix_repo() { # fix_repo <dir> <find prune expression...>
    local repo="$1"; shift
    local modes
    modes="$(mktemp)"
    if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
        # "<mode> <obj> <stage>\t<path>" -> "<755|644>\t<path>"
        git -C "$repo" ls-files -s | awk -F'\t' '{split($1, a, " "); print (a[1] == "100755" ? "755" : "644") "\t" $2}' > "$modes"
    fi

    # Join the unreadable files with their git modes in one pass;
    # untracked files come out as "?".
    local bad
    bad="$(mktemp)"
    (cd "$repo" && find . -name .git -prune -o "$@" -type f ! -perm -0004 -print) | sed 's#^\./##' > "$bad"
    while IFS=$'\t' read -r m rel; do
        if [ "$m" = "?" ]; then
            case "$(head -c 4 "$repo/$rel" 2>/dev/null | od -An -c | tr -d ' ')" in
                \#!*|177ELF) m=755 ;;
                *) m=644 ;;
            esac
        fi
        # A single file that cannot be changed (e.g. a read-only object of a
        # nested git checkout) must not stop the whole pass.
        chmod "$m" "$repo/$rel" 2>/dev/null && changed=$((changed + 1)) || true
    done < <(awk -F'\t' 'FILENAME == ARGV[1] { mode[$2] = $1; next } { print (($0 in mode) ? mode[$0] : "?") "\t" $0 }' "$modes" "$bad")
    rm -f "$bad"

    rm -f "$modes"
}

if [ "$mode" = repo ]; then
    # The project checkout: everything except the OpenWrt tree inside it.
    fix_repo . \( -path ./.git -o -path ./openwrt \) -prune -o
    find . \( -name .git -o -path ./openwrt \) -prune -o -type d ! -perm -0005 -exec chmod 755 {} + 2>/dev/null || true
    echo "$changed"
    exit 0
fi

# The OpenWrt tree, without build output and without the feeds (fixed below
# with their own git index).
fix_repo . \( -path ./.git -o -path ./build_dir -o -path ./staging_dir -o -path ./bin \
    -o -path ./dl -o -path ./tmp -o -path ./logs -o -path ./feeds \) -prune -o

for feed in feeds/*/; do
    [ -d "$feed" ] || continue
    fix_repo "${feed%/}" -path ./.git -prune -o
done

find . \( -name .git -o -path ./build_dir -o -path ./staging_dir -o -path ./bin -o -path ./dl -o -path ./tmp \) -prune \
    -o -type d ! -perm -0005 -exec chmod 755 {} + 2>/dev/null || true

echo "$changed"
