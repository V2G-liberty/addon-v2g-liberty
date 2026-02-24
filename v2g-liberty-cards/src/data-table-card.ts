import { css, html, LitElement, nothing, TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { callFunction } from './util/appdaemon';
import { partial } from './util/translate';

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

  setConfig(_config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
  }

  connectedCallback() {
    super.connectedCallback();
  }

  protected firstUpdated() {
    this._fetchData();
  }

  // ── Data fetching ─────────────────────────────────────────────

  private async _fetchData() {
    if (!this._hass) return;

    const { start, end } = this._getViewWindow();

    this._isLoading = true;
    this._error = null;

    try {
      const result = await callFunction(
        this._hass,
        'v2g_data_query',
        { start, end, granularity: this._granularity },
        30000
      );

      if (result.error) {
        this._error = result.error;
        this._data = [];
      } else {
        this._data = (result.data || []).slice().reverse();
        this._error = null;
      }
    } catch (e) {
      this._error = e instanceof Error ? e.message : 'Unknown error';
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
        d.setMonth(d.getMonth() + direction);
        break;
      case 'weeks':
        d.setMonth(d.getMonth() + 3 * direction);
        break;
      case 'months':
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

  private _fmtCurrency(value: number | null | undefined): string {
    if (value === null || value === undefined) return '−';
    const currency = this._getCurrency();
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(value);
    } catch {
      return `${value.toFixed(2)}`;
    }
  }

  private _fmtCents(value: number | null | undefined): string {
    if (value === null || value === undefined) return '−';
    return (value * 100).toFixed(3);
  }

  private _fmtPct(value: number | null | undefined, decimals = 1): string {
    if (value === null || value === undefined) return '−';
    return value.toFixed(decimals);
  }

  private _fmtWh(value: number | null | undefined): string {
    if (value === null || value === undefined) return '−';
    return Math.round(value).toString();
  }

  private _fmtKwh(value: number | null | undefined): string {
    if (value === null || value === undefined) return '−';
    return value.toFixed(2);
  }

  private _fmtKg(value: number | null | undefined): string {
    if (value === null || value === undefined) return '−';
    return value.toFixed(1);
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
    return String(d.getHours()).padStart(2, '0');
  }

  private _fmtDayDate(isoStr: string): string {
    if (!isoStr) return '−';
    const d = new Date(isoStr);
    const dayName = d.toLocaleDateString(undefined, { weekday: 'short' });
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    return `${dayName} ${dd}-${mm}`;
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
      case 'weeks':
        return this._fmtDayDate(isoStr);
      case 'months':
        return this._fmtMonthYear(isoStr);
      case 'years':
        return this._fmtYear(isoStr);
    }
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

    const cfg = STATE_ICONS[state];
    if (!cfg) return html`<span>${state}</span>`;

    const title = tp(`app-state.${state}`);
    return html`
      <ha-icon
        icon="${cfg.icon}"
        style="color: ${cfg.color}; --mdc-icon-size: 20px;"
        title="${title}"
      ></ha-icon>
    `;
  }

  private _renderPriceIndicator(
    rating: string | null | undefined
  ): TemplateResult {
    if (!rating) return html`<span>−</span>`;
    const level = rating.replace(/_/g, '-');
    const title = tp(`price-rating.${level}`);
    return html`
      <div class="price-indicator" data-level="${level}" title="${title}">
        <div class="fill"></div>
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

  // ── Table rendering per granularity ───────────────────────────

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
            <th>${this._col('soc', '%')}</th>
            <th class="num">${this._col('consumption', '¢/kWh')}</th>
            <th class="num">${this._col('production', '¢/kWh')}</th>
            <th class="indicator-col">${this._col('rate')}</th>
            <th class="num">${this._col('energy', 'Wh')}</th>
            <th class="num">${this._col('cost-revenue')}</th>
          </tr>
        </thead>
        <tbody>
          ${this._data.map(
            (row) => html`
              <tr>
                <td>${this._fmtTime(row.period_start)}</td>
                <td class="indicator-cell">${this._renderAppState(row.app_state)}</td>
                <td>${this._fmtPct(row.soc_pct)}</td>
                <td class="num">${this._fmtCents(row.consumption_price)}</td>
                <td class="num">${this._fmtCents(row.production_price)}</td>
                <td class="indicator-cell">${this._renderPriceIndicator(row.price_rating)}</td>
                <td class="num">${this._fmtWh(row.energy_wh)}</td>
                <td class="num">${this._fmtCostRevenue(row)}</td>
              </tr>
            `
          )}
        </tbody>
      </table>
    `;
  }

  private _renderHourTable(): TemplateResult {
    return html`
      <table>
        <thead>
          <tr>
            <th>${this._col('period')}</th>
            <th class="indicator-col">${this._col('status')}</th>
            <th>${this._col('soc', '%')}</th>
            <th class="num">${this._col('avg-price', '¢/kWh')}</th>
            <th class="indicator-col">${this._col('rate')}</th>
            <th class="num">${this._col('charge', 'Wh')}</th>
            <th class="num">${this._col('charge-cost')}</th>
            <th class="num">${this._col('discharge', 'Wh')}</th>
            <th class="num">${this._col('discharge-revenue')}</th>
          </tr>
        </thead>
        <tbody>
          ${this._data.map(
            (row) => html`
              <tr>
                <td>${this._fmtHour(row.period_start)}</td>
                <td class="indicator-cell">${this._renderAppState(row.app_state)}</td>
                <td>${this._fmtPct(row.soc_pct)}</td>
                <td class="num">${this._fmtCents(row.avg_price)}</td>
                <td class="indicator-cell">${this._renderPriceIndicator(row.price_rating)}</td>
                <td class="num">${this._fmtWh(row.charge_wh)}</td>
                <td class="num">${this._fmtCurrency(row.charge_cost)}</td>
                <td class="num">${this._fmtWh(row.discharge_wh)}</td>
                <td class="num">${this._fmtCurrency(row.discharge_revenue)}</td>
              </tr>
            `
          )}
        </tbody>
      </table>
    `;
  }

  private _renderDayTable(): TemplateResult {
    return html`
      <table>
        <thead>
          <tr>
            <th>${this._col('period')}</th>
            <th class="num">${this._col('availability', '%')}</th>
            <th class="num">${this._col('charge-kwh', 'kWh')}</th>
            <th class="num">${this._col('charge-cost')}</th>
            <th class="num">${this._col('discharge-kwh', 'kWh')}</th>
            <th class="num">${this._col('discharge-revenue')}</th>
            <th class="num">${this._col('net', 'kWh')}</th>
            <th class="num">${this._col('net-cost')}</th>
            <th class="num">${this._col('emissions', 'kg CO₂')}</th>
          </tr>
        </thead>
        <tbody>
          ${this._data.map(
            (row) => html`
              <tr>
                <td>${this._fmtPeriod(row.period_start)}</td>
                <td class="num">${this._fmtPct(row.availability_pct, 0)}</td>
                <td class="num">${this._fmtKwh(row.charge_kwh)}</td>
                <td class="num">${this._fmtCurrency(row.charge_cost)}</td>
                <td class="num">${this._fmtKwh(row.discharge_kwh)}</td>
                <td class="num">${this._fmtCurrency(row.discharge_revenue)}</td>
                <td class="num">${this._fmtKwh(row.net_kwh)}</td>
                <td class="num">${this._fmtCurrency(row.net_cost)}</td>
                <td class="num">${this._fmtKg(row.co2_kg)}</td>
              </tr>
            `
          )}
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
      <div class="page-layout">
        <ha-card
          .header=${`${tp('card-title')} — ${tp('granularity.' + this._granularity)}`}
        >
          <div class="table-container">
            ${this._isLoading
              ? html`<div class="center">
                  <ha-circular-progress indeterminate></ha-circular-progress>
                </div>`
              : this._error
                ? html`<div class="center error">${this._error}</div>`
                : this._data.length === 0
                  ? html`<div class="center muted">${tp('no-data')}</div>`
                  : this._renderTable()}
          </div>
        </ha-card>

        <ha-card .header=${'Totaal'}>
          <div class="card-content muted">
            &nbsp;
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

          ${GRANULARITIES.map(
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

  // ── Styles ────────────────────────────────────────────────────

  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      min-height: calc(100vh - 56px);
      padding: 8px;
      container-type: inline-size;
    }

    /* ── Page layout ──────────────────────────────── */

    .page-layout {
      display: grid;
      grid-template-columns: 1fr 300px;
      gap: 8px;
    }

    .page-layout ha-card {
      --ha-card-border-radius: 12px;
      --ha-card-border-width: 1px;
      --ha-card-border-color: var(--divider-color, #e0e0e0);
      overflow: hidden;
    }

    .page-layout > ha-card:last-child {
      align-self: start;
      position: sticky;
      top: 0;
    }

    @container (max-width: 1024px) {
      .page-layout {
        grid-template-columns: 1fr;
      }

      .page-layout > ha-card:last-child {
        order: -1;
        position: static;
      }
    }

    .card-content {
      padding: 0 16px 16px;
    }

    /* ── Floating bar ─────────────────────────────── */

    .floating-bar {
      margin-top: auto;
      position: sticky;
      bottom: 16px;
      align-self: center;
      z-index: 5;
    }

    .bar-content {
      display: flex;
      align-items: center;
      gap: 4px;
      background: var(--card-background-color, white);
      border-radius: 12px;
      padding: 4px 8px;
      box-shadow: 0px 4px 12px rgba(0, 0, 0, 0.25);
      min-height: 48px;
    }

    .bar-separator {
      width: 1px;
      height: 24px;
      background: var(--divider-color, #e0e0e0);
      margin: 0 4px;
    }

    .pill {
      border: none;
      border-radius: 18px;
      padding: 6px 12px;
      font-size: 13px;
      background: transparent;
      color: var(--primary-text-color);
      cursor: pointer;
      white-space: nowrap;
    }

    .pill:hover {
      background: var(--secondary-background-color, #f5f5f5);
    }

    .pill.active {
      background: var(--primary-color);
      color: var(--text-primary-color, #fff);
    }

    .date-wrapper {
      position: relative;
      text-align: center;
      cursor: pointer;
      min-width: 130px;
    }

    .date-label {
      font-size: 14px;
      font-weight: 500;
      color: var(--primary-text-color);
    }

    .date-input {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      opacity: 0;
      cursor: pointer;
    }

    /* ── Table ─────────────────────────────────────── */

    .table-container {
      max-height: calc(100vh - 140px);
      overflow-y: auto;
      padding-bottom: 80px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      font-variant-numeric: tabular-nums;
    }

    thead th {
      text-align: left;
      vertical-align: top;
      padding: 8px 12px;
      font-weight: 500;
      font-size: 12px;
      color: var(--primary-text-color);
      position: sticky;
      top: 0;
      z-index: 1;
      background: var(--card-background-color, white);
      box-shadow:
        0 1px 0 var(--divider-color, #e0e0e0),
        0 2px 4px rgba(0, 0, 0, 0.06);
    }

    thead th .unit {
      display: block;
      font-weight: 400;
      font-size: 11px;
      color: var(--secondary-text-color, #797979);
    }

    thead th.num {
      text-align: right;
    }

    tbody td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--divider-color, #e0e0e0);
      white-space: nowrap;
    }

    tbody td.num {
      text-align: right;
    }

    tbody tr:hover {
      background: var(--table-row-alternative-background-color, #f9f9f9);
    }

    .center {
      display: flex;
      justify-content: center;
      align-items: center;
      padding: 24px 0;
    }

    .muted {
      color: var(--secondary-text-color);
      font-size: 14px;
    }

    .error {
      color: var(--error-color, #db4437);
      font-size: 14px;
    }

    /* ── Price indicator ───────────────────────────── */

    .indicator-col,
    .indicator-cell {
      text-align: center;
    }

    .price-indicator {
      width: 12px;
      height: 24px;
      border-radius: 3px;
      position: relative;
      background: #e0e0e0;
      display: inline-block;
      box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.15);
    }

    .price-indicator .fill {
      position: absolute;
      bottom: 0;
      width: 100%;
      border-radius: 0 0 3px 3px;
      transition:
        height 0.2s ease,
        background-color 0.2s ease;
    }

    .price-indicator[data-level='very-low'] .fill {
      height: 20%;
      background-color: #90caf9;
    }
    .price-indicator[data-level='low'] .fill {
      height: 40%;
      background-color: #5c8dc9;
    }
    .price-indicator[data-level='average'] .fill {
      height: 60%;
      background-color: #7e57c2;
    }
    .price-indicator[data-level='high'] .fill {
      height: 80%;
      background-color: #6a1b9a;
    }
    .price-indicator[data-level='very-high'] .fill {
      height: 100%;
      background-color: #4a0072;
    }

    @media (prefers-color-scheme: dark) {
      .price-indicator {
        background: #2a2a2a;
        box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.15);
      }

      .price-indicator[data-level='very-low'] .fill {
        background-color: #37474f;
      }
      .price-indicator[data-level='low'] .fill {
        background-color: #5c6bc0;
      }
      .price-indicator[data-level='average'] .fill {
        background-color: #9575cd;
      }
      .price-indicator[data-level='high'] .fill {
        background-color: #ba68c8;
      }
      .price-indicator[data-level='very-high'] .fill {
        background-color: #e040fb;
      }
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'v2g-liberty-data-table-card': DataTableCard;
  }
}
