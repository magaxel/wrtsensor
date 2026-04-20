import {
  css,
  html,
  LitElement,
  nothing,
} from "https://cdn.jsdelivr.net/gh/lit/dist@3/all/lit-all.min.js";

const CARD_VERSION = "1.0.0";
const CARD_TYPE = "dns-stats-card";
const EDITOR_TYPE = `${CARD_TYPE}-editor`;

const fmtInt = (n) => (n == null ? "—" : new Intl.NumberFormat().format(Math.round(n)));
const fmtCompact = (n) =>
  n == null
    ? "—"
    : new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(
        Math.round(n),
      );
const fmtRate = (n) =>
  n == null ? "—" : n >= 100 ? n.toFixed(0) : n >= 10 ? n.toFixed(1) : n.toFixed(2);
const fmtPct = (n) => (n == null ? "—" : `${n.toFixed(1)}%`);
const fmtMs = (n) => (n == null ? "—" : `${Math.round(n)} ms`);

class DnsStatsCard extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
  };

  static styles = css`
    ha-card {
      padding: 16px;
      display: block;
      user-select: text;
      -webkit-user-select: text;
      cursor: text;
    }
    .title {
      font-size: var(--ha-card-header-font-size, 24px);
      font-weight: var(--ha-card-header-font-weight, 400);
      color: var(--primary-text-color);
      line-height: 1.2;
      margin-bottom: 12px;
      letter-spacing: -0.012em;
    }
    .hit-pct {
      font-size: 2.4em;
      font-weight: 300;
      line-height: 1;
      color: var(--primary-text-color);
    }
    .hit-pct .unit {
      font-size: 0.5em;
      color: var(--secondary-text-color);
      margin-left: 4px;
    }
    .subtitle {
      color: var(--secondary-text-color);
      font-size: 0.85em;
      margin-top: 4px;
    }
    .bar {
      display: flex;
      height: 8px;
      width: 100%;
      border-radius: 4px;
      overflow: hidden;
      margin: 14px 0 6px;
      background: var(--divider-color, #333);
    }
    .bar-hit {
      background: var(--success-color, #4caf50);
    }
    .bar-miss {
      background: var(--warning-color, #ff9800);
    }
    .legend {
      display: flex;
      justify-content: space-between;
      font-size: 0.8em;
      color: var(--secondary-text-color);
    }
    .legend .val {
      color: var(--primary-text-color);
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px 16px;
      margin-top: 16px;
    }
    .kv {
      display: flex;
      flex-direction: column;
    }
    .kv .k {
      font-size: 0.75em;
      color: var(--secondary-text-color);
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }
    .kv .v {
      font-size: 1.1em;
      color: var(--primary-text-color);
      font-variant-numeric: tabular-nums;
    }
    .servers {
      margin-top: 16px;
      border-top: 1px solid var(--divider-color, #333);
      padding-top: 12px;
    }
    .servers .hdr {
      font-size: 0.75em;
      color: var(--secondary-text-color);
      text-transform: uppercase;
      letter-spacing: 0.03em;
      margin-bottom: 6px;
    }
    .server {
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 4px 12px;
      font-size: 0.9em;
      font-variant-numeric: tabular-nums;
      padding: 2px 0;
      color: var(--primary-text-color);
    }
    .server .addr {
      font-family: var(--code-font-family, monospace);
      word-break: break-all;
      min-width: 0;
    }
    .server .meta {
      color: var(--secondary-text-color);
      white-space: nowrap;
    }
    .unavailable {
      color: var(--secondary-text-color);
      font-style: italic;
    }
    .skeleton {
      height: 140px;
      background: linear-gradient(
        90deg,
        var(--divider-color, #2a2a2a) 0%,
        var(--card-background-color, #1c1c1c) 50%,
        var(--divider-color, #2a2a2a) 100%
      );
      background-size: 200% 100%;
      animation: shimmer 1.4s infinite linear;
      border-radius: 6px;
    }
    @keyframes shimmer {
      0% {
        background-position: 200% 0;
      }
      100% {
        background-position: -200% 0;
      }
    }
  `;

  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    if (!config.entity) throw new Error("Missing required 'entity'");
    this._config = {
      title: "DNS Cache",
      ...config,
    };
  }

  getCardSize() {
    return 3;
  }

  shouldUpdate(changed) {
    if (!this._config) return false;
    if (changed.has("_config")) return true;
    if (!changed.has("hass")) return false;
    const old = changed.get("hass");
    if (!old) return true;
    const e = this._config.entity;
    return old.states?.[e] !== this.hass.states?.[e];
  }

  render() {
    if (!this._config) return nothing;
    if (!this.hass) return html`<ha-card><div class="skeleton"></div></ha-card>`;

    const state = this.hass.states[this._config.entity];
    if (!state) {
      return html`
        <ha-card>
          <div class="title">${this._config.title}</div>
          <div class="unavailable">Entity not found: ${this._config.entity}</div>
        </ha-card>
      `;
    }
    if (["unavailable", "unknown"].includes(state.state)) {
      return html`
        <ha-card>
          <div class="title">${this._config.title}</div>
          <div class="unavailable">${this._config.entity} is ${state.state}</div>
        </ha-card>
      `;
    }

    const d = state.attributes.dns_stats;
    if (!d) {
      return html`
        <ha-card>
          <div class="title">${this._config.title}</div>
          <div class="unavailable">No dns_stats attribute on ${this._config.entity}</div>
        </ha-card>
      `;
    }

    const hitPct = d.hit_pct ?? d.hit_pct_lifetime;
    const hitPctLabel = d.hit_pct != null ? "since last scan" : "lifetime (since dnsmasq start)";
    const hitW = Math.max(0, Math.min(100, hitPct ?? 0));
    const missW = 100 - hitW;

    return html`
      <ha-card>
        <div class="title">${this._config.title}</div>
        <div class="hit-pct">
          ${fmtPct(hitPct)}<span class="unit">hit rate</span>
        </div>
        <div class="subtitle">${hitPctLabel}</div>

        <div
          class="bar"
          title="Hits ${fmtPct(hitW)} · Misses ${fmtPct(missW)}"
        >
          <div class="bar-hit" style="width:${hitW}%"></div>
          <div class="bar-miss" style="width:${missW}%"></div>
        </div>
        <div class="legend">
          <span>Hits <span class="val">${fmtRate(d.hits_per_sec)}/s</span></span>
          <span>Misses <span class="val">${fmtRate(d.misses_per_sec)}/s</span></span>
        </div>

        <div class="grid">
          <div class="kv">
            <span class="k">Upstream latency</span>
            <span class="v">${fmtMs(d.latency_ms)}</span>
          </div>
          <div class="kv">
            <span class="k">Cache size</span>
            <span class="v">${fmtInt(d.cache_size)}</span>
          </div>
          <div class="kv">
            <span class="k">Hits total</span>
            <span class="v" title="${fmtInt(d.hits_total)}">${fmtCompact(d.hits_total)}</span>
          </div>
          <div class="kv">
            <span class="k">Misses total</span>
            <span class="v" title="${fmtInt(d.misses_total)}">${fmtCompact(d.misses_total)}</span>
          </div>
        </div>

        ${
          d.servers?.length
            ? html`
                <div class="servers">
                  <div class="hdr">Upstream servers</div>
                  ${d.servers.map(
                    (s) => html`
                      <div class="server">
                        <span class="addr">${s.addr}</span>
                        <span class="meta" title="${fmtInt(s.queries)} queries">
                          ${fmtCompact(s.queries)} queries
                          ${s.latency_ms != null ? html` · ${fmtMs(s.latency_ms)}` : nothing}
                        </span>
                      </div>
                    `,
                  )}
                </div>
              `
            : nothing
        }
      </ha-card>
    `;
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }

  static getStubConfig() {
    return { entity: "sensor.network_scanner", title: "DNS Cache" };
  }
}

class DnsStatsCardEditor extends LitElement {
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
    ];
  }

  _computeLabel = (s) => ({ entity: "Entity", title: "Title" })[s.name] ?? s.name;

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
      title: this._config.title ?? "DNS Cache",
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

customElements.define(EDITOR_TYPE, DnsStatsCardEditor);
customElements.define(CARD_TYPE, DnsStatsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TYPE,
  name: "DNS Stats",
  description: "dnsmasq cache hit rate, latency and totals from network_scanner sensor",
  preview: false,
});

console.info(
  `%c DNS-STATS-CARD %c ${CARD_VERSION} `,
  "color:white;background:#009688;font-weight:700",
  "color:#009688;background:transparent",
);
