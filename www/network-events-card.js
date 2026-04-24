import {
  css,
  html,
  LitElement,
  nothing,
} from "https://cdn.jsdelivr.net/gh/lit/dist@3/all/lit-all.min.js";

const CARD_VERSION = "1.1.0";
const CARD_TYPE = "network-events-card";
const EDITOR_TYPE = `${CARD_TYPE}-editor`;
const WS_TYPE_RECENT_EVENTS = "wrtsensor/recent_events";

function fmtBytes(bytes) {
  if (bytes == null) return "—";
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(2)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes >= 1e3) return `${Math.round(bytes / 1e3)} KB`;
  return `${bytes} B`;
}

const EVENT_TYPES = [
  { type: "connect", icon: "→", color: "#4CAF50", label: "Connect" },
  { type: "disconnect", icon: "←", color: "#F44336", label: "Disconnect" },
  { type: "roam", icon: "⇄", color: "#2196F3", label: "Roam" },
  { type: "band_change", icon: "↕", color: "#FF9800", label: "Band" },
  { type: "new_device", icon: "★", color: "#9C27B0", label: "New" },
  { type: "hostname_change", icon: "✎", color: "#FF9800", label: "Rename" },
  { type: "ip_change", icon: "⟳", color: "#00BCD4", label: "IPv4" },
  { type: "ip6_change", icon: "⟳", color: "#00BCD4", label: "IPv6" },
  { type: "ap_online", icon: "▲", color: "#4CAF50", label: "AP Up" },
  { type: "ap_offline", icon: "▼", color: "#F44336", label: "AP Down" },
  { type: "wan_online", icon: "▲", color: "#4CAF50", label: "WAN Up" },
  { type: "wan_offline", icon: "▼", color: "#F44336", label: "WAN Down" },
  { type: "wan_ip_change", icon: "⟳", color: "#00BCD4", label: "WAN IP" },
  { type: "wan_ip6_change", icon: "⟳", color: "#00BCD4", label: "WAN IP6" },
];

const TYPE_MAP = Object.fromEntries(EVENT_TYPES.map((t) => [t.type, t]));
const TYPE_KEYS = new Set(EVENT_TYPES.map((t) => t.type));

function typeCssVar(type) {
  return `--evt-color-${String(type).replaceAll("_", "-")}`;
}

function eventColor(type) {
  const info = TYPE_MAP[type];
  return `var(${typeCssVar(type)}, ${info?.color ?? "var(--secondary-text-color)"})`;
}

function isInputTarget(target) {
  const tag = target?.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "HA-TEXTFIELD";
}

// ── Card ──────────────────────────────────────────────────────────────────────

