import { html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityBlock, renderButton } from './util/render';
import { t, partial } from './util/translate';
import { styles } from './card.styles';
import { elapsedTimeSince } from './util/time';
import { showCarReservationCalendarSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.car-reservation-calendar');
const tc = partial('settings.common');

enum CaldavConnectionStatus {
  Connected = 'Successfully connected',
  Failed = 'Failed to connect',
  ConnectionError = 'Connection error',
}

@customElement('v2g-liberty-car-reservation-calendar-settings-card')
export class CarReservationCalendarSettingsCard extends LitElement {
  @state() private _calendarSettingsInitialised: HassEntity;
  @state() private _carCalendarSource: HassEntity;
  @state() private _integrationCalendarEntityName: HassEntity;
  @state() private _calendarAccountUrl: HassEntity;
  @state() private _calendarAccountUsername: HassEntity;
  @state() private _carCalendarName: HassEntity;
  @state() private _caldavConnectionStatus: HassEntity;

  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  static styles = styles;

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._calendarSettingsInitialised =
      hass.states[entityIds.calendarSettingsInitialised];
    this._carCalendarSource = hass.states[entityIds.carCalendarSource];
    this._integrationCalendarEntityName =
      hass.states[entityIds.integrationCalendarEntityName];
    this._calendarAccountUrl = hass.states[entityIds.calendarAccountUrl];
    this._calendarAccountUsername =
      hass.states[entityIds.calendarAccountUsername];
    this._carCalendarName = hass.states[entityIds.carCalendarName];
    this._caldavConnectionStatus = hass.states[entityIds.calendarAccountConnectionStatus]
  }

  render() {
    const isInitialised = this._calendarSettingsInitialised.state === 'on';
    const content = isInitialised
      ? this._renderInitialisedContent()
      : this._renderUninitialisedContent();
    return html`<ha-card header="${tp('header')}">${content}</ha-card>`;
  }

  private _renderUninitialisedContent() {
    const editCallback = () => showCarReservationCalendarSettingsDialog(this);

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
    const editCallback = () => showCarReservationCalendarSettingsDialog(this);
    const isRemoteCaldav = this._carCalendarSource.state === 'remoteCaldav'
    const content = isRemoteCaldav
      ? this._renderCaldavDetails()
      : this._renderHomeAssistantDetails();

    return html`
      <div class="card-content">
        ${content}
      </div>
      <div class="card-actions">
        ${renderButton(
          this.hass,
          editCallback,
          true,
          this._hass.localize('ui.common.edit')
        )}
      </div>
    `;
  }

  private _renderCaldavDetails() {
    const title = tp(`source-selection.${this._carCalendarSource.state}.title`)
    return html`
          ${this._renderCaldavConnectionStatus()}
          <p>
            ${tp('type')}: <strong>${title}</strong>
          </p>
          ${renderEntityBlock(this._calendarAccountUrl)}
          ${renderEntityBlock(this._calendarAccountUsername)}
          ${renderEntityBlock(this._carCalendarName)}
        `
  }

  private _renderCaldavConnectionStatus() {
    const state = this._caldavConnectionStatus.state;
    const isConnected = state === CaldavConnectionStatus.Connected;
    const hasConnectionError =
      state ===  CaldavConnectionStatus.ConnectionError || state === CaldavConnectionStatus.Failed;
    const error = tp('connection-error');
    const success = tp('connection-success', {
      time: elapsedTimeSince(this._caldavConnectionStatus.last_updated),
    });
    return isConnected
      ? html`<ha-alert alert-type="success">${success}</ha-alert>`
      : hasConnectionError
        ? html`<ha-alert alert-type="error">${error}</ha-alert>`
        : nothing;
  }

  private _renderHomeAssistantDetails() {
    const calendarStateObj =
      this._hass.states[this._integrationCalendarEntityName.state];
    const title = tp(`source-selection.${this._carCalendarSource.state}.title`)

    return html`
        <p>${tp('type')}: <strong>${title}</strong></p>
        ${renderEntityBlock(this._integrationCalendarEntityName, {
          state: calendarStateObj.attributes.friendly_name,
        })}
      `;
  }
}
