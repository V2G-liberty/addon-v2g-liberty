import { css, html, LitElement } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig, navigate } from 'custom-card-helpers';

import { partial, setLanguage } from './util/translate';
import * as entityIds from './entity-ids';

const tp = partial('homepage-stats');

@customElement('v2g-liberty-homepage-stats-card')
export class HomepageStatsCard extends LitElement {
  @state() private _chargedKwh: HassEntity;
  @state() private _chargeCost: HassEntity;
  @state() private _dischargedKwh: HassEntity;
  @state() private _dischargeRevenue: HassEntity;

  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    setLanguage(hass.locale?.language ?? (hass as any).language);
    this._chargedKwh = hass.states[entityIds.chargedTodayKwh];
    this._chargeCost = hass.states[entityIds.chargeCostToday];
    this._dischargedKwh = hass.states[entityIds.dischargedTodayKwh];
    this._dischargeRevenue = hass.states[entityIds.dischargeRevenueToday];
  }

  private _navigateToData() {
    
    navigate(this._hass, '/lovelace-yaml/data', true);
  }

  render() {
    const chargeCost = this._parseNumber(this._chargeCost);
    const dischargeRevenue = this._parseNumber(this._dischargeRevenue);
    const chargedKwh = this._parseNumber(this._chargedKwh);

    const netCost = chargeCost - dischargeRevenue;

    return html`
      <ha-card @click=${this._navigateToData}>
        <div class="header">
          <span class="title">${tp('header')}</span>
          <span class="details-link">${tp('details')} <ha-icon icon="mdi:chevron-right"></ha-icon></span>
        </div>
        <div class="values">
          <span class="cost">${this._formatNetCost(netCost)}</span>
          <span class="kwh">${chargedKwh.toFixed(2)} kWh</span>
        </div>
      </ha-card>
    `;
  }

  private _parseNumber(entity: HassEntity | undefined): number {
    if (!entity || entity.state === 'unavailable' || entity.state === 'unknown') {
      return 0;
    }
    const val = parseFloat(entity.state);
    return isNaN(val) ? 0 : val;
  }

  private _formatNetCost(value: number): string {
    if (value < 0) {
      return `- \u20AC ${Math.abs(value).toFixed(2)}`;
    }
    return `\u20AC ${value.toFixed(2)}`;
  }

  static styles = css`
    ha-card {
      cursor: pointer;
      padding: 16px;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .title {
      color: var(--secondary-text-color);
      font-size: 14px;
    }
    .details-link {
      display: flex;
      align-items: center;
      color: var(--primary-color);
      font-size: 14px;
    }
    .details-link ha-icon {
      --mdc-icon-size: 18px;
    }
    .values {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-top: 8px;
    }
    .cost,
    .kwh {
      font-size: 28px;
      font-weight: 500;
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'v2g-liberty-homepage-stats-card': HomepageStatsCard;
  }
}
