#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
#
# Hardware-free tests for the modemmanager proto's network selection
# (netselect home / auto / manual) and SIM home-PLMN detection.
#
#   tests/netselect/run.sh
#
# modemmanager_home_plmn and modemmanager_network_select are cut out of the
# proto script and run against an mmcli stub that serves a fixture SIM and
# records every call, so the tests assert on what the proto asks the modem to
# do: register in the SIM's home PLMN, fall back to automatic, or lock to the
# configured PLMN.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PROTO=${PROTO:-$HERE/../../openwrt-overlay/feeds/packages/net/modemmanager/files/lib/netifd/proto/modemmanager.sh}

PASS=0; FAIL=0
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/netselect-test.XXXXXX"); trap 'rm -rf "$ROOT"' EXIT
BIN=$ROOT/bin; mkdir -p "$BIN"

# --- stubs -----------------------------------------------------------------
# $ROOT/sim: SIM key-value dump. $ROOT/home_fail: register-in-operator fails.
cat > "$BIN/mmcli" <<EOF
#!/bin/sh
echo "mmcli \$*" >> "$ROOT/calls"
case "\$*" in
    *--sim=any*--output-keyvalue*) [ -s "$ROOT/sim" ] && cat "$ROOT/sim" || exit 1 ;;
    *--output-keyvalue*) [ -s "$ROOT/op" ] && echo "modem.3gpp.operator-code : \$(cat "$ROOT/op")" || exit 1 ;;
    *--3gpp-register-in-operator=*) [ -e "$ROOT/home_fail" ] && exit 1 || exit 0 ;;
    *--3gpp-register-home*) exit 0 ;;
esac
EOF
chmod +x "$BIN/mmcli"

{
    awk '/^modemmanager_set_plmn\(\)/,/^}/' "$PROTO"
    awk '/^modemmanager_home_plmn\(\)/,/^}/' "$PROTO"
    awk '/^modemmanager_network_select\(\)/,/^}/' "$PROTO"
    awk '/^modemmanager_netselect_connected\(\)/,/^}/' "$PROTO"
} > "$ROOT/funcs.sh"
grep -q '^modemmanager_network_select()' "$ROOT/funcs.sh" || { echo "FAIL: functions not found in $PROTO"; exit 1; }

# netifd helpers used on the error path
cat > "$ROOT/lib.sh" <<EOF
proto_notify_error() { echo "notify_error \$*" >> "$ROOT/calls"; }
proto_block_restart() { echo "block_restart \$*" >> "$ROOT/calls"; }
EOF

sim() { # sim <imsi> [operator-code]
    printf 'sim.dbus-path : /org/freedesktop/ModemManager1/SIM/0\nsim.properties.imsi : %s\nsim.properties.operator-code : %s\n' "$1" "${2:---}" > "$ROOT/sim"
}
reset() { rm -f "$ROOT/sim" "$ROOT/op" "$ROOT/home_fail" "$ROOT/calls" "$ROOT/mm-netselect-modem"; touch "$ROOT/calls"; }
STATE=$ROOT/mm-netselect-modem

run() { # run <shell snippet>
    NETSELECT_STATE_DIR=$ROOT PATH="$BIN:$PATH" sh -c ". '$ROOT/lib.sh'; . '$ROOT/funcs.sh'; $1" 2>&1
}

check() { name=$1; shift
    if "$@"; then PASS=$((PASS + 1)); printf '  ok   %s\n' "$name"
    else FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$name"; sed 's/^/       calls: /' "$ROOT/calls"; fi
}
called() { grep -q -- "$1" "$ROOT/calls"; }
not_called() { ! grep -q -- "$1" "$ROOT/calls"; }

echo "netselect:"

# --- home PLMN detection ---------------------------------------------------
reset; sim 510011720015905
check "IMSI 51001... -> 51001 (Indosat)" [ "$(run 'modemmanager_home_plmn M')" = 51001 ]

reset; sim 510101234567890
check "IMSI 51010... -> 51010 (Telkomsel)" [ "$(run 'modemmanager_home_plmn M')" = 51010 ]

reset; sim 310260123456789
check "3-digit-MNC country (MCC 310) -> 6 digits" [ "$(run 'modemmanager_home_plmn M')" = 310260 ]

reset; sim 510891234567890 51001
check "SIM operator-code wins over the IMSI prefix" [ "$(run 'modemmanager_home_plmn M')" = 51001 ]

