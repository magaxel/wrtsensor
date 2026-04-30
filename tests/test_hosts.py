"""Tests for host endpoint parsing."""

from __future__ import annotations

import pytest

from custom_components.wrtsensor.hosts import HostEndpointError, parse_host_endpoint


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
