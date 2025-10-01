import { LitElement, nothing } from 'lit';
import { customElement } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';
import { showSettingsErrorAlertDialog } from './show-dialogs';
import { hasUninitializedEntities } from './util/settings-error-alert';

@customElement('settings-error-alert-card')
export class SettingsErrorAlertCard extends LitElement {
  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._checkUnInitialisedEntities();
  }

  private async _checkUnInitialisedEntities() {
    if (hasUninitializedEntities(this._hass)) {
      await showSettingsErrorAlertDialog(this);
    }
  }

  protected render() {
    return nothing;
  }
}
