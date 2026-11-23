#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
#
# Network-free tests for modem-speedtest's Ookla engine.
#
#   DEVICE=root@192.168.8.1 tests/speedtest-ookla/run.sh   # needs ucode + jsonfilter
#
# A stub "speedtest" replays Speedtest CLI JSON-lines output (success, server
# unreachable, not installed) and the tests check the status/history files the
# LuCI page reads: Mbps conversion from bytes/s, server/ISP, result URL, errors.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT_SRC=${SCRIPT_SRC:-$HERE/../../msm89xx/base-files/usr/sbin/modem-speedtest}

if [ -n "${DEVICE:-}" ]; then
    SSH="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes -o LogLevel=ERROR"
    D=/tmp/speedtest-ookla-test.$$
    $SSH "$DEVICE" "mkdir -p $D" || exit 1
    $SSH "$DEVICE" "cat > $D/run.sh" < "$0"
    $SSH "$DEVICE" "cat > $D/modem-speedtest" < "$SCRIPT_SRC"
    $SSH "$DEVICE" "SCRIPT_SRC=$D/modem-speedtest sh $D/run.sh; rc=\$?; rm -rf $D; exit \$rc"
    exit $?
fi

for t in ucode jsonfilter; do
    command -v $t >/dev/null 2>&1 || { echo "SKIP: $t not found (try DEVICE=root@<dongle>)"; exit 0; }
done

PASS=0; FAIL=0
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/speedtest-ookla-test.XXXXXX"); trap 'rm -rf "$ROOT"' EXIT
STATUS=$ROOT/.speedtest_status.json
HIST=$ROOT/.speedtest_history.json

check() { name=$1; shift
    if "$@" >/dev/null 2>&1; then PASS=$((PASS + 1)); printf '  ok   %s\n' "$name"
    else FAIL=$((FAIL + 1)); printf '  FAIL %s\n' "$name"; sed 's/^/       status: /' "$STATUS" 2>/dev/null | head -30; fi
}
field() { jsonfilter -i "$STATUS" -e "@.$1"; }
is() { [ "$(field "$1")" = "$2" ]; }
num_is() { awk -v a="$(field "$1")" -v b="$2" 'BEGIN { exit !(a != "" && a + 0 == b + 0) }'; }

stub() { # stub <fixture-file>: speedtest that prints the fixture and records its args
    cat > "$ROOT/speedtest" <<EOF
#!/bin/sh
echo "\$*" > "$ROOT/args"
cat "$1"
EOF
    chmod +x "$ROOT/speedtest"
}

run() { # run [server-id]
    SPEEDTEST_STATE_DIR=$ROOT SPEEDTEST_BIN=$ROOT/speedtest sh "$SCRIPT_SRC" ookla standard "${1:-}" >/dev/null 2>&1
}

# Speedtest CLI -f jsonl -p yes output, trimmed to the fields that matter.
cat > "$ROOT/ok.jsonl" <<'EOF'
{"type":"testStart","timestamp":"2026-11-23T00:30:00Z","isp":"Indosat Ooredoo","interface":{"internalIp":"10.53.183.153","name":"wwan0","isVpn":false},"server":{"id":12345,"host":"speedtest.example.id","port":8080,"name":"Indosat Ooredoo","location":"Jakarta","country":"Indonesia","ip":"203.0.113.10"}}
{"type":"ping","timestamp":"2026-11-23T00:30:01Z","ping":{"jitter":3.1,"latency":41.5,"progress":0.5}}
{"type":"download","timestamp":"2026-11-23T00:30:05Z","download":{"bandwidth":1250000,"bytes":5000000,"elapsed":4000,"progress":0.5}}
{"type":"upload","timestamp":"2026-11-23T00:30:15Z","upload":{"bandwidth":250000,"bytes":1000000,"elapsed":4000,"progress":0.5}}
{"type":"result","timestamp":"2026-11-23T00:30:20Z","ping":{"jitter":2.8,"latency":40.2,"low":35.1,"high":52.3},"download":{"bandwidth":2500000,"bytes":30000000,"elapsed":10000},"upload":{"bandwidth":500000,"bytes":6000000,"elapsed":10000},"packetLoss":0,"isp":"Indosat Ooredoo","interface":{"internalIp":"10.53.183.153","name":"wwan0","isVpn":false},"server":{"id":12345,"host":"speedtest.example.id","port":8080,"name":"Indosat Ooredoo","location":"Jakarta","country":"Indonesia","ip":"203.0.113.10"},"result":{"id":"abc-123","url":"https://www.speedtest.net/result/c/abc-123","persisted":true}}
EOF

cat > "$ROOT/down.jsonl" <<'EOF'
{"type":"log","timestamp":"2026-11-23T00:30:00Z","message":"Configuration - Couldn't resolve host name (HostNotFoundException)","level":"error"}
{"error":"Cannot read: "}
EOF

echo "speedtest-ookla:"

stub "$ROOT/ok.jsonl"; run
check "completed result is written" is state completed
check "download 2,500,000 B/s -> 20 Mbps" num_is download_mbps 20
check "upload 500,000 B/s -> 4 Mbps" num_is upload_mbps 4
check "ping / jitter / low / high from the result" sh -c "[ \"\$(jsonfilter -i '$STATUS' -e @.ping_avg -e @.jitter -e @.ping_min -e @.ping_max | tr '\n' ' ')\" = '40.2 2.8 35.1 52.3 ' ]"
check "server, location and ISP from Ookla" sh -c "[ \"\$(jsonfilter -i '$STATUS' -e @.server -e @.location -e @.isp | tr '\n' '|')\" = 'Indosat Ooredoo|Jakarta|Indosat Ooredoo|' ]"
check "result URL kept for the speedtest.net link" is result_url "https://www.speedtest.net/result/c/abc-123"
check "engine marked ookla" is engine ookla
check "license accepted non-interactively, JSON-lines with progress" grep -q -- "--accept-license --accept-gdpr -f jsonl -p yes" "$ROOT/args"
check "appended to history" sh -c "[ \"\$(jsonfilter -i '$HIST' -e '@[0].result_url')\" = 'https://www.speedtest.net/result/c/abc-123' ]"
check "pid file removed" [ ! -e "$ROOT/.speedtest.pid" ]

rm -f "$STATUS" "$HIST"; stub "$ROOT/ok.jsonl"; run 12345
check "server id is passed with -s" grep -q -- "-s 12345" "$ROOT/args"

rm -f "$STATUS" "$HIST"; stub "$ROOT/ok.jsonl"; run '1;reboot'
check "non-numeric server id is ignored (no -s, no injection)" sh -c "! grep -q -- ' -s ' '$ROOT/args'"

rm -f "$STATUS" "$HIST"; stub "$ROOT/down.jsonl"; run
check "unreachable servers -> failed with Ookla's message" sh -c "[ \"\$(jsonfilter -i '$STATUS' -e @.state)\" = failed ] && jsonfilter -i '$STATUS' -e @.error | grep -q -e 'resolve host' -e 'Cannot read'"
check "failed run is not added to history" [ ! -s "$HIST" ]

rm -f "$STATUS" "$HIST" "$ROOT/speedtest"; run
check "not installed -> failed, tells how to install" sh -c "[ \"\$(jsonfilter -i '$STATUS' -e @.state)\" = failed ] && jsonfilter -i '$STATUS' -e @.error | grep -q ookla-speedtest-install"

echo "speedtest-ookla: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
