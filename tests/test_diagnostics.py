"""Tests for wrtsensor diagnostics redaction."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_WRT = _ROOT / "custom_components" / "wrtsensor"

_diag_name = "custom_components.wrtsensor.diagnostics"
if _diag_name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_diag_name, _WRT / "diagnostics.py")
    _mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    _mod.__package__ = "custom_components.wrtsensor"
    sys.modules[_diag_name] = _mod
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]

_diag = sys.modules[_diag_name]
_const = sys.modules["custom_components.wrtsensor.const"]


def _diagnostics_for(data: dict) -> dict:
    entry = types.SimpleNamespace(entry_id="test-entry", version=99)
    coordinator = types.SimpleNamespace(data=data)
    hass = types.SimpleNamespace(data={_const.DOMAIN: {entry.entry_id: coordinator}})
    return asyncio.run(_diag.async_get_config_entry_diagnostics(hass, entry))


def _blob(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def test_diagnostics_redacts_host_stats_and_uses_fixed_shape():
    result = _diagnostics_for(
        {
            "scan_duration": 1.25,
            "device_count": 2,
            "wan_ip": "198.51.100.10",
            "wan_ip6": "2001:db8::1",
            "partial": 1,
            "host_stats": {
                "192.0.2.1": {
                    "hostname": "gateway-secret",
                    "model": "secret-model",
                    "board_name": "secret-board",
                    "cpu": 12.5,
                    "ram": 44.0,
                    "disk": None,
                },
                "192.0.2.22": {
                    "hostname": "ap-secret",
                    "model": "other-model",
                    "board_name": "other-board",
                    "cpu": None,
                    "ram": 50.0,
                    "disk": 21.0,
                },
            },
            "devices": [
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "vendor": "Vendor",
                    "connection": "wifi",
                    "ap": "AP1",
                    "band": "5GHz",
                    "online": True,
                    "ip": "192.0.2.50",
                    "hostname": "phone-secret",
                }
            ],
        }
    )

    assert set(result) == {
        "integration_version",
        "scan_duration_s",
        "device_count",
        "wan_ip",
        "wan_ip6",
        "partial_scan",
        "host_metrics_summary",
        "devices",
    }
    assert result["integration_version"] == _const.VERSION
    assert result["wan_ip"] == "redacted"
    assert result["wan_ip6"] == "redacted"
    assert result["partial_scan"] is True
    assert result["host_metrics_summary"] == {
        "host_count": 2,
        "metrics_present": ["cpu", "disk", "ram"],
        "hosts_with_missing_metrics": 2,
    }
    assert "host_stats" not in result
    # AP labels are intentional diagnostics context; hostnames and IPs are not.
    assert result["devices"] == [
        {
            "mac": "AA:BB:CC:xx:xx:xx",
            "vendor": "Vendor",
            "connection": "wifi",
            "ap": "AP1",
            "band": "5GHz",
            "online": True,
        }
    ]
    serialized = _blob(result)
    for secret in (
        "192.0.2.1",
        "192.0.2.22",
        "gateway-secret",
        "ap-secret",
        "secret-model",
        "secret-board",
        "other-model",
        "other-board",
        "198.51.100.10",
        "2001:db8::1",
        "phone-secret",
        "DD:EE:FF",
    ):
        assert secret not in serialized


def test_diagnostics_host_metrics_disabled_summary_is_empty():
    result = _diagnostics_for(
        {
            "partial": False,
            "host_stats": {},
            "devices": [],
        }
    )

    assert result["host_metrics_summary"] == {
        "host_count": 0,
        "metrics_present": [],
        "hosts_with_missing_metrics": 0,
    }
    assert result["partial_scan"] is False
