import {
  css,
  html,
  LitElement,
  nothing,
} from "https://cdn.jsdelivr.net/gh/lit/dist@3/all/lit-all.min.js";

const CARD_VERSION = "2.8.0";
const CARD_TYPE = "network-table-card";
const EDITOR_TYPE = `${CARD_TYPE}-editor`;

// ── formatters ────────────────────────────────────────────────────────────────

function fmtBytes(bytes) {
  if (bytes == null) return "—";
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(2)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes >= 1e3) return `${Math.round(bytes / 1e3)} KB`;
  return `${bytes} B`;
}

function fmtAge(ts) {
  if (ts == null) return "—";
  const secs = Math.floor(Date.now() / 1000) - ts;
  if (secs < 120) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  if (secs < 86400 * 2) return "1 day";
  return `${Math.floor(secs / 86400)} days`;
}

function fmtMbps(bps) {
  if (bps == null) return "—";
  const m = (bps * 8) / 1e6;
  return m >= 10 ? m.toFixed(0) : m >= 1 ? m.toFixed(1) : m.toFixed(2);
}

function ipSortKey(ip) {
  const p = (ip || "").split(".");
  if (p.length !== 4) return "999.999.999.999";
  return p.map((n) => String(parseInt(n, 10) || 0).padStart(3, "0")).join(".");
}

// ── columns ───────────────────────────────────────────────────────────────────

const ARROW_DOWN = html`<span class="dl">↓</span>`;
const ARROW_UP = html`<span class="ul">↑</span>`;

const ALL_COLS = [
  { key: "ip", label: "IP", filterable: true, filterKey: "ip" },
  { key: "ip6", label: "IPv6", filterable: true, filterKey: "ip6" },
  { key: "ip6_enabled", label: "IPv6", filterable: false },
  { key: "hostname", label: "Hostname", filterable: true, filterKey: "host" },
  { key: "vendor", label: "Vendor", filterable: true, filterKey: "vendor" },
  { key: "mac", label: "MAC", filterable: true, filterKey: "mac" },
  { key: "connection", label: "", filterable: false },
  { key: "ap", label: "Port/AP", filterable: true, filterKey: "ap" },
  { key: "band", label: "Band", filterable: true, filterKey: "band" },
  { key: "tx_rate", label: "TX (Mbit/s)", filterable: false },
  { key: "signal", label: "Signal (dBm)", filterable: false },
  { key: "rx_bps", label: "↓ Mbit/s", labelTpl: html`${ARROW_DOWN} Mbit/s`, filterable: false },
  { key: "tx_bps", label: "↑ Mbit/s", labelTpl: html`${ARROW_UP} Mbit/s`, filterable: false },
  { key: "noise", label: "Noise (dBm)", filterable: false },
  { key: "snr", label: "SNR (dB)", filterable: false },
  { key: "rx_rate", label: "RX (Mbit/s)", filterable: false },
  { key: "exp_tput", label: "Exp. (Mbit/s)", filterable: false },
  { key: "rx_total", label: "↓ Total", labelTpl: html`${ARROW_DOWN} Total`, filterable: false },
  { key: "tx_total", label: "↑ Total", labelTpl: html`${ARROW_UP} Total`, filterable: false },
  { key: "first_seen", label: "Discovered", filterable: false },
  { key: "bw_since", label: "BW since", filterable: false },
];

const DEFAULT_COLS = [
  "ip",
  "ip6_enabled",
  "hostname",
  "vendor",
  "connection",
  "ap",
  "band",
  "tx_rate",
  "rx_rate",
  "rx_total",
  "tx_total",
];

const COL_DISPLAY_NAME = {
  ip: "IP",
  ip6: "IPv6 (full address)",
  ip6_enabled: "IPv6 (enabled indicator)",
  hostname: "Hostname",
  vendor: "Vendor",
  mac: "MAC",
  connection: "Connection",
  ap: "Port/AP",
  band: "Band",
  tx_rate: "TX (Mbit/s)",
  signal: "Signal (dBm)",
  rx_bps: "↓ Download (Mbit/s)",
  tx_bps: "↑ Upload (Mbit/s)",
  noise: "Noise floor (dBm)",
  snr: "SNR (dB)",
  rx_rate: "RX PHY rate (Mbit/s)",
  exp_tput: "Expected throughput (Mbit/s)",
  rx_total: "↓ Total downloaded",
  tx_total: "↑ Total uploaded",
  first_seen: "Discovered (time since first seen)",
  bw_since: "BW since (time tracking started)",
};

