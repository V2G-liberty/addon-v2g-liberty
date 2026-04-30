import { css, html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity, HassEvent } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityBlock, renderEntityRow, renderLoadbalancerInfo, isLoadbalancerEnabled, renderButton } from './util/render';
import { partial, setLanguage } from './util/translate';
import { elapsedTimeSince } from './util/time';
import { callFunction } from './util/appdaemon';
import { styles } from './card.styles';
import { showChargerSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.charger');
const tc = partial('settings.common');

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
  @state() private _loadBalancerLimit: HassEntity;

  // Charger phase (from JSON settings, not HA entity)
  @state() private _connectedToPhase: number | number[] | null = null;
  @state() private _phaseRequired: boolean = false;

  private _hass: HomeAssistant;
  private _phaseLoaded: boolean = false;
  private _unsubPhase: (() => void) | null = null;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    const firstSet = !this._hass;
    this._hass = hass;
    setLanguage(hass.locale?.language ?? (hass as any).language);
    if (firstSet) {
      this._loadPhaseInfo();
      this._subscribeToPhaseEvents();
    }
    this._chargerSettingsInitialised =
      hass.states[entityIds.chargerSettingsInitialised];
    this._chargerHost = hass.states[entityIds.chargerHostname];
    this._chargerPort = hass.states[entityIds.chargerPort];
    this._chargerConnectionStatus =
      hass.states[entityIds.chargerConnectionStatus];
    this._useReducedMaxPower = hass.states[entityIds.useReducedMaxChargePower];
    this._chargerMaxChargingPower =
      hass.states[entityIds.chargerMaxChargingPower];
    this._chargerMaxDischargingPower =
      hass.states[entityIds.chargerMaxDischargingPower];
    this._loadBalancerLimit = hass.states[entityIds.quasarLoadBalancerLimit];
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
      </div>
      <div class="card-actions">
        ${renderButton(
          this._hass,
          editCallback,
          true,
          tc('configure')
        )}
      </div>
    `;
  }

  private _renderInitialisedContent() {
    const editCallback = () => showChargerSettingsDialog(this);
    const _isLoadBalancerEnabled = isLoadbalancerEnabled(this._loadBalancerLimit.state)
    return html`
      <div class="card-content">
        ${this._renderChargerConnectionStatus()}
        ${renderEntityBlock(this._hass, this._chargerHost)}
        ${renderEntityRow(this._chargerPort)}
        ${this._renderMaxChargeConfiguration()}
        ${this._renderChargerPhase()}
        ${renderLoadbalancerInfo(_isLoadBalancerEnabled)}
      </div>
      <div class="card-actions">
        ${renderButton(
          this._hass,
          editCallback,
          true,
          this._hass.localize('ui.common.edit')
        )}
      </div>
    `;
  }

  private _renderChargerConnectionStatus() {
    const state = this._chargerConnectionStatus.state;
    const isConnected = state === ChargerConnectionStatus.Connected;
    const hasConnectionError =
      state === ChargerConnectionStatus.ConnectionError || state === ChargerConnectionStatus.Failed;
    const error = tp('connection-error');
    const success = tp('connected-since', {
      time: elapsedTimeSince(this._chargerConnectionStatus.last_updated),
    });
    return isConnected
      ? html`<ha-alert alert-type="success">${success}</ha-alert>`
      : hasConnectionError
        ? html`<ha-alert alert-type="error">${error}</ha-alert>`
        : nothing;
  }

  private async _loadPhaseInfo() {
    try {
      const data = await callFunction(this._hass, 'get_charger_phase');
      this._connectedToPhase = data.connected_to_phase ?? null;
      this._phaseRequired = data.required ?? false;
      this._phaseLoaded = true;
    } catch (e) {
      // Ignore — phase info not available
    }
  }

  private async _subscribeToPhaseEvents() {
    this._unsubPhase = await this._hass.connection.subscribeEvents<HassEvent>(
      () => this._loadPhaseInfo(),
      'save_charger_phase.result'
    );
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._unsubPhase) {
      this._unsubPhase();
      this._unsubPhase = null;
    }
  }

  private _renderChargerPhase() {
    if (!this._phaseLoaded) return nothing;

    if (this._connectedToPhase === null) {
      if (this._phaseRequired) {
        return html`<ha-alert alert-type="warning">Charger phase not configured.</ha-alert>`;
      }
      return nothing;
    }

    const phaseValue = Array.isArray(this._connectedToPhase)
      ? this._connectedToPhase.map(p => `L${p}`).join(', ')
      : `L${this._connectedToPhase}`;

    return html`
      <ha-settings-row>
        <span slot="heading">
          <ha-icon icon="mdi:electric-switch"></ha-icon>&nbsp; &nbsp;
          Connected to phase
        </span>
        <div class="text-content value state">${phaseValue}</div>
      </ha-settings-row>
    `;
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
      : nothing;
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
