import { css, html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityBlock, renderEntityRow } from './util/render';
import { partial } from './util/translate';
import { elapsedTimeSince } from './util/time';
import { styles } from './card.styles';
import { showChargerSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.charger');

enum ChargerConnectionStatus {
  Connected = 'Successfully connected',
  Failed = 'Failed to connect',
  ConnectionError = 'Connection error',
}

@customElement('v2g-liberty-charger-settings-card')
export class ChargerSettingsCard extends LitElement {
  @state() private _chargerSettingsInitialised: HassEntity;
  @state() private _chargerHost: HassEntity;
  @state() private _chargerPort: HassEntity;
  @state() private _chargerConnectionStatus: HassEntity;
  @state() private _useReducedMaxPower: HassEntity;
  @state() private _chargerMaxChargingPower: HassEntity;
  @state() private _chargerMaxDischargingPower: HassEntity;

  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._chargerSettingsInitialised =
      hass.states[entityIds.chargerSettingsInitialised];
    this._chargerHost = hass.states[entityIds.chargerHostUrl];
    this._chargerPort = hass.states[entityIds.chargerPort];
    this._chargerConnectionStatus =
      hass.states[entityIds.chargerConnectionStatus];
    this._useReducedMaxPower = hass.states[entityIds.useReducedMaxChargePower];
    this._chargerMaxChargingPower =
      hass.states[entityIds.chargerMaxChargingPower];
    this._chargerMaxDischargingPower =
      hass.states[entityIds.chargerMaxDischargingPower];
  }

  render() {
    const header = tp('header');
    const isInitialised = this._chargerSettingsInitialised.state === 'on';
    const content = isInitialised
      ? this._renderInitialisedContent()
      : this._renderUninitialisedContent();
    return html`<ha-card header="${header}">${content}</ha-card>`;
  }

  private _renderUninitialisedContent() {
    const editCallback = () => showChargerSettingsDialog(this);

    return html`
      <div class="card-content">
        <ha-alert alert-type="warning">${tp('alert')}</ha-alert>
        <div class="card-actions">
          <mwc-button test-id="configure" @click=${editCallback}>
            ${this._hass.localize('ui.common.configure') || 'Configure'}
          </mwc-button>
        </div>
      </div>
    `;
  }

  private _renderInitialisedContent() {
    const info = tp('safety-info');
    const editCallback = () => showChargerSettingsDialog(this);

    return html`
      <div class="card-content">
        ${this._renderChargerConnectionStatus()}
        ${renderEntityBlock(this._chargerHost)}
        ${renderEntityRow(this._chargerPort)}
        ${this._renderMaxChargeConfiguration()}
        <ha-alert alert-type="info">
          <ha-markdown breaks .content=${info}></ha-markdown>
        </ha-alert>
        <div class="card-actions">
          <mwc-button test-id="edit" @click=${editCallback}>
            ${this._hass.localize('ui.common.edit')}
          </mwc-button>
        </div>
      </div>
    `;
  }

  private _renderChargerConnectionStatus() {
    const state = this._chargerConnectionStatus.state;
    const isConnected = state === ChargerConnectionStatus.Connected;
    const hasConnectionError =
      state === ChargerConnectionStatus.ConnectionError || state === ChargerConnectionStatus.Failed;
    const error = tp('connection-error');
    const success = tp('connection-success', {
      time: elapsedTimeSince(this._chargerConnectionStatus.last_updated),
    });
    return isConnected
      ? html`<ha-alert alert-type="success">${success}</ha-alert>`
      : hasConnectionError
        ? html`<ha-alert alert-type="error">${error}</ha-alert>`
        : nothing;
  }

  private _renderMaxChargeConfiguration() {
    const maxAvailablePower =
      this._hass.states[entityIds.chargerMaxAvailablePower].state;
    const maxPowerDescription = tp('max-power-description', {
      value: maxAvailablePower,
    });
    const isUsingReducedMaxPower = this._useReducedMaxPower.state === 'on';
    const usingReducedMaxPowerDescription = isUsingReducedMaxPower
      ? tp('reduce-max-power-description')
      : tp('do-not-reduce-max-power-description');
    const maxChargingPowerEntityRows = isUsingReducedMaxPower
      ? html`
          ${renderEntityRow(this._chargerMaxChargingPower, {
        state: this._hass.formatEntityState(this._chargerMaxChargingPower),
      })}
          ${renderEntityRow(this._chargerMaxDischargingPower, {
        state: this._hass.formatEntityState(
          this._chargerMaxDischargingPower
        ),
      })}
        `
      : nothing;

    return html`
      <p>
        <ha-markdown breaks .content=${maxPowerDescription}></ha-markdown>
      </p>
      <p>
        <ha-markdown
          breaks
          .content=${usingReducedMaxPowerDescription}
        ></ha-markdown>
        ${maxChargingPowerEntityRows}
      </p>
    `;
  }

  static styles = [
    styles,
    css`
        .name {
          font-weight: bold;
        }
      `
  ];
}
