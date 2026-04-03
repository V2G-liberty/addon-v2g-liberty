import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';

import { callFunction } from './util/appdaemon';
import {
  renderSpinner,
  renderButton,
  renderDialogHeader,
  renderInputNumber,
  isNewHaDialogAPI,
} from './util/render';
import { partial } from './util/translate';
import { defaultState, DialogBase } from './dialog-base';
import * as entityIds from './entity-ids';

export const tagName = 'edit-car-settings-dialog';
const tp = partial('settings.car-dialog');
const tp_dialogs = partial('settings.dialogs');

const enum RetrievalStatus {
  NotStarted = 'not-started',
  Retrieving = 'retrieving',
  Success = 'success',
  Failed = 'failed',
}

type DialogPage =
  | '1-connect-car'      // EVtec only: prompt user to connect car
  | '2-manual-entry'     // Wallbox or EVtec after retrieval: form with fields
  | '3-scheduling-limits' // Scheduling limits: min/max SoC and allowed duration
  | 'loading'            // Spinner during retrieval
  | 'error';             // Error state (e.g. no charger configured)

@customElement(tagName)
class EditCarSettingsDialog extends DialogBase {
  // State variables
  @state() private _carName: string;
  @state() private _usableCapacity: string;
  @state() private _roundtripEfficiency: string;
  @state() private _carEnergyConsumption: string;
  @state() private _minSoc: string;
  @state() private _maxSoc: string;
  @state() private _allowedDurationAboveMax: string;
  @state() private _currentPage: DialogPage;
  @state() private _retrievalStatus: RetrievalStatus;
  @state() private _errorMessage: string;
  @state() private _chargerType: string;
  @state() private _evId: string;
  @state() private _isEdit: boolean;  // Add vs Edit mode
  @state() private _expandedHelpField: string | null = null;  // Track which help is expanded
  @state() private _showDifferentCarPrompt: boolean = false;
  @state() private _differentCarEvId: string = '';
  @state() private _differentCarCapacity: number | null = null;

