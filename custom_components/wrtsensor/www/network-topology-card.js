// Icons — 20×20 viewBox, uses currentColor
const ICON_ROUTER = `
  <rect x="2" y="8" width="16" height="8" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <line x1="6" y1="8" x2="5" y2="3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  <line x1="14" y1="8" x2="15" y2="3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  <circle cx="6" cy="13" r="1" fill="currentColor"/>
  <circle cx="10" cy="13" r="1" fill="currentColor"/>
  <circle cx="14" cy="13" r="1" fill="currentColor"/>`;

const ICON_WIFI = `
  <circle cx="10" cy="16" r="1.5" fill="currentColor"/>
  <path d="M6.5 12.5a5 5 0 0 1 7 0" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  <path d="M3.5 9a9 9 0 0 1 13 0" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`;

const ICON_PHONE = `
  <rect x="6" y="2" width="8" height="15" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <circle cx="10" cy="14.5" r="0.9" fill="currentColor"/>
  <line x1="8" y1="4.5" x2="12" y2="4.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>`;

const ICON_LAPTOP = `
  <rect x="3" y="4" width="14" height="9" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <path d="M1 17 h18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  <line x1="4" y1="17" x2="3" y2="13" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>
  <line x1="16" y1="17" x2="17" y2="13" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>`;

const ICON_TV = `
  <rect x="2" y="3" width="16" height="11" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <line x1="8" y1="14" x2="7" y2="18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  <line x1="12" y1="14" x2="13" y2="18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  <line x1="6" y1="18" x2="14" y2="18" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`;

const ICON_SERVER = `
  <rect x="3" y="2" width="14" height="5" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <rect x="3" y="9" width="14" height="5" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <circle cx="15" cy="4.5" r="1" fill="currentColor"/>
  <circle cx="15" cy="11.5" r="1" fill="currentColor"/>`;

const ICON_PRINTER = `
  <rect x="4" y="7" width="12" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <rect x="6" y="3" width="8" height="4" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <rect x="6" y="11" width="8" height="3.5" rx="0.5" fill="none" stroke="currentColor" stroke-width="1"/>
  <circle cx="14" cy="10.5" r="0.8" fill="currentColor"/>`;

const ICON_DESKTOP = `
  <rect x="3" y="3" width="14" height="10" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <line x1="10" y1="13" x2="10" y2="17" stroke="currentColor" stroke-width="1.5"/>
  <line x1="7" y1="17" x2="13" y2="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`;

const ICON_TABLET = `
  <rect x="4" y="2" width="12" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/>
  <circle cx="10" cy="15.5" r="0.9" fill="currentColor"/>`;

const ICON_VPN = `
  <path d="M10 2 L17 5 V11 C17 14.5 14 17.5 10 18 C6 17.5 3 14.5 3 11 V5 Z"
        fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
  <circle cx="10" cy="10" r="2" fill="currentColor"/>`;

// Hostname-based patterns — checked against "hostname vendor" combined hint
const HOSTNAME_PATTERNS = [
  [/phone|mobile|iphone|android|pixel|galaxy|oneplus|xiaomi|huawei|cmf|sm-|redmi/, ICON_PHONE],
  [/laptop|notebook|macbook|thinkpad|xps|zenbook|surface/, ICON_LAPTOP],
  [
    /\btv\b|television|chromecast|firetv|fire.tv|appletv|apple.tv|shield|roku|bravia|tizen|vizio|hisense|lgtv|androidtv/,
    ICON_TV,
  ],
  [/printer|epson|canon|brother|laserjet|officejet/, ICON_PRINTER],
  [/nas|synology|qnap|server|\brpi\b|raspberry|pi-|plex|unraid/, ICON_SERVER],
  [/tablet|ipad/, ICON_TABLET],
];

// Vendor (OUI) patterns — checked against vendor string only
const VENDOR_PATTERNS = [
  [/raspberry pi/, ICON_SERVER],
  [/synology|qnap/, ICON_SERVER],
  [/brother|seiko epson|canon|hp inc|hewlett/, ICON_PRINTER],
  [/intel corp/, ICON_DESKTOP],
  [/samsung electronics/, ICON_PHONE],
  [/google|nest labs/, ICON_TV],
];

function _deviceIcon(d) {
  const hint = `${d.hostname ?? ""} ${d.vendor ?? ""}`.toLowerCase();
  const vendor = (d.vendor ?? "").toLowerCase();

  for (const [re, icon] of HOSTNAME_PATTERNS) {
    if (re.test(hint)) return icon;
  }
  for (const [re, icon] of VENDOR_PATTERNS) {
    if (re.test(vendor)) return icon;
  }
  if (d.connection === "wifi" || d.ap) return ICON_PHONE;
  return ICON_DESKTOP;
}

function _esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _fmtAge(ts) {
  if (ts == null) return "—";
  const secs = Math.floor(Date.now() / 1000) - ts;
  if (secs < 120) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  if (secs < 86400 * 2) return "1 day";
  return `${Math.floor(secs / 86400)} days`;
}

function _fmtMbps(bps) {
  if (bps == null) return null;
  const m = (bps * 8) / 1e6;
  return m >= 10 ? m.toFixed(0) : m >= 1 ? m.toFixed(1) : m.toFixed(2);
}

