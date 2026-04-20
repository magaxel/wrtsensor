"""End-to-end tests for openwrt_collector.sh against captured raw iwinfo/iw output.

Each AP directory under tests/fixtures/openwrt/<version>/ap*/ may contain raw
captures (iwinfo-list.txt, iwinfo-<iface>-info.txt, iwinfo-<iface>-assoclist.txt,
iw-station-<iface>.txt, proc-stat.txt, proc-meminfo.txt, df-root.txt). When
present, we stub iwinfo/iw/grep-targets as shell scripts and run the real
collector script in a subprocess. This catches awk regressions when a new
OpenWrt release changes iwinfo assoclist formatting.

Tests skip gracefully when an AP directory only contains collector-output.txt
(which is the common case until someone runs tools/capture_fixtures.sh).
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COLLECTOR = ROOT / "custom_components" / "wrtsensor" / "openwrt_collector.sh"
FIXTURES = ROOT / "tests" / "fixtures" / "openwrt"


def _iter_ap_dirs() -> list[tuple[str, str, Path]]:
    cases: list[tuple[str, str, Path]] = []
    if not FIXTURES.is_dir():
        return cases
    for version_dir in sorted(FIXTURES.iterdir()):
        if not version_dir.is_dir():
            continue
        for ap_dir in sorted(version_dir.iterdir()):
            if ap_dir.is_dir() and ap_dir.name.startswith("ap"):
                cases.append((version_dir.name, ap_dir.name, ap_dir))
    return cases


AP_CASES = _iter_ap_dirs()
AP_IDS = [f"{v}-{ap}" for v, ap, _ in AP_CASES]


def _has_raw_captures(ap_dir: Path) -> bool:
    return (ap_dir / "iwinfo-list.txt").is_file()


def _iface_from_assoc_filename(name: str) -> str:
    # iwinfo-<iface>-assoclist.txt
    m = re.match(r"iwinfo-(.+)-assoclist\.txt$", name)
    return m.group(1) if m else ""


def _build_stub_dir(tmp_path: Path, ap_dir: Path) -> Path:
    """Create iwinfo/iw stub binaries that cat the captured outputs."""
    stub = tmp_path / "stubs"
    stub.mkdir()

    iwinfo_list = ap_dir / "iwinfo-list.txt"

    # iwinfo [iface [info|assoclist]]
    iwinfo = stub / "iwinfo"
    iwinfo.write_text(
        f"""#!/bin/sh
case "$#" in
  0) cat {iwinfo_list!s} ;;
  2)
    case "$2" in
      info)      f="{ap_dir!s}/iwinfo-$1-info.txt" ;;
      assoclist) f="{ap_dir!s}/iwinfo-$1-assoclist.txt" ;;
      *) exit 1 ;;
    esac
    [ -f "$f" ] && cat "$f" || exit 1
    ;;
  *) exit 1 ;;
esac
"""
    )

    # iw dev <iface> station dump
    iw = stub / "iw"
    iw.write_text(
        f"""#!/bin/sh
# Expects: iw dev <iface> station dump
if [ "$1" = "dev" ] && [ "$3" = "station" ] && [ "$4" = "dump" ]; then
    f="{ap_dir!s}/iw-station-$2.txt"
    [ -f "$f" ] && cat "$f"
    exit 0
fi
exit 1
"""
    )

    for p in (iwinfo, iw):
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


@pytest.mark.parametrize(("version", "ap", "ap_dir"), AP_CASES, ids=AP_IDS or ["none"])
def test_collector_script_runs_against_raw_fixtures(
    version: str, ap: str, ap_dir: Path, tmp_path: Path
) -> None:
    if not _has_raw_captures(ap_dir):
        pytest.skip(
            f"no raw iwinfo/iw captures for {version}/{ap} — "
            "run tools/capture_fixtures.sh to produce them"
        )

    stub = _build_stub_dir(tmp_path, ap_dir)
    env = {**os.environ, "PATH": f"{stub}:{os.environ.get('PATH', '')}"}

    # start_new_session isolates the collector's process group — its
    # `trap 'kill 0' EXIT` would otherwise terminate pytest itself.
    # Non-zero exit is expected (same trap makes sh exit non-zero) —
    # judge success by output content instead.
    result = subprocess.run(
        ["sh", str(COLLECTOR)],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
        start_new_session=True,
    )
    assert result.stdout, f"collector produced no stdout (stderr: {result.stderr!r})"

    lines = [line for line in result.stdout.splitlines() if line]
    assert lines, "collector produced no output"

    stat_lines = [line for line in lines if line.startswith("STAT|")]
    assert len(stat_lines) == 1, "expected exactly one STAT| line"
    assert "ERROR" not in stat_lines[0], f"collector reported error: {stat_lines[0]}"

    # Every non-STAT line must be a pipe-delimited client entry starting with a MAC.
    client_lines = [line for line in lines if not line.startswith("STAT|")]
    mac_re = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}\|")
    for line in client_lines:
        assert mac_re.match(line), f"malformed client line: {line!r}"
        fields = line.split("|")
        assert len(fields) == 12, f"expected 12 fields, got {len(fields)}: {line!r}"
        band = fields[1]
        assert band in {"2.4GHz", "5GHz", "6GHz", "unknown"}
