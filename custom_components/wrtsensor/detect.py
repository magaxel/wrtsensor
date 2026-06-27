"""Hardware-independent OpenWrt role autodetection for wrtsensor.

Each configured host is SSH-probed for three capability signals and classified
as ``gateway``, ``ap`` or ``switch`` — without any hardcoded hardware list:

* **gateway** — the box with the internet uplink, identified by an ``up`` ``wan``
  interface (an explicit ``=gateway`` override or a cached gateway also count).
  Relational next-hop voting only *breaks ties* between multiple wan hosts — it
  never promotes a host that has no wan signal. If nothing qualifies there is no
  gateway (the "APs-only / switch-only" mode).
* **ap** — a non-gateway host with Wi-Fi radios serving clients (``iwinfo``).
* **switch** — a wired-only managed switch (no Wi-Fi, not the gateway).

``classify`` is a pure function so it is trivially unit-testable; ``probe_role``
performs the (impure) SSH probe.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .const import ROLE_AP, ROLE_GATEWAY, ROLE_SWITCH, VALID_ROLES

_LOGGER = logging.getLogger(__name__)

# One tiny POSIX-sh command emitting a single parseable line, e.g.
#   ROLE|wan=1|nexthop=203.0.113.1|wifi=0|waniface=eth0|laniface=br-lan
# `wan` = an `up` OpenWrt interface literally named "wan" (uplink convention);
# `nexthop` = the device's own default-route gateway IP; `wifi` = count of
# wireless ESSIDs (0 on switches / iwinfo-less builds); `waniface` = the
# default-route device; `laniface` = the interface holding this host's own
# management IP (the server IP of the incoming SSH session). The latter two are
# only consumed for the detected gateway, to autodetect the WAN/LAN interface
# names without hardcoding.
DETECT_PROBE = (
    "wan=$(ubus call network.interface.wan status 2>/dev/null "
    "| grep -o '\"up\": *true' | head -1); "
    "nh=$(ip route show default 2>/dev/null | awk '{print $3; exit}'); "
    "wi=$(ip route show default 2>/dev/null "
    "| awk '{for(i=1;i<=NF;i++) if($i==\"dev\") print $(i+1); exit}'); "
    "sip=$(echo \"$SSH_CONNECTION\" | awk '{print $3}'); "
    "li=$(ip -o addr show 2>/dev/null "
    '| awk -v ip="$sip" \'$4 ~ "^"ip"/" {print $2; exit}\'); '
    "wifi=$(command -v iwinfo >/dev/null 2>&1 "
    "&& iwinfo 2>/dev/null | grep -c ESSID || echo 0); "
    "printf 'ROLE|wan=%s|nexthop=%s|wifi=%s|waniface=%s|laniface=%s\\n' "
    '"$([ -n "$wan" ] && echo 1 || echo 0)" "$nh" "$wifi" "$wi" "$li"'
)


@dataclass(frozen=True)
class RoleSignals:
    """Capability signals read from one host. ``None`` means it was unreachable."""

    wan: bool
    next_hop: str | None
    wifi: int
    wan_iface: str | None = None
    lan_iface: str | None = None


@dataclass(frozen=True)
class Classification:
    gateway: str | None
    aps: list[str]
    switches: list[str]


def parse_probe_output(stdout: str | None) -> RoleSignals | None:
    """Parse the ``ROLE|wan=..|nexthop=..|wifi=..`` line. None if absent/garbled."""
    if not stdout:
        return None
    for line in stdout.splitlines():
        if not line.startswith("ROLE|"):
            continue
        fields: dict[str, str] = {}
        for part in line.split("|")[1:]:
            key, _, value = part.partition("=")
            fields[key.strip()] = value.strip()
        try:
            wifi = int(fields.get("wifi", "0") or "0")
        except ValueError:
            wifi = 0
        return RoleSignals(
            wan=fields.get("wan") == "1",
            next_hop=fields.get("nexthop") or None,
            wifi=wifi,
            wan_iface=fields.get("waniface") or None,
            lan_iface=fields.get("laniface") or None,
        )
    return None


async def probe_role(
    host: str, ssh_key_path: str, ssh_port: int = 22, timeout: int = 10
) -> RoleSignals | None:
    """SSH into ``host`` and return its capability signals, or None if unreachable."""
    import asyncssh

    try:
        async with asyncio.timeout(timeout):
            async with asyncssh.connect(
                host,
                port=ssh_port,
                username="root",
                client_keys=[ssh_key_path],
                known_hosts=None,
            ) as conn:
                result = await conn.run(DETECT_PROBE, check=False)
                return parse_probe_output(result.stdout)
    except Exception as err:  # noqa: BLE001 — any failure means "unreachable"
        _LOGGER.debug("Role probe failed for %s: %s", host, err)
        return None


def classify(
    signals: dict[str, RoleSignals | None],
    overrides: dict[str, str] | None = None,
    cached: dict[str, str] | None = None,
) -> Classification:
    """Classify hosts into roles. Pure; ``signals`` keys are the configured hosts.

    * Manual ``overrides`` (host → role) win outright.
    * Exactly one gateway at most, and only on a *positive* signal: an explicit
      ``=gateway`` override, an ``up`` ``wan`` interface, or a still-configured
      cached gateway. Next-hop votes only break ties *between* wan hosts — they
      never promote a non-wan host, so an APs-only / switch-only topology stays
      gateway-less unless one of those signals fires.
    * Remaining hosts: Wi-Fi present → AP, else switch. An unreachable host with
      no fresh signal reuses its cached role, defaulting to switch.
    """
    overrides = {h: r for h, r in (overrides or {}).items() if r in VALID_ROLES}
    cached = cached or {}
    hosts = list(signals.keys())
    roles: dict[str, str] = {}

    # 1. Apply ap/switch overrides now; gateway overrides are resolved in step 2
    #    (a non-selected gateway override must still fall through to a real role,
    #    never vanish from the buckets).
    for host, role in overrides.items():
        if host in signals and role != ROLE_GATEWAY:
            roles[host] = role

    # 2. Gateway — requires a positive signal (override / wan / cached-unreachable).
    gateway = next((h for h, r in overrides.items() if r == ROLE_GATEWAY), None)
    if gateway is None:
        candidates = [h for h in hosts if h not in overrides]
        wan_hosts = [
            h for h in candidates if signals.get(h) is not None and signals[h].wan
        ]
        if wan_hosts:
            # Tie-break only among wan hosts: prefer the one the most others use
            # as their default next-hop, then the lowest host string.
            votes = {
                h: sum(
                    1
                    for other in hosts
                    if signals.get(other) is not None and signals[other].next_hop == h
                )
                for h in wan_hosts
            }
            gateway = max(wan_hosts, key=lambda h: (votes[h], _neg_key(h)))
        else:
            # No wan signal anywhere. Keep a previously-known gateway only when
            # its fresh signal is *unavailable* (a transient probe failure) — not
            # when it was probed and now reports no wan, which is a real demotion
            # that must re-detect. Otherwise there is no gateway.
            gateway = next(
                (
                    h
                    for h in candidates
                    if cached.get(h) == ROLE_GATEWAY and signals.get(h) is None
                ),
                None,
            )
    if gateway is not None:
        roles[gateway] = ROLE_GATEWAY

    # 3. Remaining hosts: Wi-Fi → AP, else switch (cached fallback if unreachable).
    for host in hosts:
        if host in roles:
            continue
        sig = signals.get(host)
        if sig is None:
            roles[host] = cached.get(host, ROLE_SWITCH)
        elif sig.wifi > 0:
            roles[host] = ROLE_AP
        else:
            roles[host] = ROLE_SWITCH

    gateway_out = next((h for h, r in roles.items() if r == ROLE_GATEWAY), None)
    aps = [h for h in hosts if roles.get(h) == ROLE_AP]
    switches = [h for h in hosts if roles.get(h) == ROLE_SWITCH]
    return Classification(gateway=gateway_out, aps=aps, switches=switches)


def roles_from_cache(
    hosts: list[str],
    overrides: dict[str, str] | None = None,
    cached: dict[str, str] | None = None,
) -> Classification:
    """Assign roles from overrides + the stored cache only (no live probe).

    Used to populate the coordinator's buckets synchronously at construction,
    before async detection refines them. Hosts with neither an override nor a
    cached role are left unassigned (they get a real role once detection runs).
    """
    overrides = overrides or {}
    cached = cached or {}
    roles: dict[str, str] = {}
    for host in hosts:
        role = overrides.get(host) or cached.get(host)
        if role in VALID_ROLES:
            roles[host] = role
    gateway = next((h for h in hosts if roles.get(h) == ROLE_GATEWAY), None)
    aps = [h for h in hosts if roles.get(h) == ROLE_AP]
    switches = [h for h in hosts if roles.get(h) == ROLE_SWITCH]
    return Classification(gateway=gateway, aps=aps, switches=switches)


def _neg_key(host: str) -> tuple:
    """Deterministic descending tie-break key (lowest host string wins on ties)."""
    return tuple(-b for b in host.encode())
