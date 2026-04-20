"""Tests for detect_events() and related helpers."""

import sys
import time

import pytest

coord = sys.modules["custom_components.wrtsensor.coordinator"]

Device = coord.Device
StateEntry = coord.StateEntry
detect_events = coord.detect_events
device_to_state = coord.device_to_state
_can_emit = coord._can_emit
_carry_ts = coord._carry_ts
remap_random_macs = coord.remap_random_macs

DISCONNECT_MISS_THRESHOLD = coord.DISCONNECT_MISS_THRESHOLD

TS = "2026-04-19T10:00:00"


# ── helpers ───────────────────────────────────────────────────────────────────


def _dev(
    mac="AA:BB:CC:DD:EE:01",
    *,
    online=True,
    ap="AP1",
    band="5GHz",
    essid="Home",
    hostname="phone",
    ip="192.168.1.10",
    ip6="",
    connection="wifi",
    signal=-60,
):
    return Device(
        mac=mac,
        online=online,
        ap=ap,
        band=band,
        essid=essid,
        hostname=hostname,
        ip=ip,
        ip6=ip6,
        connection=connection,
        signal=signal,
        vendor="Apple",
    )


def _state(
    mac="AA:BB:CC:DD:EE:01",
    *,
    online=True,
    ap="AP1",
    band="5GHz",
    essid="Home",
    hostname="phone",
    ip="192.168.1.10",
    miss=0,
    first_seen=0.0,
    last_seen=0.0,
    last_event_ts=None,
):
    e = StateEntry(
        mac=mac,
        online=online,
        ap=ap,
        band=band,
        essid=essid,
        hostname=hostname,
        ip=ip,
        miss=miss,
        first_seen=first_seen,
        last_seen=last_seen,
    )
    if last_event_ts:
        e.last_event_ts = last_event_ts
    return e


def _run(prev, devices, *, threshold=DISCONNECT_MISS_THRESHOLD, min_interval=0.0):
    return detect_events(
        prev,
        devices,
        TS,
        min_interval_s=min_interval,
        disconnect_miss_threshold=threshold,
    )


def _types(events):
    return [e["type"] for e in events]


# ── new_device ────────────────────────────────────────────────────────────────


def test_new_device_no_prev():
    events, state = _run({}, [_dev()])
    assert "new_device" in _types(events)
    assert events[0]["mac"] == "AA:BB:CC:DD:EE:01"


def test_new_device_state_entry_created():
    _, state = _run({}, [_dev()])
    assert "AA:BB:CC:DD:EE:01" in state
    assert state["AA:BB:CC:DD:EE:01"].online


def test_no_new_device_if_known():
    prev = {"AA:BB:CC:DD:EE:01": _state()}
    events, _ = _run(prev, [_dev()])
    assert "new_device" not in _types(events)


# ── connect / disconnect ───────────────────────────────────────────────────────


def test_connect_after_threshold():
    prev = {"AA:BB:CC:DD:EE:01": _state(online=False, miss=DISCONNECT_MISS_THRESHOLD)}
    events, state = _run(prev, [_dev(online=True)])
    assert "connect" in _types(events)
    assert state["AA:BB:CC:DD:EE:01"].miss == 0


def test_no_connect_below_threshold():
    # miss < threshold → device is still considered online (temporarily absent)
    prev = {
        "AA:BB:CC:DD:EE:01": _state(online=True, miss=DISCONNECT_MISS_THRESHOLD - 1)
    }
    events, _ = _run(prev, [_dev(online=True)])
    assert "connect" not in _types(events)


def test_disconnect_device_absent_at_threshold():
    # Device not in this scan's device list; miss counter hits threshold
    prev = {
        "AA:BB:CC:DD:EE:01": _state(online=True, miss=0, first_seen=time.time() - 300)
    }
    events, state = _run(prev, [], threshold=1)
    assert "disconnect" in _types(events)
    assert not state["AA:BB:CC:DD:EE:01"].online


def test_disconnect_device_present_but_offline_at_threshold():
    prev = {
        "AA:BB:CC:DD:EE:01": _state(online=True, miss=0, first_seen=time.time() - 300)
    }
    events, state = _run(prev, [_dev(online=False)], threshold=1)
    assert "disconnect" in _types(events)
    assert not state["AA:BB:CC:DD:EE:01"].online


