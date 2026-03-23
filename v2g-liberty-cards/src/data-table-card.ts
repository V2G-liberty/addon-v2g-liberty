import { html, LitElement, nothing, TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators';
import { dataTableStyles } from './data-table-card.styles';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { callFunction } from './util/appdaemon';
import { partial } from './util/translate';
import { showSettingsErrorAlertDialog, showResetDatabaseDialog } from './show-dialogs';
import { hasUninitializedEntities } from './util/settings-error-alert';

const tp = partial('data-table');

type Granularity =
  | 'quarter_hours'
  | 'hours'
  | 'days'
  | 'weeks'
  | 'months'
  | 'years';

const GRANULARITIES: Granularity[] = [
  'quarter_hours',
  'hours',
  'days',
  'weeks',
  'months',
  'years',
];

@customElement('v2g-liberty-data-table-card')
export class DataTableCard extends LitElement {
  @state() private _hass: HomeAssistant;
  @state() private _granularity: Granularity = 'days';
  @state() private _viewDate: Date = new Date();
  @state() private _data: any[] = [];
  @state() private _isLoading: boolean = false;
  @state() private _error: string | null = null;
  @state() private _narrowBar = false;
  @state() private _granMenuOpen = false;
  @state() private _firstAvailable: string | null = null;
  @state() private _openTip: string | null = null;
  @state() private _overflowMenuOpen = false;

  private _resizeObserver?: ResizeObserver;
  private _syncHeightHandler?: () => void;
  private _docClickHandler?: (e: MouseEvent) => void;

  setConfig(_config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._checkUninitialisedEntities();
  }

  private _checkUninitialisedEntities() {
    if (hasUninitializedEntities(this._hass)) {
      showSettingsErrorAlertDialog(this);
    }
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._resizeObserver?.disconnect();
    if (this._syncHeightHandler) window.removeEventListener('resize', this._syncHeightHandler);
    if (this._docClickHandler) document.removeEventListener('click', this._docClickHandler);
  }

  protected firstUpdated() {
    const container = this.shadowRoot?.querySelector('.table-container') as HTMLElement;
    if (container) {
      container.addEventListener('scroll', () => {
        container.classList.toggle('scrolled', container.scrollTop > 0);
      });
    }

    // Set table-container max-height from its actual viewport position rather than
    // guessing HA's chrome height via CSS calc(). Measured values are always correct
    // regardless of HA version, theme, view type or number of navigation bars.
    this._syncHeightHandler = () => {
      if (!container) return;
      const top = container.getBoundingClientRect().top;
      if (top <= 0) return; // Not yet positioned in DOM
      // 40px = bar midpoint (bottom:12 + half of ~56px bar height) so the card
      // visually extends halfway behind the floating island.
      const h = Math.max(200, Math.floor(window.innerHeight - top - 40));
      container.style.maxHeight = `${h}px`;
    };

    const syncNarrow = () => {
      this._narrowBar = this.offsetWidth <= 800;
    };

    this._resizeObserver = new ResizeObserver(() => requestAnimationFrame(() => {
      this._syncHeightHandler!();
      syncNarrow();
    }));
    this._resizeObserver.observe(this);
    window.addEventListener('resize', this._syncHeightHandler);
    requestAnimationFrame(syncNarrow); // initial check after layout

    // Close dropdowns when clicking outside the shadow DOM
    this._docClickHandler = (e: MouseEvent) => {
      const path = e.composedPath() as Node[];
      if (this._granMenuOpen) {
        const menu = this.shadowRoot?.querySelector('.gran-menu');
        if (menu && !path.includes(menu)) this._granMenuOpen = false;
      }
      if (this._overflowMenuOpen) {
        const overflow = this.shadowRoot?.querySelector('.overflow-menu');
        if (overflow && !path.includes(overflow)) this._overflowMenuOpen = false;
      }
    };
    document.addEventListener('click', this._docClickHandler);

    this._fetchData();
  }

  // ── Data fetching ─────────────────────────────────────────────

  private async _fetchData() {
    if (!this._hass) return;

    const { start, end } = this._getViewWindow();

    this._isLoading = true;
    this._error = null;
    this._data = [];

    try {
      const result = await callFunction(
        this._hass,
        'v2g_data_query',
        { start, end, granularity: this._granularity },
        30000
      );

      if (result.error) {
        this._error = tp('error');
        this._data = [];
      } else {
        this._data = (result.data || []).slice().reverse();
        this._error = null;
        if (result.first_available) this._firstAvailable = result.first_available;
      }
    } catch (e) {
      const isTimeout = e instanceof Error && e.message.includes('timed out');
      this._error = isTimeout ? tp('error-timeout') : tp('error');
      this._data = [];
    } finally {
      this._isLoading = false;
    }
  }

  // ── View window calculation ───────────────────────────────────

  private _getViewWindow(): { start: string; end: string } {
    const d = this._viewDate;
    let start: Date;
    let end: Date;

    switch (this._granularity) {
      case 'quarter_hours':
      case 'hours':
        start = new Date(d.getFullYear(), d.getMonth(), d.getDate());
        end = new Date(start);
        end.setDate(end.getDate() + 1);
        break;
      case 'days':
        start = new Date(d.getFullYear(), d.getMonth(), 1);
        end = new Date(d.getFullYear(), d.getMonth() + 1, 1);
        break;
      case 'weeks':
        // Quarter: floor to quarter start
        const quarterMonth = Math.floor(d.getMonth() / 3) * 3;
        start = new Date(d.getFullYear(), quarterMonth, 1);
        end = new Date(d.getFullYear(), quarterMonth + 3, 1);
        break;
      case 'months':
        start = new Date(d.getFullYear(), 0, 1);
        end = new Date(d.getFullYear() + 1, 0, 1);
        break;
      case 'years':
        start = new Date(2020, 0, 1);
        end = new Date();
        end.setDate(end.getDate() + 1);
        break;
    }

    return {
      start: this._toLocalIso(start!),
      end: this._toLocalIso(end!),
    };
  }

  private _toLocalIso(date: Date): string {
    const offset = -date.getTimezoneOffset();
    const sign = offset >= 0 ? '+' : '-';
    const absOffset = Math.abs(offset);
    const hh = String(Math.floor(absOffset / 60)).padStart(2, '0');
    const mm = String(absOffset % 60).padStart(2, '0');
    const y = date.getFullYear();
    const mo = String(date.getMonth() + 1).padStart(2, '0');
    const da = String(date.getDate()).padStart(2, '0');
    const h = String(date.getHours()).padStart(2, '0');
    const mi = String(date.getMinutes()).padStart(2, '0');
    const s = String(date.getSeconds()).padStart(2, '0');
    return `${y}-${mo}-${da}T${h}:${mi}:${s}${sign}${hh}:${mm}`;
  }

  // ── Navigation ────────────────────────────────────────────────

  private _navigate(direction: number) {
    const d = new Date(this._viewDate);

    switch (this._granularity) {
      case 'quarter_hours':
      case 'hours':
        d.setDate(d.getDate() + direction);
        break;
      case 'days':
        d.setDate(1);
        d.setMonth(d.getMonth() + direction);
        break;
      case 'weeks':
        d.setDate(1);
        d.setMonth(d.getMonth() + 3 * direction);
        break;
      case 'months':
        d.setDate(1);
        d.setFullYear(d.getFullYear() + direction);
        break;
    }

    this._viewDate = d;
    this._fetchData();
  }

  private _goToNow() {
    this._viewDate = new Date();
    this._fetchData();
  }

  private _setGranularity(g: Granularity) {
    this._granularity = g;
    this._fetchData();
  }

  private _toggleGranMenu() {
    this._granMenuOpen = !this._granMenuOpen;
  }

  private _selectGranFromMenu(g: Granularity) {
    this._setGranularity(g);
    this._granMenuOpen = false;
  }

  private _onDateChange(e: Event) {
    const input = e.target as HTMLInputElement;
    if (input.value) {
      const parts = input.value.split('-');
      this._viewDate = new Date(
        parseInt(parts[0]),
        parseInt(parts[1]) - 1,
        parseInt(parts[2])
      );
      this._fetchData();
    }
  }

  private _isNextDisabled(): boolean {
    if (this._granularity === 'years') return true;
    const { end } = this._getViewWindow();
    const endDate = new Date(end);
    return endDate > new Date();
  }

  // ── Date label ────────────────────────────────────────────────

  private _getDateLabel(): string {
    const d = this._viewDate;
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const yyyy = d.getFullYear();

    switch (this._granularity) {
      case 'quarter_hours':
      case 'hours':
        return `${dd}-${mm}-${yyyy}`;
      case 'days': {
        const monthName = d.toLocaleDateString(undefined, { month: 'long' });
        return `${monthName} ${yyyy}`;
      }
      case 'weeks': {
        const qStart = Math.floor(d.getMonth() / 3) * 3;
        const qEnd = qStart + 2;
        const startMonth = new Date(yyyy, qStart, 1).toLocaleDateString(
          undefined,
          { month: 'short' }
        );
        const endMonth = new Date(yyyy, qEnd, 1).toLocaleDateString(
          undefined,
          { month: 'short' }
        );
        return `${startMonth} – ${endMonth} ${yyyy}`;
      }
      case 'months':
        return `${yyyy}`;
      case 'years':
        return tp('all-time');
    }
  }

  private _getDateInputValue(): string {
    const d = this._viewDate;
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const da = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${da}`;
  }

  // ── Formatting helpers ────────────────────────────────────────

  private _getCurrency(): string {
    return this._hass?.config?.currency || 'EUR';
  }

  private _fmtCurrency(value: number | null | undefined, decimals = 2): string {
    if (value === null || value === undefined) return '−';
    const currency = this._getCurrency();
    const factor = Math.pow(10, decimals);
    const rounded = Math.round((value + Number.EPSILON) * factor) / factor;
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency,
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(rounded);
    } catch {
      return `${rounded.toFixed(decimals)}`;
    }
  }

  private _fmtCents(value: number | null | undefined): string {
    if (value === null || value === undefined) return '−';
    return (value * 100).toLocaleString(undefined, {
      minimumFractionDigits: 3,
      maximumFractionDigits: 3,
    });
  }

  private _fmtNum(value: number | null | undefined, decimals = 2): string {
    if (value === null || value === undefined) return '−';
    const factor = Math.pow(10, decimals);
    const rounded = Math.round((value + Number.EPSILON) * factor) / factor;
    return rounded.toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }

  private _fmtWh(value: number | null | undefined): string {
    if (value === null || value === undefined) return '−';
    return Math.round(value).toString();
  }


  private _fmtTime(isoStr: string): string {
    if (!isoStr) return '−';
    const d = new Date(isoStr);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `${hh}:${mm}`;
  }

  private _fmtHour(isoStr: string): string {
    if (!isoStr) return '−';
    const d = new Date(isoStr);
    const hh = String(d.getHours()).padStart(2, '0');
    return `${hh}:00`;
  }

  private _fmtDayDate(isoStr: string): string {
    if (!isoStr) return '−';
    const d = new Date(isoStr);
    const dayName = d.toLocaleDateString(undefined, { weekday: 'short' });
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    return `${dayName} ${dd}-${mm}`;
  }

  private _fmtWeek(weekStr: string): string {
    // weekStr is like "2026-W08" (ISO week key from the backend)
    const m = /^(\d{4})-W(\d{2})$/.exec(weekStr);
    if (!m) return weekStr;
    const year = parseInt(m[1]);
    const week = parseInt(m[2]);
    // ISO week 1 always contains Jan 4; Mon=1…Sun=7
    const jan4 = new Date(year, 0, 4);
    const weekday = jan4.getDay() || 7;
    const monday = new Date(year, 0, 4 - (weekday - 1) + (week - 1) * 7);
    const dd = String(monday.getDate()).padStart(2, '0');
    const mm = String(monday.getMonth() + 1).padStart(2, '0');
    return `W${m[2]} ${dd}-${mm}`;
  }

  private _fmtMonthYear(isoStr: string): string {
    if (!isoStr) return '−';
    const d = new Date(isoStr);
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    return `${mm}-${d.getFullYear()}`;
  }

  private _fmtYear(isoStr: string): string {
    if (!isoStr) return '−';
    return new Date(isoStr).getFullYear().toString();
  }

  private _fmtPeriod(isoStr: string): string {
    switch (this._granularity) {
      case 'quarter_hours':
        return this._fmtTime(isoStr);
      case 'hours':
        return this._fmtHour(isoStr);
      case 'days':
        return this._fmtDayDate(isoStr);
      case 'weeks':
        return this._fmtWeek(isoStr);
      case 'months':
        return this._fmtMonthYear(isoStr);
      case 'years':
        return this._fmtYear(isoStr);
    }
  }

  private _fmtTitleDate(isoStr: string): string {
    if (!isoStr) return '−';
    switch (this._granularity) {
      case 'months':
        return this._fmtMonthYear(isoStr);
      case 'years':
        return this._fmtYear(isoStr);
      case 'weeks':
        return this._fmtWeek(isoStr);
      default: {
        const d = new Date(isoStr);
        const dd = String(d.getDate()).padStart(2, '0');
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        return `${dd}-${mm}-${d.getFullYear()}`;
      }
    }
  }

  private _getPageTitle(): string {
    const base = tp('page-title');
    if (!this._data?.length) return base;
    const first = this._data[this._data.length - 1];
    const last = this._data[0];
    const from = this._fmtTitleDate(first.period_start);
    const to = this._fmtTitleDate(last.period_start);
    if (from === to) return `${base}: ${from}`;
    return `${base}: ${from} – ${to}`;
  }

  private _renderAppState(state: string | null | undefined): TemplateResult {
    if (!state) return html`<span>−</span>`;

    const STATE_ICONS: Record<string, { icon: string; color: string }> = {
      automatic: { icon: 'mdi:auto-fix', color: 'var(--primary-color)' },
      charge: {
        icon: 'mdi:battery-arrow-up-outline',
        color: 'var(--primary-color)',
      },
      discharge: {
        icon: 'mdi:battery-arrow-down-outline',
        color: 'var(--primary-color)',
      },
      pause: {
        icon: 'mdi:pause-box-outline',
        color: 'var(--secondary-text-color)',
      },
      max_boost: { icon: 'mdi:flash-alert', color: 'var(--warning-color)' },
      not_connected: {
        icon: 'mdi:power-plug-off-outline',
        color: 'var(--secondary-text-color)',
      },
      error: { icon: 'mdi:alert', color: 'var(--error-color)' },
      unknown: {
        icon: 'mdi:help-rhombus-outline',
        color: 'var(--secondary-text-color)',
      },
    };

    const mixed = state.endsWith('+');
    const baseState = mixed ? state.slice(0, -1) : state;
    const cfg = STATE_ICONS[baseState];
    if (!cfg) return html`<span>${state}</span>`;

    const title = mixed
      ? `${tp(`app-state.${baseState}`)} + ${tp('app-state.mixed')}`
      : tp(`app-state.${baseState}`);
    return html`
      <span class="state-cell" title="${title}">
        <ha-icon
          icon="${cfg.icon}"
          style="color: ${cfg.color}; --mdc-icon-size: 20px;"
        ></ha-icon>${mixed ? html`<sup class="state-plus">+</sup>` : nothing}
      </span>
    `;
  }

  private _currencySymbol(): string {
    const code = this._hass?.config?.currency ?? 'EUR';
    const symbols: Record<string, string> = {
      EUR: '€', GBP: '£', USD: '$', AUD: '$', CAD: '$',
      NOK: 'kr', SEK: 'kr', DKK: 'kr', CHF: 'Fr',
      JPY: '¥', CNY: '¥', INR: '₹', BRL: 'R$',
    };
    return symbols[code] ?? code;
  }

  private _renderPriceIndicator(
    rating: string | null | undefined
  ): TemplateResult {
    if (!rating) return html`<span>−</span>`;
    const level = rating.replace(/_/g, '-');
    const title = tp(`price-rating.${level}`);
    return html`
      <div class="price-track" data-level="${level}" title="${title}">
        <span class="price-marker">${this._currencySymbol()}</span>
      </div>
    `;
  }

  private _fmtCostRevenue(row: any): string {
    if (row.charge_cost && row.charge_cost > 0) {
      return this._fmtCurrency(row.charge_cost);
    }
    if (row.discharge_revenue && row.discharge_revenue > 0) {
      return this._fmtCurrency(row.discharge_revenue);
    }
    return '−';
  }

  // ── Totals card ───────────────────────────────────────────────

  private _computeTotals() {
    if (!this._data?.length) return null;
    const first = this._data[this._data.length - 1];
    const last = this._data[0];
    const sum = (key: string) =>
      this._data.reduce((s: number, r: any) => s + (r[key] ?? 0), 0);
    const mean = (key: string) => {
      const vals = this._data
        .map((r: any) => r[key])
        .filter((v: any) => v != null) as number[];
      return vals.length
        ? vals.reduce((a, b) => a + b, 0) / vals.length
        : null;
    };

    if (this._granularity === 'quarter_hours') {
      const chargeWh = this._data.reduce(
        (s: number, r: any) => s + Math.max(0, r.energy_wh ?? 0),
        0
      );
      const dischargeWh = this._data.reduce(
        (s: number, r: any) => s + Math.max(0, -(r.energy_wh ?? 0)),
        0
      );
      const chargeDurationMin = this._data.filter(
        (r: any) => (r.energy_wh ?? 0) > 0
      ).length * 15;
      const dischargeDurationMin = this._data.filter(
        (r: any) => (r.energy_wh ?? 0) < 0
      ).length * 15;
      return {
        kind: 'quarter_hours' as const,
        first,
        last,
        avgCons: mean('consumption_price'),
        avgProd: mean('production_price'),
        chargeWh,
        chargeDurationMin,
        dischargeWh,
        dischargeDurationMin,
        chargeCost: sum('charge_cost'),
        dischargeRev: sum('discharge_revenue'),
        hasRepaired: this._data.some((r: any) => r.has_repaired),
      };
    }

    if (this._granularity === 'hours') {
      const socs = this._data
        .map((r: any) => r.soc_pct)
        .filter((v: any) => v != null) as number[];
      return {
        kind: 'hours' as const,
        first,
        last,
        socMin: socs.length ? Math.min(...socs) : null,
        socMax: socs.length ? Math.max(...socs) : null,
        avgPrice: mean('avg_price'),
        chargeWh: sum('charge_wh'),
        chargeDurationMin: sum('charge_duration_min'),
        dischargeWh: sum('discharge_wh'),
        dischargeDurationMin: sum('discharge_duration_min'),
        chargeCost: sum('charge_cost'),
        dischargeRev: sum('discharge_revenue'),
        hasRepaired: this._data.some((r: any) => r.has_repaired),
      };
    }

    // days / weeks / months / years
    return {
      kind: 'days' as const,
      first,
      last,
      avgAvail: mean('availability_pct'),
      chargeKwh: sum('charge_kwh'),
      chargeCost: sum('charge_cost'),
      chargeCo2Kg: sum('charge_co2_kg'),
      chargeDurationMin: sum('charge_duration_min'),
      dischargeKwh: sum('discharge_kwh'),
      dischargeRev: sum('discharge_revenue'),
      dischargeCo2Kg: sum('discharge_co2_kg'),
      dischargeDurationMin: sum('discharge_duration_min'),
      netKwh: sum('net_kwh'),
      netCost: sum('net_cost'),
      co2Kg: sum('co2_kg'),
      hasRepaired: this._data.some((r: any) => r.has_repaired),
    };
  }

  private _noDataHint(): TemplateResult | typeof nothing {
    if (!this._firstAvailable) return nothing;
    const { end } = this._getViewWindow();
    if (new Date(end) > new Date(this._firstAvailable)) return nothing;
    const firstDate = new Date(this._firstAvailable).toLocaleDateString(
      undefined,
      { month: 'long', year: 'numeric' }
    );
    return html`<small>${tp('no-data-hint')} ${firstDate}</small>`;
  }

  // NOTE: ha-tooltip and ha-icon (HA design system) do not work reliably in custom
  // card shadow DOM. ha-tooltip is absent from the DOM entirely; ha-icon in <th>
  // elements appears in the DOM but produces no visual output. The custom SVG +
  // click-toggle approach below is the correct solution for info icons in this card.
  private _renderInfoTip(tipKey: string, tooltipKey: string): TemplateResult {
    return html`
      <span class="info-container">
        <svg class="info-icon" viewBox="0 0 24 24" aria-hidden="true"
          @click=${() => { this._openTip = this._openTip === tipKey ? null : tipKey; }}>
          <path fill="currentColor" d="M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z"/>
        </svg>
        ${this._openTip === tipKey ? html`<span class="info-popup">${tp(tooltipKey)}</span>` : nothing}
      </span>
    `;
  }

  private _toggleOverflowMenu() {
    this._overflowMenuOpen = !this._overflowMenuOpen;
  }

  private _onResetDatabase() {
    this._overflowMenuOpen = false;
    showResetDatabaseDialog(this);
  }

  private _renderEstimatedNote(hasRepaired: boolean): TemplateResult | typeof nothing {
    if (!hasRepaired) return nothing;
    return html`
      <div class="estimated-note">
        ${tp('estimated-note')}
        ${this._renderInfoTip('estimated', 'estimated-tooltip')}
      </div>
    `;
  }

  private _fmtDurationVal(minutes: number | null | undefined): string {
    if (minutes == null || minutes === 0) return '−';
    if (this._granularity === 'quarter_hours') {
      const h = Math.floor(minutes / 60);
      const m = minutes % 60;
      return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
    }
    return `${Math.round(minutes / 60)}`;
  }

  private _renderTotals(): TemplateResult {
    if (this._isLoading) {
      return html`<div class="center muted">
        <span class="spinner"></span>
      </div>`;
    }
    const t = this._computeTotals();
    if (!t) {
      return html`<div class="center muted"><div class="no-data-msg">${tp('no-data')}${this._noDataHint()}</div></div>`;
    }

    const tt = (key: string) => tp(`totals.${key}`);
    const cur = this._currencySymbol();
    const savingsVal = this._fmtNum(2.33, 2);
    const savingsProfit = true;

    // Each column is a 2-col grid: value (right-aligned) | unit (left-aligned).
    // The header icon sits in the value column, the title in the unit column.
    const val = (value: string, unit: string, profit = false) => html`
      <span class="totals-val ${profit ? 'profit' : ''}">${value}</span>
      <span class="totals-unit">${unit}</span>
    `;

    // Summary column (Totalen): single-column, left-aligned, value+unit together.
    const sval = (value: string, unit: string, profit = false) => html`
      <span class="totals-val ${profit ? 'profit' : ''}">${value} <span class="totals-unit">${unit}</span></span>
    `;

    const label = (content: TemplateResult | string) => html`
      <span class="totals-label">${content}</span>
    `;

    if (t.kind === 'quarter_hours') {
      const netWh = t.chargeWh - t.dischargeWh;
      const netCost = t.chargeCost - t.dischargeRev;
      return html`
        <div class="totals-grid">
          <div class="totals-col totals-col-summary">
            <div class="totals-col-header">
              <ha-icon class="totals-col-icon" icon="mdi:bullseye-arrow"></ha-icon>
              <span class="totals-col-title">${tt('card-title')}</span>
            </div>
            ${sval(this._fmtCents(t.avgCons), `${cur}c/kWh`)}
            ${label(tt('avg-cons-price'))}
            ${sval(this._fmtCents(t.avgProd), `${cur}c/kWh`)}
            ${label(tt('avg-prod-price'))}
            ${label(html`${tt('savings-label')} ${this._renderInfoTip('savings', 'totals.savings-tooltip')}`)}
            ${sval(savingsVal, cur, savingsProfit)}
          </div>
          <div class="totals-col">
            <ha-icon class="totals-col-icon" icon="mdi:calculator-variant-outline"></ha-icon>
            <span class="totals-col-title">${tp('col.net')}</span>
            ${val(this._fmtWh(netWh), 'Wh')}
            ${val(this._fmtNum(netCost, 2), cur, netCost < 0)}
          </div>
          <div class="totals-col">
            <ha-icon class="totals-col-icon" icon="mdi:car-arrow-right"></ha-icon>
            <span class="totals-col-title">${tp('col.charge')}</span>
            ${val(this._fmtDurationVal(t.chargeDurationMin), 'uur')}
            ${val(this._fmtWh(t.chargeWh), 'Wh')}
            ${val(this._fmtNum(t.chargeCost, 2), cur)}
          </div>
          <div class="totals-col">
            <ha-icon class="totals-col-icon" icon="mdi:car-arrow-left"></ha-icon>
            <span class="totals-col-title">${tp('col.discharge')}</span>
            ${val(this._fmtDurationVal(t.dischargeDurationMin), 'uur')}
            ${val(this._fmtWh(t.dischargeWh), 'Wh')}
            ${val(this._fmtNum(t.dischargeRev, 2), cur, true)}
          </div>
        </div>
      `;
    }

    if (t.kind === 'hours') {
      const netWh = t.chargeWh - t.dischargeWh;
      const netCost = t.chargeCost - t.dischargeRev;
      return html`
        <div class="totals-grid">
          <div class="totals-col totals-col-summary">
            <div class="totals-col-header">
              <ha-icon class="totals-col-icon" icon="mdi:bullseye-arrow"></ha-icon>
              <span class="totals-col-title">${tt('card-title')}</span>
            </div>
            ${t.socMin != null
              ? html`
                  ${sval(`${this._fmtNum(t.socMin, 1)}% – ${this._fmtNum(t.socMax, 1)}`, '%')}
                  ${label(tt('soc-range'))}
                `
              : nothing}
            ${sval(this._fmtCents(t.avgPrice), `${cur}c/kWh`)}
            ${label(tt('avg-price'))}
            ${label(html`${tt('savings-label')} ${this._renderInfoTip('savings', 'totals.savings-tooltip')}`)}
            ${sval(savingsVal, cur, savingsProfit)}
          </div>
          <div class="totals-col">
            <ha-icon class="totals-col-icon" icon="mdi:calculator-variant-outline"></ha-icon>
            <span class="totals-col-title">${tp('col.net')}</span>
            ${val(this._fmtWh(netWh), 'Wh')}
            ${val(this._fmtNum(netCost, 2), cur, netCost < 0)}
          </div>
          <div class="totals-col">
            <ha-icon class="totals-col-icon" icon="mdi:car-arrow-right"></ha-icon>
            <span class="totals-col-title">${tp('col.charge')}</span>
            ${val(this._fmtDurationVal(t.chargeDurationMin), 'uur')}
            ${val(this._fmtWh(t.chargeWh), 'Wh')}
            ${val(this._fmtNum(t.chargeCost, 2), cur)}
          </div>
          <div class="totals-col">
            <ha-icon class="totals-col-icon" icon="mdi:car-arrow-left"></ha-icon>
            <span class="totals-col-title">${tp('col.discharge')}</span>
            ${val(this._fmtDurationVal(t.dischargeDurationMin), 'uur')}
            ${val(this._fmtWh(t.dischargeWh), 'Wh')}
            ${val(this._fmtNum(t.dischargeRev, 2), cur, true)}
          </div>
        </div>
      `;
    }

    // days / weeks / months / years
    const kwhDec = 0;
    const kgDec  = 0;
    const curDec = this._granularity === 'years' ? 0 : 2;
    const netPriceProfit = t.netKwh !== 0 && t.netCost / t.netKwh < 0;
    return html`
      <div class="totals-grid">
        <div class="totals-col totals-col-summary">
          <div class="totals-col-header">
            <ha-icon class="totals-col-icon" icon="mdi:bullseye-arrow"></ha-icon>
            <span class="totals-col-title">${tt('card-title')}</span>
          </div>
          ${label(html`${tt('savings-label')} ${this._renderInfoTip('savings', 'totals.savings-tooltip')}`)}
          ${sval(savingsVal, cur, savingsProfit)}
          ${label(html`${tt('availability')} ${this._renderInfoTip('avail-totals', 'col.availability-tooltip')}`)}
          ${sval(this._fmtNum(t.avgAvail, 0), '%')}
        </div>
        <div class="totals-col">
          <ha-icon class="totals-col-icon" icon="mdi:calculator-variant-outline"></ha-icon>
          <span class="totals-col-title">${tp('col.net')}</span>
          ${val(this._fmtCents(t.netKwh !== 0 ? t.netCost / t.netKwh : null), `${cur}c/kWh`, netPriceProfit)}
          ${val(this._fmtNum(t.netKwh, kwhDec), 'kWh')}
          ${val(this._fmtNum(t.netCost, curDec), cur, t.netCost < 0)}
          ${val(this._fmtNum(t.co2Kg, kgDec), 'kg CO₂', t.co2Kg < 0)}
        </div>
        <div class="totals-col">
          <ha-icon class="totals-col-icon" icon="mdi:car-arrow-right"></ha-icon>
          <span class="totals-col-title">${tp('col.charge')}</span>
          ${val(this._fmtDurationVal(t.chargeDurationMin), 'uur')}
          ${val(this._fmtNum(t.chargeKwh, kwhDec), 'kWh')}
          ${val(this._fmtNum(t.chargeCost, curDec), cur)}
          ${val(this._fmtNum(t.chargeCo2Kg, kgDec), 'kg CO₂')}
        </div>
        <div class="totals-col">
          <ha-icon class="totals-col-icon" icon="mdi:car-arrow-left"></ha-icon>
          <span class="totals-col-title">${tp('col.discharge')}</span>
          ${val(this._fmtDurationVal(t.dischargeDurationMin), 'uur')}
          ${val(this._fmtNum(t.dischargeKwh, kwhDec), 'kWh')}
          ${val(this._fmtNum(t.dischargeRev, curDec), cur, true)}
          ${val(this._fmtNum(t.dischargeCo2Kg, kgDec), 'kg CO₂')}
        </div>
      </div>
    `;
  }

  // ── Table rendering per granularity ───────────────────────────

  private _renderTbody(
    colSpan: number,
    renderRows: () => TemplateResult[]
  ): TemplateResult {
    if (this._isLoading) {
      return html`<tr><td colspan="${colSpan}"><div class="center muted"><span class="spinner"></span>${tp('loading')}</div></td></tr>`;
    }
    if (this._data.length === 0) {
      return html`<tr><td colspan="${colSpan}"><div class="center muted"><div class="no-data-msg">${tp('no-data')}${this._noDataHint()}</div></div></td></tr>`;
    }
    return html`${renderRows()}`;
  }

  private _col(key: string, unit?: string): TemplateResult | string {
    if (unit) {
      return html`${tp(`col.${key}`)}<span class="unit">${unit}</span>`;
    }
    return tp(`col.${key}`);
  }

  private _renderQuarterTable(): TemplateResult {
    return html`
      <table>
        <thead>
          <tr>
            <th>${this._col('period')}</th>
            <th class="indicator-col">${this._col('status')}</th>
            <th class="num">${this._col('soc', '%')}</th>
            <th class="num">${this._col('energy', 'Wh')}</th>
            <th class="num">${this._col('consumption', '€¢/kWh')}</th>
            <th class="num">${this._col('production', '€¢/kWh')}</th>
            <th class="indicator-col">${this._col('rate')}</th>
            <th class="num">${this._col('cost-revenue')}</th>
          </tr>
        </thead>
        <tbody>
          ${this._renderTbody(8, () => this._data.map(
            (row) => html`
              <tr class="${row.has_repaired ? 'repaired' : ''}">
                <td>${this._fmtTime(row.period_start)}</td>
                <td class="indicator-cell">${this._renderAppState(row.app_state)}</td>
                <td class="num">${this._fmtNum(row.soc_pct, 1)}</td>
                <td class="num">${this._fmtWh(row.energy_wh)}</td>
                <td class="num">${this._fmtCents(row.consumption_price)}</td>
                <td class="num">${this._fmtCents(row.production_price)}</td>
                <td class="indicator-cell">${this._renderPriceIndicator(row.price_rating)}</td>
                <td class="num">${this._fmtCostRevenue(row)}</td>
              </tr>
            `
          ))}
        </tbody>
      </table>
    `;
  }

  private _renderHourTable(): TemplateResult {
    return html`
      <table>
        <thead class="grouped">
          <tr>
            <th>${tp('col.period')}</th>
            <th class="indicator-col">${tp('col.status')}</th>
            <th class="num">${tp('col.soc')}</th>
            <th class="num">${tp('col.avg-price')}</th>
            <th class="indicator-col">${tp('col.rate')}</th>
            <th class="group-header group-sep" colspan="2">${tp('col.charge')}</th>
            <th class="group-header group-sep" colspan="2">${tp('col.discharge')}</th>
            <th class="group-header group-sep" colspan="2">${tp('col.net')}</th>
          </tr>
          <tr class="sub-header">
            <th></th>
            <th class="indicator-col"></th>
            <th class="num">%</th>
            <th class="num">€¢/kWh</th>
            <th class="indicator-col"></th>
            <th class="num group-sep">${tp('col.energy')} (Wh)</th>
            <th class="num">${tp('col.cost')}</th>
            <th class="num group-sep">${tp('col.energy')} (Wh)</th>
            <th class="num">${tp('col.revenue')}</th>
            <th class="num group-sep">${tp('col.energy')} (Wh)</th>
            <th class="num">${tp('col.cost')}</th>
          </tr>
        </thead>
        <tbody>
          ${this._renderTbody(11, () => this._data.map(
            (row) => html`
              <tr class="${row.has_repaired ? 'repaired' : ''}">
                <td>${this._fmtHour(row.period_start)}</td>
                <td class="indicator-cell">${this._renderAppState(row.app_state)}</td>
                <td class="num">${this._fmtNum(row.soc_pct, 1)}</td>
                <td class="num">${this._fmtCents(row.avg_price)}</td>
                <td class="indicator-cell">${this._renderPriceIndicator(row.price_rating)}</td>
                <td class="num group-sep">${this._fmtWh(row.charge_wh)}</td>
                <td class="num">${this._fmtCurrency(row.charge_cost)}</td>
                <td class="num group-sep">${this._fmtWh(row.discharge_wh)}</td>
                <td class="num profit">${this._fmtCurrency(row.discharge_revenue)}</td>
                <td class="num group-sep">${this._fmtWh((row.charge_wh ?? 0) - (row.discharge_wh ?? 0))}</td>
                <td class="num ${(row.charge_cost ?? 0) - (row.discharge_revenue ?? 0) < 0 ? 'profit' : ''}">${this._fmtCurrency((row.charge_cost ?? 0) - (row.discharge_revenue ?? 0))}</td>
              </tr>
            `
          ))}
        </tbody>
      </table>
    `;
  }

  private _renderDayTable(): TemplateResult {
    return html`
      <table>
        <thead class="grouped">
          <tr>
            <th>${tp('col.period')}</th>
            <th class="num"><span class="wide-title-wrapper">${tp('col.availability')}</span></th>
            <th class="group-header group-sep" colspan="3">${tp('col.charge')}</th>
            <th class="group-header group-sep" colspan="3">${tp('col.discharge')}</th>
            <th class="group-header group-sep" colspan="2">${tp('col.net')}</th>
            <th class="num"></th>
          </tr>
          <tr class="sub-header">
            <th></th>
            <th class="num">%
              ${this._renderInfoTip('avail-header', 'col.availability-tooltip')}
            </th>
            <th class="num group-sep">${tp('col.energy')} (kWh)</th>
            <th class="num">${tp('col.cost')}</th>
            <th class="num">CO₂ (kg)</th>
            <th class="num group-sep">${tp('col.energy')} (kWh)</th>
            <th class="num">${tp('col.revenue')}</th>
            <th class="num">CO₂ (kg)</th>
            <th class="num group-sep">${tp('col.energy')} (kWh)</th>
            <th class="num">${tp('col.cost')}</th>
            <th class="num">CO₂ (kg)</th>
          </tr>
        </thead>
        <tbody>
          ${this._renderTbody(11, () => {
            const aggGran = ['weeks', 'months', 'years'].includes(this._granularity);
            const kwhDec = aggGran ? 0 : 2;
            const kgDec  = aggGran ? 0 : 1;
            const curDec = this._granularity === 'years' ? 0 : 2;
            return this._data.map(
              (row) => html`
                <tr class="${row.has_repaired ? 'repaired' : ''}">
                  <td>${this._fmtPeriod(row.period_start)}</td>
                  <td class="num">${this._fmtNum(row.availability_pct, 0)}</td>
                  <td class="num group-sep">${this._fmtNum(row.charge_kwh, kwhDec)}</td>
                  <td class="num">${this._fmtCurrency(row.charge_cost, curDec)}</td>
                  <td class="num">${this._fmtNum(row.charge_co2_kg, kgDec)}</td>
                  <td class="num group-sep">${this._fmtNum(row.discharge_kwh, kwhDec)}</td>
                  <td class="num profit">${this._fmtCurrency(row.discharge_revenue, curDec)}</td>
                  <td class="num profit">${this._fmtNum(row.discharge_co2_kg, kgDec)}</td>
                  <td class="num group-sep">${this._fmtNum(row.net_kwh, kwhDec)}</td>
                  <td class="num ${row.net_cost < 0 ? 'profit' : ''}">${this._fmtCurrency(row.net_cost, curDec)}</td>
                  <td class="num ${row.co2_kg < 0 ? 'profit' : ''}">${this._fmtNum(row.co2_kg, kgDec)}</td>
                </tr>
              `
            );
          })}
        </tbody>
      </table>
    `;
  }

  private _renderTable(): TemplateResult {
    switch (this._granularity) {
      case 'quarter_hours':
        return this._renderQuarterTable();
      case 'hours':
        return this._renderHourTable();
      default:
        return this._renderDayTable();
    }
  }

  // ── Main render ───────────────────────────────────────────────

  render() {
    return html`
      <div class="page-header">
        <h1 class="page-title">${this._getPageTitle()}</h1>
        <div class="overflow-menu">
          <ha-icon-button
            .path=${'M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z'}
            @click=${this._toggleOverflowMenu}
          ></ha-icon-button>
          ${this._overflowMenuOpen ? html`
            <ul class="overflow-dropdown">
              <li>
                <button class="overflow-item" @click=${this._onResetDatabase}>
                  ${tp('overflow-reset-database')}
                </button>
              </li>
            </ul>
          ` : nothing}
        </div>
      </div>
      <div class="page-layout">
        <ha-card>
          <div class="totals-card-content">
            ${this._renderEstimatedNote(this._data?.some((r: any) => r.has_repaired) ?? false)}
            ${this._renderTotals()}
          </div>
        </ha-card>

        <ha-card>
          <div class="table-container">
            ${this._error
              ? html`<div class="center error">${this._error}</div>`
              : this._renderTable()}
          </div>
        </ha-card>
      </div>

      <div class="floating-bar">
        <div class="bar-content">
          ${this._granularity !== 'years'
            ? html`
                <ha-icon-button
                  .path=${'M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z'}
                  @click=${() => this._navigate(-1)}
                ></ha-icon-button>
              `
            : nothing}

          <div class="date-wrapper">
            <span class="date-label">${this._getDateLabel()}</span>
            ${this._granularity !== 'years'
              ? html`
                  <input
                    type="date"
                    class="date-input"
                    .value=${this._getDateInputValue()}
                    @change=${this._onDateChange}
                  />
                `
              : nothing}
          </div>

          ${this._granularity !== 'years'
            ? html`
                <ha-icon-button
                  .path=${'M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z'}
                  ?disabled=${this._isNextDisabled()}
                  @click=${() => this._navigate(1)}
                ></ha-icon-button>
                <button class="pill now-btn" @click=${this._goToNow}>
                  ${tp('now')}
                </button>
              `
            : nothing}

          <div class="bar-separator"></div>

          ${this._narrowBar
            ? html`
                <div class="gran-menu">
                  <button class="gran-trigger" @click=${this._toggleGranMenu}>
                    ${tp(`granularity.${this._granularity}`)}
                    <svg viewBox="0 0 24 24" width="18" height="18">
                      <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z" fill="currentColor"/>
                    </svg>
                  </button>
                  ${this._granMenuOpen
                    ? html`
                        <ul class="gran-dropdown">
                          ${GRANULARITIES.map(
                            (g) => html`
                              <li>
                                <button
                                  class="gran-item ${g === this._granularity ? 'active' : ''}"
                                  @click=${() => this._selectGranFromMenu(g)}
                                >
                                  ${tp(`granularity.${g}`)}
                                </button>
                              </li>
                            `
                          )}
                        </ul>
                      `
                    : nothing}
                </div>
              `
            : GRANULARITIES.map(
                (g) => html`
                  <button
                    class="pill ${this._granularity === g ? 'active' : ''}"
                    @click=${() => this._setGranularity(g)}
                  >
                    ${tp(`granularity.${g}`)}
                  </button>
                `
              )}
        </div>
      </div>
    `;
  }

  static styles = dataTableStyles;
}

declare global {
  interface HTMLElementTagNameMap {
    'v2g-liberty-data-table-card': DataTableCard;
  }
}
