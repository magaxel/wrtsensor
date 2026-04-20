# Development

## Table of contents

- [Commands](#commands)
- [Repository layout](#repository-layout)
- [Tests](#tests)
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

# Inspect the live event log
cat /dev/shm/netscan_events.json
```

The scanner writes state under `/dev/shm` on HA (or `/tmp/netscan` locally): previous-scan device state, MAC vendor cache, DNS cache, CPU delta state, and the event log.

## Repository layout

```
diagnose.py                  # Standalone scanner (manual/command_line path)
openwrt_collector.sh         # Symlink → custom_components/wrtsensor/openwrt_collector.sh
command_line.yaml            # Example command_line sensor definitions (manual path)
templates.yaml               # Example derived template sensors (manual path)
www/                         # Lovelace custom cards (manual path — copy to /config/www/)
  network-list-card.js
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
tests/                       # pytest suite — 99 tests
  fixtures/                  # sanitised AP output captures (AP1.txt, AP2.txt, AP3.txt)
  test_coordinator.py
  test_events.py
  test_parser.py
tools/
  redact_ips.py              # Helper for redacting IP addresses from screenshots
hacs.json
```

## Tests

```bash
python3 -m pytest tests/ -q
```

Fixtures in `tests/fixtures/` are sanitised captures of real AP output — MACs and SSIDs have been replaced with synthetic values while preserving the shape and bit-properties the parsers depend on.

## CI

`.github/workflows/ci.yml` runs three jobs on every push and PR:

| Job | What it runs |
|-----|--------------|
| `lint`     | ruff check + ruff format --check + shellcheck + jq JSON validation |
| `test`     | `python3 -m pytest tests/ -q` |
| `hassfest` | `home-assistant/actions/hassfest@master` — HA's official integration validator |

The HACS validator is intentionally omitted — it requires brand assets submitted to `home-assistant/brands`. The placeholder icons under `custom_components/wrtsensor/brand/` are ready when that PR is made.