def test_no_disconnect_below_threshold():
    prev = {"AA:BB:CC:DD:EE:01": _state(online=True, miss=0)}
    events, state = _run(prev, [], threshold=3)
    assert "disconnect" not in _types(events)
    # miss incremented
    assert state["AA:BB:CC:DD:EE:01"].miss == 1


def test_disconnect_includes_duration():
    t0 = time.time() - 600
    prev = {"AA:BB:CC:DD:EE:01": _state(online=True, miss=0, first_seen=t0)}
    events, _ = _run(prev, [], threshold=1)
    disc = next(e for e in events if e["type"] == "disconnect")
    assert disc["duration"] >= 599


# ── roam ──────────────────────────────────────────────────────────────────────


def test_roam_between_aps():
    prev = {"AA:BB:CC:DD:EE:01": _state(ap="AP1")}
    events, state = _run(prev, [_dev(ap="AP2")])
    assert "roam" in _types(events)
    roam = next(e for e in events if e["type"] == "roam")
    assert roam["from_ap"] == "AP1"
    assert roam["ap"] == "AP2"


def test_no_roam_same_ap():
    prev = {"AA:BB:CC:DD:EE:01": _state(ap="AP1")}
    events, _ = _run(prev, [_dev(ap="AP1")])
    assert "roam" not in _types(events)


def test_roam_carries_from_signal():
    prev = {"AA:BB:CC:DD:EE:01": _state(ap="AP1")}
    prev["AA:BB:CC:DD:EE:01"].signal = -70
    events, _ = _run(prev, [_dev(ap="AP2", signal=-55)])
    roam = next(e for e in events if e["type"] == "roam")
    assert roam["from_signal"] == -70
    assert roam["signal"] == -55


# ── band_change ───────────────────────────────────────────────────────────────


def test_band_change_same_ap():
    prev = {"AA:BB:CC:DD:EE:01": _state(ap="AP1", band="2.4GHz")}
    events, _ = _run(prev, [_dev(ap="AP1", band="5GHz")])
    assert "band_change" in _types(events)
    bc = next(e for e in events if e["type"] == "band_change")
    assert bc["from_band"] == "2.4GHz"


def test_no_band_change_different_ap():
    # AP change takes priority — only roam is emitted, not band_change
    prev = {"AA:BB:CC:DD:EE:01": _state(ap="AP1", band="2.4GHz")}
    events, _ = _run(prev, [_dev(ap="AP2", band="5GHz")])
    assert "band_change" not in _types(events)
    assert "roam" in _types(events)


# ── hostname_change ────────────────────────────────────────────────────────────


def test_hostname_change():
    prev = {"AA:BB:CC:DD:EE:01": _state(hostname="old-name")}
    d = _dev(hostname="new-name")
    events, _ = _run(prev, [d])
    assert "hostname_change" in _types(events)
    hc = next(e for e in events if e["type"] == "hostname_change")
    assert hc["from_hostname"] == "old-name"


def test_no_hostname_change_when_empty():
    # hostname_change only fires when both old and new are non-empty
    prev = {"AA:BB:CC:DD:EE:01": _state(hostname="")}
    events, _ = _run(prev, [_dev(hostname="new-name")])
    assert "hostname_change" not in _types(events)


# ── ap_online / ap_offline ────────────────────────────────────────────────────


def test_ap_online_new_device_in_ap_macs():
    ap_mac = "DE:AD:BE:EF:00:01"
    devices = [Device(mac=ap_mac, online=True, vendor="Ubiquiti")]
    events, _ = detect_events({}, devices, TS, ap_macs={ap_mac})
    assert "ap_online" in _types(events)


def test_ap_offline_absent_at_threshold():
    ap_mac = "DE:AD:BE:EF:00:01"
    prev = {
        ap_mac: StateEntry(mac=ap_mac, online=True, miss=0, first_seen=time.time() - 60)
    }
    events, _ = detect_events(
        prev, [], TS, ap_macs={ap_mac}, disconnect_miss_threshold=1
    )
    assert "ap_offline" in _types(events)


