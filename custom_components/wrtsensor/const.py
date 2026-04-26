from __future__ import annotations

from datetime import timedelta

DOMAIN = "wrtsensor"
VERSION = "1.0.0"

PLATFORMS = ["sensor", "binary_sensor", "device_tracker"]

SCAN_INTERVAL = timedelta(seconds=60)
SSH_TIMEOUT = 20
AP_SSH_TIMEOUT = 8
SSH_KNOWN_HOSTS_POLICY = "autoadd"

DEFAULT_SSH_KEY = "/config/ssh/id_ed25519"
DEFAULT_SSH_PORT = 22
DEFAULT_LAN_IFACE = "br-lan"
DEFAULT_WAN_IFACE = "eth0"
DEFAULT_DHCP_LEASES = "/tmp/dhcp.leases"
DEFAULT_SCAN_INTERVAL = 60

STATE_DIR_HA = "/dev/shm"
STATE_DIR_LOCAL = "/tmp/netscan"

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
CONF_SCAN_INTERVAL = "scan_interval"
CONF_PRESENCE_MACS = "presence_macs"
CONF_ENABLE_DNS_STATS = "enable_dns_stats"
DEFAULT_ENABLE_DNS_STATS = True

# Static path served by HA http component
STATIC_PATH_URL = "/wrtsensor_static"
