import { html, LitElement, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';

import { renderEntityBlock, renderEntityRow, renderButton } from './util/render';
import { partial } from './util/translate';
import { styles } from './card.styles';
import { showElectricityContractSettingsDialog } from './show-dialogs';
import * as entityIds from './entity-ids';

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

  static styles = styles;

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
    const labelConfigure = this._hass.localize('ui.common.configure') || 'Configure'

    return html`
      <div class="card-content">
        <ha-alert alert-type="warning">${alert}</ha-alert>
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

  private _renderInitialisedContent() {
    const editCallback = () => showElectricityContractSettingsDialog(this);

    return html`
      <div class="card-content">
        ${renderEntityBlock(this._electricityProvider)}
        ${this._renderNLGenericContractDetails()}
        ${this._renderAmberContractDetails()}
        ${this._renderOctopusContractDetails()}
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

  private _renderNLGenericContractDetails() {
    return this._electricityProvider.state === 'nl_generic'
      ? html`
          ${renderEntityRow(this._energyPriceVat, {
            // @ts-ignore
            state: this._hass.formatEntityState(this._energyPriceVat),
          })}
          ${renderEntityRow(this._energyPriceMarkup, {
            // @ts-ignore
            state: this._hass.formatEntityState(this._energyPriceMarkup),
          })}
        `
      : nothing;
  }

  private _renderAmberContractDetails() {
    return this._electricityProvider.state === 'au_amber_electric'
      ? html`
          ${renderEntityBlock(this._ownConsumptionPriceEntityId)}
          ${renderEntityBlock(this._ownProductionPriceEntityId)}
        `
      : nothing;
  }

  private _renderOctopusContractDetails() {
    return this._electricityProvider.state === 'gb_octopus_energy'
      ? html`
          ${renderEntityBlock(this._octopusImportCode)}
          ${renderEntityBlock(this._octopusExportCode)}
          ${renderEntityBlock(this._gbDnoRegion)}
        `
      : nothing;
  }
}
