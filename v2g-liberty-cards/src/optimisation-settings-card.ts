import { html, LitElement } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityRow } from './util/render';
import { partial, t } from './util/translate';
import { styles } from './card.styles';
import {
  showOptimisationModeDialog,
  showCarBatteryLowerChargeLimitDialog,
  showCarBatteryUpperChargeLimitDialog,
  showAllowedDurationAboveMaxDialog,
} from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.optimisation');

@customElement('v2g-liberty-optimisation-settings-card')
class OptimisationSettingsCard extends LitElement {
  @state() private _optimisationMode: HassEntity;
  @state() private _lowerChargeLimit: HassEntity;
  @state() private _upperChargeLimit: HassEntity;
  @state() private _allowedDurationAboveMax: HassEntity;

  // private property
  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._optimisationMode = hass.states[entityIds.optimisationMode];
    this._lowerChargeLimit = hass.states[entityIds.lowerChargeLimit];
    this._upperChargeLimit = hass.states[entityIds.upperChargeLimit];
    this._allowedDurationAboveMax =
      hass.states[entityIds.allowedDurationAboveMax];
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
        <p>${tp('description')}</p>
        ${this._renderOptimisationMode()} ${this._renderLowerChargeLimit()}
        ${this._renderUpperChargeLimit()}
        ${this._renderAllowedDurationAboveMax()}
      </div>
    `;
  }

  private _renderOptimisationMode() {
    const stateObj = this._optimisationMode;
    const callback = () =>
      showOptimisationModeDialog(this, {
        entity_id: entityIds.optimisationMode,
      });

    return html`<div>${renderEntityRow(stateObj, { callback })}</div>`;
  }

  private _renderLowerChargeLimit() {
    const stateObj = this._lowerChargeLimit;
    const callback = () =>
      showCarBatteryLowerChargeLimitDialog(this, {
        entity_id: entityIds.lowerChargeLimit,
      });

    return html` <div>${renderEntityRow(stateObj, { callback })}</div> `;
  }

  private _renderUpperChargeLimit() {
    const stateObj = this._upperChargeLimit;
    const callback = () =>
      showCarBatteryUpperChargeLimitDialog(this, {
        entity_id: entityIds.upperChargeLimit,
      });

    return html` <div>${renderEntityRow(stateObj, { callback })}</div> `;
  }

  private _renderAllowedDurationAboveMax() {
    const stateObj = this._allowedDurationAboveMax;
    const callback = () =>
      showAllowedDurationAboveMaxDialog(this, {
        entity_id: entityIds.allowedDurationAboveMax,
      });

    return html`<div>${renderEntityRow(stateObj, { callback })}</div>`;
  }
}