function _fmtBytes(bytes) {
  if (bytes == null) return null;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

function _truncate(str, n) {
  if (!str) return "";
  if (str.length <= n) return str;
  if (n <= 1) return "…";

  const cut = str.slice(0, n - 1);
  const boundary = Math.max(cut.lastIndexOf("-"), cut.lastIndexOf("_"), cut.lastIndexOf("."));
  return `${boundary > n * 0.6 ? cut.slice(0, boundary) : cut}…`;
}

function findWireguardSensor(hass, entityId, override) {
  const empty = { state: null, entityId: null, configured: false, available: false };
  if (!hass) return empty;
  const wrap = (eid) => {
    const state = hass.states?.[eid] ?? null;
    return {
      state,
      entityId: eid,
      configured: true,
      available: !!state?.attributes?.wireguard?.available,
    };
  };

  if (override) return wrap(override);

  const entities = hass.entities ?? {};
  const deviceId = entities[entityId]?.device_id;
  if (deviceId) {
    for (const [eid, reg] of Object.entries(entities)) {
      if (reg.device_id !== deviceId) continue;
      const uniq = (reg.unique_id ?? "").toLowerCase();
      if (eid.endsWith("_wireguard") || uniq.endsWith("_wireguard")) return wrap(eid);
    }
  }

  const fallback = Object.keys(hass.states ?? {}).filter(
    (eid) => eid.startsWith("sensor.") && eid.endsWith("_wireguard"),
  );
  return fallback.length === 1 ? wrap(fallback[0]) : empty;
}

function _endpointHost(endpoint) {
  const value = String(endpoint ?? "");
  if (!value) return "";
  if (value.startsWith("[")) return value.slice(1, value.indexOf("]"));
  const colon = value.lastIndexOf(":");
  if (colon > -1 && value.indexOf(":") === colon) return value.slice(0, colon);
  return value;
}

// SVG embedded stylesheet — inherits HA CSS custom properties through shadow DOM
const SVG_STYLE = `
  <defs><style>
    .ntc-bg       { fill: var(--card-background-color, #1c1c1c); }
    .ntc-inet     { fill: var(--primary-color, #009ac7); }
    .ntc-gw       { fill: var(--teal-color, #009688); }
    .ntc-ap       { fill: var(--indigo-color, #3f51b5); }
    .ntc-wire     { fill: var(--blue-grey-color, #607d8b); }
    .ntc-wg-peer  { fill: #88171a; }
    .ntc-wg-icon  { color: #fff; }
    .ntc-icon     { color: #fff; }
    .ntc-label    { fill: var(--primary-text-color, #e1e1e1); font-family: var(--ha-font-family-body, Roboto, sans-serif); }
    .ntc-sub      { fill: var(--secondary-text-color, #9b9b9b); font-family: var(--ha-font-family-body, Roboto, sans-serif); }
    .ntc-wan      { fill: var(--secondary-text-color, #9b9b9b); font-family: var(--ha-font-family-body, Roboto, sans-serif); }
    .ntc-link      { fill: none; stroke: rgba(var(--rgb-primary-text-color, 225,225,225), 0.18); stroke-width: 1.5; }
    .ntc-link-wifi { fill: none; stroke: rgba(var(--rgb-primary-text-color, 225,225,225), 0.18); stroke-width: 1.5; stroke-dasharray: 5 3; stroke-opacity: 0.65; }
    .ntc-link-inet { fill: none; stroke: rgba(var(--rgb-primary-text-color, 225,225,225), 0.18); stroke-width: 1.5; stroke-opacity: 0.6; }
    .ntc-link-wg    { fill: none; stroke: rgba(var(--rgb-primary-text-color, 225,225,225), 0.18); stroke-width: 1.4; stroke-dasharray: 4 3; stroke-opacity: 0.75; }
    .ntc-link-wg-off{ stroke: var(--secondary-text-color, #888); stroke-opacity: 0.4; }
    .ntc-warn      { fill: var(--warning-color, #ffa600); font-family: var(--ha-font-family-body, Roboto, sans-serif); }
    .ntc-unknown   { opacity: 0.6; font-style: italic; }
  </style></defs>`;

class NetworkTopologyCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._lastUpdated = null;
    this._iconCache = {};
    this._overlay = null;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity required");
    this._config = {
      entity: config.entity,
      title: config.title ?? "Network Map",
      gateway_label: config.gateway_label ?? config.gateway_hostname ?? "gw",
      column_width: config.column_width ?? config.col_width ?? 200,
      show_offline: config.show_offline ?? false,
      show_wireguard_peers: config.show_wireguard_peers ?? false,
      show_offline_wireguard: config.show_offline_wireguard ?? true,
      wireguard_entity: config.wireguard_entity ?? null,
    };
    // Fix: reset so a config change always triggers a re-render
    this._lastUpdated = null;
  }

  set hass(hass) {
    this._hass = hass;
    const state = hass.states[this._config.entity];
    if (!state) {
      this._renderUnavailable(`Entity not found: ${this._config.entity}`);
      return;
    }
    if (["unavailable", "unknown"].includes(state.state)) {
      this._renderUnavailable(`${this._config.entity} is ${state.state}`);
      return;
    }
    const wg = findWireguardSensor(hass, this._config.entity, this._config.wireguard_entity);
    const wgState = wg.available ? wg.state : null;
    const cacheKey = [
      this._config.entity,
      state.last_updated,
      this._config.wireguard_entity ?? "",
      wg.entityId ?? "",
      wgState?.last_updated ?? "",
      this._config.show_wireguard_peers ? 1 : 0,
      this._config.show_offline_wireguard ? 1 : 0,
    ].join("|");
    if (cacheKey === this._lastUpdated) return;
    this._lastUpdated = cacheKey;
    try {
      this._render(state, wgState);
    } catch (e) {
      this._renderError(String(e));
    }
  }

  _renderUnavailable(message) {
    this.shadowRoot.innerHTML = `
      <style>:host { display: block; }</style>
      <ha-card>
        <div style="padding:16px;color:var(--secondary-text-color);font-size:0.9em">${_esc(message)}</div>
      </ha-card>`;
  }

  _renderError(message) {
    this.shadowRoot.innerHTML = `
      <style>:host { display: block; }</style>
      <ha-card>
        <div style="padding:16px;color:var(--error-color,#db4437)">
          <b>Network Topology Card error</b>
          <div style="margin-top:6px;font-size:0.85em;color:var(--secondary-text-color)">${_esc(message)}</div>
        </div>
      </ha-card>`;
  }

  _signalColor(sig) {
    const s = Number(sig);
    if (Number.isNaN(s)) return "#888";
    if (s > -60) return "#4caf50";
    if (s > -70) return "#ff9800";
    if (s > -75) return "#ff5722";
    return "#f44336";
  }

  _render(state, wgState) {
    const attr = state.attributes ?? {};
    const allDevices = attr.devices ?? [];
    const wanIp = attr.wan_ip ?? "";
    const wanIp6 = attr.wan_ip6 ?? "";
    const gatewayMac = String(attr.gateway_mac ?? "").toLowerCase();
    const partial = attr.partial ?? false;
    const gatewayLabel = this._config.gateway_label;

    const devices = this._config.show_offline
      ? allDevices
      : allDevices.filter((d) => d.online !== false);
    const wgEnabled = this._config.show_wireguard_peers && wgState != null;
    const wgIfaces = wgEnabled ? (wgState.attributes?.wireguard?.interfaces ?? []) : [];
    const allWgPeers = wgIfaces.flatMap((iface) =>
      (iface.peers ?? []).map((peer) => ({ ...peer, _iface: iface.name, _host: iface.host })),
    );
    const wgPeers = this._config.show_offline_wireguard
      ? allWgPeers
      : allWgPeers.filter((peer) => peer.online === true);

    const apHostnames = new Set();
    for (const d of allDevices) {
      if (d.ap) apHostnames.add(d.ap);
    }

    // Prefer the authoritative gateway_mac from the scanner; fall back to
    // connection === "gateway" so partial scans (gateway_mac === "") and
    // stale sensor data still render a gateway node with real device info.
    const gateway = allDevices.find((d) => {
      if (gatewayMac && String(d.mac ?? "").toLowerCase() === gatewayMac) return true;
      return d.connection === "gateway";
    });
    const aps = allDevices.filter(
      (d) => apHostnames.has(d.hostname) || apHostnames.has(d.mac?.toLowerCase()),
    );
    const wifiDevices = devices.filter(
      (d) => (d.connection === "wifi" || d.ap) && !aps.includes(d),
    );
    const wiredDevices = devices.filter(
      (d) => d !== gateway && !aps.includes(d) && d.connection !== "wifi" && !d.ap,
    );

    const byAp = {};
    for (const ap of aps) {
      byAp[ap.hostname] = [];
    }
    const unknownApDevices = [];
    for (const d of wifiDevices) {
      if (d.ap && byAp[d.ap] !== undefined) byAp[d.ap].push(d);
      else unknownApDevices.push(d);
    }

    const NODE_R = 20,
      GW_R = 26,
      AP_R = 22,
      ROW_H = 52,
      WG_R = AP_R,
      WG_ROW_GAP = 110,
      WG_ROW_H = 56,
      WG_CELL_W = 190;
    const COL_PAD = 40,
      COL_WIDTH = this._config.column_width;
    const MAX_COL = 8;

    const sortDevs = (arr) =>
      [...arr].sort((a, b) => {
        const na = (a.hostname || "").toLowerCase();
        const nb = (b.hostname || "").toLowerCase();
        if (!na && nb) return 1;
        if (na && !nb) return -1;
        return na.localeCompare(nb);
      });

    const columns = [];
    const sortedWired = sortDevs(wiredDevices);
    for (let i = 0; i < sortedWired.length; i += MAX_COL)
      columns.push({ devices: sortedWired.slice(i, i + MAX_COL), ap: null });
    for (const ap of aps) columns.push({ devices: sortDevs(byAp[ap.hostname]), ap });
    if (unknownApDevices.length > 0)
      columns.push({ devices: sortDevs(unknownApDevices), ap: null });

    // Dynamic width — each column gets a fixed minimum, no shrinking on mobile
    const W = Math.max(columns.length * COL_WIDTH + COL_PAD * 2, 600);

    const wgPerRow = Math.max(1, Math.floor((W - 2 * COL_PAD) / WG_CELL_W));
    const wgCount = wgEnabled ? wgPeers.length : 0;
    const wgRows = wgCount ? Math.ceil(wgCount / wgPerRow) : 0;
    const wgBlockH = wgCount ? (wgRows - 1) * WG_ROW_H + WG_ROW_GAP : 0;

    const topMargin = 80 + wgBlockH,
      gwY = topMargin + 90,
      apRowY = gwY + 110,
      devStartY = apRowY + 90;
    const maxDevs = Math.max(...columns.map((c) => c.devices.length), 0);
    const totalH = devStartY + Math.max(maxDevs * ROW_H + 20, 60) + 40;

    const nCols = Math.max(columns.length, 1);
    const colW = (W - 2 * COL_PAD) / nCols;
    const colCenters = columns.map((_, i) => COL_PAD + colW * i + colW / 2);
    const inetX = W / 2;
    const gwX = inetX,
      inetY = topMargin;

    const nodeTitle = (d) => {
      const parts = [];
      if (d.hostname) parts.push(d.hostname);
      if (d.ip) parts.push(`IPv4: ${d.ip}`);
      if (d.ip6) parts.push(`IPv6: ${d.ip6}`);
      if (d.vendor) parts.push(d.vendor);
      if (d.mac) parts.push(d.mac);
      if (d.signal != null) parts.push(`Signal: ${d.signal} dBm`);
      if (d.noise != null) parts.push(`Noise: ${d.noise} dBm`);
      if (d.snr != null) parts.push(`SNR: ${d.snr} dB`);
      if (d.tx_rate != null) parts.push(`TX rate: ${d.tx_rate} Mbit/s`);
      if (d.rx_rate != null) parts.push(`RX rate: ${d.rx_rate} Mbit/s`);
      if (d.exp_tput != null) parts.push(`Expected: ${d.exp_tput} Mbit/s`);
      if (d.band) parts.push(`Band: ${d.band}`);
      const rx = _fmtMbps(d.rx_bps),
        tx = _fmtMbps(d.tx_bps);
      if (rx != null || tx != null) parts.push(`↓ ${rx ?? "—"}  ↑ ${tx ?? "—"} Mbit/s`);
      const rxTot = _fmtBytes(d.rx_total),
        txTot = _fmtBytes(d.tx_total);
      if (rxTot != null || txTot != null) parts.push(`Total: ↓ ${rxTot ?? "—"}  ↑ ${txTot ?? "—"}`);
      if (d.first_seen) {
        const age = _fmtAge(d.first_seen);
        const date = new Date(d.first_seen * 1000).toLocaleDateString();
        parts.push(`Discovered: ${age} ago (${date})`);
      }
      if (d.bw_since) {
        const age = _fmtAge(d.bw_since);
        const date = new Date(d.bw_since * 1000).toLocaleDateString();
        parts.push(`BW tracking: ${age} (since ${date})`);
      }
      return parts.join("\n");
    };

    const wgTitle = (p) => {
      const parts = [];
      if (p.name) parts.push(p.name);
      if (p._host) parts.push(`Host: ${p._host}${p._iface ? ` (${p._iface})` : ""}`);
      if (p.public_key) parts.push(`Pubkey: ${p.public_key.slice(0, 16)}…`);
      if (p.endpoint) parts.push(`Endpoint: ${p.endpoint}`);
      if (p.allowed_ips?.length) parts.push(`Allowed IPs: ${p.allowed_ips.join(", ")}`);
      if (p.last_handshake) parts.push(`Handshake: ${_fmtAge(p.last_handshake)}`);
      const rxT = _fmtBytes(p.rx_bytes),
        txT = _fmtBytes(p.tx_bytes);
      if (rxT != null || txT != null) parts.push(`Total: ↓ ${rxT ?? "—"}  ↑ ${txT ?? "—"}`);
      if (p.persistent_keepalive_s) parts.push(`Keepalive: ${p.persistent_keepalive_s}s`);
      parts.push(`Status: ${p.online === true ? "online" : "offline"}`);
      return parts.join("\n");
    };

    const svgNode = (
      x,
      y,
      r,
      icon,
      label,
      sublabel,
      nodeClass,
      opacity,
      title,
      signalColor = null,
      unknown = false,
    ) => {
      const circleFill = signalColor ? `style="fill:${signalColor}"` : `class="${nodeClass}"`;
      const labelClass = unknown ? "ntc-unknown ntc-label" : "ntc-label";
      const subClass = unknown ? "ntc-unknown ntc-sub" : "ntc-sub";
      return `
      <g opacity="${opacity}">
        <title>${_esc(title)}</title>
        <circle cx="${x}" cy="${y}" r="${r}" ${circleFill} stroke="var(--card-background-color, #1c1c1c)" stroke-width="2"/>
        <svg x="${x - 10}" y="${y - 10}" width="20" height="20" viewBox="0 0 20 20" class="ntc-icon" pointer-events="none">${icon}</svg>
        <text x="${x + r + 6}" y="${y + 4}" class="${labelClass}" font-size="11" text-anchor="start">${_esc(_truncate(label, 20))}</text>
        ${sublabel ? `<text x="${x + r + 6}" y="${y + 16}" class="${subClass}" font-size="9" text-anchor="start">${_esc(_truncate(sublabel, 22))}</text>` : ""}
      </g>`;
    };

    const svgWgPeerNode = (x, y, r, label, sublabel, opacity, title) => `
      <g opacity="${opacity}">
        <title>${_esc(title)}</title>
        <circle cx="${x}" cy="${y}" r="${r}" class="ntc-wg-peer" stroke="var(--card-background-color, #1c1c1c)" stroke-width="2"/>
        <svg x="${x - 10}" y="${y - 10}" width="20" height="20" viewBox="0 0 20 20" class="ntc-icon ntc-wg-icon" pointer-events="none">${ICON_VPN}</svg>
        <text x="${x + r + 6}" y="${y + 4}" class="ntc-label" font-size="11" text-anchor="start">${_esc(_truncate(label, 20))}</text>
        ${sublabel ? `<text x="${x + r + 6}" y="${y + 16}" class="ntc-sub" font-size="9" text-anchor="start">${_esc(sublabel)}</text>` : ""}
      </g>`;

    const curve = (x1, y1, x2, y2, cls = "ntc-link") =>
      `<path class="${cls}" d="M${x1},${y1} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}"/>`;

    const line = (x1, y1, x2, y2, cls = "ntc-link", signalColor = null) => {
      const extra = signalColor ? `style="stroke:${signalColor}"` : "";
      return `<line class="${cls}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" ${extra}/>`;
    };

    const paths = [],
      nodes = [];

    const inetTooltip =
      [wanIp ? `IPv4: ${wanIp}` : "", wanIp6 ? `IPv6: ${wanIp6}` : ""].filter(Boolean).join("\n") ||
      "Internet";
    nodes.push(`
      <g>
        <title>${_esc(inetTooltip)}</title>
        <ellipse cx="${inetX}" cy="${inetY}" rx="44" ry="22" class="ntc-inet" opacity="0.9"/>
        <text x="${inetX}" y="${inetY + 5}" text-anchor="middle" font-size="11" fill="#fff"
              font-family="var(--ha-font-family-body, Roboto, sans-serif)" font-weight="500">Internet</text>
        ${wanIp ? `<text x="${inetX + 50}" y="${inetY - 2}" text-anchor="start" font-size="9" class="ntc-wan">${_esc(wanIp)}</text>` : ""}
        ${wanIp6 ? `<text x="${inetX + 50}" y="${inetY + 10}" text-anchor="start" font-size="8" class="ntc-wan" opacity="0.75">${_esc(wanIp6)}</text>` : ""}
      </g>`);

    if (wgEnabled && wgPeers.length) {
      const inetTopY = inetY - 22;
      for (let i = 0; i < wgPeers.length; i++) {
        const peer = wgPeers[i];
        const row = Math.floor(i / wgPerRow);
        const inRow = i % wgPerRow;
        const peersInRow = Math.min(wgPerRow, wgPeers.length - row * wgPerRow);
        const px = inetX + (inRow - (peersInRow - 1) / 2) * WG_CELL_W;
        const py = inetY - WG_ROW_GAP - (wgRows - 1 - row) * WG_ROW_H;
        const online = peer.online === true;
        const opacity = online ? "1" : "0.45";
        const linkClass = online ? "ntc-link-wg" : "ntc-link-wg ntc-link-wg-off";
        const label =
          peer.name || (peer.public_key ? peer.public_key.slice(0, 8) : `peer-${i + 1}`);

        paths.push(curve(px, py + WG_R, inetX, inetTopY, linkClass));
        nodes.push(
          svgWgPeerNode(px, py, WG_R, label, _endpointHost(peer.endpoint), opacity, wgTitle(peer)),
        );
      }
    }

    paths.push(curve(inetX, inetY + 22, gwX, gwY - GW_R, "ntc-link-inet"));

    const gwOp = gateway ? (gateway.online !== false ? "1" : "0.4") : "1";
    nodes.push(
      svgNode(
        gwX,
        gwY,
        GW_R,
        ICON_ROUTER,
        gateway?.hostname ?? gatewayLabel,
        gateway?.ip ?? "",
        "ntc-gw",
        gwOp,
        gateway ? nodeTitle(gateway) : gatewayLabel,
      ),
    );

    if (partial) {
      nodes.push(
        `<text x="${W - 10}" y="18" text-anchor="end" font-size="11" class="ntc-warn">⚠ partial data</text>`,
      );
    }

    for (let ci = 0; ci < columns.length; ci++) {
      const col = columns[ci];
      const cx = colCenters[ci];
      const colTopY = col.ap ? apRowY : devStartY;
      paths.push(curve(gwX, gwY + GW_R, cx, colTopY - (col.ap ? AP_R : NODE_R)));

      if (col.ap) {
        const ap = col.ap;
        const apOp = ap.online !== false ? "1" : "0.4";
        nodes.push(
          svgNode(
            cx,
            apRowY,
            AP_R,
            ICON_WIFI,
            ap.hostname,
            ap.ip ?? "",
            "ntc-ap",
            apOp,
            nodeTitle(ap),
          ),
        );

        for (let di = 0; di < col.devices.length; di++) {
          const d = col.devices[di];
          const dy = devStartY + di * ROW_H;
          const sc = this._signalColor(d.signal);
          paths.push(line(cx, apRowY + AP_R, cx, dy - NODE_R, "ntc-link-wifi"));
          const devSub = d.ip ?? "";
          const unknown = !d.hostname && !d.vendor;
          if (!this._iconCache[d.mac]) this._iconCache[d.mac] = _deviceIcon(d);
          const icon = this._iconCache[d.mac];
          const devOp = d.online !== false ? "1" : "0.4";
          nodes.push(
            svgNode(
              cx,
              dy,
              NODE_R,
              icon,
              d.hostname || d.mac,
              devSub,
              null,
              devOp,
              nodeTitle(d),
              sc,
              unknown,
            ),
          );
        }
      } else {
        for (let di = 0; di < col.devices.length; di++) {
          const d = col.devices[di];
          const dy = devStartY + di * ROW_H;
          const prevY = di === 0 ? gwY + GW_R : devStartY + (di - 1) * ROW_H + NODE_R;
          const prevX = di === 0 ? gwX : cx;
          if (di === 0) paths.push(curve(prevX, prevY, cx, dy - NODE_R));
          else paths.push(line(prevX, prevY, cx, dy - NODE_R));
          if (!this._iconCache[d.mac]) this._iconCache[d.mac] = _deviceIcon(d);
          const icon = this._iconCache[d.mac];
          const unknown = !d.hostname && !d.vendor;
          const wiredSub = d.ip ?? "";
          const wireOp = d.online !== false ? "1" : "0.4";
          nodes.push(
            svgNode(
              cx,
              dy,
              NODE_R,
              icon,
              d.hostname || d.mac,
              wiredSub,
              "ntc-wire",
              wireOp,
              nodeTitle(d),
              null,
              unknown,
            ),
          );
        }
      }
    }

    const svg = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${totalH}"
           style="width:100%;display:block;border-radius:var(--ha-card-border-radius,12px)">
        ${SVG_STYLE}
        <rect width="${W}" height="${totalH}" class="ntc-bg" rx="12"/>
        ${paths.join("\n")}
        ${nodes.join("\n")}
      </svg>`;

    const svgWrapper = svg;

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .card-header {
          font-family: var(--ha-card-header-font-family, var(--primary-font-family, inherit));
          font-size: var(--ha-card-header-font-size, 24px);
          font-weight: var(--ha-card-header-font-weight, normal);
          color: var(--ha-card-header-color, var(--primary-text-color));
          letter-spacing: -0.012em;
          line-height: 48px;
          padding: 12px 16px 16px;
          display: block;
        }
        .body { padding: 8px; cursor: zoom-in; user-select: none; touch-action: manipulation; }
        .updated {
          padding: 2px 16px 12px;
          font-size: var(--ha-font-size-xs, 10px);
          color: var(--secondary-text-color);
        }
      </style>
      <ha-card>
        <div class="card-header">${_esc(this._config.title)}</div>
        <div class="body" tabindex="0" role="button" aria-label="${_esc(this._config.title)}">${svgWrapper}</div>
        <div class="updated">${(() => {
          const onlineCount = allDevices.filter((d) => d.online !== false).length;
          const offlineCount = allDevices.filter((d) => d.online === false).length;
          const updated = new Date(state.last_updated).toLocaleTimeString();
          const wgText = wgEnabled
            ? ` · WG ${allWgPeers.filter((p) => p.online === true).length}/${allWgPeers.length} online`
            : "";
          return `Updated: ${updated} · ${onlineCount} online${offlineCount ? ` · ${offlineCount} offline` : ""}${wgText}`;
        })()}</div>
      </ha-card>`;

    const body = this.shadowRoot.querySelector(".body");
    const openDialog = () => this._openDialog(svg, this._config.title, state.last_updated);
    body.addEventListener("click", openDialog);
    body.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openDialog();
      }
    });
  }

  _openDialog(svg, title, lastUpdated) {
    if (this._overlay) this._overlay.remove();
    const focusAfterClose = this.shadowRoot.querySelector(".body");

    const overlay = document.createElement("div");
    this._overlay = overlay;
    overlay.className = "ntc-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", title);
    overlay.style.cssText = `
      position:fixed;inset:0;z-index:9999;
      background:rgba(0,0,0,0.75);
      display:flex;flex-direction:column;align-items:center;justify-content:center;
      cursor:zoom-out;animation:ntc-fade 0.18s ease;`;

    const style = document.createElement("style");
    style.textContent = `
      @keyframes ntc-fade { from{opacity:0} to{opacity:1} }
      .ntc-box {
        background: var(--card-background-color, #fff);
        color: var(--primary-text-color);
        border-radius: var(--ha-card-border-radius, 12px);
        box-shadow: var(--ha-card-box-shadow, 0 8px 32px rgba(0,0,0,0.4));
        padding: 16px;
        width: min(96vw, 1400px);
        max-height: 92vh;
        display: flex;
        flex-direction: column;
        cursor: default;
        user-select: none;
      }
      .ntc-box-header {
        display:flex;justify-content:space-between;align-items:center;
        margin-bottom:10px;flex-shrink:0;
      }
      .ntc-box-title {
        font-family: var(--ha-font-family-body, Roboto, sans-serif);
        font-size: var(--ha-font-size-m, 14px);
        font-weight: var(--ha-font-weight-medium, 500);
        color: var(--primary-text-color);
      }
      .ntc-box-meta {
        font-family: var(--ha-font-family-body, Roboto, sans-serif);
        font-size: var(--ha-font-size-xs, 10px);
        color: var(--secondary-text-color);
      }
      .ntc-close {
        background:none;border:none;
        color: var(--secondary-text-color);
        font-size:22px;cursor:pointer;line-height:1;padding:4px 8px;
      }
      .ntc-close:hover { color: var(--primary-text-color); }
      .ntc-svg-wrap {
        flex:1;overflow:hidden;touch-action:none;position:relative;
        border-radius:8px;
      }
      .ntc-svg-wrap svg { width:100%;display:block; }
      .ntc-hint {
        flex-shrink:0;
        font-size:10px;color:var(--secondary-text-color);
        text-align:center;padding-top:6px;
      }
      .ntc-info {
        display:none;
        position:fixed;
        bottom:24px;
        left:50%;
        transform:translateX(-50%);
        background:var(--card-background-color,#1c1c1c);
        color:var(--primary-text-color);
        border:1px solid var(--divider-color,rgba(255,255,255,0.12));
        border-radius:12px;
        padding:10px 14px;
        max-width:min(88vw,320px);
        z-index:10000;
        font-size:12px;
        line-height:1.6;
        white-space:pre-wrap;
        pointer-events:none;
        box-shadow:0 4px 20px rgba(0,0,0,0.5);
      }`;
    overlay.appendChild(style);

    const infoPanel = document.createElement("div");
    infoPanel.className = "ntc-info";
    overlay.appendChild(infoPanel);

    const box = document.createElement("div");
    box.className = "ntc-box";
    box.innerHTML = `
      <div class="ntc-box-header">
        <span class="ntc-box-title">${_esc(title)}</span>
        <span class="ntc-box-meta">Updated: ${new Date(lastUpdated).toLocaleTimeString()}</span>
        <button class="ntc-close" title="Close" aria-label="Close network map">✕</button>
      </div>
      <div class="ntc-svg-wrap">${svg}</div>
      <div class="ntc-hint">Scroll or pinch to zoom · drag to pan · double-tap to reset</div>`;

    let infoTimeout;
    const showInfo = (text) => {
      clearTimeout(infoTimeout);
      infoPanel.textContent = text;
      infoPanel.style.display = "block";
      infoTimeout = setTimeout(() => {
        infoPanel.style.display = "none";
      }, 4000);
    };
    const hideInfo = () => {
      clearTimeout(infoTimeout);
      infoPanel.style.display = "none";
    };

    const mouseMove = (e) => {
      if (!mouseDragging) return;
      const rect = wrap?.getBoundingClientRect();
      if (!rect) return;
      const dx = ((e.clientX - mousePanStartX) / rect.width) * vw;
      const dy = ((e.clientY - mousePanStartY) / rect.height) * vh;
      if (Math.abs(e.clientX - mousePanStartX) > 3 || Math.abs(e.clientY - mousePanStartY) > 3)
        mouseMoved = true;
      vx = mousePanStartVx - dx;
      vy = mousePanStartVy - dy;
      clamp();
      setVB();
    };
    const mouseUp = () => {
      mouseDragging = false;
      if (wrap) wrap.style.cursor = "grab";
    };

    const closeOverlay = () => {
      clearTimeout(infoTimeout);
      window.removeEventListener("mousemove", mouseMove);
      window.removeEventListener("mouseup", mouseUp);
      overlay.remove();
      if (this._overlay === overlay) this._overlay = null;
      document.removeEventListener("keydown", onKey);
      focusAfterClose?.focus();
    };
    const getFocusable = () =>
      Array.from(
        box.querySelectorAll(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => !el.hasAttribute("disabled"));
    const onKey = (e) => {
      if (e.key === "Escape") {
        closeOverlay();
        return;
      }
      if (e.key === "Tab") {
        const focusable = getFocusable();
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = this.shadowRoot?.activeElement ?? document.activeElement;
        if (e.shiftKey && active === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    box.querySelector(".ntc-close").addEventListener("click", closeOverlay);
    overlay.addEventListener("click", closeOverlay);
    box.addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("keydown", onKey);

    overlay.appendChild(box);
    document.body.appendChild(overlay);
    box.querySelector(".ntc-close")?.focus();

    // Stamp node info onto <g> elements for tap delegation
    box.querySelectorAll("svg g").forEach((g) => {
      const titleEl = g.querySelector(":scope > title");
      if (titleEl?.textContent.trim()) {
        g.dataset.ntcInfo = titleEl.textContent.trim();
        g.style.cursor = "pointer";
      }
    });

    // ── Zoom / pan ────────────────────────────────────────────────────────────
    const wrap = box.querySelector(".ntc-svg-wrap");
    const svgEl = wrap.querySelector("svg");
    const origVB = svgEl.getAttribute("viewBox").split(" ").map(Number); // [x,y,w,h]
    let [vx, vy, vw, vh] = origVB;

    const setVB = () => svgEl.setAttribute("viewBox", `${vx} ${vy} ${vw} ${vh}`);

    // Mouse drag state — declared here so mouseMove/mouseUp/closeOverlay can all share them
    let mouseDragging = false,
      mouseMoved = false;
    let mousePanStartVx = vx,
      mousePanStartVy = vy;
    let mousePanStartX = 0,
      mousePanStartY = 0;

    // Clamp viewBox so you can't pan completely off the diagram
    const clamp = () => {
      const [ox, oy, ow, oh] = origVB;
      const margin = Math.min(vw, vh) * 0.4;
      vx = Math.max(ox - margin, Math.min(ox + ow + margin - vw, vx));
      vy = Math.max(oy - margin, Math.min(oy + oh + margin - vh, vy));
    };

    // Mouse drag pan (desktop)
    wrap.style.cursor = "grab";

    wrap.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      mouseDragging = true;
      mouseMoved = false;
      mousePanStartVx = vx;
      mousePanStartVy = vy;
      mousePanStartX = e.clientX;
      mousePanStartY = e.clientY;
      wrap.style.cursor = "grabbing";
      e.preventDefault();
    });

    window.addEventListener("mousemove", mouseMove);
    window.addEventListener("mouseup", mouseUp);

    // Prevent click-on-node firing after a drag
    wrap.addEventListener(
      "click",
      (e) => {
        if (mouseMoved) {
          e.stopImmediatePropagation();
          mouseMoved = false;
        }
      },
      true,
    );

    // Wheel zoom (desktop)
    wrap.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        const rect = wrap.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        // Keep the point under the cursor fixed
        const svgMx = vx + (mx / rect.width) * vw;
        const svgMy = vy + (my / rect.height) * vh;
        vw /= factor;
        vh /= factor;
        // Clamp scale: never smaller than original, never more than 10×
        const [, , ow, oh] = origVB;
        vw = Math.max(ow / 10, Math.min(ow, vw));
        vh = Math.max(oh / 10, Math.min(oh, vh));
        vx = svgMx - (mx / rect.width) * vw;
        vy = svgMy - (my / rect.height) * vh;
        clamp();
        setVB();
      },
      { passive: false },
    );

    // Touch zoom + pan
    const active = {}; // identifier → {x, y}
    let lastDist = null,
      lastMid = null;
    let panStartVx = vx,
      panStartVy = vy,
      panStartX = 0,
      panStartY = 0;
    let lastTapTime = 0;

    const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
    const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

    wrap.addEventListener(
      "touchstart",
      (e) => {
        e.preventDefault();
        for (const t of e.changedTouches) active[t.identifier] = { x: t.clientX, y: t.clientY };
        const pts = Object.values(active);
        if (pts.length === 2) {
          lastDist = dist(pts[0], pts[1]);
          lastMid = mid(pts[0], pts[1]);
        } else if (pts.length === 1) {
          panStartVx = vx;
          panStartVy = vy;
          panStartX = pts[0].x;
          panStartY = pts[0].y;
        }
      },
      { passive: false },
    );

    wrap.addEventListener(
      "touchmove",
      (e) => {
        e.preventDefault();
        for (const t of e.changedTouches) active[t.identifier] = { x: t.clientX, y: t.clientY };
        const pts = Object.values(active);
        const rect = wrap.getBoundingClientRect();

        if (pts.length >= 2 && lastDist !== null) {
          const d = dist(pts[0], pts[1]);
          const m = mid(pts[0], pts[1]);
          const factor = d / lastDist;

          // Zoom centered on pinch midpoint
          const mx = m.x - rect.left;
          const my = m.y - rect.top;
          const svgMx = vx + (mx / rect.width) * vw;
          const svgMy = vy + (my / rect.height) * vh;
          const [, , ow, oh] = origVB;
          vw = Math.max(ow / 10, Math.min(ow, vw / factor));
          vh = Math.max(oh / 10, Math.min(oh, vh / factor));
          vx = svgMx - (mx / rect.width) * vw;
          vy = svgMy - (my / rect.height) * vh;

          // Pan with midpoint translation
          if (lastMid) {
            vx -= ((m.x - lastMid.x) / rect.width) * vw;
            vy -= ((m.y - lastMid.y) / rect.height) * vh;
          }
          lastDist = d;
          lastMid = m;
          clamp();
          setVB();
        } else if (pts.length === 1) {
          const dx = ((pts[0].x - panStartX) / rect.width) * vw;
          const dy = ((pts[0].y - panStartY) / rect.height) * vh;
          vx = panStartVx - dx;
          vy = panStartVy - dy;
          clamp();
          setVB();
        }
      },
      { passive: false },
    );

    wrap.addEventListener(
      "touchend",
      (e) => {
        e.preventDefault();
        const changed = [...e.changedTouches];

        // Tap detection (single finger, minimal movement, short duration)
        if (changed.length === 1 && Object.keys(active).length === 1) {
          const t = changed[0];
          const moved = Math.hypot(t.clientX - panStartX, t.clientY - panStartY);
          if (moved < 12) {
            // Double-tap → reset zoom
            const now = Date.now();
            if (now - lastTapTime < 300) {
              [vx, vy, vw, vh] = origVB;
              setVB();
            } else {
              // Single tap → show node info
              const el = document.elementFromPoint(t.clientX, t.clientY);
              const g = el?.closest("g[data-ntc-info]");
              if (g) showInfo(g.dataset.ntcInfo);
              else hideInfo();
            }
            lastTapTime = now;
          }
        }

        for (const t of changed) delete active[t.identifier];
        lastDist = null;
        lastMid = null;
        const remaining = Object.values(active);
        if (remaining.length === 1) {
          panStartVx = vx;
          panStartVy = vy;
          panStartX = remaining[0].x;
          panStartY = remaining[0].y;
        }
      },
      { passive: false },
    );
  }

  getCardSize() {
    return 8;
  }
  static getConfigElement() {
    return document.createElement("network-topology-card-editor");
  }
  static getStubConfig() {
    return { entity: "", title: "Network Map", gateway_label: "gw", column_width: 200 };
  }
}

