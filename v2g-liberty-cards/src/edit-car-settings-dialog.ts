import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';

import { callFunction } from './util/appdaemon';
import {
  renderSpinner,
  renderButton,
  renderDialogHeader,
  renderInputNumber,
} from './util/render';
import { partial } from './util/translate';
import { defaultState, DialogBase } from './dialog-base';
import * as entityIds from './entity-ids';

export const tagName = 'edit-car-settings-dialog';
const tp = partial('settings.car-dialog');

const enum RetrievalStatus {
  NotStarted = 'not-started',
  Retrieving = 'retrieving',
  Success = 'success',
  Failed = 'failed',
}

type DialogPage =
  | '1-connect-car'      // EVtec only: prompt user to connect car
  | '2-manual-entry'     // Wallbox or EVtec after retrieval: form with fields
  | 'loading';           // Spinner during retrieval

@customElement(tagName)
class EditCarSettingsDialog extends DialogBase {
  // State variables
  @state() private _carName: string;
  @state() private _usableCapacity: string;
  @state() private _roundtripEfficiency: string;
  @state() private _carEnergyConsumption: string;
  @state() private _currentPage: DialogPage;
  @state() private _retrievalStatus: RetrievalStatus;
  @state() private _errorMessage: string;
  @state() private _chargerType: string;
  @state() private _evId: string;
  @state() private _isEdit: boolean;  // Add vs Edit mode
  @state() private _expandedHelpField: string | null = null;  // Track which help is expanded