  public async showDialog(): Promise<void> {
    super.showDialog();

    // Load current values
    this._carName = defaultState(this.hass.states[entityIds.carName], '');
    this._usableCapacity = defaultState(this.hass.states[entityIds.usableCapacity], '');
    this._roundtripEfficiency = defaultState(this.hass.states[entityIds.roundtripEfficiency], '85');
    this._carEnergyConsumption = defaultState(this.hass.states[entityIds.carEnergyConsumption], '');
    this._minSoc = defaultState(this.hass.states[entityIds.lowerChargeLimit], '20');
    this._maxSoc = defaultState(this.hass.states[entityIds.upperChargeLimit], '80');
    this._allowedDurationAboveMax = defaultState(this.hass.states[entityIds.allowedDurationAboveMax], '4');
    this._chargerType = this.hass.states[entityIds.chargerType]?.state || '';
    this._isEdit = this.hass.states[entityIds.carSettingsInitialised]?.state === 'on';

    // Check if charger is configured
    const chargerInitialised = this.hass.states[entityIds.chargerSettingsInitialised]?.state === 'on';
    if (!chargerInitialised) {
      this._errorMessage = tp('errors.no-charger-configured');
      this._currentPage = 'error';
      this._evId = '';
      this._expandedHelpField = null;
      await this.updateComplete;
      return;
    }

    // Determine starting page based on charger type and mode
    if (this._chargerType === 'evtec-bidi-pro-10' && !this._isEdit) {
      // New EVtec car: immediately attempt to retrieve data from connected car
      this._currentPage = 'loading';
    } else {
      this._currentPage = '2-manual-entry';  // Edit mode or Wallbox: go to form
    }

    this._retrievalStatus = RetrievalStatus.NotStarted;
    this._errorMessage = '';
    this._evId = '';
    this._expandedHelpField = null;
    this._showDifferentCarPrompt = false;
    this._differentCarEvId = '';
    this._differentCarCapacity = null;

    // In edit mode with EVtec: check if a different car is connected
    if (this._isEdit && this._chargerType === 'evtec-bidi-pro-10') {
      this._checkForDifferentCar();
    }

    await this.updateComplete;

    // New EVtec car: start auto-retrieval after dialog is rendered
    if (this._chargerType === 'evtec-bidi-pro-10' && !this._isEdit) {
      this._retrieveCarData();
    }
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const header = this._getDialogHeader();
    const _isNew = isNewHaDialogAPI(this.hass);

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${_isNew ? null : renderDialogHeader(this.hass, header)}
        .headerTitle=${_isNew ? header : null}
      >
        ${this._currentPage === 'error'
          ? html`
              <ha-alert alert-type="error">${this._errorMessage}</ha-alert>
              ${renderButton(
                this.hass,
                () => this.closeDialog(),
                true,
                this.hass.localize('ui.common.close')
              )}
            `
          : this._currentPage === '1-connect-car'
            ? this._renderConnectCarPrompt()
            : this._currentPage === 'loading'
              ? this._renderLoading()
              : this._currentPage === '3-scheduling-limits'
                ? this._renderSchedulingLimits()
                : this._renderManualEntry()}
      </ha-dialog>
    `;
  }

  private _getDialogHeader(): string {
    if (this._currentPage === 'error') {
      return tp('errors.header');
    } else if (this._currentPage === '1-connect-car' || this._currentPage === 'loading') {
      return tp('1-connect-car.header');
    } else if (this._currentPage === '3-scheduling-limits') {
      return tp('3-scheduling-limits.header');
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
      <ha-alert alert-type="warning">${tp('1-connect-car.no-car-connected')}</ha-alert>
      <br/>
      <ha-markdown breaks .content=${tp('1-connect-car.description')}></ha-markdown>
      <br/>
      ${renderButton(
        this.hass,
        () => this._retrieveCarData(),
        true,
        tp('1-connect-car.try-again-button')
      )}
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
  //     Step 2: Manual Entry / Review Form       //
  //////////////////////////////////////////////////

  private _renderManualEntry() {
    const usableCapacityState = this.hass.states[entityIds.usableCapacity];
    const roundtripEfficiencyState = this.hass.states[entityIds.roundtripEfficiency];
    const carEnergyConsumptionState = this.hass.states[entityIds.carEnergyConsumption];

    return html`
      ${this._showDifferentCarPrompt ? html`
        <ha-alert alert-type="warning">
          ${tp('edit.different-car-detected', { evId: this._differentCarEvId })}
          <br/>
          <mwc-button @click=${() => this._replaceWithNewCar()}>
            ${tp('edit.replace-car-button')}
          </mwc-button>
        </ha-alert>
        <br/>
      ` : nothing}

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
        evt => { this._usableCapacity = evt.target.value; }
      )}
      ${this._renderExpandableHelp(
        'usableCapacity',
        'fields.usable-capacity-help-short',
        'car-battery-usable-capacity.description'
      )}

      ${renderInputNumber(
        this._roundtripEfficiency,
        roundtripEfficiencyState,
        evt => { this._roundtripEfficiency = evt.target.value; }
      )}
      ${this._renderExpandableHelp(
        'roundtripEfficiency',
        'fields.roundtrip-efficiency-help-short',
        'roundtrip-efficiency.description'
      )}

      ${renderInputNumber(
        this._carEnergyConsumption,
        carEnergyConsumptionState,
        evt => { this._carEnergyConsumption = evt.target.value; }
      )}
      ${this._renderExpandableHelp(
        'carEnergyConsumption',
        'fields.car-energy-consumption-help-short',
        'car-energy-consumption.description'
      )}

      ${renderButton(
        this.hass,
        () => { this._currentPage = '3-scheduling-limits'; this._expandedHelpField = null; },
        true,
        this.hass.localize('ui.common.continue')
      )}
    `;
  }

  //////////////////////////////////////////////////
  //     Step 3: Scheduling Limits               //
  //////////////////////////////////////////////////

  private _renderSchedulingLimits() {
    const lowerChargeLimitState = this.hass.states[entityIds.lowerChargeLimit];
    const upperChargeLimitState = this.hass.states[entityIds.upperChargeLimit];
    const allowedDurationState = this.hass.states[entityIds.allowedDurationAboveMax];

    return html`
      ${renderInputNumber(
        this._minSoc,
        lowerChargeLimitState,
        evt => { this._minSoc = evt.target.value; }
      )}
      ${this._renderExpandableHelp(
        'minSoc',
        'fields.min-soc-help-short',
        'car-battery-lower-charge-limit.description'
      )}

      ${renderInputNumber(
        this._maxSoc,
        upperChargeLimitState,
        evt => { this._maxSoc = evt.target.value; }
      )}
      ${this._renderExpandableHelp(
        'maxSoc',
        'fields.max-soc-help-short',
        'car-battery-upper-charge-limit.description'
      )}

      ${renderInputNumber(
        this._allowedDurationAboveMax,
        allowedDurationState,
        evt => { this._allowedDurationAboveMax = evt.target.value; }
      )}
      ${this._renderExpandableHelp(
        'allowedDuration',
        'fields.allowed-duration-help-short',
        'allowed-duration-above-max.description'
      )}

      ${renderButton(
        this.hass,
        () => { this._currentPage = '2-manual-entry'; this._expandedHelpField = null; },
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
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
    const fullText = tp_dialogs(fullTextKey);

    return html`
      <div class="help-text">
        ${isExpanded
          ? html`
              <ha-markdown breaks .content=${fullText}></ha-markdown>
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

  private async _checkForDifferentCar() {
    try {
      const result = await callFunction(this.hass, 'get_car_details', {}, 10000);
      if (result.success && result.is_new) {
        this._differentCarEvId = result.ev_id;
        this._differentCarCapacity = result.battery_capacity_kwh;
        this._showDifferentCarPrompt = true;
      }
    } catch {
      // Car not connected or timeout — ignore, stay in edit mode
    }
  }

  private _replaceWithNewCar() {
    if (this._differentCarEvId) {
      this._evId = this._differentCarEvId;
    }
    if (this._differentCarCapacity) {
      this._usableCapacity = this._differentCarCapacity.toString();
    }
    this._carName = '';
    this._showDifferentCarPrompt = false;
  }

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
      return;
    }

    // Generate fallback ev_id for Wallbox only (no auto-retrieval available).
    // For BiDiPro, ev_id always comes from the car; send empty to let
    // the backend preserve the existing value in edit mode.
    if (!this._evId && this._chargerType === 'wallbox-quasar-1') {
      this._evId = 'wallbox_quasar_1_car';
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
          min_soc: parseInt(this._minSoc, 10),
          max_soc: parseInt(this._maxSoc, 10),
          allowed_duration_above_max: parseInt(this._allowedDurationAboveMax, 10),
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
