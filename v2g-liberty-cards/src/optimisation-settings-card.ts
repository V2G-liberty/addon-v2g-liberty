import { html, LitElement } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityRow } from './util/render';
import { partial, setLanguage } from './util/translate';
import { styles } from './card.styles';
import { showOptimisationModeDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.optimisation');

/**
 * What to optimise the schedules for. The three scheduling limits that used to
 * sit here (lower limit, upper limit, allowed duration above the upper limit)
 * moved to the car settings: they belong to a car, not to the installation,
 * and different cars can have different limits.
 */
@customElement('v2g-liberty-optimisation-settings-card')
class OptimisationSettingsCard extends LitElement {
  @state() private _optimisationMode: HassEntity;

  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    setLanguage(hass.locale?.language ?? (hass as any).language);
    this._optimisationMode = hass.states[entityIds.optimisationMode];
  }

  static styles = styles;

  render() {
    const header = tp('header');
    return html`<ha-card header="${header}">
      <div class="card-content">
        <p>${tp('description')}</p>
        ${this._renderOptimisationMode()}
      </div>
    </ha-card>`;
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
