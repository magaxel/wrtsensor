# Development

## Table of contents

- [Commands](#commands)
- [Repository layout](#repository-layout)
- [Tests](#tests)
- [Fixture corpus](#fixture-corpus)
- [CI](#ci)

## Commands

```bash
# Lint — Python
ruff check diagnose.py custom_components/wrtsensor tests
ruff format --check diagnose.py custom_components/wrtsensor tests

# Lint — shell / JS / YAML / JSON
shellcheck custom_components/wrtsensor/openwrt_collector.sh
biome check www/*.js
yamllint command_line.yaml templates.yaml
yamlfmt -dry command_line.yaml templates.yaml
jq . custom_components/wrtsensor/manifest.json > /dev/null
jq . custom_components/wrtsensor/strings.json > /dev/null
jq . custom_components/wrtsensor/translations/en.json > /dev/null
jq . hacs.json > /dev/null

# Run the scanner locally (replace with your gateway + any APs)
python3 diagnose.py root@<gateway-ip> root@<ap-ip> [root@<ap-ip> ...]

# Inspect the manual-install event log
cat /dev/shm/netscan_events.json
```

The standalone scanner writes state under `/dev/shm` on HA (or `/tmp/netscan` locally): previous-scan device state, MAC vendor cache, DNS cache, CPU delta state, and the manual-install event log. The HACS integration keeps recent events in memory instead of writing an event log file.

## Repository layout

```
diagnose.py                  # Standalone scanner (manual/command_line path)
openwrt_collector.sh         # Symlink → custom_components/wrtsensor/openwrt_collector.sh
command_line.yaml            # Example command_line sensor definitions (manual path)
templates.yaml               # Example derived template sensors (manual path)
www/                         # Lovelace custom cards (manual path — copy to /config/www/)
  network-table-card.js
  network-topology-card.js
  network-events-card.js
  dns-stats-card.js
custom_components/wrtsensor/ # HACS integration (auto-installs everything)
  __init__.py
  manifest.json
  config_flow.py
  coordinator.py
  parser.py
  sensor.py
  binary_sensor.py
  device_tracker.py
  diagnostics.py
  const.py
  strings.json
  translations/en.json
  openwrt_collector.sh       # bundled collector — auto-deployed to APs via SFTP
  brand/                     # icon assets (prepared for home-assistant/brands PR)
  www/                       # bundled cards — served via /wrtsensor_static/
tests/                       # pytest suite
  fixtures/openwrt/<ver>/    # version-labelled captures (gateway/, ap1/, ap2/, ap3/)
  test_coordinator.py
  test_events.py
  test_parser.py
  test_openwrt_collector.py  # subprocess-runs openwrt_collector.sh vs stubbed iwinfo/iw
tools/
  capture_fixtures.sh        # SSH into an OpenWrt host, dump every command we parse
  sanitise_fixtures.py       # Rewrite real MACs/IPs → canonical fakes (run before commit)
  redact_ips.py              # Helper for redacting IP addresses from screenshots
hacs.json
```

## Tests

```bash
python3 -m pytest tests/ -q
```

Fixtures in `tests/fixtures/openwrt/<version>/` are sanitised captures of real gateway and AP output — MACs, IPs, and SSIDs have been replaced with synthetic values while preserving the shape and bit-properties the parsers depend on. See [Fixture corpus](#fixture-corpus) below for how to add a new OpenWrt version.

## Fixture corpus

Parser behaviour is pinned against a version-labelled corpus under `tests/fixtures/openwrt/`. Each `<version>/` directory holds one fully self-contained capture set (one `gateway/` subdir + one or more `ap*/` subdirs). Structural assertions in `test_parser.py` and `test_openwrt_collector.py` run across every captured version automatically — new OpenWrt releases that break output format will fail CI.

### Adding a new version

```bash
# 1. Capture — one call per role. Keys must already be in authorized_keys.
./tools/capture_fixtures.sh root@<gateway-ip> gateway 25.12.3
./tools/capture_fixtures.sh root@<ap1-ip>     ap1     25.12.3
./tools/capture_fixtures.sh root@<ap2-ip>     ap2     25.12.3
# ... as many APs as you have

# 2. Sanitise — replaces real MACs/IPs with canonical fakes (idempotent).
python3 tools/sanitise_fixtures.py tests/fixtures/openwrt/25.12.3

# 3. Verify tests pass against the new version.
python3 -m pytest tests/ -q

# 4. Commit. The .sanitise-map.json audit file is gitignored.
git add tests/fixtures/openwrt/25.12.3
git commit -m "fixtures: add OpenWrt 25.12.3 capture"
```

`capture_fixtures.sh` prints a per-command pass/fail summary — some commands (e.g. `nf_conntrack`) are missing on older releases; that's expected. `sanitise_fixtures.py` preserves the first octet of every MAC so LAA/UAA bit-parity tests keep working, and maps every real IP into the RFC 5737 `192.0.2.x` documentation range.

## CI

`.github/workflows/ci.yml` runs three jobs on every push and PR:

| Job | What it runs |
|-----|--------------|
| `lint`     | ruff check + ruff format --check + shellcheck + jq JSON validation |
| `test`     | `python3 -m pytest tests/ -q` |
| `hassfest` | `home-assistant/actions/hassfest@master` — HA's official integration validator |

The HACS validator is intentionally omitted — it requires brand assets submitted to `home-assistant/brands`. The placeholder icons under `custom_components/wrtsensor/brand/` are ready when that PR is made.
