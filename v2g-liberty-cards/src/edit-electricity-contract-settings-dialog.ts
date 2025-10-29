import { css, CSSResultGroup, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';

import { callFunction } from './util/appdaemon';
import {
  InputText,
  renderButton,
  renderDialogHeader,
  renderInputNumber,
  renderInputSelect,
  renderInputText,
  renderSelectOption,
} from './util/render';
import { partial, to } from './util/translate';
import { defaultState, DialogBase } from './dialog-base';
import * as entityIds from './entity-ids';

export const tagName = 'edit-electricity-contract-settings-dialog';
const tp = partial('settings.electricity-contract');

@customElement(tagName)
class EditElectricityContractSettingsDialog extends DialogBase {
  @state() private _currentPage: string;
  @state() private _electricityContract: string;
  @state() private _energyPriceVat: string;
  @state() private _energyPriceMarkup: string;
  @state() private _ownConsumptionPriceEntityId: string;
  @state() private _ownProductionPriceEntityId: string;
  @state() private _octopusImportCode: string;
  @state() private _octopusExportCode: string;
  @state() private _gbDnoRegion: string;

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._currentPage = 'contract-selection';
    this._electricityContract =
      this.hass.states[entityIds.electricityContract].state;
    this._energyPriceVat = this.hass.states[entityIds.energyPriceVat].state;
    this._energyPriceMarkup =
      this.hass.states[entityIds.energyPriceMarkup].state;
    this._ownConsumptionPriceEntityId = defaultState(
      this.hass.states[entityIds.ownConsumptionPriceEntityId],
      ''
    );
    this._ownProductionPriceEntityId = defaultState(
      this.hass.states[entityIds.ownProductionPriceEntityId],
      ''
    );
    this._octopusImportCode = defaultState(
      this.hass.states[entityIds.octopusImportCode],
      ''
    );
    this._octopusExportCode = defaultState(
      this.hass.states[entityIds.octopusExportCode],
      ''
    );
    this._gbDnoRegion = this.hass.states[entityIds.gbDnoRegion].state;
    await this.updateComplete;
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const header = tp('header');
    const content =
      this._currentPage === 'contract-selection'
        ? this._renderContractSelection()
        : this._renderContractDetails();
    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${renderDialogHeader(this.hass, header)}
      >
        ${content}
      </ha-dialog>
    `;
  }

  private _renderContractSelection() {
    const header = tp('selection-header');
    const stateObj = this.hass.states[entityIds.electricityContract];
    const nlProviders = filterNLProviders(stateObj.attributes.options);
    const current = this._electricityContract;
    const callback = evt => (this._electricityContract = evt.target.value);
    const isSelectionValid = stateObj.attributes.options.includes(current);

    return html`
      <p>${header}</p>
      <p><strong>${tp('nl')}</strong></p>
      ${nlProviders.map(option =>
        renderSelectOption(option, option === current, callback)
      )}
      ${renderSelectOption('nl_generic', 'nl_generic' === current, callback)}
      <p><strong>${tp('au')}</strong></p>
      ${renderSelectOption(
        'au_amber_electric',
        'au_amber_electric' === current,
        callback
      )}
      <p><strong>${tp('gb')}</strong></p>
      ${renderSelectOption(
        'gb_octopus_energy',
        'gb_octopus_energy' === current,
        callback
      )}
      ${renderButton(
        this.hass,
        this._continue,
        true,
        this.hass.localize('ui.common.continue'),
        !isSelectionValid,
        null,
      )}
    `;

    function filterNLProviders(options) {
      const result = options.filter(
        option => option.startsWith('nl_') && option !== 'nl_generic'
      );
      result.sort();
      return result;
    }
  }

  private _renderContractDetails() {
    switch (this._electricityContract) {
      case 'au_amber_electric':
        return this._renderAmberContractDetails();
      case 'gb_octopus_energy':
        return this._renderOctopusContractDetails();
      default:
        return this._renderNLContractDetails();
    }
  }

  private _renderAmberContractDetails() {
    const description = tp('amber-description');
    const consumptionPriceIdState =
      this.hass.states[entityIds.ownConsumptionPriceEntityId];
    const productionPriceIdState =
      this.hass.states[entityIds.ownProductionPriceEntityId];

    const consumptionPriceEntityIdChanged = evt =>
      (this._ownConsumptionPriceEntityId = evt.target.value);
    const productionPriceEntityIdChanged = evt =>
      (this._ownProductionPriceEntityId = evt.target.value);

    return html`
      <ha-markdown breaks .content=${description}></ha-markdown>
      ${renderInputText(
        InputText.EntityId,
        this._ownConsumptionPriceEntityId,
        consumptionPriceIdState,
        consumptionPriceEntityIdChanged
      )}
      ${renderInputText(
        InputText.EntityId,
        this._ownProductionPriceEntityId,
        productionPriceIdState,
        productionPriceEntityIdChanged
      )}
      ${renderButton(
        this.hass,
        this._back,
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${renderButton(
        this.hass,
        this._save,
        true,
        this.hass.localize('ui.common.save'),
        false,
        'save'
      )}
    `;
  }

  private _renderOctopusContractDetails() {
    const description = tp('octopus-description');
    const importCodeState = this.hass.states[entityIds.octopusImportCode];
    const exportCodeState = this.hass.states[entityIds.octopusExportCode];
    const dnoRegionState = this.hass.states[entityIds.gbDnoRegion];

    const importCodeChanged = evt =>
      (this._octopusImportCode = evt.target.value);
    const exportCodeChanged = evt =>
      (this._octopusExportCode = evt.target.value);
    const dnoRegionChanged = evt => (this._gbDnoRegion = evt.target.value);

    return html`
      <ha-markdown breaks .content=${description}></ha-markdown>
      ${renderInputText(
        InputText.OctopusCode,
        this._octopusImportCode,
        importCodeState,
        importCodeChanged
      )}
      ${renderInputText(
        InputText.OctopusCode,
        this._octopusExportCode,
        exportCodeState,
        exportCodeChanged
      )}
      ${renderInputSelect(this._gbDnoRegion, dnoRegionState, dnoRegionChanged)}
      ${renderButton(
        this.hass,
        this._back,
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${renderButton(
        this.hass,
        this._save,
        true,
        this.hass.localize('ui.common.save'),
        false,
        'save'
      )}
    `;
  }

  private _renderNLContractDetails() {
    const subHeader = tp('nl-sub-header', {
      contract: to(this._electricityContract),
      country: tp('nl'),
    });
    return html`
      <ha-markdown breaks .content=${subHeader}></ha-markdown>
      ${this._renderNLGenericContractDetails()}
      ${renderButton(
        this.hass,
        this._back,
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${renderButton(
        this.hass,
        this._save,
        true,
        this.hass.localize('ui.common.save'),
        false,
        'save'
      )}

    `;
  }

  private _renderNLGenericContractDetails() {
    if (this._electricityContract !== 'nl_generic') return nothing;

    const description = tp('nl-generic-description');
    const vatState = this.hass.states[entityIds.energyPriceVat];
    const markupState = this.hass.states[entityIds.energyPriceMarkup];

    const vatChanged = evt => (this._energyPriceVat = evt.target.value);
    const markupChanged = evt => (this._energyPriceMarkup = evt.target.value);

    return html`
      <ha-markdown breaks .content=${description}></ha-markdown>
      ${renderInputNumber(this._energyPriceVat, vatState, vatChanged)}
      ${renderInputNumber(this._energyPriceMarkup, markupState, markupChanged)}
    `;
  }

  private _continue(): void {
    this._currentPage = 'contract-details';
  }

  private _back(): void {
    this._currentPage = 'contract-selection';
  }

  private async _save(): Promise<void> {
    const selected = this._electricityContract;
    // TODO: add validation
    const nlGenericArgs =
      selected === 'nl_generic'
        ? {
            vat: this._energyPriceVat,
            markup: this._energyPriceMarkup,
          }
        : {};
    const amberArgs =
      selected === 'au_amber_electric'
        ? {
            // TODO: add validation -- check for existing entity
            consumptionPriceEntity: this._ownConsumptionPriceEntityId,
            productionPriceEntity: this._ownProductionPriceEntityId,
          }
        : {};
    const octopusArgs =
      selected === 'gb_octopus_energy'
        ? {
            importCode: this._octopusImportCode,
            exportCode: this._octopusExportCode,
            region: this._gbDnoRegion,
          }
        : {};
    const args = {
      contract: this._electricityContract,
      ...nlGenericArgs,
      ...amberArgs,
      ...octopusArgs,
    };
    const result = await callFunction(
      this.hass,
      'save_electricity_contract_settings',
      args
    );
    this.closeDialog();
  }

  static styles = css`
    .select-name {
      font-weight: bold;
    }

    .select-options {
      columns: 2;
    }
    ha-dialog {
      --mdc-dialog-min-width: 350px;
    }
  `;
}
