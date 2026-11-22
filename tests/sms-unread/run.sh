#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Hardware-free tests for smsctl's unread accounting (no root, no modem, no OpenWrt).
#
#   tests/sms-unread/run.sh
#
# smsctl is copied to a scratch dir with its jshn.sh source and ARCHIVE_DIR
# redirected, and run against stubs for jshn, mmcli, uci, logger and flock. The
# mmcli stub reports NO modem, so this also exercises the path where `counts`
# and `mark-read` must work with the modem absent (both are in smsctl's
# no-modem command list). The archive is seeded with inbound unread messages,
# one legacy record without a "read" field (must count as read), and an
# outbound one; the tests assert on `counts` unread totals and on mark-read
# clearing one peer or all.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=${SRC:-$(cd "$HERE/../../packages/luci-app-sms/root" && pwd)}
SMSCTL_SRC=$SRC/usr/bin/smsctl

PASS=0; FAIL=0
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/sms-unread-test.XXXXXX"); trap 'rm -rf "$ROOT"' EXIT

check() { local n=$1; shift
    if "$@" >/dev/null 2>&1; then PASS=$((PASS + 1)); printf '  ok   %s\n' "$n"
    else FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$n"; fi
}

# --- stubs -----------------------------------------------------------------
BIN=$ROOT/bin; mkdir -p "$BIN"
cat > "$BIN/mmcli" <<'EOF'
#!/bin/sh
# No modem present: `mmcli -L` prints nothing, so smsctl's IDX stays empty.
exit 0
EOF
for t in uci logger flock; do printf '#!/bin/sh\nexit 0\n' > "$BIN/$t"; done
chmod +x "$BIN"/*

# Minimal jshn.sh: enough of the API for `counts` and `mark-read`.
JSHN=$ROOT/jshn.sh
cat > "$JSHN" <<'EOF'
json_init() { _J=""; }
json_add_boolean() { _J="$_J \"$1\": $2,"; }
json_add_int()     { _J="$_J \"$1\": $2,"; }
json_add_string()  { _J="$_J \"$1\": \"$2\","; }
json_add_array()   { :; }
json_add_object()  { :; }
json_close_array() { :; }
json_close_object(){ :; }
json_dump() { printf '{%s }\n' "${_J%,}"; }
EOF

# --- smsctl under test, paths redirected -----------------------------------
ARCHIVE=$ROOT/srv/sms; mkdir -p "$ARCHIVE"
CTL=$ROOT/smsctl
sed -e "s#\\. /usr/share/libubox/jshn.sh#. $JSHN#" \
    -e "s#^ARCHIVE_DIR=/srv/sms#ARCHIVE_DIR=$ARCHIVE#" \
    -e "s#^SIM_DIR=/srv/sms/sim#SIM_DIR=$ARCHIVE/sim#" \
    "$SMSCTL_SRC" > "$CTL"
chmod +x "$CTL"

run() { PATH="$BIN:$PATH" sh "$CTL" "$@" 2>/dev/null; }
seed() {
    cat > "$ARCHIVE/card.jsonl" <<'EOF'
{ "id": "a", "dir": "in", "folder": "inbox", "peer": "+628111", "text": "OTP 1", "read": "0" }
{ "id": "b", "dir": "in", "folder": "inbox", "peer": "+628222", "text": "Promo", "read": "0" }
{ "id": "c", "dir": "out", "folder": "sent", "peer": "+628111", "text": "reply" }
{ "id": "d", "dir": "in", "folder": "inbox", "peer": "+628111", "text": "legacy no-read field" }
EOF
}
unread_of() { run counts | sed -n 's/.*"unread": *\([0-9]*\).*/\1/p'; }
inbox_of()  { run counts | sed -n 's/.*"inbox": *\([0-9]*\).*/\1/p'; }

# --- tests -----------------------------------------------------------------
echo "smsctl unread accounting:"

seed
check "counts: inbox totals all inbound (3)"            test "$(inbox_of)"  = 3
check "counts: unread counts only read:0 (2)"          test "$(unread_of)" = 2

seed
run mark-read +628111 >/dev/null
check "mark-read <peer>: clears that peer only (1 left)" test "$(unread_of)" = 1

seed
run mark-read all >/dev/null
check "mark-read all: clears everything (0 left)"       test "$(unread_of)" = 0
check "mark-read all: inbox total unchanged (3)"        test "$(inbox_of)"  = 3

seed
run mark-read all >/dev/null
check "legacy record survives mark-read"                grep -q "legacy no-read field" "$ARCHIVE/card.jsonl"

# mark-read must not require a modem (regression: it was missing from the
# no-modem command list, so it died with "no modem found").
seed
check "mark-read works with no modem present"           sh -c "PATH='$BIN:$PATH' sh '$CTL' mark-read all | grep -q '\"ok\": 1'"
check "counts works with no modem present"              sh -c "PATH='$BIN:$PATH' sh '$CTL' counts | grep -q '\"unread\"'"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
