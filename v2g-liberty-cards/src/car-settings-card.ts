import { html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityRow } from './util/render';
import { partial } from './util/translate';
import { styles } from './card.styles';
import {
  showCarBatteryUsableCapacityDialog,
  showRoundtripEfficiencyDialog,
  showCarEnergyConsumptionDialog,
} from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.car');

@customElement('v2g-liberty-car-settings-card')
class CarSettingsCard extends LitElement {
  @state() private _usableCapacity: HassEntity;
  @state() private _roundtripEfficiency: HassEntity;
  @state() private _carEnergyConsumption: HassEntity;

  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._usableCapacity = hass.states[entityIds.usableCapacity];
    this._roundtripEfficiency = hass.states[entityIds.roundtripEfficiency];
    this._carEnergyConsumption = hass.states[entityIds.carEnergyConsumption];
  }

  static styles = styles;

  render() {
    const header = tp('header');
    const content = this._renderContent();
    return html`<ha-card header="${header}">${content}</ha-card>`;
  }

  private _renderContent() {
    return html`
      <div class="card-content">
        ${this._renderNotInitialisedAlert()} ${this._renderUsableCapacity()}
        ${this._renderRoundtripEfficiency()}
        ${this._renderCarEnergyConsumption()}
      </div>
    `;
  }

  private _renderNotInitialisedAlert() {
    const isInitialised =
      this._usableCapacity.attributes.initialised &&
      this._roundtripEfficiency.attributes.initialised &&
      this._carEnergyConsumption.attributes.initialised;
    return isInitialised
      ? nothing
      : html`<ha-alert alert-type="warning">${tp('alert')}</ha-alert`;
  }

  private _renderUsableCapacity() {
    const stateObj = this._usableCapacity;
    const state = this._hass.formatEntityState(stateObj);
    const callback = () =>
      showCarBatteryUsableCapacityDialog(this, {
        entity_id: entityIds.usableCapacity,
      });

    return html`<div>${renderEntityRow(stateObj, { callback, state })}</div>`;
  }

  private _renderRoundtripEfficiency() {
    const stateObj = this._roundtripEfficiency;
    const state = this._hass.formatEntityState(stateObj);
    const callback = () =>
      showRoundtripEfficiencyDialog(this, {
        entity_id: entityIds.roundtripEfficiency,
      });

    return html`<div>${renderEntityRow(stateObj, { callback, state })}</div>`;
  }

  private _renderCarEnergyConsumption() {
    const stateObj = this._carEnergyConsumption;
    const state = this._hass.formatEntityState(stateObj);
    const callback = () =>
      showCarEnergyConsumptionDialog(this, {
        entity_id: entityIds.carEnergyConsumption,
      });

    return html`<div>${renderEntityRow(stateObj, { callback, state })}</div>`;
  }
}
