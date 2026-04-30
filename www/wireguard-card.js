import {
  css,
  html,
  LitElement,
  nothing,
} from "https://cdn.jsdelivr.net/gh/lit/dist@3/all/lit-all.min.js";

const CARD_VERSION = "1.1.0";
const CARD_TYPE = "wireguard-card";
const EDITOR_TYPE = `${CARD_TYPE}-editor`;

const SWIPE_DX_THRESHOLD = 40;
const SWIPE_RATIO = 1.5;
const SWIPE_CLEAR_MS = 250;

const num = (n) => (Number.isFinite(Number(n)) ? Number(n) : null);

const fmtBytes = (n) => {
  const v = num(n);
  if (v == null) return "—";
  const abs = Math.abs(v);
  if (abs < 1024) return `${v} B`;
  if (abs < 1024 ** 2) return `${(v / 1024).toFixed(1)} KB`;
  if (abs < 1024 ** 3) return `${(v / 1024 ** 2).toFixed(1)} MB`;
  if (abs < 1024 ** 4) return `${(v / 1024 ** 3).toFixed(2)} GB`;
  return `${(v / 1024 ** 4).toFixed(2)} TB`;
};

const fmtRate = (n) => {
  const v = num(n);
  if (v == null) return "—";
  if (v < 1024) return `${v} B/s`;
  if (v < 1024 ** 2) return `${(v / 1024).toFixed(1)} KB/s`;
  return `${(v / 1024 ** 2).toFixed(2)} MB/s`;
};

const fmtAge = (epoch) => {
  const v = num(epoch);
  if (v == null || v <= 0) return "never";
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - v));
  if (elapsed < 60) return `${elapsed}s ago`;
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)}m ago`;
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)}h ago`;
  return `${Math.floor(elapsed / 86400)}d ago`;
};

const sanitizeId = (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, "_");

