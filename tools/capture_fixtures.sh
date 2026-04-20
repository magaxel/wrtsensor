#!/bin/sh
# Capture OpenWrt command output for the wrtsensor test fixture corpus.
#
# Usage: ./tools/capture_fixtures.sh <ssh-target> <role> <version>
#   role    = gateway | ap1 | ap2 | ap3 | ap<N>
#   version = OpenWrt release label, e.g. 25.12.2
#
# Output lands in tests/fixtures/openwrt/<version>/<role>/.
# After running, pipe through tools/sanitise_fixtures.py before committing.

set -u

if [ $# -ne 3 ]; then
    echo "usage: $0 <ssh-target> <role> <version>" >&2
    echo "  role    = gateway | ap1 | ap2 | ap3 ..." >&2
    echo "  version = e.g. 25.12.2" >&2
    exit 1
fi

target=$1
role=$2
version=$3

repo_root=$(cd "$(dirname "$0")/.." && pwd)
out="$repo_root/tests/fixtures/openwrt/$version/$role"
mkdir -p "$out"

fail_count=0

run() {
    label=$1
    shift
    cmd="$*"
    printf '  %-32s ' "$label"
    if ssh -o BatchMode=yes "$target" "$cmd" >"$out/$label" 2>/dev/null; then
        echo "ok"
    else
        echo "failed (command not available on this version?)"
        fail_count=$((fail_count + 1))
    fi
}

echo "→ capturing $role on $target into $out"

# Common to every role
run hostname.txt          'cat /proc/sys/kernel/hostname'
run proc-stat.txt         'grep "^cpu " /proc/stat'
run proc-meminfo.txt      'awk "/^MemTotal:|^MemAvailable:/" /proc/meminfo'
run df-root.txt           'df /'
run ip-addr-br-lan.txt    'ip addr show br-lan'

case "$role" in
    gateway)
        run dhcp.leases          'cat /tmp/dhcp.leases'
        run ip-neigh.txt         'ip -4 neigh show dev br-lan'
        run ip-neigh6.txt        'ip -6 neigh show dev br-lan'
        run ip-addr-wan.txt      'ip addr show eth0'
        run wan-rx-bytes.txt     'cat /sys/class/net/eth0/statistics/rx_bytes'
        run wan-tx-bytes.txt     'cat /sys/class/net/eth0/statistics/tx_bytes'
        # shellcheck disable=SC2016  # $(pidof dnsmasq) intentionally expands on the remote
        run logread-dnsmasq.txt  'kill -USR1 $(pidof dnsmasq) 2>/dev/null; sleep 1; logread -l 60 | grep dnsmasq'
        run nf_conntrack.txt     'cat /proc/net/nf_conntrack'
        ;;
    ap*)
        run iwinfo-list.txt      'iwinfo'
        # Discover wireless interfaces, then capture per-interface state
        ifaces=$(ssh -o BatchMode=yes "$target" 'iwinfo 2>/dev/null | awk "/ESSID/ {print \$1}"')
        for iface in $ifaces; do
            run "iwinfo-${iface}-info.txt"      "iwinfo $iface info"
            run "iwinfo-${iface}-assoclist.txt" "iwinfo $iface assoclist"
            run "iw-station-${iface}.txt"       "iw dev $iface station dump"
        done
        # End-to-end collector output for parse_wifi_output coverage
        collector="$repo_root/custom_components/wrtsensor/openwrt_collector.sh"
        if [ -f "$collector" ]; then
            echo "  piping openwrt_collector.sh via stdin..."
            # The collector's `trap 'kill 0' EXIT` makes ssh return non-zero even on
            # success, so judge by output content (a valid run starts with STAT|).
            ssh -o BatchMode=yes "$target" 'sh -s' <"$collector" >"$out/collector-output.txt" 2>/dev/null
            if head -1 "$out/collector-output.txt" 2>/dev/null | grep -q '^STAT|'; then
                echo "  collector-output.txt              ok"
            else
                echo "  collector-output.txt              failed"
                fail_count=$((fail_count + 1))
            fi
        fi
        ;;
    *)
        echo "unknown role: $role" >&2
        exit 2
        ;;
esac

echo
if [ "$fail_count" -gt 0 ]; then
    echo "completed with $fail_count failure(s). Some commands aren't available on this OpenWrt release — that's expected." >&2
fi
echo "next: python3 tools/sanitise_fixtures.py $out"
