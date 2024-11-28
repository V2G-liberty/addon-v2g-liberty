import { html, LitElement, TemplateResult, nothing, CSSResultGroup } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { t, partial } from './util/translate';
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
        ${this._renderEntityRow(this._adminMobileName)}
        ${this._renderEntityRow(this._adminMobilePlatform)}
        <mwc-button @click=${editCallback}>
          ${this._hass.localize('ui.common.edit')}
        </mwc-button>
      </div>
    `;
  }

  private _renderUninitialisedContent() {
    const editCallback = () => showAdministratorSettingsDialog(this);

    return html`
      <div class="card-content">
        <ha-alert alert-type="warning">${tp('alert')}</ha-alert>
        <div class="description">${tp('sub-header')}</div>
        <mwc-button @click=${editCallback}>
          ${this._hass.localize('ui.common.configure') || 'Configure'}
        </mwc-button>
      </div>
    `;
  }

  private _renderEntityRow(stateObj) {
    const stateLabel = t(stateObj.state) || stateObj.state;
    return html`
      <ha-settings-row>
        <span slot="heading" test-id="${stateObj.entity_id}">
          <ha-icon .icon=${stateObj.attributes.icon}></ha-icon>
          ${stateLabel}
        </span>
        <span slot="description">${stateObj.attributes.friendly_name}</span>
      </ha-settings-row>
    `;
  }

  static styles = styles;
}