function colDisplayName(col) {
  return COL_DISPLAY_NAME[col.key] ?? col.label ?? col.key;
}

function portApValue(d) {
  if (isUnknownPath(d)) return "Unknown";
  if (isConfirmedWifi(d) && d.ap) return d.ap;
  if (d.switch_port) {
    return d._switchName ? `${d._switchName} #${d.switch_port}` : `Port ${d.switch_port}`;
  }
  if (d._topoPort) {
    return d._topoName ? `${d._topoName} #${d._topoPort}` : `Port ${d._topoPort}`;
  }
  return "";
}

function isConfirmedWifi(d) {
  return (
    d.connection === "wifi" &&
    (d.signal != null || d.tx_rate != null || d.rx_rate != null || d.exp_tput != null)
  );
}

function isUnknownPath(d) {
  if (d.connection === "gateway") return false;
  if (d.switch_port || d.switch_host) return false;
  // An AP or switch sits on a port that also relays everything behind it, so
  // resolve_switch_ports discards that port as an uplink and leaves the host
  // with no switch_port. host_topology resolves the same link without the
  // MAC-count threshold, so a host with a parent there has a known path.
  if (d._topoPort) return false;
  if (isConfirmedWifi(d)) return false;
  return d.connection === "wifi" || d.connection === "wired" || !!d.ap;
}

function normalizeColumns(columns) {
  const validKeys = new Set(ALL_COLS.map((c) => c.key));
  const normalized = [];
  for (const key of columns) {
    const nextKey = key === "switch_port" ? "ap" : key;
    if (!validKeys.has(nextKey) || normalized.includes(nextKey)) continue;
    normalized.push(nextKey);
  }
  return normalized;
}

// ── card ──────────────────────────────────────────────────────────────────────

