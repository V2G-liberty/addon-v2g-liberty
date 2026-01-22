import { html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityRow, renderEntityBlock, renderButton } from './util/render';
import { partial } from './util/translate';
import { styles } from './card.styles';
import { showCarSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.car');
const tc = partial('settings.common');

@customElement('v2g-liberty-car-settings-card')
class CarSettingsCard extends LitElement {
  @state() private _carName: HassEntity;
  @state() private _usableCapacity: HassEntity;
  @state() private _roundtripEfficiency: HassEntity;
  @state() private _carEnergyConsumption: HassEntity;

  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._carName = hass.states[entityIds.carName];
    this._usableCapacity = hass.states[entityIds.usableCapacity];
    this._roundtripEfficiency = hass.states[entityIds.roundtripEfficiency];
    this._carEnergyConsumption = hass.states[entityIds.carEnergyConsumption];
  }

  static styles = styles;

  render() {
    const isInitialised = this._carName?.attributes.initialised;

    const header = isInitialised ? this._carName.state : tp('header');

    const content = isInitialised
      ? this._renderInitialisedContent()
      : this._renderUninitialisedContent();

    return html`<ha-card header="${header}">${content}</ha-card>`;
  }

  private _renderUninitialisedContent() {
    const editCallback = () => showCarSettingsDialog(this);

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
    const editCallback = () => showCarSettingsDialog(this);

    return html`
      <div class="card-content">
        ${renderEntityRow(this._usableCapacity, {
          state: this._hass.formatEntityState(this._usableCapacity)
        })}
        ${renderEntityRow(this._roundtripEfficiency, {
          state: this._hass.formatEntityState(this._roundtripEfficiency)
        })}
        ${renderEntityRow(this._carEnergyConsumption, {
          state: this._hass.formatEntityState(this._carEnergyConsumption)
        })}
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
}