// ── Visual config editor ──────────────────────────────────────────────────────

class NetworkTopologyCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const wg = findWireguardSensor(hass, this._config?.entity, this._config?.wireguard_entity);
    const sig = `${wg.configured ? 1 : 0}|${wg.available ? 1 : 0}|${wg.entityId ?? ""}`;
    if (sig !== this._wgSig) {
      this._wgSig = sig;
      this._render();
      return;
    }
    const picker = this.shadowRoot.querySelector("#entity_picker");
    if (picker) picker.hass = hass;
    const wgPicker = this.shadowRoot.querySelector("#wg_picker");
    if (wgPicker) wgPicker.hass = hass;
  }

  _fire(config) {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      }),
    );
  }

  _render() {
    const c = this._config;
    this.shadowRoot.innerHTML = `
      <style>
        .form {
          display: flex;
          flex-direction: column;
          gap: 16px;
          padding: 16px 0;
        }
        ha-entity-picker { width: 100%; display: block; }
        ha-textfield { width: 100%; display: block; }
        .cb-row {
          display: flex; align-items: center; gap: 8px;
          font-size: var(--ha-font-size-m, 14px);
          cursor: pointer; user-select: none;
        }
      </style>
      <div class="form">
        <ha-entity-picker
          id="entity_picker"
          label="Entity"
          allow-custom-entity
        ></ha-entity-picker>
        <ha-textfield id="title" label="Title"></ha-textfield>
        <ha-textfield id="gateway_label" label="Gateway label"></ha-textfield>
        <ha-textfield id="column_width" label="Column width" type="number" min="120" step="10"></ha-textfield>
        <label class="cb-row">
          <ha-checkbox id="show_offline"></ha-checkbox>
          <span>Show offline devices (dimmed)</span>
        </label>
        <div id="wg-section"></div>
      </div>`;

    const picker = this.shadowRoot.querySelector("#entity_picker");
    picker.value = c.entity ?? "";
    picker.includeDomains = ["sensor"];
    if (this._hass) picker.hass = this._hass;
    picker.addEventListener("value-changed", (e) => {
      this._fire({ ...this._config, entity: e.detail.value });
    });

    const title = this.shadowRoot.querySelector("#title");
    title.value = c.title ?? "Network Map";
    title.addEventListener("input", () => {
      this._fire({ ...this._config, title: title.value });
    });

    const gatewayLabel = this.shadowRoot.querySelector("#gateway_label");
    gatewayLabel.value = c.gateway_label ?? "gw";
    gatewayLabel.addEventListener("input", () => {
      this._fire({ ...this._config, gateway_label: gatewayLabel.value });
    });

    const columnWidth = this.shadowRoot.querySelector("#column_width");
    columnWidth.value = c.column_width ?? 200;
    columnWidth.addEventListener("input", () => {
      const value = Number(columnWidth.value);
      this._fire({
        ...this._config,
        column_width: Number.isFinite(value) && value >= 120 ? value : 200,
      });
    });

    const offlineCb = this.shadowRoot.querySelector("#show_offline");
    offlineCb.checked = c.show_offline ?? false;
    offlineCb.addEventListener("change", () => {
      this._fire({ ...this._config, show_offline: offlineCb.checked });
    });

    const wgSection = this.shadowRoot.querySelector("#wg-section");
    if (!wgSection) return;
    const wg = findWireguardSensor(this._hass, c.entity, c.wireguard_entity);
    if (wg.configured) {
      wgSection.innerHTML = `
        <label class="cb-row">
          <ha-checkbox id="show_wg"></ha-checkbox>
          <span>Show WireGuard peers</span>
        </label>
        <label class="cb-row" id="show_wg_off_row" style="margin-left:24px">
          <ha-checkbox id="show_wg_off"></ha-checkbox>
          <span>Show offline WireGuard peers (dimmed)</span>
        </label>
        <ha-entity-picker id="wg_picker" label="WireGuard sensor (override, optional)" allow-custom-entity></ha-entity-picker>
        ${
          wg.available
            ? ""
            : `<div style="font-size:var(--ha-font-size-s,12px);color:var(--secondary-text-color)">Selected WireGuard sensor reports no interfaces right now (available: false). Peers will appear once an integration scan succeeds.</div>`
        }`;

      const wgCb = wgSection.querySelector("#show_wg");
      const wgOffRow = wgSection.querySelector("#show_wg_off_row");
      const wgOffCb = wgSection.querySelector("#show_wg_off");
      const wgPicker = wgSection.querySelector("#wg_picker");

      wgCb.checked = c.show_wireguard_peers ?? false;
      wgOffCb.checked = c.show_offline_wireguard ?? true;
      wgOffRow.style.display = wgCb.checked ? "" : "none";

      wgCb.addEventListener("change", () => {
        wgOffRow.style.display = wgCb.checked ? "" : "none";
        this._fire({ ...this._config, show_wireguard_peers: wgCb.checked });
      });
      wgOffCb.addEventListener("change", () => {
        this._fire({ ...this._config, show_offline_wireguard: wgOffCb.checked });
      });

      wgPicker.value = c.wireguard_entity ?? "";
      wgPicker.includeDomains = ["sensor"];
      if (this._hass) wgPicker.hass = this._hass;
      wgPicker.addEventListener("value-changed", (e) => {
        this._fire({ ...this._config, wireguard_entity: e.detail.value || null });
      });
    } else {
      wgSection.innerHTML = `
        <ha-entity-picker id="wg_picker" label="WireGuard sensor (override, optional)" allow-custom-entity></ha-entity-picker>
        <div style="font-size:var(--ha-font-size-s,12px);color:var(--secondary-text-color)">
          WireGuard peers: enable WireGuard in the integration, or choose a WireGuard sensor override if auto-detect cannot pick one.
        </div>`;
      const wgPicker = wgSection.querySelector("#wg_picker");
      wgPicker.value = c.wireguard_entity ?? "";
      wgPicker.includeDomains = ["sensor"];
      if (this._hass) wgPicker.hass = this._hass;
      wgPicker.addEventListener("value-changed", (e) => {
        this._fire({ ...this._config, wireguard_entity: e.detail.value || null });
      });
    }
  }
}

if (!customElements.get("network-topology-card-editor")) {
  customElements.define("network-topology-card-editor", NetworkTopologyCardEditor);
}

// ── Registration ──────────────────────────────────────────────────────────────

if (!customElements.get("network-topology-card")) {
  customElements.define("network-topology-card", NetworkTopologyCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "network-topology-card")) {
  window.customCards.push({
    type: "network-topology-card",
    name: "Network Topology",
    description: "Live SVG network map from network_scanner sensor",
  });
}