class NetworkTableCard extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
    _filters: { state: true },
    _sortKey: { state: true },
    _sortDir: { state: true },
    _activeFilterCol: { state: true },
  };

  constructor() {
    super();
    this._filters = {};
    this._sortKey = "ip";
    this._sortDir = "asc";
    this._activeFilterCol = null;
  }

  static styles = css`
    :host {
      display: block;
    }
    .wrap {
      overflow-x: auto;
      padding: 0 16px 16px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9em;
      white-space: nowrap;
    }
    th {
      text-align: left;
      padding: 6px 6px;
      border-bottom: 1px solid var(--divider-color, #333);
      color: var(--secondary-text-color, #888);
      font-weight: 500;
      font-size: 0.875em;
      vertical-align: bottom;
    }
    th.th-sortable {
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }
    th.th-sortable:hover,
    th.th-sortable:focus-visible {
      color: var(--primary-color, #009ac7);
    }
    th.th-sortable:focus-visible {
      outline: 2px solid var(--primary-color, #009ac7);
      outline-offset: -2px;
    }
    th.th-sorted-asc,
    th.th-sorted-desc {
      color: var(--primary-color, #009ac7);
    }
    th[data-filter] .th-label {
      cursor: text;
      border-bottom: 1px dashed transparent;
    }
    th[data-filter] .th-label:hover {
      border-bottom-color: currentColor;
    }
    th.th-filter-active .th-label {
      font-style: italic;
      color: var(--primary-color, #009ac7);
    }
    .th-sort-ind {
      font-size: 1.2em;
      font-weight: 600;
      opacity: 0.5;
    }
    th.th-sortable:hover .th-sort-ind,
    th.th-sorted-asc .th-sort-ind,
    th.th-sorted-desc .th-sort-ind {
      opacity: 1;
    }
    th input {
      background: transparent;
      border: none;
      border-bottom: 1px solid var(--primary-color, #009ac7);
      color: var(--primary-text-color);
      font-size: 1em;
      font-weight: 500;
      font-family: inherit;
      padding: 0;
      outline: none;
      width: 100%;
      box-sizing: border-box;
      min-width: 40px;
    }
    th input::placeholder {
      color: var(--secondary-text-color);
      font-style: italic;
      font-weight: normal;
    }
    td {
      padding: 3px 6px;
      border-bottom: 1px solid var(--divider-color, #1a1a1a);
      vertical-align: middle;
      user-select: text;
    }
    td.mono {
      font-family: monospace;
      font-size: 0.9em;
    }
    td.ellipsis {
      max-width: 12em;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    tr.row-offline td {
      opacity: 0.4;
    }
    .small {
      font-size: 0.85em;
    }
    .dim {
      color: var(--disabled-text-color, #555);
    }
    .dl {
      color: var(--success-color);
      font-size: 1.2em;
      font-weight: 600;
    }
    .ul {
      color: var(--warning-color);
      font-size: 1.2em;
      font-weight: 600;
    }
    .ic-green {
      color: var(--success-color);
    }
    .ic-orange {
      color: var(--warning-color);
    }
    .ic-deep-orange {
      color: var(--warning-color);
      color: color-mix(in srgb, var(--warning-color) 55%, var(--error-color) 45%);
    }
    .ic-red {
      color: var(--error-color);
    }
    .ic-cyan {
      color: var(--info-color, #4dd0e1);
    }
    .msg {
      padding: 16px;
      color: var(--secondary-text-color);
      font-size: 0.9em;
    }
  `;

  setConfig(config) {
    if (!config?.entity) throw new Error("entity required");
    let cols = DEFAULT_COLS;
    if (Array.isArray(config.columns) && config.columns.length) {
      const validKeys = new Set([...ALL_COLS.map((c) => c.key), "switch_port"]);
      const unknown = config.columns.filter((k) => !validKeys.has(k));
      if (unknown.length) {
        console.warn(`[${CARD_TYPE}] Ignoring unknown column key(s): ${unknown.join(", ")}`);
      }
      cols = normalizeColumns(config.columns);
    }
    this._config = {
      entity: config.entity,
      title: config.title ?? "Network",
      show_offline: config.show_offline ?? true,
      show_unknown: config.show_unknown ?? true,
      columns: cols,
    };
  }

  connectedCallback() {
    super.connectedCallback();
    this._escHandler = (e) => {
      if (e.key !== "Escape") return;
      if (this.renderRoot?.activeElement?.tagName === "INPUT") return;
      if (Object.values(this._filters).some(Boolean)) {
        this._filters = {};
        this._activeFilterCol = null;
      }
    };
    document.addEventListener("keydown", this._escHandler);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._escHandler) document.removeEventListener("keydown", this._escHandler);
  }

  updated(changed) {
    if (changed.has("_activeFilterCol") && this._activeFilterCol) {
      const input = this.renderRoot.querySelector(`th[data-col="${this._activeFilterCol}"] input`);
      if (input) {
        input.focus();
        input.select();
      }
    }
  }

  render() {
    if (!this._config) return nothing;
    if (!this.hass) {
      return html`<ha-card><div class="msg">Loading…</div></ha-card>`;
    }
    const state = this.hass.states[this._config.entity];
    if (!state) {
      return html`<ha-card .header=${this._config.title}>
        <div class="msg">Entity not found: ${this._config.entity}</div>
      </ha-card>`;
    }
    if (state.state === "unavailable" || state.state === "unknown") {
      return html`<ha-card .header=${this._config.title}>
        <div class="msg">${this._config.entity} is ${state.state}</div>
      </ha-card>`;
    }

    // Infra hosts (gateway/AP/switch) whose SSH probe failed this cycle are
    // still listed in `devices` via stale ARP data — host_stats.available is
    // the authoritative signal for whether the device itself is up, in BOTH
    // directions. Only falling through on available===false (and leaving
    // available===true untouched) would let a stale ARP-derived d.online
    // keep a row stuck offline after the host is confirmed back up, e.g.
    // right after a reboot.
    const hostStats = state.attributes?.host_stats ?? {};
    const switchNames = state.attributes?.switch_names ?? {};
    const hostNames = state.attributes?.host_names ?? {};
    const switchHosts = state.attributes?.switch_hosts ?? [];
    const apNames = state.attributes?.ap_names ?? {};
    const hostTopology = state.attributes?.host_topology ?? {};
    // Friendly name for whichever infra host a topology edge points at — it
    // may be a switch, an AP, or the gateway, so try every name map.
    const infraName = (host) =>
      switchNames[host] || apNames[host] || hostNames[host] || "";
    // Resolve a wired device's owning switch to a friendly name, mirroring the
    // topology card: switch_names → host_names → single-switch fallback.
    const switchName = (d) => {
      const key = d.switch_host;
      if (!key) return "";
      return (
        switchNames[key] ||
        hostNames[key] ||
        (switchHosts.length === 1
          ? switchNames[switchHosts[0]] || hostNames[switchHosts[0]]
          : "") ||
        ""
      );
    };
    const all = (state.attributes?.devices ?? []).map((d) => {
      const stats = d.ip ? hostStats[d.ip] : undefined;
      const _switchName = switchName(d);
      // Configured APs/switches only: host_topology is keyed by their IP.
      const topo = d.ip ? hostTopology[d.ip] : undefined;
      const _topoPort = topo?.parent_port ?? "";
      const _topoName = _topoPort ? infraName(topo.parent_host) : "";
      const extra = { _switchName, _topoPort, _topoName };
      if (stats?.available === true) return { ...d, ...extra, online: true };
      if (stats?.available === false) return { ...d, ...extra, online: false };
      return { ...d, ...extra };
    });
    const visible = all.filter(
      (d) =>
        (this._config.show_offline || d.online !== false) &&
        (this._config.show_unknown || !isUnknownPath(d)),
    );
    const cols = ALL_COLS.filter((c) => this._config.columns.includes(c.key));
    const sorted = this._sortDevices(visible);
    const filtered = sorted.filter((d) => this._matchesFilters(d));

    return html`
      <ha-card .header=${this._config.title}>
        <div class="wrap">
          <table>
            <thead>
              <tr>
                ${cols.map((c) => this._renderHeader(c))}
              </tr>
            </thead>
            <tbody>
              ${filtered.map((d) => this._renderRow(d, cols))}
            </tbody>
          </table>
        </div>
      </ha-card>
    `;
  }

  _renderHeader(col) {
    const sorted = this._sortKey === col.key;
    const sortedClass = sorted ? `th-sorted-${this._sortDir}` : "";
    const sortInd = sorted ? (this._sortDir === "asc" ? "▲" : "▼") : "↕";
    const ariaSort = sorted ? (this._sortDir === "asc" ? "ascending" : "descending") : "none";
    const label = colDisplayName(col);

    if (this._activeFilterCol === col.key && col.filterable) {
      const val = this._filters[col.filterKey] || "";
      return html`<th
        class="th-sortable ${sortedClass}"
        data-col=${col.key}
        scope="col"
        aria-sort=${ariaSort}
      >
        <input
          type="text"
          aria-label=${`Filter ${label}`}
          placeholder=${`Filter ${label}`}
          .value=${val}
          @input=${(e) => this._onFilterInput(col.filterKey, e.target.value)}
          @keydown=${(e) => this._onFilterKeydown(e)}
          @blur=${() => (this._activeFilterCol = null)}
        />
      </th>`;
    }

    const filterVal = col.filterable ? this._filters[col.filterKey] || "" : "";
    const filterActive = filterVal ? "th-filter-active" : "";
    const labelContent = filterVal ? filterVal : (col.labelTpl ?? col.label);

    if (!col.filterable) {
      return html`<th
        class="th-sortable ${sortedClass}"
        data-col=${col.key}
        scope="col"
        tabindex="0"
        aria-sort=${ariaSort}
        aria-label=${`Sort by ${label}`}
        @click=${() => this._handleSortClick(col.key)}
        @keydown=${(e) => this._onHeaderKeydown(e, col)}
      >
        ${labelContent} <span class="th-sort-ind" aria-hidden="true">${sortInd}</span>
      </th>`;
    }

    return html`<th
      class="th-sortable ${sortedClass} ${filterActive}"
      data-col=${col.key}
      data-filter=${col.filterKey}
      scope="col"
      tabindex="0"
      aria-sort=${ariaSort}
      aria-label=${`Sort by ${label}. Press F or slash to filter.`}
      @click=${() => this._handleSortClick(col.key)}
      @keydown=${(e) => this._onHeaderKeydown(e, col)}
    >
      <span class="th-label" @click=${(e) => this._activateFilter(e, col.key)}
        >${labelContent}</span
      >
      <span class="th-sort-ind" aria-hidden="true">${sortInd}</span>
    </th>`;
  }

  _activateFilter(e, key) {
    e.stopPropagation();
    this._activeFilterCol = key;
  }

  _onFilterInput(filterKey, value) {
    this._filters = { ...this._filters, [filterKey]: value };
  }

  _onFilterKeydown(e) {
    if (e.key === "Escape") {
      this._filters = {};
      this._activeFilterCol = null;
    } else if (e.key === "Enter") {
      e.target.blur();
    }
  }

  _onHeaderKeydown(e, col) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      this._handleSortClick(col.key);
    } else if (col.filterable && (e.key === "f" || e.key === "F" || e.key === "/")) {
      e.preventDefault();
      this._activeFilterCol = col.key;
    }
  }

  _handleSortClick(key) {
    if (this._activeFilterCol) return;
    if (this._sortKey === key) {
      this._sortDir = this._sortDir === "asc" ? "desc" : "asc";
    } else {
      this._sortKey = key;
      this._sortDir = "asc";
    }
  }

  _renderRow(d, cols) {
    const offlineCls = d.online === false ? "row-offline" : "";
    return html`<tr class=${offlineCls}>
      ${cols.map((c) => this._renderCell(c, d))}
    </tr>`;
  }

  _renderCell(col, d) {
    let cls = "";
    if (col.key === "ip" || col.key === "mac") cls = "mono";
    else if (col.key === "vendor") cls = "ellipsis";
    const title = col.key === "vendor" ? d.vendor || "" : nothing;
    return html`<td class=${cls} title=${title}>
      ${this._cellContent(col, d)}
    </td>`;
  }

  _cellContent(col, d) {
    switch (col.key) {
      case "ip":
        return d.ip || d.ip6 || "—";
      case "ip6":
        return d.ip6 ? html`<span class="mono small">${d.ip6}</span>` : "—";
      case "ip6_enabled":
        return d.ip6
          ? html`<ha-icon
              icon="mdi:check-circle"
              class="ic-green"
              title=${d.ip6}
              role="img"
              aria-label=${`IPv6 address ${d.ip6}`}
            ></ha-icon>`
          : html`<span class="dim">—</span>`;
      case "hostname":
        return d.hostname || "—";
      case "vendor":
        return d.vendor || "—";
      case "mac":
        return d.mac || "—";
      case "connection":
        if (isConfirmedWifi(d)) return this._signalIcon(d.signal);
        if (isUnknownPath(d))
          return html`<ha-icon
            icon="mdi:help-network"
            title="Unknown connection path"
            role="img"
            aria-label="Unknown connection path"
          ></ha-icon>`;
        if (d.connection === "wired") return this._wiredIcon(d.tx_rate);
        return "—";
      case "ap":
        return portApValue(d) || "—";
      case "band":
        return d.band || "—";
      case "tx_rate":
        return d.tx_rate != null ? String(d.tx_rate) : "—";
      case "signal":
        return d.signal != null ? String(d.signal) : "—";
      case "rx_bps":
        return fmtMbps(d.rx_bps);
      case "tx_bps":
        return fmtMbps(d.tx_bps);
      case "noise":
        return d.noise != null ? String(d.noise) : "—";
      case "snr":
        return d.snr != null ? String(d.snr) : "—";
      case "rx_rate":
        return d.rx_rate != null ? String(d.rx_rate) : "—";
      case "exp_tput":
        return d.exp_tput != null ? String(d.exp_tput) : "—";
      case "rx_total": {
        const t = d.bw_since
          ? `since ${new Date(d.bw_since * 1000).toLocaleDateString()}`
          : nothing;
        return html`<span title=${t}>${fmtBytes(d.rx_total)}</span>`;
      }
      case "tx_total": {
        const t = d.bw_since
          ? `since ${new Date(d.bw_since * 1000).toLocaleDateString()}`
          : nothing;
        return html`<span title=${t}>${fmtBytes(d.tx_total)}</span>`;
      }
      case "first_seen": {
        if (!d.first_seen) return "—";
        const full = new Date(d.first_seen * 1000).toLocaleString();
        return html`<span title=${full}>${fmtAge(d.first_seen)}</span>`;
      }
      case "bw_since": {
        if (!d.bw_since) return "—";
        const full = new Date(d.bw_since * 1000).toLocaleString();
        return html`<span title=${full}>${fmtAge(d.bw_since)}</span>`;
      }
      default:
        return "—";
    }
  }

  _signalIcon(sig) {
    const s = Number(sig);
    if (Number.isNaN(s))
      return html`<ha-icon
        icon="mdi:wifi"
        title="Wi-Fi signal unavailable"
        role="img"
        aria-label="Wi-Fi signal unavailable"
      ></ha-icon>`;
    if (s > -60)
      return html`<ha-icon
        icon="mdi:wifi-strength-4"
        class="ic-green"
        title=${`Excellent Wi-Fi signal (${s} dBm)`}
        role="img"
        aria-label=${`Excellent Wi-Fi signal, ${s} dBm`}
      ></ha-icon>`;
    if (s > -70)
      return html`<ha-icon
        icon="mdi:wifi-strength-3"
        class="ic-orange"
        title=${`Good Wi-Fi signal (${s} dBm)`}
        role="img"
        aria-label=${`Good Wi-Fi signal, ${s} dBm`}
      ></ha-icon>`;
    if (s > -75)
      return html`<ha-icon
        icon="mdi:wifi-strength-2"
        class="ic-deep-orange"
        title=${`Fair Wi-Fi signal (${s} dBm)`}
        role="img"
        aria-label=${`Fair Wi-Fi signal, ${s} dBm`}
      ></ha-icon>`;
    return html`<ha-icon
      icon="mdi:wifi-strength-1"
      class="ic-red"
      title=${`Weak Wi-Fi signal (${s} dBm)`}
      role="img"
      aria-label=${`Weak Wi-Fi signal, ${s} dBm`}
    ></ha-icon>`;
  }

  _wiredIcon(speed) {
    // Colour the ethernet icon by negotiated link speed, mirroring how the
    // Wi-Fi icon is coloured by signal: cyan = 2.5 Gbit/s+, green = gigabit,
    // orange = 100 Mbit/s (Fast Ethernet), red = 10 Mbit/s. Speed is unknown
    // for shared-port devices, which fall back to a plain (uncoloured) icon.
    const s = Number(speed);
    if (!Number.isFinite(s) || s <= 0)
      return html`<ha-icon
        icon="mdi:ethernet"
        title="Wired connection"
        role="img"
        aria-label="Wired connection"
      ></ha-icon>`;
    const label = s >= 1000 ? `${s / 1000} Gbit/s` : `${s} Mbit/s`;
    const cls =
      s >= 2500 ? "ic-cyan" : s >= 1000 ? "ic-green" : s >= 100 ? "ic-orange" : "ic-red";
    return html`<ha-icon
      icon="mdi:ethernet"
      class=${cls}
      title=${`Wired link: ${label}`}
      role="img"
      aria-label=${`Wired link, ${label}`}
    ></ha-icon>`;
  }

  _sortValue(d, key) {
    switch (key) {
      case "ip":
        return ipSortKey(d.ip || d.ip6 || "");
      case "ip6":
        return d.ip6 || "zzz";
      case "ip6_enabled":
        return d.ip6 ? 1 : 0;
      case "hostname":
        return (d.hostname || "zzz").toLowerCase();
      case "vendor":
        return (d.vendor || "zzz").toLowerCase();
      case "mac":
        return d.mac || "";
      case "connection":
        return d.connection || "";
      case "ap":
        return (portApValue(d) || "zzz").toLowerCase();
      case "band":
        return d.band || "";
      case "tx_rate":
        return d.tx_rate ?? -1;
      case "signal":
        return d.signal ?? -Infinity;
      case "rx_bps":
        return d.rx_bps ?? -1;
      case "tx_bps":
        return d.tx_bps ?? -1;
      case "noise":
        return d.noise ?? Infinity;
      case "snr":
        return d.snr ?? -1;
      case "rx_rate":
        return d.rx_rate ?? -1;
      case "exp_tput":
        return d.exp_tput ?? -1;
      case "rx_total":
        return d.rx_total ?? -1;
      case "tx_total":
        return d.tx_total ?? -1;
      case "first_seen":
        return d.first_seen ?? Infinity;
      case "bw_since":
        return d.bw_since ?? Infinity;
      default:
        return "";
    }
  }

  _sortDevices(devices) {
    const key = this._sortKey;
    if (!key) return devices;
    return [...devices].sort((a, b) => {
      const av = this._sortValue(a, key);
      const bv = this._sortValue(b, key);
      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv));
      return this._sortDir === "asc" ? cmp : -cmp;
    });
  }

  _matchesFilters(d) {
    for (const col of ALL_COLS) {
      if (!col.filterable) continue;
      const val = this._filters[col.filterKey];
      if (!val) continue;
      let target = "";
      switch (col.filterKey) {
        case "ip":
          target = d.ip || d.ip6 || "";
          break;
        case "ip6":
          target = d.ip6 || "";
          break;
        case "host":
          target = d.hostname || "";
          break;
        case "vendor":
          target = d.vendor || "";
          break;
        case "mac":
          target = d.mac || "";
          break;
        case "ap":
          target = [d.ap, d.switch_port, portApValue(d)].filter(Boolean).join(" ");
          break;
        case "band":
          target = d.band || "";
          break;
      }
      if (!String(target).toLowerCase().includes(val.toLowerCase())) return false;
    }
    return true;
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }

  static getStubConfig() {
    return { entity: "", title: "Network" };
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return {
      columns: "full",
      min_columns: 6,
    };
  }
}

