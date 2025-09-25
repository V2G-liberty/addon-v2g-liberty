import { html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HomeAssistant, navigate } from 'custom-card-helpers';
import { HassEntity } from 'home-assistant-js-websocket';
import * as entityIds from './entity-ids';

import { callFunction } from './util/appdaemon';
import { partial } from './util/translate';

const tp = partial('settings-alert-card');

@customElement('v2g-liberty-settings-error-alert-card')
export class SettingsErrorAlertCard extends LitElement {
  @state() private _isSnackbarOpen: boolean = false;
  @state() private _adminSettingsInitialised: HassEntity;

  private _hass: HomeAssistant;

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._adminSettingsInitialised =
      hass.states[entityIds.adminSettingsInitialised];
  }


  render() {
    const _onSnackbarClose = () => this._isSnackbarOpen = false;
    const _hasError = this._adminSettingsInitialised.state !== 'on';

    const _navigateToSettings = () => {
      navigate(this._hass, '/lovelace-yaml/settings', true);
    };

    return _hasError
      ? nothing
      : html`
        <ha-toast
          ?open=${this._isSnackbarOpen}
          labelText=${tp('error')}
          @closed=${_onSnackbarClose}
        >
          <ha-button
            slot="action"
            @click=${_navigateToSettings}
            appearance="outlined"
            size="small"
          >
            ${tp('go_to_settings')}
          </ha-button>
        </ha-toast>
      `;
  }
}


declare global {
  interface HTMLElementTagNameMap {
    'v2g-liberty-settings-error-alert-card': SettingsErrorAlertCard;
  }
}