reset
check "no SIM -> nothing" [ -z "$(run 'modemmanager_home_plmn M')" ]

# --- selection --------------------------------------------------------------
reset; sim 510011720015905; run 'modemmanager_network_select M modem "" "" 1' >/dev/null
check "default (no netselect, no plmn) = home: registers in 51001" \
    sh -c "grep -q -- '--3gpp-register-in-operator=51001' '$ROOT/calls' && ! grep -q -- '--3gpp-register-home' '$ROOT/calls'"

reset; sim 510011720015905; touch "$ROOT/home_fail"
run 'modemmanager_network_select M modem home "" 1; echo rc=$?' > "$ROOT/out"
check "home not found once -> stays locked (no automatic), no restart, count 1" \
    sh -c "! grep -q -- '--3gpp-register-home' '$ROOT/calls' && ! grep -q notify_error '$ROOT/calls' && grep -q rc=0 '$ROOT/out' && [ \"\$(cat '$STATE')\" = '51001 1' ]"

reset; sim 510011720015905; touch "$ROOT/home_fail"; echo "51001 4" > "$STATE"
run 'modemmanager_network_select M modem home "" 1' > /dev/null
check "5th failure in a row -> automatic selection" \
    sh -c "grep -q -- '--3gpp-register-home' '$ROOT/calls' && [ \"\$(cat '$STATE')\" = '51001 5' ]"

reset; sim 510011720015905; echo "51001 5" > "$STATE"
run 'modemmanager_network_select M modem home "" 1' > /dev/null
check "after 5 failures: skip the home attempt, go automatic" \
    sh -c "grep -q -- '--3gpp-register-home' '$ROOT/calls' && ! grep -q -- 'register-in-operator' '$ROOT/calls'"

reset; sim 510101234567890; echo "51001 5" > "$STATE"
run 'modemmanager_network_select M modem home "" 1' > /dev/null
check "SIM changed (Telkomsel) -> old failure count ignored, tries 51010" \
    called "--3gpp-register-in-operator=51010"

reset; sim 510011720015905; echo "51001 3" > "$STATE"
run 'modemmanager_network_select M modem home "" 1' > /dev/null
check "successful home registration clears the failure count" [ ! -e "$STATE" ]

reset; run 'modemmanager_network_select M modem home "" 1' >/dev/null
check "home with no SIM data -> automatic" called "--3gpp-register-home"

reset; sim 510011720015905; run 'modemmanager_network_select M modem auto "" 1' >/dev/null
check "auto -> automatic selection, no operator lock" \
    sh -c "grep -q -- '--3gpp-register-home' '$ROOT/calls' && ! grep -q -- 'register-in-operator' '$ROOT/calls'"

reset; sim 510011720015905; run 'modemmanager_network_select M modem manual 51011 1' >/dev/null
check "manual 51011 -> locks to 51011, not the SIM's network" \
    sh -c "grep -q -- '--3gpp-register-in-operator=51011' '$ROOT/calls' && ! grep -q -- 'operator=51001' '$ROOT/calls'"

reset; sim 510011720015905; run 'modemmanager_network_select M modem "" 51010 1' >/dev/null
check "legacy config: plmn without netselect = manual" called "--3gpp-register-in-operator=51010"

reset; sim 510011720015905; run 'modemmanager_network_select M modem manual "" 1' >/dev/null
check "manual without plmn -> automatic" called "--3gpp-register-home"

reset; sim 510011720015905; touch "$ROOT/home_fail"
run 'modemmanager_network_select M modem manual 51011 1; echo rc=$?' > "$ROOT/out"
check "manual lock failing keeps the old behaviour (restart request)" \
    sh -c "grep -q 'notify_error modem MM_INTERFACE_RESTART' '$ROOT/calls' && grep -q rc=1 '$ROOT/out'"

reset; echo "51001 3" > "$STATE"; echo 51001 > "$ROOT/op"
run 'modemmanager_netselect_connected M modem' > /dev/null
check "connected on the home network (registered late) clears the count" [ ! -e "$STATE" ]

reset; echo "51001 3" > "$STATE"; echo 51011 > "$ROOT/op"
run 'modemmanager_netselect_connected M modem' > /dev/null
check "connected on another network keeps the count" [ "$(cat "$STATE")" = "51001 3" ]

echo "netselect: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