// ── editor ────────────────────────────────────────────────────────────────────

class NetworkTableCardEditor extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
  };

  setConfig(config) {
    this._config = config;
  }

  _schema() {
    return [
      {
        name: "entity",
        required: true,
        selector: { entity: { domain: "sensor" } },
      },
      { name: "title", selector: { text: {} } },
      { name: "show_offline", selector: { boolean: {} } },
      { name: "show_unknown", selector: { boolean: {} } },
      {
        name: "columns",
        selector: {
          select: {
            multiple: true,
            mode: "list",
            options: ALL_COLS.map((col) => ({
              value: col.key,
              label: COL_DISPLAY_NAME[col.key] ?? col.label ?? col.key,
            })),
          },
        },
      },
    ];
  }

  _computeLabel = (s) => {
    const labels = {
      entity: "Entity",
      title: "Title",
      show_offline: "Show offline devices (dimmed)",
      show_unknown: "Show unknown-path devices",
      columns: "Columns",
    };
    return labels[s.name] ?? s.name;
  };

  _valueChanged = (ev) => {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: { ...this._config, ...ev.detail.value } },
        bubbles: true,
        composed: true,
      }),
    );
  };

  render() {
    if (!this.hass || !this._config) return nothing;
    const data = {
      entity: this._config.entity ?? "",
      title: this._config.title ?? "Network",
      show_offline: this._config.show_offline ?? true,
      show_unknown: this._config.show_unknown ?? true,
      columns: this._config.columns?.length ? this._config.columns : DEFAULT_COLS,
    };
    return html`
      <ha-form
        .hass=${this.hass}
        .data=${data}
        .schema=${this._schema()}
        .computeLabel=${this._computeLabel}
        @value-changed=${this._valueChanged}
      ></ha-form>
    `;
  }
}

// ── registration ──────────────────────────────────────────────────────────────

if (!customElements.get(EDITOR_TYPE)) {
  customElements.define(EDITOR_TYPE, NetworkTableCardEditor);
}
if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, NetworkTableCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD_TYPE)) {
  window.customCards.push({
    type: CARD_TYPE,
    name: "Network Table",
    description:
      "Filterable network device table from network_scanner sensor (Lit, mobile-friendly)",
    preview: false,
  });
}

console.info(
  `%c NETWORK-TABLE-CARD %c v${CARD_VERSION} `,
  "color: white; background: #009ac7; font-weight: 700;",
  "color: #009ac7; background: white; font-weight: 700;",
);
