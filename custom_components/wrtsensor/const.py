from __future__ import annotations

from datetime import timedelta

DOMAIN = "wrtsensor"
VERSION = "2.0.1"

PLATFORMS = ["sensor", "binary_sensor", "device_tracker", "update"]

SCAN_INTERVAL = timedelta(seconds=60)
SSH_TIMEOUT = 20
AP_SSH_TIMEOUT = 8
SSH_KNOWN_HOSTS_POLICY = "autoadd"

DEFAULT_SSH_KEY = "/config/ssh/id_ed25519"
DEFAULT_SSH_PORT = 22
DEFAULT_LAN_IFACE = "br-lan"
DEFAULT_WAN_IFACE = "eth0"
DEFAULT_DHCP_LEASES = "/tmp/dhcp.leases"

STATE_DIR_HA = "/dev/shm"
STATE_DIR_LOCAL = "/tmp/netscan"
STATE_FILE_PREV_STATE = ".netscan_prev_state.json"
STATE_FILE_MAC_VENDORS = ".netscan_mac_vendors"
STATE_FILE_DNS_CACHE = ".netscan_dns_cache"
STATE_FILE_DNS_HISTORY = ".netscan_dns_history.jsonl"
# Runtime state cleanup allowlist. Adding a basename here deletes it on removal.
STATE_FILE_BASENAMES = (
    STATE_FILE_PREV_STATE,
    STATE_FILE_MAC_VENDORS,
    STATE_FILE_DNS_CACHE,
    STATE_FILE_DNS_HISTORY,
)

COLLECTOR_SCRIPT_NAME = "openwrt_collector.sh"
COLLECTOR_REMOTE_PATH = "/tmp/wrtsensor_collector.sh"

DISCONNECT_MISS_THRESHOLD = 3  # kept for wrtsensor.py standalone compat
CONF_DISCONNECT_THRESHOLD = "disconnect_threshold_s"
DEFAULT_DISCONNECT_THRESHOLD = 120  # seconds
STATE_MAX_AGE_DAYS = 7

BW_MAX_AGE_S = 600
BW_MIN_ELAPSED_S = 10
BW_MAX_RATE_BPS = 125_000_000

# Config entry keys
CONF_GATEWAY_HOST = "gateway_host"
CONF_SSH_KEY_PATH = "ssh_key_path"
CONF_SSH_PORT = "ssh_port"
CONF_AP_HOSTS = "ap_hosts"
CONF_LAN_IFACE = "lan_iface"
CONF_WAN_IFACE = "wan_iface"
CONF_PRESENCE_MACS = "presence_macs"
CONF_ENABLE_NETWORK_HOSTS = "enable_network_hosts"
DEFAULT_ENABLE_NETWORK_HOSTS = True
CONF_ENABLE_WAN_BANDWIDTH = "enable_wan_bandwidth"
DEFAULT_ENABLE_WAN_BANDWIDTH = True
CONF_ENABLE_DNS_STATS = "enable_dns_stats"
DEFAULT_ENABLE_DNS_STATS = True
CONF_ENABLE_HOST_METRICS = "enable_host_metrics"
DEFAULT_ENABLE_HOST_METRICS = True
CONF_ENABLE_WIREGUARD = "enable_wireguard"
DEFAULT_ENABLE_WIREGUARD = False
CONF_WG_STALE_THRESHOLD = "wg_stale_threshold_s"
DEFAULT_WG_STALE_THRESHOLD = 180
CONF_ENABLE_ASU = "enable_asu"
DEFAULT_ENABLE_ASU = False
CONF_ASU_INTERVAL_H = "asu_interval_h"
DEFAULT_ASU_INTERVAL_H = 6
ASU_INTERVAL_MIN_H = 1
ASU_INTERVAL_MAX_H = 24
ASU_PROBE_TIMEOUT_S = 45
ASU_PROBE_GAP_S = 15

# Static path served by HA http component
STATIC_PATH_URL = "/wrtsensor_static"
