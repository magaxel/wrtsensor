import {
  css,
  html,
  LitElement,
  nothing,
} from "https://cdn.jsdelivr.net/gh/lit/dist@3/all/lit-all.min.js";

const CARD_VERSION = "3.0.0";
const CARD_TYPE = "dns-stats-card";
const EDITOR_TYPE = `${CARD_TYPE}-editor`;
const DEFAULT_PERIOD = "last_24h";
const DEFAULT_MAX_SERVERS = 8;
const VALID_PERIODS = new Set(["last_24h", "last_8h", "last_1h", "last_scan"]);
const warnedLifetimeEntities = new Set();

const num = (n) => (Number.isFinite(Number(n)) ? Number(n) : null);
const fmtInt = (n) => (num(n) == null ? "—" : new Intl.NumberFormat().format(Math.round(num(n))));
const fmtCompact = (n) =>
  num(n) == null
    ? "—"
    : new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(
        Math.round(num(n)),
      );
const fmtRate = (n) =>
  num(n) == null
    ? "—"
    : num(n) >= 100
      ? num(n).toFixed(0)
      : num(n) >= 10
        ? num(n).toFixed(1)
        : num(n).toFixed(2);
const fmtPct = (n) => (num(n) == null ? "—" : `${num(n).toFixed(1)}%`);
const fmtMs = (n) => (num(n) == null ? "—" : `${Math.round(num(n))} ms`);
const fmtRateUnit = (n) => (num(n) == null ? "—" : `${fmtRate(n)}/s`);

const normalizePeriod = (period) => (VALID_PERIODS.has(period) ? period : DEFAULT_PERIOD);

const pickPeriod = (dns, period, entity) => {
  if (period === "lifetime" && entity && !warnedLifetimeEntities.has(entity)) {
    warnedLifetimeEntities.add(entity);
    console.warn(
      `${CARD_TYPE}: period "lifetime" is no longer supported for ${entity}; using "last_24h". Re-save the card config to update it.`,
    );
  }
  const key = normalizePeriod(period);
  const data = dns[key];
  return { data, label: data?.label ?? "unavailable" };
};

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
      show_ipv6: false,
      max_servers: DEFAULT_MAX_SERVERS,
      ...config,
      period: config.period ?? DEFAULT_PERIOD,
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

    const period = pickPeriod(d, this._config.period, this._config.entity);
    const stats = period.data;
    if (!stats) {
      return html`
        <ha-card>
          <div class="title">${this._config.title}</div>
          <div class="unavailable">No DNS period data on ${this._config.entity}</div>
        </ha-card>
      `;
    }
    const hitPct = stats.hit_pct;
    const hasData = num(hitPct) != null;
    const hitW = Math.max(0, Math.min(100, num(hitPct) ?? 0));
    const missW = 100 - hitW;
    const maxServers = Math.max(
      0,
      Math.floor(num(this._config.max_servers) ?? DEFAULT_MAX_SERVERS),
    );
    const periodServers = stats.servers ?? [];
    const rawServers = d.servers ?? [];
    const servers = periodServers
      .filter((s) => this._config.show_ipv6 || !String(s.addr ?? "").includes(":"))
      .slice(0, maxServers);

    return html`
      <ha-card>
        <div class="title">${this._config.title}</div>
        <div class="hit-pct">
          ${fmtPct(hitPct)}<span class="unit">hit rate</span>
        </div>
        <div class="subtitle">${period.label}</div>

        ${
          hasData
            ? html`
                <div
                  class="bar"
                  title="Hits ${fmtPct(hitW)} · Misses ${fmtPct(missW)}"
                  aria-label="DNS hits ${fmtPct(hitW)}, misses ${fmtPct(missW)}"
                >
                  <div class="bar-hit" style="width:${hitW}%"></div>
                  <div class="bar-miss" style="width:${missW}%"></div>
                </div>
                <div class="legend">
                  <span>Hits <span class="val">${fmtRateUnit(stats.hits_per_sec)}</span></span>
                  <span>Misses <span class="val">${fmtRateUnit(stats.misses_per_sec)}</span></span>
                </div>
              `
            : html`<div class="bar" aria-hidden="true"></div>`
        }

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
            <span class="k">Hits</span>
            <span class="v" title="${fmtInt(stats.hits)}">${fmtCompact(stats.hits)}</span>
          </div>
          <div class="kv">
            <span class="k">Misses</span>
            <span class="v" title="${fmtInt(stats.misses)}">${fmtCompact(stats.misses)}</span>
          </div>
        </div>

        ${
          rawServers.length && !periodServers.length
            ? html`
                <div class="servers">
                  <div class="hdr">Upstream servers</div>
                  <div class="unavailable">No upstream query data for this window yet</div>
                </div>
              `
            : servers.length
              ? html`
                <div class="servers">
                  <div class="hdr">Upstream servers</div>
                  ${servers.map(
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
    return {
      entity: "",
      title: "DNS Cache",
      period: DEFAULT_PERIOD,
      show_ipv6: false,
      max_servers: DEFAULT_MAX_SERVERS,
    };
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
      {
        name: "period",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "last_24h", label: "Last 24h" },
              { value: "last_8h", label: "Last 8h" },
              { value: "last_1h", label: "Last 1h" },
              { value: "last_scan", label: "Last scan" },
            ],
          },
        },
      },
      { name: "show_ipv6", selector: { boolean: {} } },
      {
        name: "max_servers",
        selector: { number: { min: 0, max: 32, step: 1, mode: "box" } },
      },
    ];
  }

  _computeLabel = (s) =>
    ({
      entity: "Entity",
      title: "Title",
      period: "Period",
      show_ipv6: "Show IPv6 upstreams",
      max_servers: "Maximum upstream servers",
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
    const normalized = normalizePeriod(this._config.period ?? DEFAULT_PERIOD);
    if (this._config.period && this._config.period !== normalized) {
      // Persist the migrated period back to the dashboard YAML so opening
      // the editor once removes the deprecated "lifetime" value for good.
      queueMicrotask(() => this._valueChanged({ detail: { value: { period: normalized } } }));
    }
    const data = {
      entity: this._config.entity ?? "",
      title: this._config.title ?? "DNS Cache",
      period: normalized,
      show_ipv6: this._config.show_ipv6 ?? false,
      max_servers: this._config.max_servers ?? DEFAULT_MAX_SERVERS,
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
  customElements.define(EDITOR_TYPE, DnsStatsCardEditor);
}
if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, DnsStatsCard);
}

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
