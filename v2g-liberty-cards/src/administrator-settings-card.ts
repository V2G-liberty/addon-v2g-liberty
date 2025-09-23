import { html, LitElement } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityBlock, renderButton } from './util/render';
import { partial, t } from './util/translate';
import { styles } from './card.styles';
import { showAdministratorSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const tp = partial('settings.administrator');

@customElement('v2g-liberty-administrator-settings-card')
export class AdministratorSettingsCard extends LitElement {
  @state() private _adminSettingsInitialised: HassEntity;
  @state() private _adminMobileName: HassEntity;
  @state() private _adminMobilePlatform: HassEntity;

  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._adminSettingsInitialised =
      hass.states[entityIds.adminSettingsInitialised];
    this._adminMobileName = hass.states[entityIds.adminMobileName];
    this._adminMobilePlatform = hass.states[entityIds.adminMobilePlatform];
  }

  render() {
    const isInitialised = this._adminSettingsInitialised.state === 'on';
    const content = isInitialised
      ? this._renderInitialisedContent()
      : this._renderUninitialisedContent();
    return html`<ha-card header="${tp('header')}">${content}</ha-card>`;
  }

  private _renderInitialisedContent() {
    const editCallback = () => showAdministratorSettingsDialog(this);

    return html`
      <div class="card-content">
        <div class="description">${tp('sub-header')}</div>
        ${renderEntityBlock(this._adminMobileName)}
        ${renderEntityBlock(this._adminMobilePlatform)}
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

  private _renderUninitialisedContent() {
    const editCallback = () => showAdministratorSettingsDialog(this);
    const labelConfigure = this._hass.localize('ui.common.configure') || 'Configure'
    return html`
      <div class="card-content">
        <ha-alert alert-type="warning">${tp('alert')}</ha-alert>
        <div class="description">${tp('sub-header')}</div>
      </div>
      <div class="card-actions">
        ${renderButton(
          this._hass,
          editCallback,
          true,
          labelConfigure
        )}
      </div>
    `;
  }

  static styles = styles;
}