class WireguardCard extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
    _expanded: { state: true },
    _page: { state: true },
  };

  constructor() {
    super();
    this._expanded = new Set();
    this._page = 0;
    this._swiped = false;
    this._swipeClearTimer = null;
    this._touchStart = null;
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._swipeClearTimer != null) {
      clearTimeout(this._swipeClearTimer);
      this._swipeClearTimer = null;
    }
  }

  static styles = css`
    ha-card {
      padding: 16px;
      display: block;
      user-select: text;
      container-type: inline-size;
    }
    .title {
      font-size: var(--ha-card-header-font-size, 24px);
      font-weight: var(--ha-card-header-font-weight, 400);
      color: var(--primary-text-color);
      line-height: 1.2;
      margin-bottom: 12px;
      letter-spacing: -0.012em;
    }
    .iface {
      margin-bottom: 16px;
    }
    .iface:last-child {
      margin-bottom: 0;
    }
    .iface-header {
      font-weight: 600;
      margin-bottom: 4px;
      font-size: 0.95em;
      color: var(--secondary-text-color);
    }
    .iface-meta {
      font-size: 0.85em;
      opacity: 0.7;
      margin-left: 6px;
    }
    @container (max-width: 640px) {
      .iface-meta {
        display: block;
        margin-left: 0;
      }
    }
    .empty {
      opacity: 0.6;
      font-style: italic;
      padding: 8px 0;
    }
    .peer-scroll {
      display: block;
    }
    .peer-list {
      display: flex;
      flex-direction: column;
    }
    .peer {
      border-bottom: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
    }
    .peer:last-child {
      border-bottom: 0;
    }
    .peer.stale .peer-row,
    .peer.stale .detail {
      opacity: 0.45;
    }
    .peer-row {
      appearance: none;
      background: transparent;
      border: 0;
      margin: 0;
      padding: 8px 0;
      width: 100%;
      display: flex;
      align-items: center;
      gap: 8px;
      text-align: left;
      font: inherit;
      color: inherit;
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
    }
    .peer-row:hover {
      background: var(--state-hover-color, rgba(127, 127, 127, 0.06));
    }
    .peer-row:focus-visible {
      outline: 2px solid var(--primary-color);
      outline-offset: 2px;
    }
    .dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .dot.online {
      background: var(--success-color, #4caf50);
    }
    .dot.offline {
      background: var(--disabled-color, #888);
    }
    .peer-name {
      flex: 1 1 auto;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 0.95em;
    }
    .chevron {
      --mdc-icon-size: 20px;
      flex-shrink: 0;
      color: var(--secondary-text-color);
      transition: transform 0.2s;
    }
    .peer.expanded .chevron {
      transform: rotate(180deg);
    }
    .detail {
      padding: 4px 0 10px 16px;
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
      overflow-wrap: anywhere;
      word-break: normal;
    }
    .pager {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 12px;
      padding-top: 10px;
      margin-top: 4px;
      border-top: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
    }
    .page-btn {
      appearance: none;
      background: transparent;
      border: 0;
      padding: 4px;
      border-radius: 50%;
      color: var(--secondary-text-color);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    .page-btn:hover:not(:disabled) {
      color: var(--primary-color);
      background: var(--divider-color, rgba(127, 127, 127, 0.15));
    }
    .page-btn:focus-visible {
      outline: 2px solid var(--primary-color);
      outline-offset: 2px;
    }
    .page-btn:disabled {
      opacity: 0.35;
      cursor: default;
    }
    .page-label {
      font-size: 0.85em;
      color: var(--secondary-text-color);
      font-variant-numeric: tabular-nums;
    }
  `;

  setConfig(config) {
    if (!config?.entity) {
      throw new Error("wireguard-card: `entity` is required");
    }
    const rawMax = Math.floor(Number(config.max_peers));
    const maxPeers = Number.isFinite(rawMax) && rawMax > 0 ? rawMax : 0;
    const prevMax = this._config?.max_peers;
    this._config = { ...config, max_peers: maxPeers };
    if (prevMax !== undefined && prevMax !== maxPeers) {
      this._page = 0;
    }
  }

  getCardSize() {
    const wg = this._wg();
    if (!wg?.interfaces?.length) return 2;
    const peers = wg.interfaces.reduce((n, iface) => n + (iface.peers?.length ?? 0), 0);
    const max = this._config?.max_peers ?? 0;
    const visible = max > 0 ? Math.min(peers, max) : peers;
    return Math.max(2, 2 + Math.ceil(visible / 2));
  }

  static getStubConfig() {
    return { entity: "", max_peers: 0 };
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }

  _wg() {
    if (!this.hass || !this._config?.entity) return null;
    const state = this.hass.states[this._config.entity];
    if (!state) return null;
    return state.attributes?.wireguard ?? null;
  }

  _peerKey(iface, peer, ifaceLocalIndex) {
    // Prefer the backend's host|iface|public_key id so the same client public
    // key configured on multiple interfaces/hosts gets independent expansion
    // state and unique panel ids.
    if (peer.id) return peer.id;
    if (peer.public_key) return `${iface.host ?? ""}|${iface.name ?? "wg"}|${peer.public_key}`;
    return `${iface.host ?? ""}|${iface.name ?? "wg"}|${ifaceLocalIndex}`;
  }

  _toggleExpand(key) {
    if (this._swiped) {
      this._swiped = false;
      if (this._swipeClearTimer != null) {
        clearTimeout(this._swipeClearTimer);
        this._swipeClearTimer = null;
      }
      return;
    }
    const next = new Set(this._expanded);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    this._expanded = next;
  }

  _goPage(target, pages) {
    const clamped = Math.max(0, Math.min(target, pages - 1));
    if (clamped !== this._page) this._page = clamped;
  }

  _onTouchStart(ev) {
    const t = ev.touches?.[0];
    if (!t) return;
    this._touchStart = { x: t.clientX, y: t.clientY };
    this._swiped = false;
  }

  _onTouchEnd(ev, effectivePage, pages) {
    const start = this._touchStart;
    this._touchStart = null;
    if (!start) return;
    const t = ev.changedTouches?.[0];
    if (!t) return;
    const dx = t.clientX - start.x;
    const dy = t.clientY - start.y;
    if (Math.abs(dx) <= SWIPE_DX_THRESHOLD) return;
    if (Math.abs(dx) <= Math.abs(dy) * SWIPE_RATIO) return;
    // Once the gesture qualifies as a horizontal swipe, suppress the follow-up
    // synthetic click even if pagination is blocked at a boundary — otherwise
    // a right-swipe on page 0 (or left-swipe on the last page) would still
    // toggle whichever peer was under the finger.
    this._swiped = true;
    if (this._swipeClearTimer != null) clearTimeout(this._swipeClearTimer);
    this._swipeClearTimer = window.setTimeout(() => {
      this._swiped = false;
      this._swipeClearTimer = null;
    }, SWIPE_CLEAR_MS);
    const targetPage = dx < 0 ? effectivePage + 1 : effectivePage - 1;
    const clamped = Math.max(0, Math.min(targetPage, pages - 1));
    if (clamped === effectivePage) return;
    this._goPage(clamped, pages);
  }

  _renderPeer(iface, peer, ifaceLocalIndex) {
    const key = this._peerKey(iface, peer, ifaceLocalIndex);
    const expanded = this._expanded.has(key);
    const stale = !peer.online;
    const panelId = `wg-detail-${sanitizeId(key)}`;
    const name = peer.name ?? peer.public_key?.slice(0, 8) ?? "—";
    const rx = peer.rx_Bps;
    const tx = peer.tx_Bps;
    const rate = rx == null && tx == null ? null : (rx ?? 0) + (tx ?? 0);
    const cls = ["peer"];
    if (stale) cls.push("stale");
    if (expanded) cls.push("expanded");
    return html`
      <div class=${cls.join(" ")}>
        <button
          type="button"
          class="peer-row"
          aria-expanded=${expanded ? "true" : "false"}
          aria-controls=${panelId}
          @click=${() => this._toggleExpand(key)}
        >
          <span class="dot ${peer.online ? "online" : "offline"}"></span>
          <span class="peer-name">${name}</span>
          <ha-icon class="chevron" icon="mdi:chevron-down"></ha-icon>
        </button>
        ${
          expanded
            ? html`
              <div id=${panelId} class="detail" role="region" aria-label=${`${name} details`}>
                <div class="field">
                  <div class="flabel">Endpoint</div>
                  <div class="fvalue">${peer.endpoint ?? "—"}</div>
                </div>
                <div class="field">
                  <div class="flabel">Allowed IPs</div>
                  <div class="fvalue">${(peer.allowed_ips ?? []).join(", ") || "—"}</div>
                </div>
                <div class="field">
                  <div class="flabel">Last HS</div>
                  <div class="fvalue">${fmtAge(peer.last_handshake)}</div>
                </div>
                <div class="field">
                  <div class="flabel">Down total</div>
                  <div class="fvalue">${fmtBytes(peer.rx_bytes)}</div>
                </div>
                <div class="field">
                  <div class="flabel">Up total</div>
                  <div class="fvalue">${fmtBytes(peer.tx_bytes)}</div>
                </div>
                <div class="field">
                  <div class="flabel">Rate</div>
                  <div class="fvalue">${fmtRate(rate)}</div>
                </div>
              </div>
            `
            : nothing
        }
      </div>
    `;
  }

  _renderIface(iface, entries) {
    const peers = iface.peers ?? [];
    const active = peers.filter((p) => p.online).length;
    const ifaceName = String(iface.name ?? "").trim();
    const showIfaceName = ifaceName && !["wireguard", "wg"].includes(ifaceName.toLowerCase());
    const summary = `${active}/${peers.length} peers active${
      iface.listen_port ? ` · port ${iface.listen_port}` : ""
    }`;
    return html`
      <div class="iface">
        <div class="iface-header">
          ${showIfaceName ? html`${ifaceName}<span class="iface-meta">${summary}</span>` : summary}
        </div>
        ${
          entries.length === 0
            ? html`<div class="empty">No peers configured.</div>`
            : html`
              <div class="peer-list">
                ${entries.map(({ peer, ifaceLocalIndex }) => this._renderPeer(iface, peer, ifaceLocalIndex))}
              </div>
            `
        }
      </div>
    `;
  }

  render() {
    if (!this.hass || !this._config) return nothing;
    const wg = this._wg();
    if (!wg) {
      return html`
        <ha-card>
          <div class="empty">
            WireGuard sensor not found. Pick a wrtsensor WireGuard sensor in the
            card editor.
          </div>
        </ha-card>
      `;
    }
    if (!wg.available) {
      return html`
        <ha-card>
          <div class="empty">
            WireGuard not detected — install <code>wg</code> on a host or enable
            the option in wrtsensor settings.
          </div>
        </ha-card>
      `;
    }
    const interfaces = wg.interfaces ?? [];
    if (interfaces.length === 0) {
      return html`
        <ha-card>
          <div class="empty">No WireGuard interfaces reported this scan.</div>
        </ha-card>
      `;
    }

    const flat = [];
    for (const iface of interfaces) {
      const peers = iface.peers ?? [];
      peers.forEach((peer, ifaceLocalIndex) => {
        flat.push({ iface, peer, ifaceLocalIndex });
      });
    }

    const total = flat.length;
    const max = this._config.max_peers ?? 0;
    const paginated = max > 0 && total > max;
    const pages = paginated ? Math.ceil(total / max) : 1;
    const effectivePage = paginated ? Math.min(this._page, Math.max(0, pages - 1)) : 0;

    const visible = paginated ? flat.slice(effectivePage * max, effectivePage * max + max) : flat;

    // Re-group visible entries by interface, preserving interface order.
    const groups = new Map();
    for (const entry of visible) {
      const arr = groups.get(entry.iface) ?? [];
      arr.push(entry);
      groups.set(entry.iface, arr);
    }
    // When paginated, only render interfaces that contributed peers to this page.
    // Empty interfaces (no peers configured at all) still render their header
    // when not paginated, matching the previous "No peers configured." state.
    const ifacesToRender = paginated
      ? [...groups.keys()].map((iface) => [iface, groups.get(iface)])
      : interfaces.map((iface) => [iface, groups.get(iface) ?? []]);

    return html`
      <ha-card>
        <div class="title">${this._config.title ?? "WireGuard"}</div>
        <div
          class="peer-scroll"
          @touchstart=${this._onTouchStart}
          @touchend=${(e) => this._onTouchEnd(e, effectivePage, pages)}
        >
          ${ifacesToRender.map(([iface, entries]) => this._renderIface(iface, entries))}
        </div>
        ${
          paginated
            ? html`
              <div class="pager">
                <button
                  type="button"
                  class="page-btn"
                  aria-label="Previous page"
                  ?disabled=${effectivePage === 0}
                  @click=${() => this._goPage(effectivePage - 1, pages)}
                >
                  <ha-icon icon="mdi:chevron-left"></ha-icon>
                </button>
                <span class="page-label">Page ${effectivePage + 1} / ${pages}</span>
                <button
                  type="button"
                  class="page-btn"
                  aria-label="Next page"
                  ?disabled=${effectivePage >= pages - 1}
                  @click=${() => this._goPage(effectivePage + 1, pages)}
                >
                  <ha-icon icon="mdi:chevron-right"></ha-icon>
                </button>
              </div>
            `
            : nothing
        }
      </ha-card>
    `;
  }
}

class WireguardCardEditor extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
  };

  setConfig(config) {
    this._config = { ...config };
  }

  _schema() {
    return [
      {
        name: "entity",
        required: true,
        selector: {
          entity: {
            domain: "sensor",
            integration: "wrtsensor",
          },
        },
      },
      {
        name: "max_peers",
        selector: { number: { min: 0, mode: "box" } },
      },
    ];
  }

  _computeLabel = (s) =>
    ({
      entity: "Entity",
      max_peers: "Peers per page (0 = all)",
    })[s.name] ?? s.name;

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
      max_peers: this._config.max_peers ?? 0,
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

if (!customElements.get(EDITOR_TYPE)) {
  customElements.define(EDITOR_TYPE, WireguardCardEditor);
}
if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, WireguardCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD_TYPE)) {
  window.customCards.push({
    type: CARD_TYPE,
    name: "WireGuard",
    description: "Per-peer WireGuard tunnel status, transfer totals, and live rate from wrtsensor",
    preview: false,
  });
}

console.info(
  `%c WIREGUARD-CARD %c ${CARD_VERSION} `,
  "color:white;background:#88171a;font-weight:700",
  "color:#88171a;background:transparent",
);