  public async showDialog(): Promise<void> {
    super.showDialog();

    // Load current values
    this._carName = defaultState(this.hass.states[entityIds.carName], '');
    this._usableCapacity = defaultState(this.hass.states[entityIds.usableCapacity], '');
    this._roundtripEfficiency = defaultState(this.hass.states[entityIds.roundtripEfficiency], '85');
    this._carEnergyConsumption = defaultState(this.hass.states[entityIds.carEnergyConsumption], '');
    this._chargerType = this.hass.states[entityIds.chargerType]?.state || '';
    this._isEdit = this._carName !== '';

    // Determine starting page based on charger type and mode
    if (this._chargerType === 'evtec-bidi-pro-10' && !this._isEdit) {
      this._currentPage = '1-connect-car';  // New EVtec car: start with connect prompt
    } else {
      this._currentPage = '2-manual-entry';  // Edit mode or Wallbox: go to form
    }

    this._retrievalStatus = RetrievalStatus.NotStarted;
    this._errorMessage = '';
    this._evId = '';
    this._expandedHelpField = null;  // Reset any expanded help sections

    await this.updateComplete;
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const header = this._getDialogHeader();

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${renderDialogHeader(this.hass, header)}
      >
        ${this._currentPage === '1-connect-car'
          ? this._renderConnectCarPrompt()
          : this._currentPage === 'loading'
            ? this._renderLoading()
            : this._renderManualEntry()}
      </ha-dialog>
    `;
  }

  private _getDialogHeader(): string {
    if (this._currentPage === '1-connect-car') {
      return tp('1-connect-car.header');
    } else if (this._isEdit) {
      return tp('edit.header', { name: this._carName });
    } else {
      return tp('add.header');
    }
  }

  //////////////////////////////////////////////////
  //     Step 1: Connect Car (EVtec only)         //
  //////////////////////////////////////////////////

  private _renderConnectCarPrompt() {
    return html`
      <ha-markdown breaks .content=${tp('1-connect-car.description')}></ha-markdown>
      <br/>
      ${this._retrievalStatus === RetrievalStatus.Failed
        ? html`<ha-alert alert-type="error">${this._errorMessage}</ha-alert>`
        : nothing}
      <div class="actions">
        ${renderButton(
          this.hass,
          () => this._retrieveCarData(),
          true,
          tp('1-connect-car.retrieve-button')
        )}
        ${renderButton(
          this.hass,
          () => { this._currentPage = '2-manual-entry'; },
          false,
          tp('1-connect-car.manual-entry-button')
        )}
      </div>
    `;
  }

  //////////////////////////////////////////////////
  //          Step 2: Loading Spinner             //
  //////////////////////////////////////////////////

  private _renderLoading() {
    return html`
      <div class="loading">
        ${renderSpinner()}
        <p>${tp('loading.message')}</p>
      </div>
    `;
  }

  //////////////////////////////////////////////////
  //     Step 3: Manual Entry / Review Form       //
  //////////////////////////////////////////////////

  private _renderManualEntry() {
    const usableCapacityState = this.hass.states[entityIds.usableCapacity];
    const roundtripEfficiencyState = this.hass.states[entityIds.roundtripEfficiency];
    const carEnergyConsumptionState = this.hass.states[entityIds.carEnergyConsumption];

    return html`
      <ha-markdown breaks .content=${tp('2-manual-entry.description')}></ha-markdown>
      <br/>

      <ha-settings-row style="height: 85px;">
        <span slot="heading">
          <ha-icon .icon=${'mdi:car-electric'}></ha-icon>
        </span>
        <ha-textfield
          type="text"
          required="required"
          .label=${tp('fields.name')}
          .placeholder=${tp('fields.name-placeholder')}
          .value=${this._carName}
          @change=${evt => (this._carName = evt.target.value)}
          test-id="${entityIds.carName}"
          style="width: 100%"
        ></ha-textfield>
      </ha-settings-row>

      ${renderInputNumber(
        this._usableCapacity,
        usableCapacityState,
        (value) => { this._usableCapacity = value; }
      )}
      ${this._renderExpandableHelp(
        'usableCapacity',
        'fields.usable-capacity-help-short',
        'car-battery-usable-capacity.description'
      )}

      ${renderInputNumber(
        this._roundtripEfficiency,
        roundtripEfficiencyState,
        (value) => { this._roundtripEfficiency = value; }
      )}
      ${this._renderExpandableHelp(
        'roundtripEfficiency',
        'fields.roundtrip-efficiency-help-short',
        'roundtrip-efficiency.description'
      )}

      ${renderInputNumber(
        this._carEnergyConsumption,
        carEnergyConsumptionState,
        (value) => { this._carEnergyConsumption = value; }
      )}
      ${this._renderExpandableHelp(
        'carEnergyConsumption',
        'fields.car-energy-consumption-help-short',
        'car-energy-consumption.description'
      )}

      ${renderButton(
        this.hass,
        () => this._saveSettings(),
        true,
        this.hass.localize('ui.common.save')
      )}
    `;
  }

  //////////////////////////////////////////////////
  //              Helper Methods                  //
  //////////////////////////////////////////////////

  private _renderExpandableHelp(
    fieldId: string,
    condensedTextKey: string,
    fullTextKey: string
  ) {
    const isExpanded = this._expandedHelpField === fieldId;
    const tp_dialogs = partial('settings.dialogs');

    return html`
      <div class="help-text">
        ${isExpanded
          ? html`
              <ha-markdown breaks .content=${tp_dialogs(fullTextKey)}></ha-markdown>
              <a href="#" class="help-toggle" @click=${(e) => { e.preventDefault(); this._expandedHelpField = null; }}>
                ${tp('fields.show-less')}
              </a>
            `
          : html`
              <span class="help-condensed">${tp(condensedTextKey)}</span>
              <a href="#" class="help-toggle" @click=${(e) => { e.preventDefault(); this._expandedHelpField = fieldId; }}>
                ${tp('fields.learn-more')}
              </a>
            `
        }
      </div>
    `;
  }

  //////////////////////////////////////////////////
  //              Business Logic                  //
  //////////////////////////////////////////////////

  private async _retrieveCarData() {
    this._currentPage = 'loading';
    this._retrievalStatus = RetrievalStatus.Retrieving;

    try {
      const result = await callFunction(
        this.hass,
        'get_car_details',
        {},
        30000  // 30 second timeout
      );

      if (result.success) {
        // Prefill form with retrieved data
        this._evId = result.ev_id;
        this._usableCapacity = result.battery_capacity_kwh?.toString() || '';
        // Note: efficiency and consumption not available from charger
        this._retrievalStatus = RetrievalStatus.Success;
        this._currentPage = '2-manual-entry';
      } else {
        this._retrievalStatus = RetrievalStatus.Failed;
        this._errorMessage = result.error || tp('errors.retrieval-failed');
        this._currentPage = '1-connect-car';  // Back to connect prompt
      }
    } catch (error) {
      this._retrievalStatus = RetrievalStatus.Failed;
      this._errorMessage = tp('errors.timeout');
      this._currentPage = '1-connect-car';
    }
  }

  private async _saveSettings() {
    // Validation
    if (!this._carName.trim()) {
      // Show error
      return;
    }

    // Generate fake ev_id for Wallbox if not already set
    if (!this._evId && this._chargerType === 'wallbox-quasar-1') {
      this._evId = `wallbox_${this._carName.replace(/\s/g, '_')}`;
    }

    try {
      await callFunction(
        this.hass,
        'save_car_settings',
        {
          name: this._carName,
          capacity_kwh: parseFloat(this._usableCapacity),
          efficiency: parseFloat(this._roundtripEfficiency),
          consumption_wh_km: parseFloat(this._carEnergyConsumption),
          ev_id: this._evId,
        },
        10000
      );

      this.closeDialog();
    } catch (error) {
      // Show error
    }
  }

  static styles = css`
    .help-text {
      margin-top: 4px;
      margin-bottom: 12px;
      font-size: 0.9em;
      color: var(--secondary-text-color);
      line-height: 1.5;
    }

    .help-condensed {
      display: inline;
    }

    .help-toggle {
      color: var(--primary-color);
      text-decoration: none;
      margin-left: 4px;
      cursor: pointer;
    }

    .help-toggle:hover {
      text-decoration: underline;
    }
  `;
}
