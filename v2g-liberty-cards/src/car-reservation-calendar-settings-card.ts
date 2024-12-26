import { html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityBlock } from './util/render';
import { partial } from './util/translate';
import { styles } from './card.styles';
import { showCarReservationCalendarSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.car-reservation-calendar');

@customElement('v2g-liberty-car-reservation-calendar-settings-card')
export class CarReservationCalendarSettingsCard extends LitElement {
  @state() private _calendarSettingsInitialised: HassEntity;
  @state() private _carCalendarSource: HassEntity;
  @state() private _integrationCalendarEntityName: HassEntity;
  @state() private _calendarAccountUrl: HassEntity;
  @state() private _calendarAccountUsername: HassEntity;
  @state() private _carCalendarName: HassEntity;

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
        <div class="button-row">
          <mwc-button @click=${editCallback}>
            ${this._hass.localize('ui.common.configure') || 'Configure'}
          </mwc-button>
        </div>
      </div>
    `;
  }

  private _renderInitialisedContent() {
    const editCallback = () => showCarReservationCalendarSettingsDialog(this);

    return html`
      <div class="card-content">
        ${this._renderCaldavDetails()} ${this._renderHomeAssistantDetails()}
        <div class="button-row">
          <mwc-button @click=${editCallback}>
            ${this._hass.localize('ui.common.edit')}
          </mwc-button>
        </div>
      </div>
    `;
  }

  private _renderCaldavDetails() {
    return this._carCalendarSource.state === 'Direct caldav source'
      ? html`
          <p>
            ${tp('type')}: <strong>${this._carCalendarSource.state}</strong>
          </p>
          ${renderEntityBlock(this._calendarAccountUrl)}
          ${renderEntityBlock(this._calendarAccountUsername)}
          ${renderEntityBlock(this._carCalendarName)}
        `
      : nothing;
  }

  private _renderHomeAssistantDetails() {
    if (this._carCalendarSource.state === 'Home Assistant integration') {
      const calendarStateObj =
        this._hass.states[this._integrationCalendarEntityName.state];
      return html`
        <p>${tp('type')}: <strong>${this._carCalendarSource.state}</strong></p>
        ${renderEntityBlock(this._integrationCalendarEntityName, {
          state: calendarStateObj.attributes.friendly_name,
        })}
      `;
    }
    return nothing;
  }
}
