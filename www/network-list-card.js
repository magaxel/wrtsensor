import {
  css,
  html,
  LitElement,
  nothing,
} from "https://cdn.jsdelivr.net/gh/lit/dist@3/all/lit-all.min.js";

const CARD_VERSION = "1.0.0";
const CARD_TYPE = "network-list-card";
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

// ── column definitions ───────────────────────────────────────────────────────
// Shared with the table card so `columns:` YAML transfers unchanged.

const ALL_COL_KEYS = [
  "ip",
  "ip6",
  "ip6_enabled",
  "hostname",
  "vendor",
  "mac",
  "connection",
  "ap",
  "band",
  "tx_rate",
  "signal",
  "rx_bps",
  "tx_bps",
  "noise",
  "snr",
  "rx_rate",
  "exp_tput",
  "rx_total",
  "tx_total",
  "first_seen",
  "bw_since",
];

const DEFAULT_COLS = [
  "ip",
  "ip6_enabled",
  "hostname",
  "vendor",
  "mac",
  "connection",
  "ap",
  "band",
  "tx_rate",
  "signal",
];

const COL_DISPLAY_NAME = {
  ip: "IP",
  ip6: "IPv6",
  ip6_enabled: "IPv6 enabled",
  hostname: "Hostname",
  vendor: "Vendor",
  mac: "MAC",
  connection: "Connection",
  ap: "AP",
  band: "Band",
  tx_rate: "TX",
  signal: "Signal",
  rx_bps: "Download",
  tx_bps: "Upload",
  noise: "Noise",
  snr: "SNR",
  rx_rate: "RX",
  exp_tput: "Expected",
  rx_total: "Total down",
  tx_total: "Total up",
  first_seen: "Discovered",
  bw_since: "BW since",
};

// Sort options (a curated subset — sorting by e.g. noise is rarely useful).
const SORT_OPTIONS = [
  { key: "hostname", label: "Hostname" },
  { key: "ip", label: "IP" },
  { key: "vendor", label: "Vendor" },
  { key: "ap", label: "AP" },
  { key: "signal", label: "Signal" },
  { key: "rx_bps", label: "Download" },
  { key: "tx_bps", label: "Upload" },
  { key: "rx_total", label: "Total down" },
  { key: "tx_total", label: "Total up" },
  { key: "first_seen", label: "Discovered" },
];

// ── card ──────────────────────────────────────────────────────────────────────