def test_ap_online_not_new_device():
    ap_mac = "DE:AD:BE:EF:00:01"
    devices = [Device(mac=ap_mac, online=True, vendor="Ubiquiti")]
    events, _ = detect_events({}, devices, TS, ap_macs={ap_mac})
    assert "new_device" not in _types(events)


# ── last_event_ts dedup ────────────────────────────────────────────────────────


def test_dedup_suppresses_connect_within_interval():
    recent_ts = time.time() - 30  # 30 s ago
    prev = {
        "AA:BB:CC:DD:EE:01": _state(
            online=False,
            miss=DISCONNECT_MISS_THRESHOLD,
            last_event_ts={"connect": recent_ts},
        )
    }
    events, _ = _run(prev, [_dev(online=True)], min_interval=120.0)
    assert "connect" not in _types(events)


def test_dedup_allows_connect_after_interval():
    old_ts = time.time() - 300  # 5 min ago
    prev = {
        "AA:BB:CC:DD:EE:01": _state(
            online=False,
            miss=DISCONNECT_MISS_THRESHOLD,
            last_event_ts={"connect": old_ts},
        )
    }
    events, _ = _run(prev, [_dev(online=True)], min_interval=120.0)
    assert "connect" in _types(events)


def test_dedup_suppresses_roam_within_interval():
    recent_ts = time.time() - 10
    prev = {"AA:BB:CC:DD:EE:01": _state(ap="AP1", last_event_ts={"roam": recent_ts})}
    events, _ = _run(prev, [_dev(ap="AP2")], min_interval=120.0)
    assert "roam" not in _types(events)


def test_last_event_ts_carried_to_new_state():
    old_ts = time.time() - 50
    prev = {"AA:BB:CC:DD:EE:01": _state(last_event_ts={"connect": old_ts})}
    _, new_state = _run(prev, [_dev()])
    # Timestamp should be preserved in new state
    assert new_state["AA:BB:CC:DD:EE:01"].last_event_ts.get("connect") == pytest.approx(
        old_ts
    )


# ── _can_emit ─────────────────────────────────────────────────────────────────


def test_can_emit_no_prev():
    assert _can_emit("connect", None, 120.0, time.time())


def test_can_emit_no_min_interval():
    e = _state()
    e.last_event_ts["connect"] = time.time()
    assert _can_emit("connect", e, 0.0, time.time())


def test_can_emit_blocked_recent():
    e = _state()
    e.last_event_ts["connect"] = time.time() - 10
    assert not _can_emit("connect", e, 120.0, time.time())


def test_can_emit_allowed_after_interval():
    e = _state()
    e.last_event_ts["connect"] = time.time() - 200
    assert _can_emit("connect", e, 120.0, time.time())


# ── _carry_ts ──────────────────────────────────────────────────────────────────


def test_carry_ts_merges_prev_and_emitted():
    prev = _state()
    prev.last_event_ts = {"roam": 100.0, "connect": 50.0}
    entry = _state()
    _carry_ts(entry, prev, {"connect": 999.0})
    assert entry.last_event_ts["roam"] == 100.0
    assert entry.last_event_ts["connect"] == 999.0  # emitted overwrites


def test_carry_ts_no_prev_only_emitted():
    entry = _state()
    _carry_ts(entry, None, {"new_device": 42.0})
    assert entry.last_event_ts == {"new_device": 42.0}


def test_carry_ts_no_prev_no_emitted():
    entry = _state()
    _carry_ts(entry, None, {})
    assert entry.last_event_ts == {}


# ── miss counter carry ────────────────────────────────────────────────────────


def test_miss_increments_across_scans():
    prev: dict[str, StateEntry] = {}
    d = _dev(online=True)
    _, s1 = _run(prev, [d])
    _, s2 = _run(s1, [])  # device absent
    assert s2["AA:BB:CC:DD:EE:01"].miss == 1
    _, s3 = _run(s2, [])
    assert s3["AA:BB:CC:DD:EE:01"].miss == 2


def test_miss_resets_on_reconnect():
    prev = {"AA:BB:CC:DD:EE:01": _state(miss=5, online=False)}
    _, state = _run(prev, [_dev(online=True)])
    assert state["AA:BB:CC:DD:EE:01"].miss == 0


# ── remap_random_macs ─────────────────────────────────────────────────────────

