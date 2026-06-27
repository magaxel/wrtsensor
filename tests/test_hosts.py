"""Tests for host endpoint parsing."""

from __future__ import annotations

import pytest

from custom_components.wrtsensor.hosts import (
    HostEndpointError,
    parse_host_endpoint,
    parse_hosts_field,
)


@pytest.mark.parametrize(
    ("raw", "host", "port"),
    [
        ("192.0.2.1", "192.0.2.1", 22),
        ("192.0.2.1:2222", "192.0.2.1", 2222),
        ("2001:db8::1", "2001:db8::1", 22),
        ("[2001:db8::1]:2222", "2001:db8::1", 2222),
    ],
)
def test_parse_host_endpoint(raw, host, port):
    endpoint = parse_host_endpoint(raw)

    assert endpoint.raw == raw
    assert endpoint.host == host
    assert endpoint.port == port


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "192.0.2.1:nope",
        "192.0.2.1:0",
        "192.0.2.1:65536",
        "[2001:db8::1",
        "[2001:db8::1]:nope",
        "2001:db8::1:2222",
    ],
)
def test_parse_host_endpoint_rejects_invalid(raw):
    with pytest.raises(HostEndpointError):
        parse_host_endpoint(raw)


def test_parse_hosts_field_plain_and_ports_and_overrides():
    pairs = parse_hosts_field(
        "192.0.2.1, 192.0.2.22:2222=ap , [2001:db8::1]=switch, 192.0.2.5=gateway"
    )
    assert [(ep.host, ep.port, role) for ep, role in pairs] == [
        ("192.0.2.1", 22, None),
        ("192.0.2.22", 2222, "ap"),
        ("2001:db8::1", 22, "switch"),
        ("192.0.2.5", 22, "gateway"),
    ]


def test_parse_hosts_field_skips_blanks():
    pairs = parse_hosts_field("192.0.2.1, , 192.0.2.2,")
    assert [ep.host for ep, _ in pairs] == ["192.0.2.1", "192.0.2.2"]


def test_parse_hosts_field_rejects_duplicate_normalized_host():
    with pytest.raises(HostEndpointError):
        parse_hosts_field("192.0.2.5,192.0.2.5=switch")


def test_parse_hosts_field_rejects_multiple_gateway_overrides():
    with pytest.raises(HostEndpointError):
        parse_hosts_field("192.0.2.1=gateway,192.0.2.2=gateway")


def test_parse_hosts_field_allows_one_gateway_with_other_roles():
    pairs = parse_hosts_field("192.0.2.1=gateway,192.0.2.2=ap,192.0.2.3=switch")
    assert [role for _, role in pairs] == ["gateway", "ap", "switch"]


@pytest.mark.parametrize(
    "raw",
    [
        "192.0.2.5=router",
        "192.0.2.5=",
        "192.0.2.5=GATEWAY=x",
    ],
)
def test_parse_hosts_field_rejects_bad_role(raw):
    with pytest.raises(HostEndpointError):
        parse_hosts_field(raw)
