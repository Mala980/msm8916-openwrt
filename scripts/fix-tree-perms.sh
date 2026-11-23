#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# fix-tree-perms.sh <openwrt-tree>
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
# each feed): 100755 -> 755, anything else -> 644. Untracked files get 755 if
# they start with "#!", else 644. Directories without o+rx get 755.
#
# Prints the number of files changed; exit status 0.
set -euo pipefail

tree="${1:?usage: fix-tree-perms.sh <openwrt-tree>}"
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
    (cd "$repo" && find . "$@" -type f ! -perm -0004 -print) | sed 's#^\./##' > "$bad"
    while IFS=$'\t' read -r m rel; do
        if [ "$m" = "?" ]; then
            if head -c 2 "$repo/$rel" 2>/dev/null | grep -q '^#!'; then m=755; else m=644; fi
        fi
        chmod "$m" "$repo/$rel"
        changed=$((changed + 1))
    done < <(awk -F'\t' 'NR == FNR { mode[$2] = $1; next } { print (($0 in mode) ? mode[$0] : "?") "\t" $0 }' "$modes" "$bad")
    rm -f "$bad"

    rm -f "$modes"
}

# The OpenWrt tree, without build output and without the feeds (fixed below
# with their own git index).
fix_repo . \( -path ./.git -o -path ./build_dir -o -path ./staging_dir -o -path ./bin \
    -o -path ./dl -o -path ./tmp -o -path ./logs -o -path ./feeds \) -prune -o

for feed in feeds/*/; do
    [ -d "$feed" ] || continue
    fix_repo "${feed%/}" -path ./.git -prune -o
done

find . \( -path ./build_dir -o -path ./staging_dir -o -path ./bin -o -path ./dl -o -path ./tmp \) -prune \
    -o -type d ! -perm -0005 -exec chmod 755 {} +

echo "$changed"