class NetworkListCard extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
    _filterText: { state: true },
    _sortKey: { state: true },
    _sortDir: { state: true },
    _expanded: { state: true },
  };

  constructor() {
    super();
    this._filterText = "";
    this._sortKey = "ip";
    this._sortDir = "asc";
    this._expanded = new Set();
  }

  static styles = css`
    :host {
      display: block;
      height: 100%;
      user-select: text;
      -webkit-user-select: text;
    }

    ha-card {
      overflow: hidden;
      display: flex;
      flex-direction: column;
      height: 100%;
    }

    /* ── header ────────────────────────────────────────────────────────── */
    .header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      padding: 12px 16px 4px;
      gap: 12px;
    }
    /* Match HA's native ha-card header so the card lines up with stock cards. */
    .title {
      font-family: var(--ha-card-header-font-family, inherit);
      font-size: var(--ha-card-header-font-size, 24px);
      font-weight: normal;
      color: var(--ha-card-header-color, var(--primary-text-color));
      line-height: 32px;
      letter-spacing: -0.012em;
    }
    .count {
      font-size: 0.9rem;
      color: var(--secondary-text-color);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .count .online {
      color: var(--success-color, #43a047);
      font-weight: 600;
    }

    /* ── controls ──────────────────────────────────────────────────────── */
    .controls {
      display: flex;
      gap: 8px;
      padding: 4px 16px 12px;
      flex-wrap: nowrap;
    }
    .search {
      flex: 1 1 auto;
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      background: var(--secondary-background-color, #1f1f1f);
      border-radius: 22px;
      min-width: 0;
    }
    .search ha-icon {
      --mdc-icon-size: 18px;
      color: var(--secondary-text-color);
      flex-shrink: 0;
    }
    .search input {
      flex: 1;
      min-width: 0;
      background: transparent;
      border: none;
      outline: none;
      color: var(--primary-text-color);
      font: inherit;
      font-size: 0.9rem;
      padding: 2px 0;
    }
    .search input::placeholder {
      color: var(--secondary-text-color);
    }
    .search-clear {
      background: none;
      border: none;
      padding: 2px;
      cursor: pointer;
      color: var(--secondary-text-color);
      display: flex;
    }
    .search-clear:hover {
      color: var(--primary-text-color);
    }

    .sort {
      display: flex;
      align-items: center;
      gap: 4px;
      background: var(--secondary-background-color, #1f1f1f);
      border-radius: 22px;
      padding: 2px 4px 2px 12px;
    }
    .sort select {
      background: transparent;
      border: none;
      outline: none;
      color: var(--primary-text-color);
      font: inherit;
      font-size: 0.9rem;
      padding: 4px 2px;
      appearance: none;
      -webkit-appearance: none;
      cursor: pointer;
    }
    .sort select option {
      background: var(--card-background-color, #1c1c1c);
      color: var(--primary-text-color);
    }
    .sort-dir,
    .expand-all {
      background: none;
      border: none;
      padding: 4px;
      cursor: pointer;
      color: var(--secondary-text-color);
      display: flex;
      border-radius: 50%;
    }
    .sort-dir:hover,
    .expand-all:hover {
      color: var(--primary-color);
      background: var(--divider-color);
    }
    .expand-all {
      background: var(--secondary-background-color, #1f1f1f);
      border-radius: 22px;
      padding: 6px 8px;
    }

    /* ── list ──────────────────────────────────────────────────────────── */
    .list-wrap {
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
      flex: 1 1 auto;
      min-height: 0;
    }
    .list {
      display: flex;
      flex-direction: column;
    }

    .device {
      border-top: 1px solid var(--divider-color, #2a2a2a);
      transition: background 0.15s;
    }
    .device:last-child {
      border-bottom: none;
    }
    .device.expanded {
      background: var(--secondary-background-color, #1a1a1a);
    }
    .device.offline .row {
      opacity: 0.5;
    }

    .row {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 16px;
      min-height: 48px;
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
    }
    .row:hover {
      background: var(--state-hover-color, rgba(255, 255, 255, 0.04));
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
      background: var(--disabled-color, #555);
    }
    .dot.on {
      background: var(--success-color, #43a047);
      box-shadow: 0 0 4px var(--success-color, #43a047);
    }

    .icon {
      display: flex;
      align-items: center;
      flex-shrink: 0;
    }
    .icon ha-icon {
      --mdc-icon-size: 22px;
      color: var(--secondary-text-color);
    }
    .icon .sig-4 {
      color: #4caf50;
    }
    .icon .sig-3 {
      color: #ff9800;
    }
    .icon .sig-2 {
      color: #ff5722;
    }
    .icon .sig-1 {
      color: #f44336;
    }

    .identity {
      flex: 1 1 auto;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .name {
      font-size: 0.95rem;
      font-weight: 500;
      color: var(--primary-text-color);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .meta {
      font-size: 0.8rem;
      color: var(--secondary-text-color);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-family: var(--code-font-family, monospace);
      display: flex;
      align-items: baseline;
    }
    .meta > span:first-child {
      min-width: 14ch;
      flex: 0 0 auto;
    }
    .meta .sep {
      opacity: 0.5;
      margin: 0 6px;
    }
    .meta .v {
      font-family: var(--primary-font-family, inherit);
    }

    .sort-badge {
      font-size: 0.8rem;
      color: var(--secondary-text-color);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
      flex-shrink: 0;
    }

    .chevron {
      --mdc-icon-size: 20px;
      color: var(--secondary-text-color);
      flex-shrink: 0;
      transition: transform 0.2s;
    }
    .device.expanded .chevron {
      transform: rotate(180deg);
    }

    /* ── detail ────────────────────────────────────────────────────────── */
    .detail {
      padding: 4px 16px 14px 40px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 6px 16px;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 1px;
      min-width: 0;
    }
    .flabel {
      font-size: 0.7rem;
      color: var(--secondary-text-color);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .fvalue {
      font-size: 0.9rem;
      color: var(--primary-text-color);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .fvalue.mono {
      font-family: var(--code-font-family, monospace);
      font-size: 0.85rem;
    }

    /* ── misc ──────────────────────────────────────────────────────────── */
    .empty {
      padding: 24px 16px;
      text-align: center;
      color: var(--secondary-text-color);
      font-size: 0.9rem;
    }
    .msg {
      padding: 16px;
      color: var(--secondary-text-color);
      font-size: 0.9rem;
    }

    .ic-green {
      color: #4caf50;
    }
    .ic-dim {
      color: var(--disabled-color, #555);
    }
  `;

  setConfig(config) {
    if (!config?.entity) throw new Error("entity required");
    // Empty array is honored; only fall back to defaults when columns is
    // genuinely absent (new card). The editor's fallback used to collide
    // with this one, re-enabling everything when the user cleared the list.
    const cols = Array.isArray(config.columns)
      ? config.columns.filter((k) => ALL_COL_KEYS.includes(k))
      : DEFAULT_COLS;
    this._config = {
      entity: config.entity,
      title: config.title ?? "Network",
      show_offline: config.show_offline ?? false,
      columns: cols,
      max_height: Number.isFinite(config.max_height) ? config.max_height : 560,
    };
  }

  updated() {
    const wrap = this.shadowRoot?.querySelector(".list-wrap");
    if (wrap && this._savedScroll != null) {
      wrap.scrollTop = this._savedScroll;
    }
  }

  _saveScroll = () => {
    const wrap = this.shadowRoot?.querySelector(".list-wrap");
    if (wrap) this._savedScroll = wrap.scrollTop;
  };

  render() {
    if (!this._config) return nothing;
    if (!this.hass) {
      return html`<ha-card><div class="msg">Loading…</div></ha-card>`;
    }

    const state = this.hass.states[this._config.entity];
    if (!state) {
      return html`<ha-card>
        <div class="header"><div class="title">${this._config.title}</div></div>
        <div class="msg">Entity not found: ${this._config.entity}</div>
      </ha-card>`;
    }
    if (state.state === "unavailable" || state.state === "unknown") {
      return html`<ha-card>
        <div class="header"><div class="title">${this._config.title}</div></div>
        <div class="msg">${this._config.entity} is ${state.state}</div>
      </ha-card>`;
    }

    const all = state.attributes?.devices ?? [];
    const visible = this._config.show_offline ? all : all.filter((d) => d.online !== false);
    const filtered = this._applyFilter(visible);
    const sorted = this._sortDevices(filtered);

    // Counter reflects the true device totals, independent of the filter —
    // so "25/26" correctly shows "1 offline" even when show_offline is false.
    const onlineCount = all.filter((d) => d.online !== false).length;
    const totalCount = all.length;

    return html`
      <ha-card>
        <div class="header">
          <div class="title">${this._config.title}</div>
          <div class="count">
            <span class="online">${onlineCount}</span> / ${totalCount}
          </div>
        </div>
        <div class="controls">
          <div class="search">
            <ha-icon icon="mdi:magnify"></ha-icon>
            <input
              type="search"
              placeholder="Search hostname, IP, vendor, MAC…"
              .value=${this._filterText}
              @input=${(e) => {
                this._filterText = e.target.value;
              }}
            />
            ${
              this._filterText
                ? html`<button
                  class="search-clear"
                  @click=${() => {
                    this._filterText = "";
                  }}
                  aria-label="Clear search"
                >
                  <ha-icon icon="mdi:close"></ha-icon>
                </button>`
                : nothing
            }
          </div>
          <div class="sort">
            <select
              @change=${(e) => {
                this._sortKey = e.target.value;
              }}
              aria-label="Sort by"
            >
              ${SORT_OPTIONS.map(
                (s) => html`
                  <option value=${s.key} ?selected=${this._sortKey === s.key}>
                    ${s.label}
                  </option>
                `,
              )}
            </select>
            <button
              class="sort-dir"
              @click=${this._toggleSortDir}
              aria-label=${this._sortDir === "asc" ? "Ascending" : "Descending"}
            >
              <ha-icon
                icon=${this._sortDir === "asc" ? "mdi:arrow-up" : "mdi:arrow-down"}
              ></ha-icon>
            </button>
          </div>
          <button
            class="expand-all"
            @click=${() => this._toggleExpandAll(sorted)}
            aria-label=${sorted.every((d) => this._expanded.has(d.mac || `${d.ip || ""}_${d.hostname || ""}`)) ? "Collapse all" : "Expand all"}
          >
            <ha-icon
              icon=${sorted.every((d) => this._expanded.has(d.mac || `${d.ip || ""}_${d.hostname || ""}`)) ? "mdi:arrow-collapse-all" : "mdi:arrow-expand-all"}
            ></ha-icon>
          </button>
        </div>
        <div class="list-wrap" style=${this._config.max_height > 0 ? `max-height:${this._config.max_height}px` : ""} @scroll=${this._saveScroll}>
        <div class="list">
          ${
            sorted.length === 0
              ? html`<div class="empty">No devices match</div>`
              : sorted.map((d) => this._renderDevice(d))
          }
        </div>
        </div>
      </ha-card>
    `;
  }

  _renderDevice(d) {
    const mac = d.mac || `${d.ip || ""}_${d.hostname || ""}`;
    const expanded = this._expanded.has(mac);
    const offline = d.online === false;
    const classes = ["device"];
    if (offline) classes.push("offline");
    if (expanded) classes.push("expanded");

    const cols = this._config.columns;
    const has = (k) => cols.includes(k);

    // Primary name: first selected name-column that has a value. Falls back to
    // MAC (regardless of cols) so the device stays identifiable if the user
    // deselected every name-bearing column.
    const nameKey = ["hostname", "vendor", "mac"].find((k) => has(k) && d[k]);
    const name = nameKey ? d[nameKey] : d.mac || d.hostname || d.vendor || "Unknown";

    const addrKey = ["ip", "ip6"].find((k) => has(k) && d[k]);
    const addr = addrKey ? d[addrKey] : null;

    const vendorInline = has("vendor") ? (d.vendor && d.vendor !== name ? d.vendor : d.mac) : null;
    const showVendorInline = vendorInline && vendorInline !== name;
    const showConnIcon = has("connection");

    // When sorting by a field that isn't already visible (not name/addr/vendor),
    // show the sort value in the meta line so the sort order makes visual sense.
    const sortBadge = this._sortBadge(this._sortKey, d);

    return html`
      <div class=${classes.join(" ")}>
        <div
          class="row"
          @click=${() => this._toggleExpand(mac)}
          role="button"
          tabindex="0"
          @keydown=${(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              this._toggleExpand(mac);
            }
          }}
        >
          <div class="dot ${offline ? "" : "on"}"></div>
          ${showConnIcon ? html`<div class="icon">${this._renderConnIcon(d)}</div>` : nothing}
          <div class="identity">
            <div class="name">${name}</div>
            ${
              addr || showVendorInline || sortBadge
                ? html`<div class="meta">
                  ${addr ? html`<span>${addr}</span>` : nothing}
                  ${addr && showVendorInline ? html`<span class="sep">·</span>` : nothing}
                  ${showVendorInline ? html`<span class="v">${vendorInline}</span>` : nothing}
                </div>`
                : nothing
            }
          </div>
          ${sortBadge ? html`<span class="sort-badge">${sortBadge}</span>` : nothing}
          <ha-icon class="chevron" icon="mdi:chevron-down"></ha-icon>
        </div>
        ${expanded ? this._renderDetail(d) : nothing}
      </div>
    `;
  }

  _renderConnIcon(d) {
    if (d.connection === "wifi") {
      const s = Number(d.signal);
      let icon = "mdi:wifi";
      let cls = "";
      if (!Number.isNaN(s)) {
        if (s > -60) {
          icon = "mdi:wifi-strength-4";
          cls = "sig-4";
        } else if (s > -70) {
          icon = "mdi:wifi-strength-3";
          cls = "sig-3";
        } else if (s > -75) {
          icon = "mdi:wifi-strength-2";
          cls = "sig-2";
        } else {
          icon = "mdi:wifi-strength-1";
          cls = "sig-1";
        }
      }
      return html`<ha-icon class=${cls} icon=${icon}></ha-icon>`;
    }
    if (d.connection === "wired") {
      return html`<ha-icon icon="mdi:ethernet"></ha-icon>`;
    }
    return html`<ha-icon icon="mdi:help-circle-outline"></ha-icon>`;
  }

  _renderDetail(d) {
    const fields = this._config.columns
      .map((k) => this._renderField(k, d))
      .filter((f) => f !== nothing);

    if (fields.length === 0) {
      return html`<div class="detail">
        <div class="field"><div class="fvalue">No extra details configured</div></div>
      </div>`;
    }
    return html`<div class="detail">${fields}</div>`;
  }

  _renderField(key, d) {
    const value = this._fieldValue(key, d);
    if (value == null) return nothing;
    const label = COL_DISPLAY_NAME[key] ?? key;
    const monoClass = key === "mac" || key === "ip6" ? "mono" : "";
    return html`
      <div class="field">
        <div class="flabel">${label}</div>
        <div class="fvalue ${monoClass}">${value}</div>
      </div>
    `;
  }

  _fieldValue(key, d) {
    switch (key) {
      case "ip":
        return d.ip || d.ip6 || null;
      case "ip6":
        return d.ip6 || null;
      case "ip6_enabled":
        return d.ip6
          ? html`<ha-icon class="ic-green" icon="mdi:check-circle" title=${d.ip6}></ha-icon>`
          : html`<span class="ic-dim">No</span>`;
      case "hostname":
        return d.hostname || null;
      case "vendor":
        return d.vendor || "Unknown";
      case "mac":
        return d.mac || null;
      case "connection":
        if (d.connection === "wifi") return "Wi-Fi";
        if (d.connection === "wired") return "Wired";
        return null;
      case "ap":
        return d.ap || null;
      case "band":
        return d.band || null;
      case "tx_rate":
        return d.tx_rate != null ? `${d.tx_rate} Mbit/s` : null;
      case "signal":
        return d.signal != null ? `${d.signal} dBm` : null;
      case "rx_bps":
        return d.rx_bps != null ? `${fmtMbps(d.rx_bps)} Mbit/s` : null;
      case "tx_bps":
        return d.tx_bps != null ? `${fmtMbps(d.tx_bps)} Mbit/s` : null;
      case "noise":
        return d.noise != null ? `${d.noise} dBm` : null;
      case "snr":
        return d.snr != null ? `${d.snr} dB` : null;
      case "rx_rate":
        return d.rx_rate != null ? `${d.rx_rate} Mbit/s` : null;
      case "exp_tput":
        return d.exp_tput != null ? `${d.exp_tput} Mbit/s` : null;
      case "rx_total": {
        if (d.rx_total == null) return null;
        const title = d.bw_since
          ? `since ${new Date(d.bw_since * 1000).toLocaleDateString()}`
          : nothing;
        return html`<span title=${title}>${fmtBytes(d.rx_total)}</span>`;
      }
      case "tx_total": {
        if (d.tx_total == null) return null;
        const title = d.bw_since
          ? `since ${new Date(d.bw_since * 1000).toLocaleDateString()}`
          : nothing;
        return html`<span title=${title}>${fmtBytes(d.tx_total)}</span>`;
      }
      case "first_seen": {
        if (!d.first_seen) return null;
        const full = new Date(d.first_seen * 1000).toLocaleString();
        return html`<span title=${full}>${fmtAge(d.first_seen)}</span>`;
      }
      case "bw_since": {
        if (!d.bw_since) return null;
        const full = new Date(d.bw_since * 1000).toLocaleString();
        return html`<span title=${full}>${fmtAge(d.bw_since)}</span>`;
      }
      default:
        return null;
    }
  }

  _toggleExpand(mac) {
    const next = new Set(this._expanded);
    if (next.has(mac)) next.delete(mac);
    else next.add(mac);
    this._expanded = next;
  }

  _toggleSortDir = () => {
    this._sortDir = this._sortDir === "asc" ? "desc" : "asc";
  };

  _toggleExpandAll(devices) {
    const keys = devices.map((d) => d.mac || `${d.ip || ""}_${d.hostname || ""}`);
    const allExpanded = keys.every((k) => this._expanded.has(k));
    if (allExpanded) {
      this._expanded = new Set();
    } else {
      this._expanded = new Set(keys);
    }
  }

  _sortBadge(key, d) {
    switch (key) {
      case "hostname":
      case "ip":
      case "ip6":
      case "vendor":
        return null; // already visible as name or address
      case "ap":
        return d.ap || null;
      case "signal":
        return d.signal != null ? `${d.signal} dBm` : null;
      case "rx_bps":
        return d.rx_bps != null ? `↓ ${fmtMbps(d.rx_bps)} Mbit/s` : null;
      case "tx_bps":
        return d.tx_bps != null ? `↑ ${fmtMbps(d.tx_bps)} Mbit/s` : null;
      case "rx_total":
        return d.rx_total != null ? `↓ ${fmtBytes(d.rx_total)}` : null;
      case "tx_total":
        return d.tx_total != null ? `↑ ${fmtBytes(d.tx_total)}` : null;
      case "first_seen":
        return d.first_seen != null ? fmtAge(d.first_seen) : null;
      default:
        return null;
    }
  }

  _applyFilter(devices) {
    const q = this._filterText.trim().toLowerCase();
    if (!q) return devices;
    return devices.filter((d) => {
      const haystack = [d.hostname, d.ip, d.ip6, d.vendor, d.mac, d.ap, d.band]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }

  _sortValue(d, key) {
    switch (key) {
      case "hostname":
        return (d.hostname || "zzz").toLowerCase();
      case "ip":
        return ipSortKey(d.ip || d.ip6 || "");
      case "vendor":
        return (d.vendor || "zzz").toLowerCase();
      case "ap":
        return (d.ap || "zzz").toLowerCase();
      case "signal":
        return d.signal ?? -Infinity;
      case "rx_bps":
        return d.rx_bps ?? -1;
      case "tx_bps":
        return d.tx_bps ?? -1;
      case "rx_total":
        return d.rx_total ?? -1;
      case "tx_total":
        return d.tx_total ?? -1;
      case "first_seen":
        return d.first_seen ?? Infinity;
      default:
        return "";
    }
  }

  _sortDevices(devices) {
    const key = this._sortKey;
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

  getCardSize() {
    return 4;
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }

  static getStubConfig() {
    return { entity: "sensor.network_scanner", title: "Network" };
  }
}

// ── editor ────────────────────────────────────────────────────────────────────

class NetworkListCardEditor extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
  };

  setConfig(config) {
    this._config = config;
  }

  _schema() {
    return [
      { name: "entity", required: true, selector: { entity: { domain: "sensor" } } },
      { name: "title", selector: { text: {} } },
      { name: "show_offline", selector: { boolean: {} } },
      {
        name: "max_height",
        selector: {
          number: { min: 0, max: 2000, step: 20, mode: "box", unit_of_measurement: "px" },
        },
      },
      {
        name: "columns",
        selector: {
          select: {
            multiple: true,
            mode: "list",
            options: ALL_COL_KEYS.map((k) => ({
              value: k,
              label: COL_DISPLAY_NAME[k] ?? k,
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
      max_height: "Max list height before scrolling (0 = fill available space)",
      columns: "Detail fields (shown when a device row is expanded)",
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
      show_offline: this._config.show_offline ?? false,
      max_height: this._config.max_height ?? 560,
      columns: Array.isArray(this._config.columns) ? this._config.columns : DEFAULT_COLS,
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

customElements.define(EDITOR_TYPE, NetworkListCardEditor);
customElements.define(CARD_TYPE, NetworkListCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TYPE,
  name: "Network List",
  description: "Mobile-first device list from network_scanner sensor (tap rows to expand)",
  preview: false,
});

console.info(
  `%c NETWORK-LIST-CARD %c v${CARD_VERSION} `,
  "color: white; background: #009ac7; font-weight: 700;",
  "color: #009ac7; background: white; font-weight: 700;",
);
