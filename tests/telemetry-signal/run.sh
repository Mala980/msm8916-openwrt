#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
#
# Modem-free tests for luci.proton-system's cellular reporting: the status-bar
# summary from getTelemetry (label / service / bars) and the band-lock page data
# from getSignalDetails (no invented operator/band, real registration info).
#
#   tests/telemetry-signal/run.sh                 # needs ucode with fs/uci/ubus
#   DEVICE=root@192.168.8.1 tests/telemetry-signal/run.sh   # run it on a dongle
#
# The rpcd module is copied to a scratch dir with every "/tmp/." cache path
# redirected there, then getTelemetry() is called with stubs for mmcli, qmicli
# and smsctl first in PATH. The live modem is never touched. Each case feeds a
# ModemManager key-value dump (+ optional cell-info / qmicli signal output) and
# asserts on the label, service flag and bar count the UI renders.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
MOD_SRC=${MOD_SRC:-$HERE/../../packages/luci-theme-proton2025/root/usr/share/rpcd/ucode/luci.proton-system}

if [ -n "${DEVICE:-}" ]; then
    SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes -o LogLevel=ERROR"
    D=/tmp/telemetry-signal-test.$$
    $SSH "$DEVICE" "mkdir -p $D" || exit 1
    # Feed the files over ssh stdin: dropbear has no sftp-server for scp.
    $SSH "$DEVICE" "cat > $D/run.sh" < "$0"
    $SSH "$DEVICE" "cat > $D/luci.proton-system" < "$MOD_SRC"
    $SSH "$DEVICE" "MOD_SRC=$D/luci.proton-system sh $D/run.sh; rc=\$?; rm -rf $D; exit \$rc"
    exit $?
fi

command -v ucode >/dev/null 2>&1 || { echo "SKIP: ucode not found (try DEVICE=root@<dongle>)"; exit 0; }

PASS=0; FAIL=0
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/telemetry-signal-test.XXXXXX"); trap 'rm -rf "$ROOT"' EXIT
FX=$ROOT/fx; BIN=$ROOT/bin; mkdir -p "$FX" "$BIN"

sed "s#\"/tmp/#\"$ROOT/#g" "$MOD_SRC" > "$ROOT/mod.uc"

# --- stubs -----------------------------------------------------------------
cat > "$BIN/mmcli" <<EOF
#!/bin/sh
case "\$*" in
    *--get-cell-info*)   [ -s "$FX/cell" ] && cat "$FX/cell" || exit 1 ;;
    *--signal-get*)      [ -s "$FX/sig" ]  && cat "$FX/sig"  || exit 1 ;;
    *--output-keyvalue*) [ -s "$FX/kv" ]   && cat "$FX/kv"   || exit 1 ;;
    *--command=*)        [ -s "$FX/kv" ]   && echo "response: '+COPS: 0,0,\"TESTNET\",7'" || exit 1 ;;
    *) exit 1 ;;
esac
EOF
cat > "$BIN/qmicli" <<EOF
#!/bin/sh
case "\$*" in
    *--nas-get-serving-system*) [ -s "$FX/srv" ] && cat "$FX/srv" || exit 1 ;;
    *) [ -s "$FX/qmi" ] && cat "$FX/qmi" || exit 1 ;;
