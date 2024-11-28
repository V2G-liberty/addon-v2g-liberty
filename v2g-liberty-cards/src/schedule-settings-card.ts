import { html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { partial, t } from './util/translate';
import { showScheduleSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.schedule');

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
        <mwc-button @click=${editCallback}>
          ${this._hass.localize('ui.common.configure') || 'Configure'}
        </mwc-button>
      </div>
    `;
  }

  private _renderInitialisedContent() {
    // TODO: Add warning for connection problems
    const editCallback = () => showScheduleSettingsDialog(this);
    const isUsingOtherServer = this._fmUseOtherServer.state === 'on';

    const useDefaultServer = tp('use-default-server');
    const useOtherServer = tp('use-other-server');

    return html`
      <div class="card-content">
        ${this._renderEntityRow(this._fmAccountUsername)}
        ${isUsingOtherServer
          ? html`
              <p>${useOtherServer}</p>
              ${this._renderEntityRow(this._fmHostUrl)}
            `
          : html` <p>${useDefaultServer}</p> `}
        ${this._renderEntityRow(this._fmAsset)}
        <mwc-button @click=${editCallback}>
          ${this._hass.localize('ui.common.edit')}
        </mwc-button>
      </div>
    `;
  }

  private _renderEntityRow(stateObj) {
    const stateLabel = t(stateObj.state) || stateObj.state;
    const nameLabel =
      t(stateObj.entity_id) || stateObj.attributes.friendly_name;
    return html`
      <ha-settings-row>
        <span slot="heading">
          <ha-icon .icon=${stateObj.attributes.icon}></ha-icon>
          ${stateLabel}
        </span>
        <span slot="description">${nameLabel}</span>
      </ha-settings-row>
    `;
  }
}
