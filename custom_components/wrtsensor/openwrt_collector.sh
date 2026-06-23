#!/bin/sh
# Collect WiFi associations from an OpenWrt host via iwinfo + iw.
# Outputs one pipe-delimited line per associated client:
#   MAC|band|essid|signal_dBm|tx_phy_rate|channel|sta_ul_bytes|sta_dl_bytes|noise_dBm|snr_dB|rx_phy_rate|exp_tput
# Also emits a STAT| line with host CPU/RAM/disk snapshot:
#   STAT|<cpu_stat_line>|<mem_total_kB>|<mem_available_kB>|<root_disk_use_pct>
# And one FDB| line per MAC learned on a bridged switch port (DSA):
#   FDB|<MAC>|<port_netdev>
# Called remotely over SSH by diagnose.py / coordinator.py.
# Wi-Fi collection requires iwinfo; hosts without it (e.g. a managed switch)
# still emit BOARD, STAT, and FDB lines and just skip the Wi-Fi section.

collect_host_metrics=1
if [ "${1:-}" = "--no-host-metrics" ]; then
    collect_host_metrics=0
fi

# Clean up leftover children only on interrupt/termination. Deliberately NOT on
# EXIT: `kill 0` on normal completion also tears down the SSH session's process
# group (incl. a ControlMaster mux), which truncates output for callers that run
# this script as an SSH command argument (e.g. diagnose.py).
trap 'kill 0' INT TERM

echo "BOARD|$(ubus call system board 2>/dev/null)"
if [ "$collect_host_metrics" -eq 1 ]; then
    cpu_line=$(grep '^cpu ' /proc/stat 2>/dev/null)
    mem=$(awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END{print t "|" a}' /proc/meminfo 2>/dev/null)
    disk=$(df / 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5+0}')
    echo "STAT|${cpu_line}|${mem}|${disk}"
fi

# Bridge forwarding DB: which physical port each MAC was learned on. With DSA
# every port is its own netdev (lanN) enslaved to a bridge. Prefer iproute2
# `bridge`; fall back to bridge-utils `brctl` on builds without it (e.g. the
# Zyxel GS1900). Devices with neither tool simply emit nothing here.
if command -v bridge >/dev/null 2>&1; then
    # Skip local/permanent (self) entries; keep dynamic entries learned on an
    # enslaved port (has `master`).
    bridge fdb show 2>/dev/null | awk '
      /permanent/ || / self/ { next }
      {
        mac=""; port=""; master=0
        if ($1 ~ /^[0-9A-Fa-f][0-9A-Fa-f]:/) mac=toupper($1)
        for (i=1; i<NF; i++) {
          if ($i == "dev") port=$(i+1)
          if ($i == "master") master=1
        }
        if (mac != "" && port != "" && master) print "FDB|" mac "|" port
      }'
elif command -v brctl >/dev/null 2>&1; then
    # brctl showmacs prints a decimal bridge port number; map it to the slave
    # netdev (lanN) via sysfs, where port_no is hex. Only non-local entries.
    # Uses shell builtins (read, parameter expansion) instead of cat/basename to
    # avoid dozens of fork/exec per run on RAM-constrained switches.
    for brpath in /sys/class/net/*/bridge; do
        [ -d "$brpath" ] || continue
        br=${brpath%/bridge}
        br=${br##*/}
        {
            for p in /sys/class/net/"$br"/brif/*; do
                [ -e "$p/port_no" ] || continue
                read -r pn < "$p/port_no" || continue
                printf 'P|%d|%s\n' "$pn" "${p##*/}"
            done
            brctl showmacs "$br" 2>/dev/null
        } | awk '
          /^P\|/ { split($0, a, "|"); iface[a[2]] = a[3]; next }
          $3 == "no" && $1 ~ /^[0-9]+$/ {
            mac = toupper($2); port = iface[$1]
            if (port != "") print "FDB|" mac "|" port
          }'
    done
fi

# Wi-Fi section needs iwinfo; switches and wired-only hosts stop here.
command -v iwinfo >/dev/null 2>&1 || exit 0

# Exit cleanly if no wireless interfaces are up
ifaces=$(iwinfo 2>/dev/null | grep "ESSID" | cut -d" " -f1)
[ -z "$ifaces" ] && exit 0

for iface in $ifaces; do
  info=$(iwinfo "$iface" info 2>/dev/null)
  freq=$(echo "$info" | grep -o "[0-9]\.[0-9]* GHz" | head -1)
  case "$freq" in
    2.*) band="2.4GHz" ;;
    5.*) band="5GHz" ;;
    6.*) band="6GHz" ;;
    *)   band="unknown" ;;
  esac
  channel=$(echo "$info" | awk '/Channel:/{print $2; exit}')
  essid=$(echo "$info" | grep "ESSID:" | sed 's/.*ESSID: "//;s/".*//')
  { echo "---STATION---"
    iw dev "$iface" station dump 2>/dev/null
    echo "---ASSOC---"
    iwinfo "$iface" assoclist 2>/dev/null
  } | awk -v band="$band" -v essid="$essid" -v channel="$channel" '
    function flush() {
      if (mac != "") print mac "|" band "|" essid "|" signal "|" tx_rate "|" channel \
        "|" (sta_ul[mac]+0) "|" (sta_dl[mac]+0) "|" noise "|" snr "|" rx_rate "|" exp_tput
    }
    /^---STATION---/ { section="station"; next }
    /^---ASSOC---/   { section="assoc"; next }
    section == "station" {
      if (/^Station /) { cur_mac = toupper($2) }
      else if (/rx bytes:/) { sta_ul[cur_mac] = $3 }
      else if (/tx bytes:/) { sta_dl[cur_mac] = $3 }
    }
    section == "assoc" {
      if (/^[0-9A-Fa-f][0-9A-Fa-f]:/) {
        flush()
        mac = toupper($1)
        signal = ""; noise = ""; snr = ""; tx_rate = ""; rx_rate = ""; exp_tput = ""
        for (i=2; i<=NF; i++) {
          # Handle both "-63 dBm / -95 dBm" and "-63/-95 dBm" formats
          if ($i ~ /^-[0-9]+$/ && $(i+1) == "dBm") {
            if (signal == "") signal = $i
            else if (noise == "" && $(i-1) == "/") noise = $i
          } else if ($i ~ /^-[0-9]+\/-[0-9]+$/ && $(i+1) == "dBm") {
            n = split($i, parts, "/")
            if (n == 2) { if (signal == "") signal = parts[1]; if (noise == "") noise = parts[2] }
          }
          if ($i == "(SNR") { v = $(i+1); gsub(/[^0-9]/, "", v); snr = v }
        }
      } else if (/^[[:space:]]+RX:/) {
        for (i=1; i<=NF; i++) if ($i ~ /^[0-9.]+$/ && $(i+1) ~ /^MBit\/s/) { rx_rate=$i; break }
      } else if (/^[[:space:]]+TX:/) {
        for (i=1; i<=NF; i++) if ($i ~ /^[0-9.]+$/ && $(i+1) ~ /^MBit\/s/) { tx_rate=$i; break }
      } else if (/expected throughput:/) {
        for (i=1; i<=NF; i++) if ($i ~ /^[0-9.]+$/) { exp_tput=$i; break }
      }
    }
    END { flush() }
  '
done
