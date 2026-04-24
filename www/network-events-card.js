function fmtBytes(bytes) {
  if (bytes == null) return "—";
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(2)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes >= 1e3) return `${Math.round(bytes / 1e3)} KB`;
  return `${bytes} B`;
}

function _esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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
const WS_TYPE_RECENT_EVENTS = "wrtsensor/recent_events";

// ── Card ──────────────────────────────────────────────────────────────────────

class NetworkEventsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._events = [];
    this._lastUpdated = null;
    this._rendered = false;
    this._textFilter = "";
    this._activeTypes = new Set(EVENT_TYPES.map((t) => t.type));
    this._visibleTypes = EVENT_TYPES;
    this._page = 1;
    this._perPage = 50;
    this._requestSeq = 0;
  }

  connectedCallback() {
    this._escHandler = (e) => {
      if (e.key !== "Escape") return;
      if (this.shadowRoot.activeElement?.tagName === "INPUT") return;
      if (this._textFilter || this._activeTypes.size !== this._visibleTypes.length)
        this._resetFilters();
    };
    document.addEventListener("keydown", this._escHandler);
  }

  disconnectedCallback() {
    if (this._escHandler) document.removeEventListener("keydown", this._escHandler);
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity required");
    const visibleTypes =
      config.shown_types && config.shown_types.length > 0
        ? EVENT_TYPES.filter((t) => config.shown_types.includes(t.type))
        : EVENT_TYPES;
    this._config = {
      entity: config.entity,
      title: config.title ?? "Network Events",
      shown_types: config.shown_types ?? null,
      max_height: Number.isFinite(config.max_height) ? config.max_height : 560,
      show_search: config.show_search ?? true,
      show_filters: config.show_filters ?? true,
    };
    this._visibleTypes = visibleTypes;
    this._lastUpdated = null;
    this._rendered = false;
    this._activeTypes = new Set(visibleTypes.map((t) => t.type));
    this._page = 1;
    if (!this._config.show_search) this._textFilter = "";
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
    if (state.last_updated === this._lastUpdated && this._rendered) return;
    this._lastUpdated = state.last_updated;
    this._refreshEvents(state.entity_id);
  }

  async _refreshEvents(entityId) {
    const requestSeq = ++this._requestSeq;
    if (!this._rendered) this._renderLoading();
    try {
      const result = await this._hass.callWS({
        type: WS_TYPE_RECENT_EVENTS,
        entity_id: entityId,
      });
      if (requestSeq !== this._requestSeq) return;
      this._events = [...(result?.events ?? [])].reverse();
      this._render();
    } catch (e) {
      if (requestSeq !== this._requestSeq) return;
      this._renderError(String(e?.message ?? e));
    }
  }

  // ── Formatting ──────────────────────────────────────────────────────────────

  _name(e) {
    if (e.hostname) return e.hostname;
    if (e.vendor) return `${e.vendor} (${e.mac})`;
    return e.mac ?? "?";
  }

  _fmtDur(secs) {
    if (secs == null) return null;
    if (secs >= 3600) return `${Math.floor(secs / 3600)}h\u202f${Math.floor((secs % 3600) / 60)}m`;
    if (secs >= 60) return `${Math.floor(secs / 60)}m\u202f${secs % 60}s`;
    return `${secs}s`;
  }

  _fmtConnInfo(e) {
    // "Ch. 36 · 5 GHz · -58 dBm"
    const parts = [];
    if (e.channel != null) parts.push(`Ch.\u202f${e.channel}`);
    if (e.band) parts.push(_esc(e.band));
    if (e.signal != null) parts.push(`${e.signal}\u202fdBm`);
    return parts.join(" · ");
  }

  // Returns { primary: html-string, detail: html-string|null }
  _formatBody(e) {
    const name = this._name(e);

    switch (e.type) {
      case "connect": {
        const primary = `<b>${_esc(name)}</b> connected`;
        const detail = [];
        if (e.ap && e.connection === "wifi") detail.push(`<b>${_esc(e.ap)}</b>`);
        if (e.essid) detail.push(_esc(e.essid));
        const ci = this._fmtConnInfo(e);
        if (ci) detail.push(ci);
        if (e.ip) detail.push(`IP: ${_esc(e.ip)}`);
        return { primary, detail: detail.join(" · ") || null };
      }

      case "disconnect": {
        const primary = `<b>${_esc(name)}</b> disconnected`;
        const detail = [];
        if (e.ap && e.connection === "wifi") detail.push(`<b>${_esc(e.ap)}</b>`);
        if (e.band) detail.push(_esc(e.band));
        const dur = this._fmtDur(e.duration);
        if (dur) detail.push(`Time: ${dur}`);
        if (e.signal != null) detail.push(`${e.signal}\u202fdBm`);
        if (e.tx_total != null || e.rx_total != null) {
          const up = e.tx_total != null ? fmtBytes(e.tx_total) : "?";
          const down = e.rx_total != null ? fmtBytes(e.rx_total) : "?";
          detail.push(`↑ ${up} / ↓ ${down}`);
        }
        return { primary, detail: detail.join(" · ") || null };
      }

      case "roam": {
        const primary = `<b>${_esc(name)}</b> roamed <b>${_esc(e.from_ap)}</b> → <b>${_esc(e.ap)}</b>`;
        const detail = [];
        const ci = this._fmtConnInfo(e);
        if (ci) detail.push(ci);
        if (e.from_signal != null && e.signal != null)
          detail.push(`${e.from_signal} → ${e.signal}\u202fdBm`);
        return { primary, detail: detail.join(" · ") || null };
      }

      case "band_change": {
        const primary = `<b>${_esc(name)}</b> band ${_esc(e.from_band)} → ${_esc(e.band)}`;
        const detail = [];
        if (e.ap) detail.push(`<b>${_esc(e.ap)}</b>`);
        if (e.channel != null) detail.push(`Ch.\u202f${e.channel}`);
        if (e.signal != null) detail.push(`${e.signal}\u202fdBm`);
        return { primary, detail: detail.join(" · ") || null };
      }

      case "new_device": {
        const primary = `<b>${_esc(name)}</b> — new device`;
        const detail = [];
        if (e.ip) detail.push(`IP: ${_esc(e.ip)}`);
        if (e.ip6) detail.push(`IPv6: ${_esc(e.ip6)}`);
        if (e.vendor && e.vendor !== name) detail.push(_esc(e.vendor));
        if (e.connection) detail.push(e.connection === "wifi" ? "Wi-Fi" : "Wired");
        return { primary, detail: detail.join(" · ") || null };
      }

      case "hostname_change":
        return {
          primary: `<b>${_esc(e.from_hostname)}</b> renamed → <b>${_esc(name)}</b>`,
          detail: e.ip ? `IP: ${_esc(e.ip)}` : null,
        };

      case "ip_change":
        return {
          primary: `<b>${_esc(name)}</b> IPv4 changed`,
          detail: `${_esc(e.from_ip)} → ${_esc(e.ip)}`,
        };

      case "ip6_change":
        return {
          primary: `<b>${_esc(name)}</b> IPv6 changed`,
          detail: `${_esc(e.from_ip6)} → ${_esc(e.ip6)}`,
        };

      case "ap_online":
        return {
          primary: `AP <b>${_esc(name)}</b> online`,
          detail: e.ip ? `IP: ${_esc(e.ip)}` : null,
        };

      case "ap_offline": {
        const dur = this._fmtDur(e.duration);
        return {
          primary: `AP <b>${_esc(name)}</b> offline`,
          detail: dur ? `Uptime: ${dur}` : null,
        };
      }

      case "wan_online":
        return {
          primary: `WAN online`,
          detail: e.ip ? `IP: <b>${_esc(e.ip)}</b>` : null,
        };

      case "wan_offline":
        return { primary: `WAN offline`, detail: null };

      case "wan_ip_change":
        return {
          primary: `WAN IP changed`,
          detail: `${_esc(e.from_ip)} → <b>${_esc(e.ip)}</b>`,
        };

      case "wan_ip6_change":
        return {
          primary: `WAN IPv6 changed`,
          detail: `${_esc(e.from_ip6)} → <b>${_esc(e.ip6)}</b>`,
        };

      default:
        return { primary: `<b>${_esc(name)}</b> ${_esc(e.type)}`, detail: null };
    }
  }

  _dateLabel(dateStr) {
    const today = new Date().toISOString().slice(0, 10);
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    if (dateStr === today) return "Today";
    if (dateStr === yesterday) return "Yesterday";
    return dateStr;
  }

  _searchString(e) {
    return [
      e.hostname,
      e.vendor,
      e.mac,
      e.ip,
      e.ip6,
      e.ap,
      e.from_ap,
      e.from_hostname,
      e.from_ip,
      e.essid,
      e.type,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  _render() {
    const prevScroll = this.shadowRoot.querySelector(".events-wrap")?.scrollTop ?? 0;

    // Group events by date
    const groups = [];
    let lastDate = null;
    for (const e of this._events) {
      const date = e.ts.slice(0, 10);
      if (date !== lastDate) {
        groups.push({ date, events: [] });
        lastDate = date;
      }
      groups[groups.length - 1].events.push(e);
    }

    const groupsHTML = groups
      .map((g) => {
        const eventsHTML = g.events
          .map((e) => {
            const info = TYPE_MAP[e.type] ?? { icon: "·", color: "#888" };
            const time = e.ts.slice(11, 19); // HH:MM:SS
            const { primary, detail } = this._formatBody(e);
            const detailHTML = detail ? `<div class="evt-detail">${detail}</div>` : "";
            return `<div class="event" data-type="${_esc(e.type)}" data-search="${_esc(this._searchString(e))}">
          <span class="evt-icon" style="color:${info.color}">${info.icon}</span>
          <span class="evt-time">${_esc(time)}</span>
          <div class="evt-content">
            <div class="evt-body">${primary}</div>
            ${detailHTML}
          </div>
        </div>`;
          })
          .join("");
        return `<div class="date-group" data-date="${_esc(g.date)}">
        <div class="date-label">${_esc(this._dateLabel(g.date))} <span class="date-count">${g.events.length}</span></div>
        ${eventsHTML}
      </div>`;
      })
      .join("");

    const typeBtns = this._visibleTypes
      .map(
        (t) =>
          `<button class="type-btn" data-type="${t.type}" data-color="${t.color}">${t.label}</button>`,
      )
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; height: 100%; user-select: text; -webkit-user-select: text; }
        ha-card { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
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
          font-weight: normal;
          color: var(--ha-card-header-color, var(--primary-text-color));
          line-height: 32px;
          letter-spacing: -0.012em;
        }
        .controls {
          padding: 4px 16px 8px;
          display: flex;
          flex-direction: column;
          gap: 6px;
          border-bottom: 1px solid var(--divider-color, rgba(255,255,255,0.1));
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
        .search-input::placeholder { color: var(--secondary-text-color); }
        .search-clear {
          background: none;
          border: none;
          color: var(--secondary-text-color);
          cursor: pointer;
          padding: 2px;
          display: none;
          line-height: 1;
        }
        .search-clear:hover { color: var(--primary-text-color); }
        .search-clear.visible { display: flex; }
        .type-btns { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
        .type-btn {
          background: transparent;
          border-radius: 12px;
          font-size: 0.75em;
          padding: 2px 8px;
          cursor: pointer;
          font-family: inherit;
          transition: opacity 0.15s;
        }
        .type-btn.inactive { opacity: 0.3; }
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
        .sel-btn:hover { color: var(--primary-text-color); }
        .sel-divider {
          color: var(--secondary-text-color);
          font-size: 0.75em;
          opacity: 0.4;
          user-select: none;
        }
        .events-wrap {
          overflow-y: auto;
          ${this._config.max_height > 0 ? `max-height: ${this._config.max_height}px;` : ""}
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
          border-bottom: 1px solid var(--divider-color, rgba(255,255,255,0.08));
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
          border-bottom: 1px solid rgba(255,255,255,0.03);
        }
        .evt-icon { text-align: center; padding-top: 1px; }
        .evt-time {
          font-family: monospace;
          color: var(--secondary-text-color);
          font-size: 0.8em;
          padding-top: 2px;
        }
        .evt-content { display: flex; flex-direction: column; gap: 1px; }
        .evt-body { font-size: 0.85em; }
        .evt-body b { color: var(--primary-text-color); }
        .evt-detail {
          font-size: 0.78em;
          color: var(--secondary-text-color);
          line-height: 1.3;
        }
        .evt-detail b { color: var(--primary-text-color); }
        .no-events {
          padding: 16px;
          color: var(--secondary-text-color);
          font-style: italic;
          font-size: 0.9em;
        }
        .pagination {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          padding: 8px 16px;
          border-top: 1px solid var(--divider-color, rgba(255,255,255,0.06));
          font-size: 0.8em;
          color: var(--secondary-text-color);
        }
        .pg-btn {
          background: none;
          border: 1px solid var(--divider-color, rgba(255,255,255,0.15));
          border-radius: 4px;
          color: var(--primary-text-color);
          cursor: pointer;
          font-size: 1em;
          padding: 2px 10px;
          font-family: inherit;
        }
        .pg-btn:disabled { opacity: 0.3; cursor: default; }
        .pg-btn:not(:disabled):hover { border-color: var(--primary-color, #009ac7); }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">${_esc(this._config.title)}</div>
        </div>
        ${
          this._config.show_search || this._config.show_filters
            ? `<div class="controls">
          ${
            this._config.show_search
              ? `<div class="search">
            <ha-icon icon="mdi:magnify"></ha-icon>
            <input class="search-input" type="text" placeholder="Search…" value="${_esc(this._textFilter)}">
            <button class="search-clear${this._textFilter ? " visible" : ""}" title="Clear search">×</button>
          </div>`
              : ""
          }
          ${
            this._config.show_filters
              ? `<div class="type-btns">
            ${typeBtns}
            <span class="sel-divider">|</span>
            <button class="sel-btn" id="select-all">All</button>
            <button class="sel-btn" id="select-none">None</button>
          </div>`
              : ""
          }
        </div>`
            : ""
        }
        <div class="events-wrap">
          ${groupsHTML || '<div class="no-events">No events recorded yet.</div>'}
        </div>
        <div class="pagination" id="evt-pagination" style="display:none">
          <button class="pg-btn" id="pg-prev">&#8249;</button>
          <span id="pg-info"></span>
          <button class="pg-btn" id="pg-next">&#8250;</button>
        </div>
      </ha-card>`;

    this._rendered = true;
    this._styleTypeButtons();

    this.shadowRoot.querySelector(".events-wrap").scrollTop = prevScroll;

    const searchInput = this.shadowRoot.querySelector(".search-input");
    const clearBtn = this.shadowRoot.querySelector(".search-clear");

    if (searchInput && clearBtn) {
      searchInput.addEventListener("input", (e) => {
        this._textFilter = e.target.value;
        this._page = 1;
        clearBtn.classList.toggle("visible", !!this._textFilter);
        this._applyFilters();
      });

      searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Escape") this._resetFilters();
      });

      clearBtn.addEventListener("click", () => {
        this._textFilter = "";
        this._page = 1;
        searchInput.value = "";
        clearBtn.classList.remove("visible");
        this._applyFilters();
      });
    }

    this.shadowRoot.querySelectorAll(".type-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const t = btn.dataset.type;
        if (this._activeTypes.has(t)) {
          this._activeTypes.delete(t);
        } else {
          this._activeTypes.add(t);
        }
        this._page = 1;
        this._styleTypeButtons();
        this._applyFilters();
      });
    });

    this.shadowRoot.querySelector("#select-all")?.addEventListener("click", () => {
      this._activeTypes = new Set(this._visibleTypes.map((e) => e.type));
      this._page = 1;
      this._styleTypeButtons();
      this._applyFilters();
    });

    this.shadowRoot.querySelector("#select-none")?.addEventListener("click", () => {
      this._activeTypes = new Set();
      this._page = 1;
      this._styleTypeButtons();
      this._applyFilters();
    });

    this.shadowRoot.querySelector("#pg-prev").addEventListener("click", () => {
      if (this._page > 1) {
        this._page--;
        this._applyFilters();
      }
    });
    this.shadowRoot.querySelector("#pg-next").addEventListener("click", () => {
      this._page++;
      this._applyFilters();
    });

    this._applyFilters();
  }

  _resetFilters() {
    this._textFilter = "";
    this._activeTypes = new Set(this._visibleTypes.map((t) => t.type));
    this._page = 1;
    this._render();
  }

  _styleTypeButtons() {
    this.shadowRoot.querySelectorAll(".type-btn").forEach((btn) => {
      const active = this._activeTypes.has(btn.dataset.type);
      btn.classList.toggle("inactive", !active);
      btn.style.border = `1px solid ${btn.dataset.color}`;
      btn.style.color = btn.dataset.color;
      btn.style.background = active ? `${btn.dataset.color}22` : "transparent";
    });
  }

  _applyFilters() {
    const text = this._textFilter.toLowerCase();
    const noneOn = this._activeTypes.size === 0;

    const matching = [];
    this.shadowRoot.querySelectorAll(".event").forEach((ev) => {
      const match =
        !noneOn &&
        this._activeTypes.has(ev.dataset.type) &&
        (!text || ev.dataset.search.includes(text));
      ev._matches = match;
      if (match) matching.push(ev);
      ev.style.display = "none";
    });

    const totalPages = Math.max(1, Math.ceil(matching.length / this._perPage));
    if (this._page > totalPages) this._page = totalPages;
    const start = (this._page - 1) * this._perPage;

    matching.slice(start, start + this._perPage).forEach((ev) => {
      ev.style.display = "";
    });

    this.shadowRoot.querySelectorAll(".date-group").forEach((group) => {
      const visible = [...group.querySelectorAll(".event")].filter(
        (ev) => ev.style.display !== "none",
      );
      group.style.display = visible.length ? "" : "none";
      const countEl = group.querySelector(".date-count");
      if (countEl) countEl.textContent = String(visible.length);
    });

    const paginEl = this.shadowRoot.querySelector("#evt-pagination");
    if (paginEl) {
      paginEl.style.display = totalPages > 1 ? "" : "none";
      if (totalPages > 1) {
        paginEl.querySelector("#pg-info").textContent = `${this._page} / ${totalPages}`;
        paginEl.querySelector("#pg-prev").disabled = this._page <= 1;
        paginEl.querySelector("#pg-next").disabled = this._page >= totalPages;
      }
    }
  }

  _renderUnavailable(message) {
    this._lastUpdated = null;
    this._rendered = false;
    this.shadowRoot.innerHTML = `
      <style>:host{display:block}</style>
      <ha-card header="${_esc(this._config?.title ?? "Network Events")}">
        <div style="padding:16px;color:var(--secondary-text-color);font-size:0.9em">${_esc(message)}</div>
      </ha-card>`;
  }

  _renderLoading() {
    this.shadowRoot.innerHTML = `
      <style>:host{display:block}</style>
      <ha-card header="${_esc(this._config?.title ?? "Network Events")}">
        <div style="padding:16px;color:var(--secondary-text-color);font-size:0.9em">Loading events…</div>
      </ha-card>`;
  }

  _renderError(message) {
    this.shadowRoot.innerHTML = `
      <style>:host{display:block}</style>
      <ha-card header="${_esc(this._config?.title ?? "Network Events")}">
        <div style="padding:16px;color:var(--error-color,#db4437)">
          <b>Network Events Card error</b>
          <div style="margin-top:6px;font-size:0.85em;color:var(--secondary-text-color)">${_esc(message)}</div>
        </div>
      </ha-card>`;
  }

  getCardSize() {
    return 6;
  }
  static getConfigElement() {
    return document.createElement("network-events-card-editor");
  }
  static getStubConfig() {
    return { entity: "", title: "Network Events" };
  }
}

// ── Visual editor ─────────────────────────────────────────────────────────────

class NetworkEventsCardEditor extends HTMLElement {
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
    const picker = this.shadowRoot.querySelector("ha-entity-picker");
    if (picker) picker.hass = hass;
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
    const shownTypes = c.shown_types ?? EVENT_TYPES.map((t) => t.type);

    const checkboxesHTML = EVENT_TYPES.map(
      (t) =>
        `<label class="type-check">
          <input type="checkbox" data-type="${t.type}"${shownTypes.includes(t.type) ? " checked" : ""}>
          <span style="color:${t.color}">${t.label}</span>
        </label>`,
    ).join("");

    this.shadowRoot.innerHTML = `
      <style>
        .form { display:flex; flex-direction:column; gap:16px; padding:16px 0; }
        ha-entity-picker, ha-textfield { width:100%; display:block; }
        .section-label {
          font-size: 0.8em;
          color: var(--secondary-text-color);
          margin-bottom: 6px;
        }
        .type-checks { display:flex; flex-wrap:wrap; gap:8px; }
        .type-check {
          display: flex;
          align-items: center;
          gap: 4px;
          font-size: 0.85em;
          cursor: pointer;
        }
        .type-check input { cursor:pointer; }
      </style>
      <div class="form">
        <ha-entity-picker label="Entity" allow-custom-entity></ha-entity-picker>
        <ha-textfield name="title" label="Title" placeholder="Network Events"></ha-textfield>
        <ha-textfield name="max_height" label="Max list height (px, 0 = fill)" type="number" min="0"></ha-textfield>
        <ha-formfield label="Show search box"><ha-switch name="show_search"></ha-switch></ha-formfield>
        <ha-formfield label="Show filter buttons"><ha-switch name="show_filters"></ha-switch></ha-formfield>
        <div>
          <div class="section-label">Filter buttons</div>
          <div class="type-checks">${checkboxesHTML}</div>
        </div>
      </div>`;

    const picker = this.shadowRoot.querySelector("ha-entity-picker");
    picker.value = c.entity ?? "";
    picker.includeDomains = ["sensor"];
    if (this._hass) picker.hass = this._hass;
    picker.addEventListener("value-changed", (e) => {
      this._fire({ ...this._config, entity: e.detail.value });
    });

    const titleField = this.shadowRoot.querySelector('ha-textfield[name="title"]');
    titleField.value = c.title ?? "";
    titleField.addEventListener("input", () => {
      this._fire({ ...this._config, title: titleField.value });
    });

    const maxHeightField = this.shadowRoot.querySelector('ha-textfield[name="max_height"]');
    maxHeightField.value = c.max_height ?? 560;
    maxHeightField.addEventListener("input", () => {
      const v = parseInt(maxHeightField.value, 10);
      this._fire({ ...this._config, max_height: Number.isFinite(v) ? v : 560 });
    });

    const searchSwitch = this.shadowRoot.querySelector('ha-switch[name="show_search"]');
    searchSwitch.checked = c.show_search ?? true;
    searchSwitch.addEventListener("change", () => {
      this._fire({ ...this._config, show_search: searchSwitch.checked });
    });

    const filtersSwitch = this.shadowRoot.querySelector('ha-switch[name="show_filters"]');
    filtersSwitch.checked = c.show_filters ?? true;
    filtersSwitch.addEventListener("change", () => {
      this._fire({ ...this._config, show_filters: filtersSwitch.checked });
    });

    this.shadowRoot.querySelectorAll(".type-check input").forEach((cb) => {
      cb.addEventListener("change", () => {
        const checked = [...this.shadowRoot.querySelectorAll(".type-check input")]
          .filter((el) => el.checked)
          .map((el) => el.dataset.type);
        const newConfig = { ...this._config };
        if (checked.length === EVENT_TYPES.length) {
          delete newConfig.shown_types;
        } else {
          newConfig.shown_types = checked;
        }
        this._fire(newConfig);
      });
    });
  }
}

customElements.define("network-events-card-editor", NetworkEventsCardEditor);
customElements.define("network-events-card", NetworkEventsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "network-events-card",
  name: "Network Events",
  description: "Filterable network event log from a wrtsensor network_scanner entity",
});
