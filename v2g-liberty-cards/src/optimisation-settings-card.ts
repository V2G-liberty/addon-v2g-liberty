import { html, LitElement } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityRow } from './util/render';
import { partial } from './util/translate';
import { styles } from './card.styles';
import {
  showOptimisationModeDialog,
} from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.optimisation');

@customElement('v2g-liberty-optimisation-settings-card')
class OptimisationSettingsCard extends LitElement {
  @state() private _optimisationMode: HassEntity;

  // private property
  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) { }

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._optimisationMode = hass.states[entityIds.optimisationMode];
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
        ${this._renderOptimisationMode()}
      </div>
    `;
  }

  private _renderOptimisationMode() {
    const stateObj = this._optimisationMode;
    const callback = () =>
      showOptimisationModeDialog(this, {
        entity_id: entityIds.optimisationMode,
      });

    return html`${renderEntityRow(stateObj, { callback })}`;
  }
}
