import { html, LitElement, css, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityBlock } from './util/render';
import { partial } from './util/translate';
import { elapsedTimeSince } from './util/time';
import { styles } from './card.styles';
import { showScheduleSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.schedule');

enum ServerConnectionStatus {
  Connected = 'Successfully connected',
  Failed = 'Failed to connect',
  ConnectionError = 'Connection error',
}
@customElement('v2g-liberty-schedule-settings-card')
export class ScheduleSettingsCard extends LitElement {
  @state() private _scheduleSettingsInitialised: HassEntity;
  @state() private _fmAccountUsername: HassEntity;
  @state() private _fmUseOtherServer: HassEntity;
  @state() private _fmHostUrl: HassEntity;
  @state() private _fmConnectionStatus: HassEntity;
  @state() private _fmAsset: HassEntity;

  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  static styles = styles;

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._scheduleSettingsInitialised =
      hass.states[entityIds.scheduleSettingsInitialised];
    this._fmAccountUsername = hass.states[entityIds.fmAccountUsername];
    this._fmUseOtherServer = hass.states[entityIds.fmUseOtherServer];
    this._fmHostUrl = hass.states[entityIds.fmHostUrl];
    this._fmConnectionStatus = hass.states[entityIds.fmConnectionStatus];
    this._fmAsset = hass.states[entityIds.fmAsset];
  }

  render() {
    const header = tp('header');
    const isInitialised = this._scheduleSettingsInitialised.state === 'on';
    const content = isInitialised
      ? this._renderInitialisedContent()
      : this._renderUninitialisedContent();
    return html`<ha-card header="${header}">${content}</ha-card>`;
  }

  private _renderUninitialisedContent() {
    const alert = tp('alert');
    const editCallback = () => showScheduleSettingsDialog(this);

    return html`
      <div class="card-content">
        <ha-alert alert-type="warning">${alert}</ha-alert>
        <div class="card-actions">
          <mwc-button @click=${editCallback}>
            ${this._hass.localize('ui.common.configure') || 'Configure'}
          </mwc-button>
        </div>
      </div>
    `;
  }

  private _renderInitialisedContent() {
    const editCallback = () => showScheduleSettingsDialog(this);
    const isUsingOtherServer = this._fmUseOtherServer.state === 'on';
    const useOtherServer = tp('use-other-server');

    return html`
      <div class="card-content">
        ${this._renderFMConnectionStatus()}
        ${renderEntityBlock(this._fmAccountUsername)}
        ${isUsingOtherServer
          ? html`
              <p>${useOtherServer}</p>
              ${renderEntityBlock(this._fmHostUrl)}
            `
          : nothing }
        ${renderEntityBlock(this._fmAsset)}
        <div class="card-actions">
          <mwc-button @click=${editCallback}>
            ${this._hass.localize('ui.common.edit')}
          </mwc-button>
        </div>
      </div>
    `;
  }

  private _renderFMConnectionStatus() {
    const state = this._fmConnectionStatus.state;
    const isConnected = state === ServerConnectionStatus.Connected;
    const hasConnectionError =
      state === ServerConnectionStatus.ConnectionError || state === ServerConnectionStatus.Failed;
    const error = tp('connection-error');
    const success = tp('connection-success', {
      time: elapsedTimeSince(this._fmConnectionStatus.last_updated),
    });
    return isConnected
      ? html`<ha-alert alert-type="success">${success}</ha-alert>`
      : hasConnectionError
        ? html`<ha-alert alert-type="error">${error}</ha-alert>`
        : html`<ha-alert alert-type="error">${state}</ha-alert>`;
  }
}
