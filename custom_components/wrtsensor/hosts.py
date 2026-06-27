"""Host endpoint parsing for wrtsensor."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress

from .const import DEFAULT_SSH_PORT, ROLE_GATEWAY, VALID_ROLES


@dataclass(frozen=True)
class HostEndpoint:
    raw: str
    host: str
    port: int = DEFAULT_SSH_PORT


class HostEndpointError(ValueError):
    """Raised when a host endpoint cannot be parsed safely."""


def parse_host_endpoint(raw: str, default_port: int = DEFAULT_SSH_PORT) -> HostEndpoint:
    """Parse IPv4/IPv6 host input with optional inline SSH port.

    Supported forms:
    - 192.0.2.1
    - 192.0.2.1:2222
    - 2001:db8::1
    - [2001:db8::1]:2222
    """
    value = raw.strip()
    if not value:
        raise HostEndpointError("empty host")

    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            raise HostEndpointError("missing closing bracket")
        host = value[1:end].strip()
        rest = value[end + 1 :]
        if not rest:
            return _endpoint(value, host, default_port)
        if not rest.startswith(":"):
            raise HostEndpointError("unexpected bracket suffix")
        return _endpoint(value, host, _parse_port(rest[1:]))

    if value.count(":") == 1:
        host, _, port_raw = value.partition(":")
        return _endpoint(value, host.strip(), _parse_port(port_raw))

    if value.count(":") > 1:
        # Unbracketed IPv6 is allowed only without an inline port. If the final
        # segment looks like a decimal SSH port and the prefix is also an IPv6
        # address, require [addr]:port so user intent is unambiguous.
        prefix, _, suffix = value.rpartition(":")
        if suffix.isdecimal():
            try:
                port = int(suffix)
            except ValueError:
                port = -1
            if 1 <= port <= 65535:
                try:
                    ipaddress.ip_address(prefix)
                except ValueError:
                    pass
                else:
                    raise HostEndpointError("bracket IPv6 addresses when adding port")
        return _endpoint(value, value, default_port)

    return _endpoint(value, value, default_port)


def parse_host_endpoints(
    raw_hosts: list[str], default_port: int = DEFAULT_SSH_PORT
) -> list[HostEndpoint]:
    return [parse_host_endpoint(host, default_port) for host in raw_hosts]


def parse_hosts_field(
    raw: str, default_port: int = DEFAULT_SSH_PORT
) -> list[tuple[HostEndpoint, str | None]]:
    """Parse the merged "Hosts" field into (endpoint, override-role) pairs.

    Each comma-separated token is a host endpoint (``parse_host_endpoint`` form,
    inline ports supported) with an optional ``=role`` suffix forcing the role,
    e.g. ``192.0.2.5:2222=switch`` or ``[2001:db8::1]:2222=ap``. The role, when
    present, must be one of gateway/ap/switch.

    Duplicate hosts (two tokens resolving to the same normalized
    ``HostEndpoint.host``) raise ``HostEndpointError`` rather than silently
    last-write-wins, which keeps the detected-role cache safe to key by host.
    At most one ``=gateway`` override is allowed (a single internet uplink).
    """
    results: list[tuple[HostEndpoint, str | None]] = []
    seen: set[str] = set()
    gateway_overrides = 0
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        host_spec, sep, role_raw = token.rpartition("=")
        if sep:
            role: str | None = role_raw.strip().lower()
            if role not in VALID_ROLES:
                raise HostEndpointError(f"invalid role: {role_raw.strip()!r}")
            if role == ROLE_GATEWAY:
                gateway_overrides += 1
                if gateway_overrides > 1:
                    raise HostEndpointError("only one =gateway override allowed")
        else:
            host_spec, role = token, None
        endpoint = parse_host_endpoint(host_spec, default_port)
        if endpoint.host in seen:
            raise HostEndpointError(f"duplicate host: {endpoint.host}")
        seen.add(endpoint.host)
        results.append((endpoint, role))
    return results


def _endpoint(raw: str, host: str, port: int) -> HostEndpoint:
    if not host:
        raise HostEndpointError("empty host")
    try:
        normalized = str(ipaddress.ip_address(host))
    except ValueError as err:
        raise HostEndpointError("invalid IP address") from err
    return HostEndpoint(raw=raw, host=normalized, port=port)


def _parse_port(raw: str) -> int:
    if not raw.isdecimal():
        raise HostEndpointError("invalid port")
    port = int(raw)
    if not 1 <= port <= 65535:
        raise HostEndpointError("invalid port")
    return port
