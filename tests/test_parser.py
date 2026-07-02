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
parse_board_info = parser.parse_board_info
parse_board_model = parser.parse_board_model
parse_fdb = parser.parse_fdb
parse_self_mac = parser.parse_self_mac
resolve_switch_ports = parser.resolve_switch_ports
resolve_infra_parents = parser.resolve_infra_parents
_is_random_mac = parser._is_random_mac

FIXTURES = Path(__file__).parent / "fixtures"
OPENWRT_FIXTURES = FIXTURES / "openwrt"

# Pin concrete-value tests to this version — update when re-capturing.
PINNED_VERSION = "25.12.2"

# All captured versions — structural tests run across every version.
AVAILABLE_VERSIONS = sorted(d.name for d in OPENWRT_FIXTURES.iterdir() if d.is_dir())


def _collector_output(ap: str, version: str = PINNED_VERSION) -> str:
    return (OPENWRT_FIXTURES / version / ap / "collector-output.txt").read_text()


class TestParseWifiOutputUniFiACPro:
    """Ubiquiti UniFi AP Pro (ubnt,unifiac-pro) fixture."""

    def setup_method(self):
        self.entries, self.hoststat = parse_wifi_output(
            _collector_output("unifiac-pro"), "UniFiACPro"
        )

    def test_device_count(self):
        assert len(self.entries) == 5

    def test_mac_uppercase(self):
        macs = {e["mac"] for e in self.entries}
        assert "C8:00:00:00:00:04" in macs
        assert "D8:00:00:00:00:05" in macs

    def test_ap_name(self):
        assert all(e["ap"] == "UniFiACPro" for e in self.entries)

    def test_bands(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["C8:00:00:00:00:04"]["band"] == "2.4GHz"
        assert by_mac["D8:00:00:00:00:05"]["band"] == "5GHz"

    def test_signal(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["C8:00:00:00:00:04"]["signal"] == -58
        assert by_mac["D8:00:00:00:00:05"]["signal"] == -52

    def test_noise(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["C8:00:00:00:00:04"]["noise"] == -100
        assert by_mac["D8:00:00:00:00:05"]["noise"] == -92

    def test_snr(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["C8:00:00:00:00:04"]["snr"] == 42
        assert by_mac["D8:00:00:00:00:05"]["snr"] == 40

    def test_tx_rate(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["D8:00:00:00:00:05"]["tx_rate"] == 433.3

    def test_essid(self):
        assert all(e["essid"] == "NetA" for e in self.entries)

    def test_hoststat_cpu_line(self):
        assert len(self.hoststat) >= 2
        assert self.hoststat[0].startswith("cpu")

    def test_hoststat_10_cpu_fields(self):
        # Fixture has 10 CPU fields — parse_hoststat must handle this
        fields = self.hoststat[0].split()
        assert len(fields) == 11  # "cpu" + 10 values


class TestParseWifiOutputUniFiNanoHD:
    """Ubiquiti UniFi AP nanoHD (ubnt,unifi-nanohd) fixture."""

    def setup_method(self):
        self.entries, _ = parse_wifi_output(
            _collector_output("unifi-nanohd"), "UniFiNanoHD"
        )

    def test_device_count(self):
        assert len(self.entries) == 6

    def test_roam_candidate_present(self):
        # 38:00:00:00:00:01 roams between UniFiACPro and UniFiNanoHD
        macs = {e["mac"] for e in self.entries}
        assert "38:00:00:00:00:01" in macs

    def test_per_client_essid(self):
        # 48:00:00:00:00:0B is on SSID "NetB", rest on "NetA"
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["48:00:00:00:00:0B"]["essid"] == "NetB"
        assert by_mac["2E:00:00:00:00:01"]["essid"] == "NetA"

    def test_low_byte_counters(self):
        by_mac = {e["mac"]: e for e in self.entries}
        assert by_mac["2E:00:00:00:00:01"]["sta_ul_bytes"] == 3720849
        assert by_mac["2E:00:00:00:00:01"]["sta_dl_bytes"] == 3719870


# ── Structural checks across every captured OpenWrt version ──────────────────


AP_BOARDS = ["unifiac-pro", "unifi-nanohd"]


@pytest.mark.parametrize("version", AVAILABLE_VERSIONS)
@pytest.mark.parametrize("board", AP_BOARDS)
def test_collector_output_parses_across_versions(version, board):
    """Every captured collector output must yield well-formed entries on any OpenWrt version."""
    path = OPENWRT_FIXTURES / version / board / "collector-output.txt"
    if not path.exists():
        pytest.skip(f"no {board} capture for {version}")
    entries, hoststat = parse_wifi_output(path.read_text(), board.upper())
    assert entries, f"no entries parsed from {version}/{board}"
    for e in entries:
        assert e["mac"] == e["mac"].upper()
        assert len(e["mac"].split(":")) == 6
        assert e["band"] in {"2.4GHz", "5GHz", "6GHz", "unknown"}
        assert isinstance(e["essid"], str)
        assert isinstance(e["signal"], int)
        assert e["ap"] == board.upper()
    # STAT line always emitted by the collector
    assert hoststat, f"no hoststat from {version}/{board}"
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


# ── parse_board_model ─────────────────────────────────────────────────────────


class TestParseBoardModel:
    def test_board_info_includes_hostname(self):
        out = _collector_output("unifiac-pro")
        assert parse_board_info(out) == {
            "model": "Ubiquiti UniFi AP Pro",
            "board_name": "ubnt,unifiac-pro",
            "hostname": "ap1",
        }

    def test_board_info_multiline_openwrt_output(self):
        out = """BOARD|{
        "kernel": "6.12.87",
        "hostname": "Switch",
        "model": "Zyxel GS1900-24 A1",
        "board_name": "zyxel,gs1900-24-a1"
}
STAT|cpu  1 0 1 98|128000|64000|12
"""

        assert parse_board_info(out) == {
            "model": "Zyxel GS1900-24 A1",
            "board_name": "zyxel,gs1900-24-a1",
            "hostname": "Switch",
        }
        assert parse_board_model(out) == ("Zyxel GS1900-24 A1", "zyxel,gs1900-24-a1")

    def test_unifiac_pro_fixture(self):
        out = _collector_output("unifiac-pro")
        model, board_name = parse_board_model(out)
        assert model == "Ubiquiti UniFi AP Pro"
        assert board_name == "ubnt,unifiac-pro"

    def test_unifi_nanohd_fixture(self):
        out = _collector_output("unifi-nanohd")
        model, board_name = parse_board_model(out)
        assert model == "Ubiquiti UniFi AP nanoHD"
        assert board_name == "ubnt,unifi-nanohd"

    def test_missing_board_line(self):
        assert parse_board_model("STAT|cpu 1 0 1 1|1024|512|5\n") == ("", "")

    def test_malformed_json(self):
        assert parse_board_model("BOARD|not-json\n") == ("", "")

    def test_empty_payload(self):
        assert parse_board_model("BOARD|\n") == ("", "")

    def test_unclosed_board_json_stops_at_next_metadata_token(self):
        out = 'BOARD|{\n  "hostname": "bad"\nWIREGUARD|wg0|peer\n'

        assert parse_board_info(out) == {}

    def test_unclosed_board_json_is_bounded(self):
        out = "BOARD|{\n" + "\n".join(f'  "k{i}": "v",' for i in range(80))

        assert parse_board_info(out) == {}

    def test_parse_unaffected_by_board_line(self):
        # Adding a BOARD line must not produce a spurious WiFi entry
        out = _collector_output("unifiac-pro")
        entries, _ = parse_wifi_output(out, "test")
        assert len(entries) == 5  # same count as without BOARD line


# ── Gateway fixture drift tests ────────────────────────────────────────────────
# Structural assertions that run across every captured OpenWrt version. Their
# job is not to check numeric values (which differ between captures) but to
# catch output-format regressions in a new OpenWrt release.


def _gateway_lines(version: str, fname: str) -> list[str]:
    path = OPENWRT_FIXTURES / version / "usg" / fname
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
    gw = OPENWRT_FIXTURES / version / "usg"
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


# ── WireGuard parsing ────────────────────────────────────────────────────────


import json  # noqa: E402

parse_wg_show_sections = parser.parse_wg_show_sections
parse_wg_uci = parser.parse_wg_uci
wg_peer_id = parser.wg_peer_id


def _wg_fixture_sections() -> dict[str, list[str]]:
    text = (FIXTURES / "wg_show_sections.txt").read_text()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("---") and line.endswith("---"):
            current = line[3:-3]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


class TestParseWgShowSections:
    def setup_method(self):
        self.sections = _wg_fixture_sections()
        # `now` chosen so peer 1's handshake is recent (online), peer 2's is
        # well past the threshold (offline).
        self.now = 1777151913 + 30  # 30s after first peer's handshake
        self.threshold = 180
        self.interfaces = parse_wg_show_sections(
            "192.0.2.1", self.sections, self.threshold, self.now
        )

    def test_single_interface_with_literal_capital_W_name(self):
        assert len(self.interfaces) == 1
        assert self.interfaces[0]["name"] == "Wireguard"

    def test_listen_port_parsed(self):
        assert self.interfaces[0]["listen_port"] == 51820

    def test_three_peers(self):
        assert len(self.interfaces[0]["peers"]) == 3

    def test_online_flag_recent_handshake(self):
        peer = self.interfaces[0]["peers"][0]
        assert peer["online"] is True
        assert peer["last_handshake"] == 1777151913

    def test_offline_when_handshake_stale(self):
        peer = self.interfaces[0]["peers"][1]
        assert peer["online"] is False

    def test_offline_when_never_handshaked(self):
        peer = self.interfaces[0]["peers"][2]
        assert peer["last_handshake"] == 0
        assert peer["online"] is False

    def test_endpoint_none_normalised_to_python_none(self):
        peer = self.interfaces[0]["peers"][2]
        assert peer["endpoint"] is None

    def test_endpoint_string_preserved(self):
        peer = self.interfaces[0]["peers"][0]
        assert peer["endpoint"] == "203.0.113.130:51820"

    def test_allowed_ips_csv_split(self):
        peer = self.interfaces[0]["peers"][1]
        assert peer["allowed_ips"] == ["172.16.52.3/32", "10.0.0.0/24"]

    def test_transfer_columns(self):
        peer = self.interfaces[0]["peers"][0]
        assert peer["rx_bytes"] == 361795984
        assert peer["tx_bytes"] == 10039865612

    def test_keepalive_off_normalised(self):
        peer = self.interfaces[0]["peers"][1]
        assert peer["persistent_keepalive_s"] is None

    def test_keepalive_seconds(self):
        peer = self.interfaces[0]["peers"][0]
        assert peer["persistent_keepalive_s"] == 25

    def test_peer_id_is_opaque_hash(self):
        peer = self.interfaces[0]["peers"][0]
        # 16-char hex from sha1; not the raw pubkey
        assert len(peer["id"]) == 16
        assert all(c in "0123456789abcdef" for c in peer["id"])
        assert peer["public_key"] not in peer["id"]


def test_wg_peer_id_deterministic_for_same_inputs():
    a = wg_peer_id("gw", "wg0", "PUBKEY_A")
    b = wg_peer_id("gw", "wg0", "PUBKEY_A")
    assert a == b


def test_wg_peer_id_differs_across_hosts():
    a = wg_peer_id("gw", "wg0", "PUBKEY_A")
    b = wg_peer_id("ap1", "wg0", "PUBKEY_A")
    assert a != b


def test_wg_peer_id_differs_across_pubkeys():
    a = wg_peer_id("gw", "wg0", "PUBKEY_A")
    b = wg_peer_id("gw", "wg0", "PUBKEY_B")
    assert a != b


def test_parse_wg_show_sections_drops_malformed_transfer_rows():
    sections = {
        "WG_INTERFACES": ["wg0"],
        "WG_IFACE wg0": ["IFACE_PK", "51820"],
        "WG_PEERS wg0": ["PK_X"],
        "WG_TRANSFER wg0": ["PK_X 1 2 3"],  # 4 cols, must be dropped
        "WG_HANDSHAKES wg0": ["PK_X 0"],
    }
    [iface] = parse_wg_show_sections("h", sections, 180, 1_700_000_000.0)
    [peer] = iface["peers"]
    assert peer["rx_bytes"] == 0
    assert peer["tx_bytes"] == 0


def test_parse_wg_show_sections_no_interfaces_returns_empty():
    assert parse_wg_show_sections("h", {}, 180, 0.0) == []


class TestParseWgUci:
    def setup_method(self):
        self.lines = (FIXTURES / "wg_uci_with_secrets.txt").read_text().splitlines()
        self.parsed = parse_wg_uci(self.lines)

    def test_description_maps_to_name(self):
        pk = "PEER_PUBKEY_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
        assert self.parsed[pk]["name"] == "laptop-alice"

    def test_peer_without_description_has_no_name(self):
        pk = "PEER_PUBKEY_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
        assert "name" not in self.parsed[pk]

    def test_private_key_string_never_appears_in_output(self):
        # Defence in depth: even though the awk filter on the SSH host blocks
        # private_key lines, the Python parser must also discard them so a
        # later code change can't accidentally surface secrets.
        assert "WG_PRIV_KEY_FAKE_SHOULD_NEVER_LEAK_THROUGH" not in json.dumps(
            self.parsed
        )

    def test_preshared_key_string_never_appears_in_output(self):
        assert "WG_PSK_FAKE_SHOULD_NEVER_LEAK_THROUGH" not in json.dumps(self.parsed)


# ── Attended Sysupgrade (owut) ────────────────────────────────────────────────

OWUT_FIXTURES = FIXTURES / "owut"
USG_25_12_FIXTURES = FIXTURES / "openwrt" / "25.12.2" / "usg"

parse_asu_sections = parser.parse_asu_sections
parse_asu_output = parser.parse_asu_output
asu_version_is_newer = parser.asu_version_is_newer


def _asu_fixture(name: str) -> dict[str, list[str]]:
    return parse_asu_sections((OWUT_FIXTURES / name).read_text())


class TestParseASUOutput:
    def test_up_to_date_sets_latest_equal_to_installed(self):
        info = parse_asu_output(_asu_fixture("up_to_date.txt"))
        assert info["tool"] == "owut"
        assert info["installed_version"] == "24.10.1 r28597"
        assert info["latest_version"] == "24.10.1 r28597"
        assert info["error"] is None
        assert "no changes" in (info["summary"] or "").lower()
        assert info["installed_version_raw"].startswith("OpenWrt 24.10.1")

    def test_safe_upgrade_extracts_target_version(self):
        info = parse_asu_output(_asu_fixture("safe_upgrade.txt"))
        assert info["tool"] == "owut"
        assert info["installed_version"] == "24.10.1 r28597"
        assert info["latest_version"] == "24.10.2 r28739"
        assert info["error"] is None
        assert "safe to proceed" in (info["summary"] or "").lower()

    def test_downgrades_warn_carries_warning_summary(self):
        info = parse_asu_output(_asu_fixture("downgrades_warn.txt"))
        assert info["tool"] == "owut"
        assert info["installed_version"] == "24.10.2 r28739"
        assert info["latest_version"] == "24.10.1 r28597"
        assert info["error"] is None
        assert "downgrade" in (info["summary"] or "").lower()

    def test_server_error_keeps_latest_equal_to_installed_and_records_error(self):
        info = parse_asu_output(_asu_fixture("server_error.txt"))
        assert info["tool"] == "owut"
        # Up-to-date latest so the entity does not flap on a transient ASU 5xx.
        assert info["latest_version"] == info["installed_version"] == "24.10.1 r28597"
        assert info["error"] and "checks reveal errors" in info["error"].lower()

    def test_revision_only_upgrade_surfaces_as_different_version(self):
        """Same release, newer rNNN: parser must keep them distinct."""
        sections = {
            "ASU_TOOL": ["owut"],
            "ASU_VERSION": ["OpenWrt 24.10.1 r28597-aaaa"],
            "ASU_OUTPUT": [
                "Running: OpenWrt 24.10.1 r28597-aaaa",
                "ASU build OpenWrt 24.10.1 r28600-bbbb",
                "It is safe to proceed with an upgrade",
                "exit=0",
            ],
        }
        info = parse_asu_output(sections)
        assert info["installed_version"] == "24.10.1 r28597"
        assert info["latest_version"] == "24.10.1 r28600"
        assert asu_version_is_newer(info["latest_version"], info["installed_version"])

    def test_current_owut_check_output_with_version_to(self):
        info = parse_asu_output(
            parse_asu_sections((USG_25_12_FIXTURES / "owut-check.txt").read_text())
        )
        assert info["installed_version"] == "25.12.2 r32802"
        assert info["latest_version"] == "25.12.2 r32802"
        assert info["error"] is None
        assert not asu_version_is_newer(
            info["latest_version"], info["installed_version"]
        )

    def test_wan_failure_returns_owut_with_error_and_no_versions(self):
        info = parse_asu_output(_asu_fixture("wan_failure.txt"))
        assert info["tool"] == "owut"
        # No marker matched — parser surfaces the captured output as the error.
        assert info["error"]
        assert "could not resolve" in info["error"].lower()
        # latest_version unset so HA can't compare and the entity stays unavailable.
        assert info["latest_version"] is None

    def test_tool_missing_marks_unavailable(self):
        info = parse_asu_output(_asu_fixture("tool_missing.txt"))
        assert info["tool"] == "none"
        assert info["error"] == "owut not installed"
        assert info["installed_version"] is None
        assert info["latest_version"] is None

    def test_safe_upgrade_with_unparsable_target_returns_parser_error(self):
        sections = {
            "ASU_TOOL": ["owut"],
            "ASU_VERSION": ["OpenWrt 24.10.1 r28597-aaaa"],
            "ASU_OUTPUT": [
                "Checking sysupgrade.openwrt.org for updates",
                "Some other line",
                "It is safe to proceed with an upgrade",
                "exit=0",
            ],
        }
        info = parse_asu_output(sections)
        assert info["tool"] == "owut"
        assert info["installed_version"] is None
        assert info["latest_version"] is None
        assert "target version not found" in (info["error"] or "")


class TestASUVersionIsNewer:
    @pytest.mark.parametrize(
        "latest,installed,expected",
        [
            ("24.10.1", "24.10.1", False),
            ("24.10.2", "24.10.1", True),
            ("24.11.0", "24.10.5", True),
            ("25.0.0", "24.10.5", True),
            ("23.05.5", "24.10.1", False),
            # Build-hash-only differences must not flap an up-to-date device.
            ("OpenWrt 24.10.1 r28597-aaaa", "OpenWrt 24.10.1 r28597-bbbb", False),
            ("OpenWrt 24.10.1 r28600-bbbb", "OpenWrt 24.10.1 r28597-aaaa", True),
        ],
    )
    def test_truth_table(self, latest, installed, expected):
        assert asu_version_is_newer(latest, installed) is expected

    def test_unparsable_falls_back_to_string_inequality(self):
        # Both unparsable → treated as different strings means "newer".
        assert asu_version_is_newer("garbage-A", "garbage-B") is True
        assert asu_version_is_newer("garbage", "garbage") is False


class TestParseFdb:
    def test_extracts_mac_to_port(self):
        out = (
            "STAT|cpu  1 2 3 4|1000|500|10\n"
            "FDB|AA:BB:CC:DD:EE:01|lan5\n"
            "FDB|aa:bb:cc:dd:ee:02|lan6\n"
        )
        assert parse_fdb(out) == {
            "AA:BB:CC:DD:EE:01": "lan5",
            "AA:BB:CC:DD:EE:02": "lan6",
        }

    def test_ignores_non_fdb_and_malformed_lines(self):
        out = (
            "FDB|AA:BB:CC:DD:EE:01\nFDB||lan5\nrandom line\nFDB|AA:BB:CC:DD:EE:03|lan7"
        )
        assert parse_fdb(out) == {"AA:BB:CC:DD:EE:03": "lan7"}

    def test_last_writer_wins_for_duplicate_mac(self):
        out = "FDB|AA:BB:CC:DD:EE:01|lan5\nFDB|AA:BB:CC:DD:EE:01|lan9"
        assert parse_fdb(out) == {"AA:BB:CC:DD:EE:01": "lan9"}

    def test_wifi_parser_skips_fdb_lines(self):
        # An FDB| line must never be mistaken for a Wi-Fi station entry.
        entries, _ = parse_wifi_output("FDB|AA:BB:CC:DD:EE:01|lan5\n", "AP1")
        assert entries == []


class TestResolveSwitchPorts:
    def test_single_host_single_device(self):
        ports = resolve_switch_ports({"sw": {"AA:BB:CC:DD:EE:01": "lan5"}})
        assert ports == {"AA:BB:CC:DD:EE:01": {"port": "5", "host": "sw"}}

    def test_uplink_port_excluded(self):
        # A router uplink port carrying many MACs must not win over the switch's
        # access port; the device keeps its real access port.
        switch = {"AA:BB:CC:DD:EE:01": "lan5"}
        uplink = {f"AA:BB:CC:DD:EE:{i:02d}": "lan1" for i in range(1, 8)}
        uplink["AA:BB:CC:DD:EE:01"] = "lan1"  # also seen on the router uplink
        ports = resolve_switch_ports(
            {"switch": switch, "router": uplink}, uplink_threshold=4
        )
        assert ports["AA:BB:CC:DD:EE:01"] == {"port": "5", "host": "switch"}

    def test_fewest_macs_wins_on_tie(self):
        # Same MAC on two access-like ports: the less-populated one is the edge.
        a = {"AA:BB:CC:DD:EE:01": "lan2", "AA:BB:CC:DD:EE:09": "lan2"}
        b = {"AA:BB:CC:DD:EE:01": "lan3"}
        ports = resolve_switch_ports({"hostA": a, "hostB": b})
        assert ports["AA:BB:CC:DD:EE:01"] == {"port": "3", "host": "hostB"}

    def test_switch_host_preferred_on_count_tie(self):
        a = {"AA:BB:CC:DD:EE:01": "lan2"}  # on a plain host
        b = {"AA:BB:CC:DD:EE:01": "lan8"}  # on the designated switch
        ports = resolve_switch_ports({"plain": a, "sw": b}, switch_hosts={"sw"})
        assert ports["AA:BB:CC:DD:EE:01"] == {"port": "8", "host": "sw"}

    def test_non_numeric_port_falls_back_to_raw(self):
        ports = resolve_switch_ports({"sw": {"AA:BB:CC:DD:EE:01": "wan"}})
        assert ports == {"AA:BB:CC:DD:EE:01": {"port": "wan", "host": "sw"}}


class TestParseSelfMac:
    def test_extracts_self_mac(self):
        out = "BOARD|{}\nSTAT|cpu 1 2 3 4|1000|500|10\nSELFMAC|aa:bb:cc:dd:ee:01\n"
        assert parse_self_mac(out) == "AA:BB:CC:DD:EE:01"

    def test_missing_line_returns_empty(self):
        out = "BOARD|{}\nSTAT|cpu 1 2 3 4|1000|500|10\n"
        assert parse_self_mac(out) == ""

    def test_wifi_parser_skips_selfmac_lines(self):
        # A SELFMAC| line must never be mistaken for a Wi-Fi station entry.
        entries, _ = parse_wifi_output("SELFMAC|AA:BB:CC:DD:EE:01\n", "AP1")
        assert entries == []


class TestResolveInfraParents:
    def test_uplink_found_on_switch_port(self):
        # AP's own MAC learned on the switch's port, alongside several client
        # MACs relayed through the same physical uplink port.
        ap_mac = "AA:BB:CC:DD:EE:01"
        switch_fdb = {
            ap_mac: "lan5",
            "11:11:11:11:11:11": "lan5",
            "22:22:22:22:22:22": "lan5",
        }
        parents = resolve_infra_parents({"switch": switch_fdb}, {"ap": ap_mac})
        assert parents == {"ap": {"host": "switch", "port": "5"}}

    def test_no_uplink_threshold_applied(self):
        # A real AP uplink port legitimately carries MANY relayed client MACs
        # — that's the signature identifying it, not noise to discard. This
        # is the key behavioral divergence from resolve_switch_ports, which
        # would drop this exact port for exceeding FDB_UPLINK_MAC_THRESHOLD.
        ap_mac = "AA:BB:CC:DD:EE:01"
        switch_fdb = {ap_mac: "lan5"}
        for i in range(20):
            switch_fdb[f"33:33:33:33:33:{i:02x}"] = "lan5"
        parents = resolve_infra_parents({"switch": switch_fdb}, {"ap": ap_mac})
        assert parents == {"ap": {"host": "switch", "port": "5"}}

    def test_fewest_macs_wins_on_tie(self):
        ap_mac = "AA:BB:CC:DD:EE:01"
        a = {
            ap_mac: "lan2",
            "11:11:11:11:11:11": "lan2",
            "22:22:22:22:22:22": "lan2",
        }
        b = {ap_mac: "lan3", "11:11:11:11:11:11": "lan3"}
        parents = resolve_infra_parents({"hostA": a, "hostB": b}, {"ap": ap_mac})
        assert parents["ap"] == {"host": "hostB", "port": "3"}

    def test_shared_uplink_port_not_treated_as_dedicated_link(self):
        # A leaf AP's single wired port aggregates traffic from the ENTIRE
        # rest of the LAN — it eventually sees every other infra device's
        # MAC, not just whichever one is "closest." A port carrying two or
        # more known infra identities at once is exactly that kind of
        # shared/trunk link and must be excluded outright, regardless of MAC
        # count. Regression test for a real bug found against a live fleet:
        # a switch's own MAC was relayed onto an AP's single uplink port
        # alongside another AP's MAC and incorrectly resolved as "the switch
        # is downstream of the AP."
        switch_mac = "AA:BB:CC:DD:EE:01"
        other_ap_mac = "AA:BB:CC:DD:EE:02"
        leaf_ap_uplink = {
            switch_mac: "eth0",
            other_ap_mac: "eth0",
            "11:11:11:11:11:11": "eth0",
        }
        parents = resolve_infra_parents(
            {"leaf_ap": leaf_ap_uplink},
            {"switch": switch_mac, "other_ap": other_ap_mac},
        )
        assert parents == {}

    def test_switch_host_preferred_on_count_tie(self):
        ap_mac = "AA:BB:CC:DD:EE:01"
        a = {ap_mac: "lan2", "11:11:11:11:11:11": "lan2"}  # a plain AP relaying it
        b = {ap_mac: "lan8", "11:11:11:11:11:11": "lan8"}  # the designated switch
        parents = resolve_infra_parents(
            {"plain": a, "sw": b}, {"ap": ap_mac}, switch_hosts={"sw"}
        )
        assert parents["ap"] == {"host": "sw", "port": "8"}

    def test_own_hosts_fdb_table_excluded(self):
        # A host's own MAC never appears in its own FDB dump by construction
        # (the collector filters "self" entries), but assert the resolver
        # explicitly guards against it too, as defense in depth.
        ap_mac = "AA:BB:CC:DD:EE:01"
        parents = resolve_infra_parents({"ap": {ap_mac: "lan5"}}, {"ap": ap_mac})
        assert parents == {}

    def test_missing_self_mac_omitted(self):
        # A host still running an older collector script with no SELFMAC|
        # line produces no entry, not a crash.
        parents = resolve_infra_parents({"switch": {"XX": "lan5"}}, {"ap": ""})
        assert parents == {}

    def test_root_host_has_no_entry(self):
        # Self-mac never found in any other host's FDB — the physical root.
        parents = resolve_infra_parents(
            {"switch": {"11:11:11:11:11:11": "lan5"}},
            {"switch_top": "AA:BB:CC:DD:EE:01"},
        )
        assert parents == {}