esac
EOF
printf '#!/bin/sh\necho "{\\"inbox\\": 0, \\"unread\\": 0}"\n' > "$BIN/smsctl"
chmod +x "$BIN"/*

cat > "$ROOT/call.uc" <<'EOF'
let m = loadfile(ARGV[0])();
let r = m["luci.proton-system"].getTelemetry.call();
printf("label=%s|service=%s|bars=%d|band=%s|rat=%s|op=%s\n", r.label, r.service ? "yes" : "no", r.bars, r.band, r.rat, r.operator);
EOF
cat > "$ROOT/details.uc" <<'EOF'
let m = loadfile(ARGV[0])();
let r = m["luci.proton-system"].getSignalDetails.call();
printf("op=%s|code=%s|registered=%s|plmn=%s|status=%s|forbidden=%s|csq=%s|band=%s|bandname=%s|duplex=%s|wd=%s\n",
	r.operator.name, r.operator.code, r.operator.registered ? "yes" : "no",
	r.registration.plmn, r.registration.status, r.registration.forbidden ? "yes" : "no",
	r.signal.csq, r.serving.band, r.serving.band_name, r.serving.duplex,
	substr(r.hardware.watchdog, 0, 9));
EOF

# --- fixture helpers -------------------------------------------------------
reset_fx() { rm -f "$FX"/* "$ROOT"/.proton_* "$ROOT"/.telemetry_cache.json; }

# kv <state> <reg-state> <access-tech> <quality>
kv() {
    cat > "$FX/kv" <<EOF
modem.generic.state                             : $1
modem.generic.access-technologies               : $3
modem.generic.signal-quality.value              : $4
modem.3gpp.operator-name                        : TESTNET
modem.3gpp.operator-code                        : 51001
modem.3gpp.registration-state                   : $2
EOF
}

# qmi <rsrp> <snr>: what `qmicli --nas-get-signal-info` prints on LTE
qmi() {
    printf "LTE:\n\tRSSI: '-73 dBm'\n\tRSRQ: '-12 dB'\n\tRSRP: '%s dBm'\n\tSNR: '%s dB'\n" "$1" "$2" > "$FX/qmi"
}

# cell <earfcn>: serving LTE cell from `mmcli --get-cell-info -J`
cell() {
    printf '{"modem":{"generic":{"cell-info":["cell type: lte, serving: yes, ci: 0A1B2C, physical ci: 301, tac: 1A2B, earfcn: %s, rsrp: -90.00, rsrq: -10.00"]}}}\n' "$1" > "$FX/cell"
}

# srv <reg-state> <mcc> <mnc> <status> <forbidden>: `qmicli --nas-get-serving-system`.
# The trailing "Full operator code info" PLMN must not override Current PLMN.
srv() {
    printf "Successfully got serving system:\n\tRegistration state: '%s'\n\tCurrent PLMN:\n\t\tMCC: '%s'\n\t\tMNC: '%s'\n\tDetailed status:\n\t\tStatus: '%s'\n\t\tForbidden: '%s'\n\tFull operator code info:\n\t\tMCC: '999'\n\t\tMNC: '99'\n" "$1" "$2" "$3" "$4" "$5" > "$FX/srv"
}

expect() { name=$1 want=$2 script=${3:-call}
    got=$(PATH="$BIN:$PATH" ucode "$ROOT/$script.uc" "$ROOT/mod.uc" 2>&1 | tail -1)
    case "$got" in
        *"$want"*) PASS=$((PASS + 1)); printf '  ok   %s\n' "$name" ;;
        *) FAIL=$((FAIL + 1)); printf '  FAIL %s\n       want: %s\n       got:  %s\n' "$name" "$want" "$got" ;;
    esac
}

# --- cases -----------------------------------------------------------------
echo "telemetry-signal:"

reset_fx
expect "no modem -> dash, no service, 0 bars" "label=—|service=no|bars=0|band=|rat="

reset_fx; kv connected searching -- 83; qmi -112 -9.2
expect "stale bearer, registration searching -> No Service, 0 bars (not 83%)" "label=No Service|service=no|bars=0"

reset_fx; kv registered denied -- 80
expect "registration denied -> No Service" "label=No Service|service=no|bars=0"

reset_fx; kv connected home lte 80; qmi -112 -9.2
expect "LTE, no cell info, RSRP -112 SNR -9.2 -> 4G, 1 bar" "label=4G|service=yes|bars=1|band=|rat=4G"

reset_fx; kv connected home lte 90; qmi -84 12; cell 1850
expect "LTE B3 (EARFCN 1850), RSRP -84 SNR 12 -> 4G B3, 5 bars" "label=4G B3|service=yes|bars=5|band=B3|rat=4G"

reset_fx; kv connected home lte 70; qmi -100 -2; cell 1850
expect "RSRP -100, SNR -2 -> 3 bars minus 1" "label=4G B3|service=yes|bars=2"

reset_fx; kv connected home lte 70; qmi -90 3; cell 1850
expect "RSRP -90, SNR 3 -> 4 bars" "bars=4"

reset_fx; kv connected roaming lte 70; qmi -104 1
expect "roaming counts as service" "label=4G|service=yes|bars=3"

reset_fx; kv connected home "umts, hsdpa" 55
expect "UMTS, no RSRP -> 3G from RSSI quality (55% -> 3 bars)" "label=3G|service=yes|bars=3|band=|rat=3G"

reset_fx; kv connected home gsm 15
expect "GSM -> 2G, 1 bar" "label=2G|service=yes|bars=1"

reset_fx; kv connected home -- 70; qmi -95 5; cell 38750
expect "access tech unknown but cell read (B40) -> 4G B40" "label=4G B40|service=yes|bars=4|band=B40"

reset_fx; kv connected home lte 70; qmi -95 5; cell ""
expect "empty EARFCN is not reported as B1" "label=4G|service=yes|bars=4|band=|rat=4G"

reset_fx; kv connected searching -- 83; qmi -112 -9.2
expect "status bar: no stale operator name while searching" "service=no|bars=0|band=|rat=|op="

echo "band-lock page (getSignalDetails):"

reset_fx; kv connected searching -- 83; qmi -94 -5.8; srv registration-denied 510 11 limited yes; cell 2450
expect "not registered: stale operator dropped, camped PLMN + forbidden shown" \
    "op=|code=|registered=no|plmn=51011|status=limited|forbidden=yes" details

reset_fx; kv connected home lte 70; qmi -95 5; srv registered 510 1 available no; cell 1850
expect "registered: operator/code from the modem, 1-digit MNC padded" \
    "op=TESTNET|code=51001|registered=yes|plmn=51001|status=available|forbidden=no" details

reset_fx; kv connected home lte 70; qmi -95 5
expect "CSQ from measured RSSI (-73 dBm -> 20), not from the % value" "csq=20|" details

reset_fx; kv connected home lte 70
expect "no RSSI -> CSQ unknown, not derived from %" "csq=--|" details

reset_fx; kv connected home lte 70; qmi -95 5
expect "no cell info -> no invented band/duplex" "band=|bandname=|duplex=|" details

reset_fx; kv connected home lte 70; qmi -95 5; cell 9999
expect "EARFCN outside the band table -> unknown band, not 'LTE'" "band=|bandname=Unknown band (EARFCN 9999)|duplex=|" details

reset_fx; kv connected home lte 70; qmi -95 5; cell ""
expect "empty EARFCN -> not B1" "band=|bandname=|" details

reset_fx; kv connected home lte 70
expect "watchdog line is read from the system, not a fixed string" "wd=Modem DSP" details

echo "telemetry-signal: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
