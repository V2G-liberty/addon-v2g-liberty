import { html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { partial, t } from './util/translate';
import { showElectricityContractSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

const to = partial('option');
const tp = partial('settings.electricity-contract');

@customElement('v2g-liberty-electricity-contract-settings-card')
export class ElectricityContractSettingsCard extends LitElement {
  @state() private _electricityContractSettingsInitialised: HassEntity;
  @state() private _electricityProvider: HassEntity;
  @state() private _energyPriceVat: HassEntity;
  @state() private _energyPriceMarkup: HassEntity;
  @state() private _ownConsumptionPriceEntityId: HassEntity;
  @state() private _ownProductionPriceEntityId: HassEntity;
  @state() private _octopusImportCode: HassEntity;
  @state() private _octopusExportCode: HassEntity;
  @state() private _gbDnoRegion: HassEntity;

  private _hass: HomeAssistant;

  setConfig(config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    this._hass = hass;
    this._electricityContractSettingsInitialised =
      hass.states[entityIds.electricityContractSettingsInitialised];
    this._electricityProvider = hass.states[entityIds.electricityContract];
    this._energyPriceVat = hass.states[entityIds.energyPriceVat];
    this._energyPriceMarkup = hass.states[entityIds.energyPriceMarkup];
    this._ownConsumptionPriceEntityId =
      hass.states[entityIds.ownConsumptionPriceEntityId];
    this._ownProductionPriceEntityId =
      hass.states[entityIds.ownProductionPriceEntityId];
    this._octopusImportCode = hass.states[entityIds.octopusImportCode];
    this._octopusExportCode = hass.states[entityIds.octopusExportCode];
    this._gbDnoRegion = hass.states[entityIds.gbDnoRegion];
  }

  render() {
    const header = tp('header');
    const isInitialised =
      this._electricityContractSettingsInitialised.state === 'on';
    const content = isInitialised
      ? this._renderInitialisedContent()
      : this._renderUninitialisedContent();
    return html`<ha-card header="${header}">${content}</ha-card>`;
  }

  private _renderUninitialisedContent() {
    const alert = tp('alert');
    const editCallback = () => showElectricityContractSettingsDialog(this);

    return html`
      <div class="card-content">
        <ha-alert alert-type="warning">${alert}</ha-alert>
        <mwc-button @click=${editCallback}>
          ${this._hass.localize('ui.common.configure') || 'Configure'}
        </mwc-button>
      </div>
    `;
  }

  private _renderInitialisedContent() {
    const editCallback = () => showElectricityContractSettingsDialog(this);

    return html`
      <div class="card-content">
        ${this._renderEntityBlock(this._electricityProvider)}
        ${this._renderNLGenericContractDetails()}
        ${this._renderAmberContractDetails()}
        ${this._renderOctopusContractDetails()}
        <mwc-button @click=${editCallback}>
          ${this._hass.localize('ui.common.edit')}
        </mwc-button>
      </div>
    `;
  }

  private _renderEntityBlock(stateObj) {
    const stateLabel = to(stateObj.state) || stateObj.state;
    const description =
      t(stateObj.entity_id) || stateObj.attributes.friendly_name;
    return html`
      <ha-settings-row>
        <span slot="heading">
          <ha-icon .icon=${stateObj.attributes.icon}></ha-icon>
          ${stateLabel}
        </span>
        <span slot="description">${description}</span>
      </ha-settings-row>
    `;
  }

  private _renderEntityRow(stateObj) {
    const stateLabel =
      t(stateObj.state) || this._hass.formatEntityState(stateObj);
    const description =
      t(stateObj.entity_id) || stateObj.attributes.friendly_name;
    return html`
      <ha-settings-row>
        <span slot="heading">
          <ha-icon .icon=${stateObj.attributes.icon}></ha-icon>
          ${description}
        </span>
        <div class="text-content value state">${stateLabel}</div>
      </ha-settings-row>
    `;
  }

  private _renderNLGenericContractDetails() {
    return this._electricityProvider.state === 'nl_generic'
      ? html`
          ${this._renderEntityRow(this._energyPriceVat)}
          ${this._renderEntityRow(this._energyPriceMarkup)}
        `
      : nothing;
  }

  private _renderAmberContractDetails() {
    return this._electricityProvider.state === 'au_amber_electric'
      ? html`
          ${this._renderEntityBlock(this._ownConsumptionPriceEntityId)}
          ${this._renderEntityBlock(this._ownProductionPriceEntityId)}
        `
      : nothing;
  }

  private _renderOctopusContractDetails() {
    return this._electricityProvider.state === 'gb_octopus_energy'
      ? html`
          ${this._renderEntityBlock(this._octopusImportCode)}
          ${this._renderEntityBlock(this._octopusExportCode)}
          ${this._renderEntityBlock(this._gbDnoRegion)}
        `
      : nothing;
  }
}
