"""Tests for hardware-independent role detection (detect.classify)."""

from __future__ import annotations

import pytest

from custom_components.wrtsensor.detect import (
    Classification,
    RoleSignals,
    classify,
    parse_probe_output,
)

# Representative table: gateway (wan up, public next-hop), wired switch, three
# Wi-Fi APs, all routing through the gateway. Documentation IPs only.
GW = "192.0.2.1"
SW = "192.0.2.21"
AP1, AP2, AP3 = "192.0.2.22", "192.0.2.23", "192.0.2.24"
PUBLIC = "203.0.113.1"
SIG = {
    GW: RoleSignals(wan=True, next_hop=PUBLIC, wifi=0),
    SW: RoleSignals(wan=False, next_hop=GW, wifi=0),
    AP1: RoleSignals(wan=False, next_hop=GW, wifi=2),
    AP2: RoleSignals(wan=False, next_hop=GW, wifi=3),
    AP3: RoleSignals(wan=False, next_hop=GW, wifi=3),
}


def test_classify_full_table():
    result = classify(SIG, {}, {})
    assert result.gateway == GW
    assert set(result.aps) == {AP1, AP2, AP3}
    assert result.switches == [SW]


def test_no_wan_topology_stays_gatewayless_despite_votes():
    # No host reports the wan flag. Even though every other host routes through
    # GW, next-hop votes must NOT promote it to gateway — that would enable
    # WAN/DNS/WireGuard against a host with no internet uplink.
    sig = {
        GW: RoleSignals(wan=False, next_hop=PUBLIC, wifi=0),
        SW: RoleSignals(wan=False, next_hop=GW, wifi=0),
        AP1: RoleSignals(wan=False, next_hop=GW, wifi=2),
    }
    result = classify(sig, {}, {})
    assert result.gateway is None
    assert result.aps == [AP1]
    assert set(result.switches) == {GW, SW}


def test_no_wan_topology_gateway_via_override():
    sig = {
        GW: RoleSignals(wan=False, next_hop=PUBLIC, wifi=0),
        AP1: RoleSignals(wan=False, next_hop=GW, wifi=2),
    }
    result = classify(sig, {GW: "gateway"}, {})
    assert result.gateway == GW
    assert result.aps == [AP1]


def test_votes_only_break_ties_between_wan_hosts():
    # Two hosts both report wan=True; the one others route through wins.
    sig = {
        GW: RoleSignals(wan=True, next_hop=PUBLIC, wifi=0),
        "192.0.2.2": RoleSignals(wan=True, next_hop=PUBLIC, wifi=0),
        AP1: RoleSignals(wan=False, next_hop=GW, wifi=2),
    }
    result = classify(sig, {}, {})
    assert result.gateway == GW  # GW has a vote from AP1; 192.0.2.2 has none


def test_classify_aps_only_when_no_configured_gateway():
    sig = {
        "198.51.100.2": RoleSignals(False, "198.51.100.1", 2),
        "198.51.100.3": RoleSignals(False, "198.51.100.1", 0),
    }
    result = classify(sig, {}, {})
    assert result.gateway is None
    assert result.aps == ["198.51.100.2"]
    assert result.switches == ["198.51.100.3"]


def test_override_forces_role():
    result = classify(SIG, {SW: "ap"}, {})
    assert SW in result.aps
    assert result.gateway == GW


def test_override_can_force_gateway():
    sig = {
        "198.51.100.2": RoleSignals(False, "198.51.100.1", 2),
        "198.51.100.3": RoleSignals(False, "198.51.100.1", 0),
    }
    result = classify(sig, {"198.51.100.3": "gateway"}, {})
    assert result.gateway == "198.51.100.3"


def test_cached_gateway_preserved_when_unreachable():
    sig = {GW: None, SW: RoleSignals(False, GW, 0)}
    result = classify(sig, {}, {GW: "gateway", SW: "switch"})
    assert result.gateway == GW
    assert result.switches == [SW]


def test_cached_gateway_dropped_when_removed_from_hosts():
    sig = {SW: RoleSignals(False, GW, 0)}
    result = classify(sig, {}, {GW: "gateway", SW: "switch"})
    assert result.gateway is None


def test_cached_gateway_redetected_when_wan_moves():
    # The cached gateway is reachable but now reports no wan; another host has
    # wan. The fresh signal must win — the cache must not pin the old gateway.
    sig = {
        GW: RoleSignals(wan=False, next_hop=PUBLIC, wifi=0),
        AP1: RoleSignals(wan=True, next_hop=PUBLIC, wifi=0),
    }
    result = classify(sig, {}, {GW: "gateway"})
    assert result.gateway == AP1
    assert GW in result.switches


def test_cached_gateway_dropped_when_reachable_without_wan():
    # Reachable former gateway with no wan and no other wan host → gateway-less.
    sig = {
        GW: RoleSignals(wan=False, next_hop=PUBLIC, wifi=0),
        SW: RoleSignals(wan=False, next_hop=GW, wifi=0),
    }
    result = classify(sig, {}, {GW: "gateway", SW: "switch"})
    assert result.gateway is None
    assert set(result.switches) == {GW, SW}


def test_duplicate_gateway_overrides_do_not_drop_hosts():
    # Defensive: even if two gateway overrides reach classify (parse_hosts_field
    # rejects them upstream), every host must land in exactly one bucket.
    sig = {
        GW: RoleSignals(wan=False, next_hop=PUBLIC, wifi=0),
        AP1: RoleSignals(wan=False, next_hop=GW, wifi=2),
    }
    result = classify(sig, {GW: "gateway", AP1: "gateway"}, {})
    assigned = {result.gateway, *result.aps, *result.switches}
    assert assigned == {GW, AP1}
    assert result.gateway in (GW, AP1)


def test_unreachable_new_host_defaults_to_switch():
    result = classify({"192.0.2.50": None}, {}, {})
    assert result.gateway is None
    assert result.switches == ["192.0.2.50"]


def test_classify_returns_classification_type():
    assert isinstance(classify(SIG, {}, {}), Classification)


def test_parse_probe_output_with_interfaces():
    sig = parse_probe_output(
        "ROLE|wan=1|nexthop=203.0.113.1|wifi=0|waniface=eth0|laniface=br-lan"
    )
    assert sig.wan is True
    assert sig.next_hop == "203.0.113.1"
    assert sig.wifi == 0
    assert sig.wan_iface == "eth0"
    assert sig.lan_iface == "br-lan"


def test_parse_probe_output_back_compat_without_interfaces():
    sig = parse_probe_output("ROLE|wan=0|nexthop=198.51.100.1|wifi=2")
    assert sig.wan is False
    assert sig.wifi == 2
    assert sig.wan_iface is None
    assert sig.lan_iface is None


@pytest.mark.parametrize("text", ["", "garbage", "not a role line"])
def test_parse_probe_output_rejects_garbage(text):
    assert parse_probe_output(text) is None