class NetworkEventsCard extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
    _events: { state: true },
    _status: { state: true },
    _message: { state: true },
    _textFilter: { state: true },
    _activeTypes: { state: true },
    _visibleTypes: { state: true },
    _page: { state: true },
  };

  constructor() {
    super();
    this._events = [];
    this._lastUpdated = null;
    this._status = "loading";
    this._message = "";
    this._textFilter = "";
    this._activeTypes = new Set(EVENT_TYPES.map((t) => t.type));
    this._visibleTypes = EVENT_TYPES;
    this._page = 1;
    this._perPage = 50;
    this._requestSeq = 0;
  }

  static styles = css`
    :host {
      display: block;
      height: 100%;
      user-select: text;
      -webkit-user-select: text;
    }
    ha-card {
      display: flex;
      flex-direction: column;
      height: 100%;
      overflow: hidden;
    }
    .header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      padding: 12px 16px 4px;
      gap: 12px;
    }
    .title {
      font-family: var(--ha-card-header-font-family, inherit);
      font-size: var(--ha-card-header-font-size, 24px);
      font-weight: var(--ha-card-header-font-weight, normal);
      color: var(--ha-card-header-color, var(--primary-text-color));
      line-height: 32px;
    }
    .controls {
      padding: 4px 16px 8px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      border-bottom: 1px solid var(--divider-color, rgba(255, 255, 255, 0.1));
    }
    .search {
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
    .search-input {
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
    .search-input::placeholder {
      color: var(--secondary-text-color);
    }
    .search-clear {
      background: none;
      border: none;
      color: var(--secondary-text-color);
      cursor: pointer;
      padding: 2px;
      line-height: 1;
      font: inherit;
    }
    .search-clear:hover,
    .search-clear:focus-visible,
    .sel-btn:hover,
    .sel-btn:focus-visible {
      color: var(--primary-text-color);
    }
    button:focus-visible,
    .search-input:focus-visible {
      outline: 2px solid var(--primary-color, #009ac7);
      outline-offset: 2px;
    }
    .type-btns {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      align-items: center;
    }
    .type-btn {
      background: color-mix(in srgb, var(--evt-color) 14%, transparent);
      border: 1px solid var(--evt-color);
      border-radius: 12px;
      color: var(--evt-color);
      font-size: 0.75em;
      padding: 2px 8px;
      cursor: pointer;
      font-family: inherit;
      transition: opacity 0.15s, background 0.15s;
    }
    .type-btn.inactive {
      background: transparent;
      opacity: 0.35;
    }
    .sel-btn {
      background: none;
      border: none;
      font-size: 0.75em;
      color: var(--secondary-text-color);
      cursor: pointer;
      font-family: inherit;
      padding: 2px 4px;
      white-space: nowrap;
    }
    .sel-divider {
      color: var(--secondary-text-color);
      font-size: 0.75em;
      opacity: 0.4;
      user-select: none;
    }
    .events-wrap {
      overflow-y: auto;
      flex: 1 1 auto;
      min-height: 0;
      padding: 4px 16px 16px;
      -webkit-overflow-scrolling: touch;
    }
    .date-label {
      font-size: 0.75em;
      font-weight: 600;
      color: var(--secondary-text-color);
      padding: 8px 0 3px;
      border-bottom: 1px solid var(--divider-color, rgba(255, 255, 255, 0.08));
      margin-bottom: 2px;
      display: flex;
      align-items: baseline;
      gap: 6px;
    }
    .date-count {
      font-weight: 400;
      opacity: 0.6;
    }
    .event {
      display: grid;
      grid-template-columns: 1.4em 5em 1fr;
      gap: 0 6px;
      align-items: baseline;
      padding: 4px 0;
      border-bottom: 1px solid var(--divider-color, rgba(255, 255, 255, 0.03));
    }
    .evt-icon {
      text-align: center;
      padding-top: 1px;
      color: var(--evt-color);
    }
    .evt-time {
      font-family: monospace;
      color: var(--secondary-text-color);
      font-size: 0.8em;
      padding-top: 2px;
    }
    .evt-content {
      display: flex;
      flex-direction: column;
      gap: 1px;
    }
    .evt-body {
      font-size: 0.85em;
    }
    .evt-body b {
      color: var(--primary-text-color);
    }
    .evt-detail {
      font-size: 0.78em;
      color: var(--secondary-text-color);
      line-height: 1.3;
    }
    .evt-detail b {
      color: var(--primary-text-color);
    }
    .msg,
    .no-events {
      padding: 16px;
      color: var(--secondary-text-color);
      font-size: 0.9em;
    }
    .no-events {
      font-style: italic;
    }
    .error {
      color: var(--error-color, #db4437);
    }
    .error-detail {
      margin-top: 6px;
      font-size: 0.85em;
      color: var(--secondary-text-color);
    }
    .pagination {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 8px 16px;
      border-top: 1px solid var(--divider-color, rgba(255, 255, 255, 0.06));
      font-size: 0.8em;
      color: var(--secondary-text-color);
    }
    .pg-btn {
      background: none;
      border: 1px solid var(--divider-color, rgba(255, 255, 255, 0.15));
      border-radius: 4px;
      color: var(--primary-text-color);
      cursor: pointer;
      font-size: 1em;
      padding: 2px 10px;
      font-family: inherit;
    }
    .pg-btn:disabled {
      opacity: 0.3;
      cursor: default;
    }
    .pg-btn:not(:disabled):hover,
    .pg-btn:not(:disabled):focus-visible {
      border-color: var(--primary-color, #009ac7);
    }
  `;

  setConfig(config) {
    if (!config?.entity) throw new Error("entity required");
    const shownTypes = Array.isArray(config.shown_types) ? config.shown_types : null;
    const unknown = shownTypes ? shownTypes.filter((t) => !TYPE_KEYS.has(t)) : [];
    if (unknown.length) {
      console.warn(`[${CARD_TYPE}] Ignoring unknown event type(s): ${unknown.join(", ")}`);
    }
    const visibleTypes = shownTypes
      ? EVENT_TYPES.filter((t) => shownTypes.includes(t.type))
      : EVENT_TYPES;
    const maxHeight = Number(config.max_height);

    this._config = {
      entity: config.entity,
      title: config.title ?? "Network Events",
      shown_types: shownTypes,
      max_height: Number.isFinite(maxHeight) ? maxHeight : 560,
      show_search: config.show_search ?? true,
      show_filters: config.show_filters ?? true,
    };
    this._visibleTypes = visibleTypes;
    this._activeTypes = new Set(visibleTypes.map((t) => t.type));
    this._lastUpdated = null;
    this._status = "loading";
    this._message = "";
    this._page = 1;
    if (!this._config.show_search) this._textFilter = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const state = hass.states[this._config.entity];
    if (!state) {
      this._setStatus("unavailable", `Entity not found: ${this._config.entity}`);
      return;
    }
    if (["unavailable", "unknown"].includes(state.state)) {
      this._setStatus("unavailable", `${this._config.entity} is ${state.state}`);
      return;
    }
    if (state.last_updated === this._lastUpdated && this._status === "ready") return;
    this._lastUpdated = state.last_updated;
    this._refreshEvents(state.entity_id);
  }

  _setStatus(status, message = "") {
    this._status = status;
    this._message = message;
  }

  async _refreshEvents(entityId) {
    const requestSeq = ++this._requestSeq;
    if (this._status !== "ready") this._setStatus("loading");
    try {
      const result = await this._hass.callWS({
        type: WS_TYPE_RECENT_EVENTS,
        entity_id: entityId,
      });
      if (requestSeq !== this._requestSeq) return;
      this._events = [...(result?.events ?? [])].reverse();
      this._page = 1;
      this._setStatus("ready");
    } catch (e) {
      if (requestSeq !== this._requestSeq) return;
      this._setStatus("error", String(e?.message ?? e));
    }
  }

  render() {
    if (!this._config) return nothing;
    return html`
      <ha-card @keydown=${this._onCardKeydown}>
        <div class="header">
          <div class="title">${this._config.title}</div>
        </div>
        ${this._renderContent()}
      </ha-card>
    `;
  }

  _renderContent() {
    if (this._status === "loading") {
      return html`<div class="msg" aria-live="polite">Loading events…</div>`;
    }
    if (this._status === "error") {
      return html`<div class="msg error" aria-live="assertive">
        <b>Network Events Card error</b>
        <div class="error-detail">${this._message}</div>
      </div>`;
    }
    if (this._status === "unavailable") {
      return html`<div class="msg" aria-live="polite">${this._message}</div>`;
    }

    const filtered = this._filteredEvents();
    const totalPages = Math.max(1, Math.ceil(filtered.length / this._perPage));
    const page = Math.min(this._page, totalPages);
    const start = (page - 1) * this._perPage;
    const visible = filtered.slice(start, start + this._perPage);
    const groups = this._groupEvents(visible);
    const wrapStyle = this._config.max_height > 0 ? `max-height: ${this._config.max_height}px` : "";

    return html`
      ${this._renderControls()}
      <div class="events-wrap" style=${wrapStyle} role="list" aria-label="Network events">
        ${
          groups.length
            ? groups.map((group) => this._renderGroup(group))
            : html`<div class="no-events" aria-live="polite">No events match the current filters.</div>`
        }
      </div>
      ${totalPages > 1 ? this._renderPagination(page, totalPages) : nothing}
    `;
  }

  _renderControls() {
    if (!this._config.show_search && !this._config.show_filters) return nothing;
    return html`<div class="controls">
      ${
        this._config.show_search
          ? html`<div class="search">
            <ha-icon icon="mdi:magnify" aria-hidden="true"></ha-icon>
            <input
              class="search-input"
              type="text"
              aria-label="Search network events"
              placeholder="Search…"
              .value=${this._textFilter}
              @input=${this._onSearchInput}
            />
            ${
              this._textFilter
                ? html`<button
                  class="search-clear"
                  type="button"
                  title="Clear search"
                  aria-label="Clear search"
                  @click=${this._clearSearch}
                >
                  ×
                </button>`
                : nothing
            }
          </div>`
          : nothing
      }
      ${
        this._config.show_filters
          ? html`<div class="type-btns" aria-label="Event type filters">
            ${this._visibleTypes.map((type) => this._renderTypeButton(type))}
            <span class="sel-divider" aria-hidden="true">|</span>
            <button class="sel-btn" type="button" @click=${this._selectAllTypes}>All</button>
            <button class="sel-btn" type="button" @click=${this._selectNoTypes}>None</button>
          </div>`
          : nothing
      }
    </div>`;
  }

  _renderTypeButton(type) {
    const active = this._activeTypes.has(type.type);
    const style = `--evt-color: ${eventColor(type.type)}`;
    return html`<button
      class=${active ? "type-btn" : "type-btn inactive"}
      style=${style}
      type="button"
      data-type=${type.type}
      aria-pressed=${active ? "true" : "false"}
      @click=${() => this._toggleType(type.type)}
    >
      ${type.label}
    </button>`;
  }

  _renderGroup(group) {
    return html`<div class="date-group" data-date=${group.date}>
      <div class="date-label">
        ${this._dateLabel(group.date)}
        <span class="date-count">${group.events.length}</span>
      </div>
      ${group.events.map((event) => this._renderEvent(event))}
    </div>`;
  }

  _renderEvent(event) {
    const info = TYPE_MAP[event.type] ?? {
      icon: "·",
      color: "var(--secondary-text-color)",
      label: event.type,
    };
    const time = event.ts?.slice(11, 19) ?? "";
    const { primary, detail } = this._formatBody(event);
    const style = `--evt-color: ${eventColor(event.type)}`;
    return html`<div
      class="event"
      style=${style}
      role="listitem"
      data-type=${event.type}
      aria-label=${`${info.label} event at ${time}`}
    >
      <span class="evt-icon" role="img" aria-label=${info.label}>${info.icon}</span>
      <span class="evt-time">${time}</span>
      <div class="evt-content">
        <div class="evt-body">${primary}</div>
        ${detail ? html`<div class="evt-detail">${detail}</div>` : nothing}
      </div>
    </div>`;
  }

  _renderPagination(page, totalPages) {
    return html`<div class="pagination" aria-live="polite">
      <button
        class="pg-btn"
        type="button"
        aria-label="Previous page"
        ?disabled=${page <= 1}
        @click=${this._prevPage}
      >
        ‹
      </button>
      <span id="pg-info">Page ${page} of ${totalPages}</span>
      <button
        class="pg-btn"
        type="button"
        aria-label="Next page"
        ?disabled=${page >= totalPages}
        @click=${this._nextPage}
      >
        ›
      </button>
    </div>`;
  }

  _groupEvents(events) {
    const groups = [];
    let lastDate = null;
    for (const event of events) {
      const date = event.ts?.slice(0, 10) ?? "";
      if (date !== lastDate) {
        groups.push({ date, events: [] });
        lastDate = date;
      }
      groups[groups.length - 1].events.push(event);
    }
    return groups;
  }

  _filteredEvents() {
    const text = this._textFilter.toLowerCase();
    if (this._activeTypes.size === 0) return [];
    return this._events.filter((event) => {
      if (!this._activeTypes.has(event.type)) return false;
      return !text || this._searchString(event).includes(text);
    });
  }

  // ── Formatting ──────────────────────────────────────────────────────────────

  _name(event) {
    if (event.hostname) return event.hostname;
    if (event.vendor) return `${event.vendor} (${event.mac})`;
    return event.mac ?? "?";
  }

  _fmtDur(secs) {
    if (secs == null) return null;
    if (secs >= 3600) return `${Math.floor(secs / 3600)}h\u202f${Math.floor((secs % 3600) / 60)}m`;
    if (secs >= 60) return `${Math.floor(secs / 60)}m\u202f${secs % 60}s`;
    return `${secs}s`;
  }

  _fmtConnInfo(event) {
    const parts = [];
    if (event.channel != null) parts.push(`Ch.\u202f${event.channel}`);
    if (event.band) parts.push(event.band);
    if (event.signal != null) parts.push(`${event.signal}\u202fdBm`);
    return parts.join(" · ");
  }

  _detail(parts) {
    const clean = parts.filter((part) => part !== null && part !== undefined && part !== "");
    if (!clean.length) return null;
    return clean.map((part, index) => html`${index > 0 ? " · " : nothing}${part}`);
  }

  _formatBody(event) {
    const name = this._name(event);

    switch (event.type) {
      case "connect":
        return {
          primary: html`<b>${name}</b> connected`,
          detail: this._detail([
            event.ap && event.connection === "wifi" ? html`<b>${event.ap}</b>` : null,
            event.essid,
            this._fmtConnInfo(event),
            event.ip ? `IP: ${event.ip}` : null,
          ]),
        };

      case "disconnect": {
        const up = event.tx_total != null ? fmtBytes(event.tx_total) : "?";
        const down = event.rx_total != null ? fmtBytes(event.rx_total) : "?";
        return {
          primary: html`<b>${name}</b> disconnected`,
          detail: this._detail([
            event.ap && event.connection === "wifi" ? html`<b>${event.ap}</b>` : null,
            event.band,
            this._fmtDur(event.duration) ? `Time: ${this._fmtDur(event.duration)}` : null,
            event.signal != null ? `${event.signal}\u202fdBm` : null,
            event.tx_total != null || event.rx_total != null ? `↑ ${up} / ↓ ${down}` : null,
          ]),
        };
      }

      case "roam":
        return {
          primary: html`<b>${name}</b> roamed <b>${event.from_ap}</b> → <b>${event.ap}</b>`,
          detail: this._detail([
            this._fmtConnInfo(event),
            event.from_signal != null && event.signal != null
              ? `${event.from_signal} → ${event.signal}\u202fdBm`
              : null,
          ]),
        };

      case "band_change":
        return {
          primary: html`<b>${name}</b> band ${event.from_band} → ${event.band}`,
          detail: this._detail([
            event.ap ? html`<b>${event.ap}</b>` : null,
            event.channel != null ? `Ch.\u202f${event.channel}` : null,
            event.signal != null ? `${event.signal}\u202fdBm` : null,
          ]),
        };

      case "new_device":
        return {
          primary: html`<b>${name}</b> — new device`,
          detail: this._detail([
            event.ip ? `IP: ${event.ip}` : null,
            event.ip6 ? `IPv6: ${event.ip6}` : null,
            event.vendor && event.vendor !== name ? event.vendor : null,
            event.connection ? (event.connection === "wifi" ? "Wi-Fi" : "Wired") : null,
          ]),
        };

      case "hostname_change":
        return {
          primary: html`<b>${event.from_hostname}</b> renamed → <b>${name}</b>`,
          detail: event.ip ? html`IP: ${event.ip}` : null,
        };

      case "ip_change":
        return {
          primary: html`<b>${name}</b> IPv4 changed`,
          detail: html`${event.from_ip} → ${event.ip}`,
        };

      case "ip6_change":
        return {
          primary: html`<b>${name}</b> IPv6 changed`,
          detail: html`${event.from_ip6} → ${event.ip6}`,
        };

      case "ap_online":
        return {
          primary: html`AP <b>${name}</b> online`,
          detail: event.ip ? html`IP: ${event.ip}` : null,
        };

      case "ap_offline":
        return {
          primary: html`AP <b>${name}</b> offline`,
          detail: this._fmtDur(event.duration)
            ? html`Uptime: ${this._fmtDur(event.duration)}`
            : null,
        };

      case "wan_online":
        return {
          primary: html`WAN online`,
          detail: event.ip ? html`IP: <b>${event.ip}</b>` : null,
        };

      case "wan_offline":
        return { primary: html`WAN offline`, detail: null };

      case "wan_ip_change":
        return {
          primary: html`WAN IP changed`,
          detail: html`${event.from_ip} → <b>${event.ip}</b>`,
        };

      case "wan_ip6_change":
        return {
          primary: html`WAN IPv6 changed`,
          detail: html`${event.from_ip6} → <b>${event.ip6}</b>`,
        };

      default:
        return { primary: html`<b>${name}</b> ${event.type}`, detail: null };
    }
  }

  _dateLabel(dateStr) {
    const today = new Date().toISOString().slice(0, 10);
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    if (dateStr === today) return "Today";
    if (dateStr === yesterday) return "Yesterday";
    return dateStr;
  }

  _searchString(event) {
    return [
      event.hostname,
      event.vendor,
      event.mac,
      event.ip,
      event.ip6,
      event.ap,
      event.from_ap,
      event.from_hostname,
      event.from_ip,
      event.essid,
      event.type,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
  }

  _onSearchInput(event) {
    this._textFilter = event.target.value;
    this._page = 1;
  }

  _clearSearch() {
    this._textFilter = "";
    this._page = 1;
  }

  _toggleType(type) {
    const activeTypes = new Set(this._activeTypes);
    if (activeTypes.has(type)) {
      activeTypes.delete(type);
    } else {
      activeTypes.add(type);
    }
    this._activeTypes = activeTypes;
    this._page = 1;
  }

  _selectAllTypes() {
    this._activeTypes = new Set(this._visibleTypes.map((type) => type.type));
    this._page = 1;
  }

  _selectNoTypes() {
    this._activeTypes = new Set();
    this._page = 1;
  }

  _prevPage() {
    if (this._page > 1) this._page -= 1;
  }

  _nextPage() {
    this._page += 1;
  }

  _resetFilters() {
    this._textFilter = "";
    this._activeTypes = new Set(this._visibleTypes.map((type) => type.type));
    this._page = 1;
  }

  _onCardKeydown(event) {
    if (event.key !== "Escape" || isInputTarget(event.target)) return;
    if (this._textFilter || this._activeTypes.size !== this._visibleTypes.length) {
      event.stopPropagation();
      this._resetFilters();
    }
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }

  static getStubConfig() {
    return { entity: "", title: "Network Events" };
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

// ── Visual editor ─────────────────────────────────────────────────────────────

class NetworkEventsCardEditor extends LitElement {
  static properties = {
    hass: { attribute: false },
    _config: { state: true },
  };

  setConfig(config) {
    this._config = { ...config };
  }

  static styles = css`
    .form {
      display: flex;
      flex-direction: column;
      gap: 16px;
      padding: 16px 0;
    }
    ha-form {
      display: block;
    }
    .section-label {
      font-size: 0.8em;
      color: var(--secondary-text-color);
      margin-bottom: 6px;
    }
    .type-checks {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .type-check {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 0.85em;
      cursor: pointer;
      color: var(--evt-color);
    }
    .type-check input {
      cursor: pointer;
    }
  `;

  _schema() {
    return [
      {
        name: "entity",
        required: true,
        selector: { entity: { domain: "sensor" } },
      },
      { name: "title", selector: { text: {} } },
      {
        name: "max_height",
        selector: { number: { min: 0, mode: "box" } },
      },
      { name: "show_search", selector: { boolean: {} } },
      { name: "show_filters", selector: { boolean: {} } },
    ];
  }

  _computeLabel = (schema) => {
    const labels = {
      entity: "Entity",
      title: "Title",
      max_height: "Max list height (px, 0 = fill)",
      show_search: "Show search box",
      show_filters: "Show filter buttons",
    };
    return labels[schema.name] ?? schema.name;
  };

  _valueChanged = (event) => {
    this._fire({ ...this._config, ...event.detail.value });
  };

  _fire(config) {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      }),
    );
  }

  render() {
    if (!this.hass || !this._config) return nothing;
    const shownTypes = Array.isArray(this._config.shown_types)
      ? this._config.shown_types
      : EVENT_TYPES.map((type) => type.type);
    const data = {
      entity: this._config.entity ?? "",
      title: this._config.title ?? "Network Events",
      max_height: this._config.max_height ?? 560,
      show_search: this._config.show_search ?? true,
      show_filters: this._config.show_filters ?? true,
    };

    return html`<div class="form">
      <ha-form
        .hass=${this.hass}
        .data=${data}
        .schema=${this._schema()}
        .computeLabel=${this._computeLabel}
        @value-changed=${this._valueChanged}
      ></ha-form>
      <div>
        <div class="section-label">Filter buttons</div>
        <div class="type-checks">
          ${EVENT_TYPES.map((type) => this._renderTypeCheckbox(type, shownTypes))}
        </div>
      </div>
    </div>`;
  }

  _renderTypeCheckbox(type, shownTypes) {
    const checked = shownTypes.includes(type.type);
    return html`<label class="type-check" style=${`--evt-color: ${eventColor(type.type)}`}>
      <input
        type="checkbox"
        data-type=${type.type}
        .checked=${checked}
        @change=${this._shownTypesChanged}
      />
      <span>${type.label}</span>
    </label>`;
  }

  _shownTypesChanged() {
    const checked = [...this.renderRoot.querySelectorAll(".type-check input")]
      .filter((element) => element.checked)
      .map((element) => element.dataset.type);
    const newConfig = { ...this._config };
    if (checked.length === EVENT_TYPES.length) {
      delete newConfig.shown_types;
    } else {
      newConfig.shown_types = checked;
    }
    this._fire(newConfig);
  }
}

customElements.define(EDITOR_TYPE, NetworkEventsCardEditor);
customElements.define(CARD_TYPE, NetworkEventsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TYPE,
  name: "Network Events",
  description: "Filterable network event log from a wrtsensor network_scanner entity",
});

console.info(
  `%c NETWORK-EVENTS-CARD %c v${CARD_VERSION} `,
  "color: white; background: #009ac7; font-weight: 700;",
  "color: #009ac7; background: white; font-weight: 700;",
);
