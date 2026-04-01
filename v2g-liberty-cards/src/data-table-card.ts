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
  private _docClickHandler?: (e: MouseEvent) => void;

  setConfig(_config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._checkUninitialisedEntities();
    // HA's runtime hass.themes has darkMode but the type doesn't include it
    const isDark = (hass as any).themes?.darkMode ?? false;
    this.classList.toggle('dark', isDark);
  }

  private _checkUninitialisedEntities() {
    if (hasUninitializedEntities(this._hass)) {
      showSettingsErrorAlertDialog(this);
    }
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._resizeObserver?.disconnect();
    if (this._docClickHandler) document.removeEventListener('click', this._docClickHandler);
  }

  protected firstUpdated() {
    const container = this.shadowRoot?.querySelector('.table-container') as HTMLElement;

    // Toggle enhanced thead shadow when the table header is sticking.
    // :host is the scroll container; thead sticks relative to it.
    this.addEventListener('scroll', () => {
      if (!container) return;
      const hostTop = this.getBoundingClientRect().top;
      const containerTop = container.getBoundingClientRect().top;
      container.classList.toggle('scrolled', containerTop <= hostTop);
    });

    const syncNarrow = () => {
      this._narrowBar = this.offsetWidth <= 800;
    };

    this._resizeObserver = new ResizeObserver(() => requestAnimationFrame(syncNarrow));
    this._resizeObserver.observe(this);
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
      case 'days':
        return `${mm}-${yyyy}`;
      case 'weeks': {
        const qStart = Math.floor(d.getMonth() / 3) * 3;
        const qStartMM = String(qStart + 1).padStart(2, '0');
        const qEndMM = String(qStart + 3).padStart(2, '0');
        return `${qStartMM} – ${qEndMM} ${yyyy}`;
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

  private _getPageTitle(): TemplateResult {
    const base = tp('page-title');
    if (!this._data?.length) return html`${base}`;
    const first = this._data[this._data.length - 1];
    const last = this._data[0];
    const from = this._fmtTitleDate(first.period_start);
    const to = this._fmtTitleDate(last.period_start);
    const range = from === to ? from : `${from} – ${to}`;
    return html`${base}: <span class="date-range">${range}</span>`;
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
        chargeCo2Kg: sum('charge_co2_kg'),
        dischargeCo2Kg: sum('discharge_co2_kg'),
        co2Kg: sum('co2_kg'),
        savingsFixed: sum('savings_fixed_eur'),
        savingsDyn: sum('savings_dynamic_eur'),
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
        chargeCo2Kg: sum('charge_co2_kg'),
        dischargeCo2Kg: sum('discharge_co2_kg'),
        co2Kg: sum('co2_kg'),
        savingsFixed: sum('savings_fixed_eur'),
        savingsDyn: sum('savings_dynamic_eur'),
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
      savingsFixed: sum('savings_fixed_eur'),
      savingsDyn: sum('savings_dynamic_eur'),
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

  private _renderMetric(
    label: string | TemplateResult,
    value: string,
    unit: string,
    profit = false,
  ): TemplateResult {
    return html`
      <div class="metric">
        <span class="metric-label">${label}</span>
        <span class="metric-value ${profit ? 'profit' : ''}"
          >${value} <span class="metric-unit">${unit}</span></span
        >
      </div>
    `;
  }

  private _renderSavingsCard(savingsFixed: number | null, savingsDyn: number | null): TemplateResult {
    const cur = this._currencySymbol();
    const tt = (key: string) => tp(`totals.${key}`);
    const fixedStr = savingsFixed != null ? this._fmtNum(savingsFixed, 2) : '−';
    const dynStr = savingsDyn != null ? this._fmtNum(savingsDyn, 2) : '−';
    return html`
      <div class="subcard subcard-savings">
        <ha-icon class="savings-piggy" icon="mdi:piggy-bank-outline"></ha-icon>
        <div class="savings-title-row">
          <span class="subcard-title">${tt('savings')}</span>
          ${this._renderInfoTip('savings', 'totals.savings-tooltip')}
        </div>
        <div class="savings-sublabel">${tt('savings-fixed-label')}</div>
        <div class="subcard-hero">${cur}\u202F${fixedStr}</div>
        <div class="savings-dyn">
          <span class="savings-dyn-amount">${cur}\u202F${dynStr}</span>
          <span class="savings-dyn-label">${tt('savings-dyn-label')}</span>
        </div>
      </div>
    `;
  }

  private _renderTotals(): TemplateResult {
    if (this._isLoading) {
      return html`<div class="center muted">
        <span class="spinner"></span>
      </div>`;
    }
    const t = this._computeTotals();
    if (!t) {
      return html`<div class="center muted">
        <div class="no-data-msg">${tp('no-data')}${this._noDataHint()}</div>
      </div>`;
    }

    const hasRepaired =
      this._data?.some((r: any) => r.has_repaired) ?? false;

    if (t.kind === 'quarter_hours') {
      return this._renderTotalsSubcards_QH(t, hasRepaired);
    }
    if (t.kind === 'hours') {
      return this._renderTotalsSubcards_Hours(t, hasRepaired);
    }
    return this._renderTotalsSubcards_Days(t, hasRepaired);
  }

  private _renderTotalsSubcards_Days(
    t: any,
    hasRepaired: boolean,
  ): TemplateResult {
    const cur = this._currencySymbol();
    const tt = (key: string) => tp(`totals.${key}`);
    const kwhDec = 0;
    const kgDec = 0;
    const curDec = this._granularity === 'years' ? 0 : 2;

    return html`
      <div class="totals-subcards-grid">
        <div class="subcard subcard-netto">
          ${this._renderEstimatedNote(hasRepaired)}
          <div class="subcard-header">
            <ha-icon icon="mdi:calculator-variant-outline"></ha-icon>
            <span class="subcard-title">${tp('col.net')}</span>
          </div>
          <div class="subcard-hero">
            ${this._fmtCents(t.netKwh !== 0 ? t.netCost / t.netKwh : null)}
            <span class="hero-unit">${cur}\u00a2ent/kWh</span>
          </div>
          <div class="metric-grid">
            ${this._renderMetric(tp('col.energy'), this._fmtNum(t.netKwh, kwhDec), 'kWh')}
            ${this._renderMetric(tp('col.cost'), this._fmtNum(t.netCost, curDec), cur, t.netCost < 0)}
            ${this._renderMetric(tp('col.emissions'), this._fmtNum(t.co2Kg, kgDec), 'kg CO\u2082', t.co2Kg < 0)}
            ${this._renderMetric(
              html`${tt('availability')} ${this._renderInfoTip('avail-totals', 'col.availability-tooltip')}`,
              this._fmtNum(t.avgAvail, 0),
              '%',
            )}
          </div>
        </div>

        ${this._renderSavingsCard(t.savingsFixed, t.savingsDyn)}

        <div class="subcard subcard-charge">
          <div class="subcard-header">
            <ha-icon icon="mdi:car-arrow-left"></ha-icon>
            <span class="subcard-title">${tp('col.charge')}</span>
          </div>
          <div class="metric-grid">
            ${this._renderMetric(tp('col.energy'), this._fmtNum(t.chargeKwh, kwhDec), 'kWh')}
            ${this._renderMetric(tp('col.cost'), this._fmtNum(t.chargeCost, curDec), cur)}
            ${this._renderMetric(tp('col.emissions'), this._fmtNum(t.chargeCo2Kg, kgDec), 'kg CO\u2082')}
            ${this._renderMetric(tp('col.duration'), this._fmtDurationVal(t.chargeDurationMin), 'uur')}
          </div>
        </div>

        <div class="subcard subcard-discharge">
          <div class="subcard-header">
            <ha-icon icon="mdi:car-arrow-right"></ha-icon>
            <span class="subcard-title">${tp('col.discharge')}</span>
          </div>
          <div class="metric-grid">
            ${this._renderMetric(tp('col.energy'), this._fmtNum(t.dischargeKwh, kwhDec), 'kWh')}
            ${this._renderMetric(tp('col.revenue'), this._fmtNum(t.dischargeRev, curDec), cur, true)}
            ${this._renderMetric(tp('col.avoided-emissions'), this._fmtNum(t.dischargeCo2Kg, kgDec), 'kg CO\u2082')}
            ${this._renderMetric(tp('col.duration'), this._fmtDurationVal(t.dischargeDurationMin), 'uur')}
          </div>
        </div>
      </div>
    `;
  }

  private _renderTotalsSubcards_Hours(
    t: any,
    hasRepaired: boolean,
  ): TemplateResult {
    const cur = this._currencySymbol();
    const tt = (key: string) => tp(`totals.${key}`);
    const netWh = t.chargeWh - t.dischargeWh;
    const netCost = t.chargeCost - t.dischargeRev;

    const netKwh = netWh / 1000;
    return html`
      <div class="totals-subcards-grid">
        <div class="subcard subcard-netto">
          ${this._renderEstimatedNote(hasRepaired)}
          <div class="subcard-header">
            <ha-icon icon="mdi:calculator-variant-outline"></ha-icon>
            <span class="subcard-title">${tp('col.net')}</span>
          </div>
          <div class="subcard-hero">
            ${this._fmtCents(netKwh !== 0 ? netCost / netKwh : null)}
            <span class="hero-unit">${cur}\u00a2ent/kWh</span>
          </div>
          <div class="metric-grid">
            ${this._renderMetric(tp('col.energy'), this._fmtWh(netWh), 'Wh')}
            ${this._renderMetric(tp('col.cost'), this._fmtNum(netCost, 2), cur, netCost < 0)}
            ${this._renderMetric(tp('col.emissions'), this._fmtNum(t.co2Kg, 1), 'kg CO\u2082', t.co2Kg < 0)}
            ${this._renderMetric(tt('avg-price'), this._fmtCents(t.avgPrice), `${cur}c/kWh`)}
          </div>
        </div>

        ${this._renderSavingsCard(t.savingsFixed, t.savingsDyn)}

        <div class="subcard subcard-charge">
          <div class="subcard-header">
            <ha-icon icon="mdi:car-arrow-left"></ha-icon>
            <span class="subcard-title">${tp('col.charge')}</span>
          </div>
          <div class="metric-grid">
            ${this._renderMetric(tp('col.energy'), this._fmtWh(t.chargeWh), 'Wh')}
            ${this._renderMetric(tp('col.cost'), this._fmtNum(t.chargeCost, 2), cur)}
            ${this._renderMetric(tp('col.emissions'), this._fmtNum(t.chargeCo2Kg, 1), 'kg CO\u2082')}
            ${this._renderMetric(tp('col.duration'), this._fmtDurationVal(t.chargeDurationMin), 'uur')}
          </div>
        </div>

        <div class="subcard subcard-discharge">
          <div class="subcard-header">
            <ha-icon icon="mdi:car-arrow-right"></ha-icon>
            <span class="subcard-title">${tp('col.discharge')}</span>
          </div>
          <div class="metric-grid">
            ${this._renderMetric(tp('col.energy'), this._fmtWh(t.dischargeWh), 'Wh')}
            ${this._renderMetric(tp('col.revenue'), this._fmtNum(t.dischargeRev, 2), cur, true)}
            ${this._renderMetric(tp('col.avoided-emissions'), this._fmtNum(t.dischargeCo2Kg, 1), 'kg CO\u2082')}
            ${this._renderMetric(tp('col.duration'), this._fmtDurationVal(t.dischargeDurationMin), 'uur')}
          </div>
        </div>
      </div>
    `;
  }

  private _renderTotalsSubcards_QH(
    t: any,
    hasRepaired: boolean,
  ): TemplateResult {
    const cur = this._currencySymbol();
    const tt = (key: string) => tp(`totals.${key}`);
    const netWh = t.chargeWh - t.dischargeWh;
    const netCost = t.chargeCost - t.dischargeRev;
    const netKwh = netWh / 1000;

    return html`
      <div class="totals-subcards-grid">
        <div class="subcard subcard-netto">
          ${this._renderEstimatedNote(hasRepaired)}
          <div class="subcard-header">
            <ha-icon icon="mdi:calculator-variant-outline"></ha-icon>
            <span class="subcard-title">${tp('col.net')}</span>
          </div>
          <div class="subcard-hero">
            ${this._fmtCents(netKwh !== 0 ? netCost / netKwh : null)}
            <span class="hero-unit">${cur}\u00a2ent/kWh</span>
          </div>
          <div class="metric-grid">
            ${this._renderMetric(tp('col.energy'), this._fmtWh(netWh), 'Wh')}
            ${this._renderMetric(tp('col.cost'), this._fmtNum(netCost, 2), cur, netCost < 0)}
            ${this._renderMetric(tp('col.emissions'), this._fmtNum(t.co2Kg, 1), 'kg CO\u2082', t.co2Kg < 0)}
            ${this._renderMetric(tt('avg-cons-price'), this._fmtCents(t.avgCons), `${cur}c/kWh`)}
          </div>
        </div>

        ${this._renderSavingsCard(t.savingsFixed, t.savingsDyn)}

        <div class="subcard subcard-charge">
          <div class="subcard-header">
            <ha-icon icon="mdi:car-arrow-left"></ha-icon>
            <span class="subcard-title">${tp('col.charge')}</span>
          </div>
          <div class="metric-grid">
            ${this._renderMetric(tp('col.energy'), this._fmtWh(t.chargeWh), 'Wh')}
            ${this._renderMetric(tp('col.cost'), this._fmtNum(t.chargeCost, 2), cur)}
            ${this._renderMetric(tp('col.emissions'), this._fmtNum(t.chargeCo2Kg, 1), 'kg CO\u2082')}
            ${this._renderMetric(tp('col.duration'), this._fmtDurationVal(t.chargeDurationMin), 'uur')}
          </div>
        </div>

        <div class="subcard subcard-discharge">
          <div class="subcard-header">
            <ha-icon icon="mdi:car-arrow-right"></ha-icon>
            <span class="subcard-title">${tp('col.discharge')}</span>
          </div>
          <div class="metric-grid">
            ${this._renderMetric(tp('col.energy'), this._fmtWh(t.dischargeWh), 'Wh')}
            ${this._renderMetric(tp('col.revenue'), this._fmtNum(t.dischargeRev, 2), cur, true)}
            ${this._renderMetric(tp('col.avoided-emissions'), this._fmtNum(t.dischargeCo2Kg, 1), 'kg CO\u2082')}
            ${this._renderMetric(tp('col.duration'), this._fmtDurationVal(t.dischargeDurationMin), 'uur')}
          </div>
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
        ${this._renderTotals()}

        <div class="table-container">
          ${this._error
            ? html`<div class="center error">${this._error}</div>`
            : this._renderTable()}
        </div>
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
