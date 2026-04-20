"""Tests for coordinator parsing functions using captured AP fixture output."""

import sys
from pathlib import Path

import pytest

# conftest.py loads the coordinator; grab it from sys.modules
coord = sys.modules["custom_components.wrtsensor.coordinator"]
parser = sys.modules["custom_components.wrtsensor.parser"]

parse_wifi_output = coord.parse_wifi_output
parse_leases = coord.parse_leases
parse_arp = coord.parse_arp
parse_ndp = coord.parse_ndp
parse_hoststat = coord.parse_hoststat
parse_dns_stats = coord.parse_dns_stats
_is_random_mac = parser._is_random_mac

FIXTURES = Path(__file__).parent / "fixtures"
OPENWRT_FIXTURES = FIXTURES / "openwrt"

# Pin concrete-value tests to this version — update when re-capturing.
PINNED_VERSION = "25.12.2"

# All captured versions — structural tests run across every version.
AVAILABLE_VERSIONS = sorted(d.name for d in OPENWRT_FIXTURES.iterdir() if d.is_dir())


def _collector_output(ap: str, version: str = PINNED_VERSION) -> str:
    return (OPENWRT_FIXTURES / version / ap / "collector-output.txt").read_text()


class TestParseWifiOutputAP3:
    def setup_method(self):
        self.entries, self.hoststat = parse_wifi_output(_collector_output("ap3"), "AP3")

    def test_device_count(self):
        assert len(self.entries) == 3

    def test_mac_uppercase(self):
        macs = {e["mac"] for e in self.entries}
        assert "38:00:00:00:00:01" in macs
        assert "C4:00:00:00:00:02" in macs
        assert "D0:00:00:00:00:03" in macs

    def test_ap_name(self):
        assert all(e["ap"] == "AP3" for e in self.entries)

    def test_bands(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["38:00:00:00:00:01"]["band"] == "5GHz"
        assert by_mac["C4:00:00:00:00:02"]["band"] == "2.4GHz"

    def test_signal(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["38:00:00:00:00:01"]["signal"] == -63
        assert by_mac["C4:00:00:00:00:02"]["signal"] == -23

    def test_noise(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["38:00:00:00:00:01"]["noise"] == -105
        assert by_mac["C4:00:00:00:00:02"]["noise"] == -95

    def test_snr(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["38:00:00:00:00:01"]["snr"] == 42
        assert by_mac["C4:00:00:00:00:02"]["snr"] == 72

    def test_tx_rate(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["38:00:00:00:00:01"]["tx_rate"] == 234.0

    def test_rx_rate(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["38:00:00:00:00:01"]["rx_rate"] == 12.0

    def test_exp_tput(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["38:00:00:00:00:01"]["exp_tput"] == 206.9

    def test_essid(self):
        assert all(e["essid"] == "NetA" for e in self.entries)

    def test_byte_counters(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["38:00:00:00:00:01"]["sta_ul_bytes"] == 1639695
        assert by_mac["38:00:00:00:00:01"]["sta_dl_bytes"] == 1509476

    def test_hoststat_cpu_line(self):
        assert len(self.hoststat) >= 2
        assert self.hoststat[0].startswith("cpu")

    def test_hoststat_10_cpu_fields(self):
        # Fixture has 10 CPU fields — parse_hoststat must handle this
        fields = self.hoststat[0].split()
        assert len(fields) == 11  # "cpu" + 10 values


class TestParseWifiOutputAP2:
    def setup_method(self):
        self.entries, _ = parse_wifi_output(_collector_output("ap2"), "AP2")

    def test_device_count(self):
        assert len(self.entries) == 6

    def test_roam_candidate_present(self):
        # 38:00:00:00:00:01 appears on both AP3 and AP2
        macs = {e["mac"] for e in self.entries}
        assert "38:00:00:00:00:01" in macs

    def test_per_client_essid(self):
        # 48:00:00:00:00:0B is on SSID "NetB", rest on "NetA"
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["48:00:00:00:00:0B"]["essid"] == "NetB"
        assert by_mac["2E:00:00:00:00:01"]["essid"] == "NetA"

    def test_low_byte_counters(self):
        # 2E:00:00:00:00:01 has very low byte counters
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["2E:00:00:00:00:01"]["sta_ul_bytes"] == 3720849
        assert by_mac["2E:00:00:00:00:01"]["sta_dl_bytes"] == 3719870


class TestParseWifiOutputAP1:
    def setup_method(self):
        self.entries, _ = parse_wifi_output(_collector_output("ap1"), "AP1")

    def test_device_count(self):
        assert len(self.entries) == 5

    def test_all_have_ap_name(self):
        assert all(e["ap"] == "AP1" for e in self.entries)


# ── Structural checks across every captured OpenWrt version ──────────────────


@pytest.mark.parametrize("version", AVAILABLE_VERSIONS)
@pytest.mark.parametrize("ap", ["ap1", "ap2", "ap3"])
def test_collector_output_parses_across_versions(version, ap):
    """Every captured collector output must yield well-formed entries on any OpenWrt version."""
    path = OPENWRT_FIXTURES / version / ap / "collector-output.txt"
    if not path.exists():
        pytest.skip(f"no {ap} capture for {version}")
    entries, hoststat = parse_wifi_output(path.read_text(), ap.upper())
    assert entries, f"no entries parsed from {version}/{ap}"
    for e in entries:
        assert e["mac"] == e["mac"].upper()
        assert len(e["mac"].split(":")) == 6
        assert e["band"] in {"2.4GHz", "5GHz", "6GHz", "unknown"}
        assert isinstance(e["essid"], str)
        assert isinstance(e["signal"], int)
        assert e["ap"] == ap.upper()
    # STAT line always emitted by the collector
    assert hoststat, f"no hoststat from {version}/{ap}"
    assert hoststat[0].startswith("cpu")


def test_parse_wifi_empty():
    entries, hoststat = parse_wifi_output("", "ap0")
    assert entries == []
    assert hoststat == []


# ── parse_hoststat ─────────────────────────────────────────────────────────────


def test_parse_hoststat_valid():
    lines = ["cpu  4192637 0 1520078 167022298 0 0 1363451 0 0 0", "120768 37352", "4"]
    result = parse_hoststat(lines)
    assert result is not None
    assert result["busy"] == 4192637 + 1520078
    assert result["idle"] == 167022298
    assert result["mem_total"] == 120768
    assert result["mem_avail"] == 37352
    assert result["disk"] == 4


def test_parse_hoststat_10_cpu_fields():
    # Fixture has "cpu  4192637 0 1520078 167022298 0 0 1363451 0 0 0" — 10 value fields
    line = "cpu  4192637 0 1520078 167022298 0 0 1363451 0 0 0"
    result = parse_hoststat([line, "120768 37352"])
    assert result is not None
    assert result["busy"] == 4192637 + 1520078
    assert result["idle"] == 167022298


def test_parse_hoststat_no_disk():
    lines = ["cpu  100 0 50 800", "131072 65536"]
    result = parse_hoststat(lines)
    assert result is not None
    assert result["disk"] is None


def test_parse_hoststat_too_few_lines():
    assert parse_hoststat([]) is None
    assert parse_hoststat(["cpu  1 0 1 1"]) is None


def test_parse_hoststat_bad_cpu_line():
    assert parse_hoststat(["notcpu 1 2 3 4", "100 50"]) is None


# ── parse_leases ───────────────────────────────────────────────────────────────


def test_parse_leases_valid():
    lines = [
        "1700000000 aa:bb:cc:dd:ee:ff 192.168.1.10 myhost *",
        "1700000001 11:22:33:44:55:66 192.168.1.11 * *",
    ]
    result = parse_leases(lines)
    assert "AA:BB:CC:DD:EE:FF" in result
    assert result["AA:BB:CC:DD:EE:FF"]["ip"] == "192.168.1.10"
    assert result["AA:BB:CC:DD:EE:FF"]["hostname"] == "myhost"
    assert result["11:22:33:44:55:66"]["hostname"] == ""


def test_parse_leases_star_hostname():
    lines = ["1700000000 aa:bb:cc:dd:ee:ff 192.168.1.10 * *"]
    result = parse_leases(lines)
    assert result["AA:BB:CC:DD:EE:FF"]["hostname"] == ""


def test_parse_leases_empty():
    assert parse_leases([]) == {}


# ── parse_arp ─────────────────────────────────────────────────────────────────


def test_parse_arp_reachable():
    lines = ["192.168.1.1 lladdr aa:bb:cc:dd:ee:ff REACHABLE"]
    states, stale, ips = parse_arp(lines)
    assert "AA:BB:CC:DD:EE:FF" in states
    assert states["AA:BB:CC:DD:EE:FF"] == "REACHABLE"
    assert "AA:BB:CC:DD:EE:FF" not in stale
    assert ips["AA:BB:CC:DD:EE:FF"] == "192.168.1.1"


def test_parse_arp_stale():
    lines = ["192.168.1.2 lladdr 11:22:33:44:55:66 STALE"]
    states, stale, ips = parse_arp(lines)
    assert "11:22:33:44:55:66" in stale


def test_parse_arp_failed_excluded():
    lines = ["192.168.1.3 lladdr aa:bb:cc:00:00:01 FAILED"]
    states, stale, ips = parse_arp(lines)
    assert "AA:BB:CC:00:00:01" not in states


def test_parse_arp_noarp():
    lines = ["192.168.1.4 lladdr aa:bb:cc:00:00:02 NOARP"]
    states, stale, ips = parse_arp(lines)
    assert "AA:BB:CC:00:00:02" in states


def test_parse_arp_empty():
    states, stale, ips = parse_arp([])
    assert states == {} and stale == set() and ips == {}


# ── parse_ndp ─────────────────────────────────────────────────────────────────


def test_parse_ndp_global():
    lines = ["2001:db8::1 lladdr aa:bb:cc:dd:ee:ff REACHABLE"]
    result = parse_ndp(lines)
    assert "AA:BB:CC:DD:EE:FF" in result
    assert result["AA:BB:CC:DD:EE:FF"] == "2001:db8::1"


def test_parse_ndp_linklocal_skipped():
    lines = ["fe80::1 lladdr aa:bb:cc:dd:ee:ff REACHABLE"]
    result = parse_ndp(lines)
    assert result == {}


def test_parse_ndp_prefers_global_over_ula():
    # 2606:4700::1 is a real global unicast (Cloudflare) — not_private=True so it wins over ULA
    lines = [
        "fd00::1 lladdr aa:bb:cc:dd:ee:ff REACHABLE",
        "2606:4700::1 lladdr aa:bb:cc:dd:ee:ff REACHABLE",
    ]
    result = parse_ndp(lines)
    assert result["AA:BB:CC:DD:EE:FF"] == "2606:4700::1"


def test_parse_ndp_4token_format():
    # OpenWrt busybox ip omits 'dev <iface>' — 4 tokens
    lines = ["2001:db8::2 lladdr bb:cc:dd:ee:ff:00 STALE"]
    result = parse_ndp(lines)
    assert "BB:CC:DD:EE:FF:00" in result


def test_parse_ndp_failed_excluded():
    lines = ["2001:db8::3 lladdr cc:dd:ee:ff:00:11 FAILED"]
    result = parse_ndp(lines)
    assert result == {}


# ── parse_dns_stats ────────────────────────────────────────────────────────────

_DNS_LOG = [
    "Apr 19 10:00:00 dnsmasq[1]: cache size 1000, 0/1754 cache insertions re-used unexpired cache entries.",
    "Apr 19 10:00:00 dnsmasq[1]: queries forwarded 370224, queries answered locally 346136",
    "Apr 19 10:00:00 dnsmasq[1]: server 1.1.1.1#53: queries sent 214592, queries retried or failed 0, avg. latency 19ms",
    "Apr 19 10:00:00 dnsmasq[1]: server 8.8.8.8#53: queries sent 155632, queries retried or failed 0, avg. latency 22ms",
]


def test_parse_dns_stats_hits_misses():
    result = parse_dns_stats(_DNS_LOG)
    assert result is not None
    assert result["hits"] == 346136
    assert result["misses"] == 370224


def test_parse_dns_stats_servers():
    result = parse_dns_stats(_DNS_LOG)
    assert result is not None
    servers = {s["addr"]: s for s in result["servers"]}
    assert "1.1.1.1#53" in servers
    assert servers["1.1.1.1#53"]["latency_ms"] == 19
    assert servers["8.8.8.8#53"]["queries"] == 155632


def test_parse_dns_stats_weighted_latency():
    result = parse_dns_stats(_DNS_LOG)
    assert result is not None
    # weighted avg: (214592*19 + 155632*22) / (214592+155632) ≈ 20.2
    assert result.get("latency_ms") is not None
    assert 19 <= result["latency_ms"] <= 23


def test_parse_dns_stats_empty():
    assert parse_dns_stats([]) is None


def test_parse_dns_stats_uses_last_dump():
    # Multiple dumps — only last counts
    lines = [
        "Apr 19 09:00:00 dnsmasq[1]: queries forwarded 100, queries answered locally 50",
        "Apr 19 10:00:00 dnsmasq[1]: queries forwarded 200, queries answered locally 100",
    ]
    result = parse_dns_stats(lines)
    assert result is not None
    assert result["misses"] == 200
    assert result["hits"] == 100


# ── _is_random_mac ─────────────────────────────────────────────────────────────


def test_is_random_mac_locally_administered():
    # Bit 1 of first octet set → locally administered / random
    assert _is_random_mac("2E:00:00:00:00:01")  # 0x2E = 0b00101110, bit1=1
    assert _is_random_mac("4A:BB:CC:DD:EE:FF")  # 0x4A = 0b01001010, bit1=1
    assert _is_random_mac("72:11:22:33:44:55")  # 0x72 = 0b01110010, bit1=1


def test_is_random_mac_globally_administered():
    # Globally administered MACs: bit 1 of first octet = 0
    assert not _is_random_mac("38:00:00:00:00:01")  # 0x38 = 0b00111000
    assert not _is_random_mac("C4:00:00:00:00:02")  # 0xC4 = 0b11000100
    assert not _is_random_mac("D0:00:00:00:00:03")  # 0xD0 = 0b11010000
    assert not _is_random_mac("00:11:22:33:44:55")  # 0x00 = 0b00000000


def test_is_random_mac_broadcast_multicast_not_random():
    # Multicast/broadcast have bit 0 set, but bit 1 may or may not be set
    assert not _is_random_mac("01:00:5E:00:00:01")  # multicast, bit1=0 → not random


def test_is_random_mac_bad_input():
    assert not _is_random_mac("")
    assert not _is_random_mac("not-a-mac")
    assert not _is_random_mac("ZZ:00:00:00:00:00")


# ── Gateway fixture drift tests ────────────────────────────────────────────────
# Structural assertions that run across every captured OpenWrt version. Their
# job is not to check numeric values (which differ between captures) but to
# catch output-format regressions in a new OpenWrt release.


def _gateway_lines(version: str, fname: str) -> list[str]:
    path = OPENWRT_FIXTURES / version / "gateway" / fname
    if not path.is_file():
        return []
    return [ln for ln in path.read_text().splitlines() if ln.strip()]


@pytest.mark.parametrize("version", AVAILABLE_VERSIONS)
def test_parse_leases_from_gateway_fixture(version):
    lines = _gateway_lines(version, "dhcp.leases")
    if not lines:
        pytest.skip(f"no dhcp.leases for {version}")
    result = parse_leases(lines)
    assert result, f"no leases parsed from {version}"
    for mac, entry in result.items():
        assert mac == mac.upper()
        assert len(mac.split(":")) == 6
        assert entry["ip"]
        assert isinstance(entry["hostname"], str)


@pytest.mark.parametrize("version", AVAILABLE_VERSIONS)
def test_parse_arp_from_gateway_fixture(version):
    lines = _gateway_lines(version, "ip-neigh.txt")
    if not lines:
        pytest.skip(f"no ip-neigh.txt for {version}")
    states, _stale, ips = parse_arp(lines)
    assert states, f"no ARP entries parsed from {version}"
    for mac, state in states.items():
        assert mac == mac.upper()
        assert len(mac.split(":")) == 6
        assert state in {"REACHABLE", "STALE", "DELAY", "PROBE", "NOARP", "PERMANENT"}
        assert ips[mac].count(".") == 3


@pytest.mark.parametrize("version", AVAILABLE_VERSIONS)
def test_parse_ndp_from_gateway_fixture(version):
    lines = _gateway_lines(version, "ip-neigh6.txt")
    if not lines:
        pytest.skip(f"no ip-neigh6.txt for {version}")
    result = parse_ndp(lines)
    # Empty is allowed (LAN could be v4-only). If populated, verify shape.
    for mac, ip in result.items():
        assert mac == mac.upper()
        assert len(mac.split(":")) == 6
        assert ":" in ip
        assert not ip.startswith("fe80")  # link-local must be filtered out


@pytest.mark.parametrize("version", AVAILABLE_VERSIONS)
def test_parse_dns_stats_from_gateway_fixture(version):
    lines = _gateway_lines(version, "logread-dnsmasq.txt")
    if not lines:
        pytest.skip(f"no logread-dnsmasq.txt for {version}")
    result = parse_dns_stats(lines)
    if result is None:
        pytest.skip(f"logread for {version} has no cache dump window")
    assert isinstance(result["hits"], int) and result["hits"] >= 0
    assert isinstance(result["misses"], int) and result["misses"] >= 0
    if result.get("servers"):
        for s in result["servers"]:
            assert s["addr"]
            assert isinstance(s["queries"], int)
            assert isinstance(s["latency_ms"], int)


@pytest.mark.parametrize("version", AVAILABLE_VERSIONS)
def test_parse_hoststat_from_gateway_fixture(version):
    # The coordinator composes hoststat from three commands; mirror that shape.
    gw = OPENWRT_FIXTURES / version / "gateway"
    cpu_path = gw / "proc-stat.txt"
    mem_path = gw / "proc-meminfo.txt"
    disk_path = gw / "df-root.txt"
    if not (cpu_path.is_file() and mem_path.is_file()):
        pytest.skip(f"no host stat files for {version}")

    cpu_line = cpu_path.read_text().strip().splitlines()[0]
    total = avail = ""
    for line in mem_path.read_text().splitlines():
        parts = line.split()
        if line.startswith("MemTotal:") and len(parts) > 1:
            total = parts[1]
        elif line.startswith("MemAvailable:") and len(parts) > 1:
            avail = parts[1]
    composed = [cpu_line, f"{total} {avail}"]
    if disk_path.is_file():
        for line in disk_path.read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                composed.append(parts[4].rstrip("%"))
                break

    result = parse_hoststat(composed)
    assert result is not None
    assert result["busy"] > 0
    assert result["idle"] > 0
    assert result["mem_total"] > 0
    assert 0 <= result["mem_avail"] <= result["mem_total"]
    if len(composed) == 3:
        assert 0 <= result["disk"] <= 100
