import {
  css,
  html,
  LitElement,
  nothing,
} from "https://cdn.jsdelivr.net/gh/lit/dist@3/all/lit-all.min.js";

const CARD_VERSION = "1.0.0";
const CARD_TYPE = "wireguard-card";
const EDITOR_TYPE = `${CARD_TYPE}-editor`;

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

class WireguardCard extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
  };

  static styles = css`
    ha-card {
      padding: 16px;
      display: block;
      user-select: text;
    }
    .iface {
      margin-bottom: 16px;
    }
    .iface-header {
      font-weight: 600;
      margin-bottom: 8px;
      font-size: 1.05em;
    }
    .iface-meta {
      font-size: 0.85em;
      opacity: 0.7;
      margin-left: 6px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92em;
    }
    th,
    td {
      padding: 4px 6px;
      text-align: left;
      border-bottom: 1px solid var(--divider-color, rgba(127, 127, 127, 0.2));
    }
    th {
      font-weight: 600;
      opacity: 0.75;
    }
    td.num {
      text-align: right;
      font-variant-numeric: tabular-nums;
    }
    tr.stale td {
      opacity: 0.45;
    }
    .empty {
      opacity: 0.6;
      font-style: italic;
      padding: 8px 0;
    }
    .dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      margin-right: 6px;
      vertical-align: middle;
    }
    .dot.online {
      background: var(--success-color, #4caf50);
    }
    .dot.offline {
      background: var(--disabled-color, #888);
    }
  `;

  setConfig(config) {
    if (!config?.entity) {
      throw new Error("wireguard-card: `entity` is required");
    }
    this._config = { ...config };
  }

  getCardSize() {
    const wg = this._wg();
    if (!wg?.interfaces?.length) return 2;
    const peers = wg.interfaces.reduce((n, iface) => n + (iface.peers?.length ?? 0), 0);
    return Math.max(2, 2 + Math.ceil(peers / 2));
  }

  static getStubConfig() {
    return { entity: "" };
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

  _renderPeerRow(peer) {
    const stale = !peer.online;
    const rx = peer.rx_Bps;
    const tx = peer.tx_Bps;
    // Both unknown -> "—". One known -> sum, treating null as 0.
    const rate = rx == null && tx == null ? null : (rx ?? 0) + (tx ?? 0);
    return html`
      <tr class=${stale ? "stale" : ""}>
        <td>
          <span class="dot ${peer.online ? "online" : "offline"}"></span>${peer.name ?? peer.public_key?.slice(0, 8) ?? "—"}
        </td>
        <td>${peer.endpoint ?? "—"}</td>
        <td>${(peer.allowed_ips ?? []).join(", ") || "—"}</td>
        <td>${fmtAge(peer.last_handshake)}</td>
        <td class="num">${fmtBytes(peer.rx_bytes)}</td>
        <td class="num">${fmtBytes(peer.tx_bytes)}</td>
        <td class="num">${fmtRate(rate)}</td>
      </tr>
    `;
  }

  _renderIface(iface) {
    const peers = iface.peers ?? [];
    const active = peers.filter((p) => p.online).length;
    return html`
      <div class="iface">
        <div class="iface-header">
          WireGuard — ${iface.name}
          <span class="iface-meta">
            (${active}/${peers.length} peers active${
              iface.listen_port ? ` · port ${iface.listen_port}` : ""
            })
          </span>
        </div>
        ${
          peers.length === 0
            ? html`<div class="empty">No peers configured.</div>`
            : html`
              <table>
                <thead>
                  <tr>
                    <th>Peer</th>
                    <th>Endpoint</th>
                    <th>Allowed IPs</th>
                    <th>Last HS</th>
                    <th class="num">↓ Total</th>
                    <th class="num">↑ Total</th>
                    <th class="num">Rate</th>
                  </tr>
                </thead>
                <tbody>
                  ${peers.map((p) => this._renderPeerRow(p))}
                </tbody>
              </table>
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
    return html`
      <ha-card>${interfaces.map((iface) => this._renderIface(iface))}</ha-card>
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
    ];
  }

  _computeLabel = (s) =>
    ({
      entity: "Entity",
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

customElements.define(EDITOR_TYPE, WireguardCardEditor);
customElements.define(CARD_TYPE, WireguardCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TYPE,
  name: "WireGuard",
  description: "Per-peer WireGuard tunnel status, transfer totals, and live rate from wrtsensor",
  preview: false,
});

console.info(
  `%c WIREGUARD-CARD %c ${CARD_VERSION} `,
  "color:white;background:#88171a;font-weight:700",
  "color:#88171a;background:transparent",
);