# 2E:... is locally-administered (0x2E & 0x02 = 2); 38:... is globally-administered
RANDOM_MAC = "2E:00:00:00:00:01"
RANDOM_MAC2 = "4A:BB:CC:DD:EE:FF"
GLOBAL_MAC = "38:00:00:00:00:01"
CANONICAL = "AA:BB:CC:DD:EE:01"  # first-seen canonical (itself random, stored in prev)


def _prev_with_hostname(mac=CANONICAL, hostname="phone", online=True):
    return {mac: StateEntry(mac=mac, hostname=hostname, online=online)}


def test_remap_rotated_random_mac_to_canonical():
    prev = _prev_with_hostname()
    d = Device(mac=RANDOM_MAC, hostname="phone", online=True)
    result = remap_random_macs([d], prev, miss_threshold=3)
    assert result[0].mac == CANONICAL


def test_remap_preserves_other_fields():
    prev = _prev_with_hostname()
    d = Device(
        mac=RANDOM_MAC, hostname="phone", online=True, ip="192.168.1.5", ap="AP1"
    )
    result = remap_random_macs([d], prev, miss_threshold=3)
    assert result[0].ip == "192.168.1.5"
    assert result[0].ap == "AP1"


def test_no_remap_global_mac():
    prev = _prev_with_hostname(mac=GLOBAL_MAC)
    d = Device(mac=GLOBAL_MAC, hostname="phone", online=True)
    result = remap_random_macs([d], prev, miss_threshold=3)
    assert result[0].mac == GLOBAL_MAC


def test_no_remap_empty_hostname():
    prev = _prev_with_hostname()
    d = Device(mac=RANDOM_MAC, hostname="", online=True)
    result = remap_random_macs([d], prev, miss_threshold=3)
    assert result[0].mac == RANDOM_MAC


def test_no_remap_when_canonical_still_in_scan():
    # Both old canonical AND new random MAC present simultaneously — no remap
    prev = _prev_with_hostname()
    devices = [
        Device(mac=CANONICAL, hostname="phone", online=True),
        Device(mac=RANDOM_MAC, hostname="phone", online=True),
    ]
    result = remap_random_macs(devices, prev, miss_threshold=3)
    macs = [d.mac for d in result]
    assert CANONICAL in macs
    assert RANDOM_MAC in macs  # not remapped


def test_no_remap_ambiguous_hostname():
    # Two prev_state entries share the same hostname — unsafe to remap
    prev = {
        CANONICAL: StateEntry(mac=CANONICAL, hostname="phone", online=True),
        GLOBAL_MAC: StateEntry(mac=GLOBAL_MAC, hostname="phone", online=True),
    }
    d = Device(mac=RANDOM_MAC, hostname="phone", online=True)
    result = remap_random_macs([d], prev, miss_threshold=3)
    assert result[0].mac == RANDOM_MAC


def test_no_remap_used_canonical_twice():
    # Two random-MAC devices claim the same hostname → only first is remapped
    prev = _prev_with_hostname()
    devices = [
        Device(mac=RANDOM_MAC, hostname="phone", online=True),
        Device(mac=RANDOM_MAC2, hostname="phone", online=True),
    ]
    result = remap_random_macs(devices, prev, miss_threshold=3)
    macs = [d.mac for d in result]
    # Exactly one remapped to canonical, the other keeps its random MAC
    assert macs.count(CANONICAL) == 1
    assert len(result) == 2


def test_remap_no_new_device_event_after_mac_rotation():
    """Full pipeline: rotation shouldn't fire new_device if hostname matches."""
    prev = _prev_with_hostname()
    d = Device(mac=RANDOM_MAC, hostname="phone", online=True)
    remapped = remap_random_macs([d], prev, miss_threshold=3)
    events, _ = detect_events(prev, remapped, TS)
    assert "new_device" not in [e["type"] for e in events]


def test_remap_stale_prev_entry_not_used():
    # Entry is offline AND miss >= threshold → excluded from h2mac
    prev = {
        CANONICAL: StateEntry(mac=CANONICAL, hostname="phone", online=False, miss=5)
    }
    d = Device(mac=RANDOM_MAC, hostname="phone", online=True)
    result = remap_random_macs([d], prev, miss_threshold=3)
    assert result[0].mac == RANDOM_MAC  # not remapped — prev too stale
