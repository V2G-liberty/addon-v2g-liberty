import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEvent } from 'home-assistant-js-websocket';

import { callFunction } from './util/appdaemon';
import {
  renderButton,
  renderDialogHeader,
  renderSpinner,
  renderSelectOptionWithLabel,
  isNewHaDialogAPI,
} from './util/render';
import { styles } from './card.styles';
import { DialogBase } from './dialog-base';

export const tagName = 'v2g-liberty-edit-grid-connection-settings-dialog';

const enum Step {
  Intro = 'intro',
  PhasesAndCapacity = 'phases_and_capacity',
  Entities = 'entities',
  Validation = 'validation',
}

interface EntityTestResult {
  [entityId: string]: boolean;
}

@customElement(tagName)
export class EditGridConnectionSettingsDialog extends DialogBase {
  @state() private _step: Step = Step.Intro;
  @state() private _phases: number | null = null;
  @state() private _capacityPerPhase: string = '';
  @state() private _consumptionEntities: string[] = [];
  @state() private _productionEntities: string[] = [];

  // Entity validation state
  @state() private _validationRunning: boolean = false;
  @state() private _validationResults: EntityTestResult = {};
  @state() private _validationDone: boolean = false;

  // Auto-detection state
  @state() private _autoDetected: boolean = false;

  // Form validation state
  @state() private _triedContinueStep2: boolean = false;
  @state() private _triedContinueStep3: boolean = false;

  // Saving state
  @state() private _saving: boolean = false;
  @state() private _saveError: string = '';

  // Available sensor entities for dropdowns
  private _sensorEntities: { id: string; name: string; isPower: boolean }[] = [];

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._step = Step.Intro;
    this._phases = null;
    this._capacityPerPhase = '';
    this._consumptionEntities = [];
    this._productionEntities = [];
    this._autoDetected = false;
    this._validationRunning = false;
    this._validationResults = {};
    this._validationDone = false;
    this._triedContinueStep2 = false;
    this._triedContinueStep3 = false;
    this._saving = false;
    this._saveError = '';

    // Load existing settings if configured
    try {
      const data = await callFunction(this.hass, 'get_grid_connection_settings');
      if (data.consumption_entities?.length > 0) {
        this._phases = data.phases;
        this._capacityPerPhase = String(data.capacity_per_phase ?? '');
        this._consumptionEntities = data.consumption_entities;
        this._productionEntities = data.production_entities;
      }
    } catch (e) {
      // Ignore — start fresh
    }

    // Auto-detect from available HA entities (only if not already configured)
    if (!this._phases) {
      try {
        const detected = await callFunction(this.hass, 'detect_grid_entities');
        if (detected.phases || detected.capacity_per_phase
            || detected.consumption_entities?.length > 0) {
          this._autoDetected = true;
        }
        if (detected.phases) {
          this._phases = detected.phases;
        }
        if (detected.capacity_per_phase) {
          this._capacityPerPhase = String(detected.capacity_per_phase);
        }
        if (detected.consumption_entities?.length > 0) {
          this._consumptionEntities = detected.consumption_entities;
        }
        if (detected.production_entities?.length > 0) {
          this._productionEntities = detected.production_entities;
        }
      } catch (e) {
        // Auto-detect failed, no problem — user fills in manually
      }
    }

    this._buildSensorEntityList();
    await this.updateComplete;
  }

  private _buildSensorEntityList() {
    const states = this.hass.states;
    this._sensorEntities = [];
    for (const entityId of Object.keys(states)) {
      if (!entityId.startsWith('sensor.')) continue;
      const stateObj = states[entityId];
      const deviceClass = stateObj.attributes.device_class ?? '';
      const unit = stateObj.attributes.unit_of_measurement ?? '';
      const isPower =
        deviceClass === 'power' ||
        ['W', 'kW', 'MW'].includes(unit);
      const name =
        stateObj.attributes.friendly_name || entityId;
      this._sensorEntities.push({ id: entityId, name, isPower });
    }
    // Sort: power sensors first, then alphabetically
    this._sensorEntities.sort((a, b) => {
      if (a.isPower !== b.isPower) return a.isPower ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const _isNew = isNewHaDialogAPI(this.hass);
    const header = 'Grid connection';
    let content;

    switch (this._step) {
      case Step.Intro:
        content = this._renderIntro();
        break;
      case Step.PhasesAndCapacity:
        content = this._renderPhasesAndCapacity();
        break;
      case Step.Entities:
        content = this._renderEntities();
        break;
      case Step.Validation:
        content = this._renderValidation();
        break;
    }

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${_isNew ? null : renderDialogHeader(this.hass, header)}
        .headerTitle=${_isNew ? header : null}
      >
        ${content}
      </ha-dialog>
    `;
  }

  // ── Step 1: Introduction ────────────────────────────────────────────

  private _renderIntro() {
    return html`
      <ha-markdown breaks .content=${`
**Why set up your grid connection?**

V2G Liberty currently optimises your charging schedule based on energy prices
and your calendar. By also monitoring your grid connection, the system learns
your household energy patterns.

Over time, this leads to **better predictions** and **smarter schedules** that
fit your specific situation.

**What you need:**
- A smart meter that reports power per phase (consumption and production separately)
- A Home Assistant integration for your smart meter that exposes these as sensor entities

**Important:** Make sure your smart meter integration is fully installed and
working in Home Assistant before continuing. You will need to select the sensor
entities in the next step, and we will verify that they are reporting data.
      `}></ha-markdown>
      ${renderButton(
        this.hass,
        () => { this._step = Step.PhasesAndCapacity; },
        true
      )}
    `;
  }

  // ── Step 2: Phases and Capacity ─────────────────────────────────────

  private _renderPhasesAndCapacity() {
    return html`
      ${this._autoDetected
        ? html`<ha-alert alert-type="info">
            Values have been pre-filled based on your available sensors.
            Please verify and adjust if needed.
          </ha-alert>`
        : nothing
      }
      <div>
        <p><strong>How many phases does your grid connection have?</strong></p>
        ${renderSelectOptionWithLabel(
          '1', '1 phase',
          this._phases === 1,
          () => { this._phases = 1; },
          'phases'
        )}
        ${renderSelectOptionWithLabel(
          '3', '3 phases',
          this._phases === 3,
          () => { this._phases = 3; },
          'phases'
        )}
        ${this._triedContinueStep2 && this._phases === null
          ? html`<div class="error">Please select the number of phases.</div>`
          : nothing
        }
        <details class="hint">
          <summary>Not sure?</summary>
          <p>Check your smart meter integration in Home Assistant. Look for separate
          L1, L2, and L3 sensors — if you have them, you have 3 phases. If you only
          see L1, you have 1 phase.</p>
        </details>
      </div>

      <div style="margin-top: 16px;">
        <p><strong>Capacity per phase (ampere)</strong></p>
        <ha-textfield
          type="number"
          inputmode="numeric"
          .value=${this._capacityPerPhase}
          @change=${(e) => { this._capacityPerPhase = e.target.value; }}
          min="6"
          max="80"
          suffix="A"
          style="width: 120px;"
          test-id="capacity-per-phase"
        ></ha-textfield>
        ${this._renderCapacityError()}
        <details class="hint">
          <summary>Where to find this</summary>
          <p>You can find this on your energy contract, or in your smart meter
          integration in Home Assistant. Look for a sensor with "fuse" or "threshold"
          in the name. Common values are 25A or 35A.</p>
          <p>Please enter the actual value — do not enter a lower value as a safety margin.</p>
        </details>
      </div>

      ${renderButton(
        this.hass,
        () => { this._triedContinueStep2 = false; this._step = Step.Intro; },
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${renderButton(
        this.hass,
        () => this._continueToEntities(),
        true
      )}
    `;
  }

  private _isCapacityValid(): boolean {
    if (this._capacityPerPhase === '') return false;
    const cap = parseFloat(this._capacityPerPhase);
    return !isNaN(cap) && Number.isInteger(cap) && cap >= 6 && cap <= 80;
  }

  private _renderCapacityError() {
    if (this._capacityPerPhase === '' && this._triedContinueStep2) {
      return html`<div class="error">Please enter the capacity.</div>`;
    }
    if (this._capacityPerPhase !== '' && !this._isCapacityValid()) {
      return html`<div class="error">Must be a whole number between 6 and 80.</div>`;
    }
    return nothing;
  }

  private _continueToEntities() {
    this._triedContinueStep2 = true;
    if (this._phases === null) return;
    if (!this._isCapacityValid()) return;

    // Initialise entity arrays to correct length if needed
    const count = this._phases;
    if (this._consumptionEntities.length !== count) {
      this._consumptionEntities = new Array(count).fill('');
    }
    if (this._productionEntities.length !== count) {
      this._productionEntities = new Array(count).fill('');
    }
    this._step = Step.Entities;
  }

  // ── Step 3: Entity Selection ────────────────────────────────────────

  private _renderEntities() {
    const count = this._phases ?? 1;
    const allSelected = this._getAllSelectedEntities();

    return html`
      ${this._autoDetected
        ? html`<ha-alert alert-type="info">
            Sensors have been pre-filled based on detected patterns.
            Please verify the selection is correct.
          </ha-alert>`
        : nothing
      }
      <div>
        <p><strong>Consumption sensors</strong> (grid power drawn from the grid)</p>
        ${Array.from({ length: count }, (_, i) => this._renderEntityDropdown(
          `Consumption L${i + 1}`,
          this._consumptionEntities[i] ?? '',
          (val) => {
            const copy = [...this._consumptionEntities];
            copy[i] = val;
            this._consumptionEntities = copy;
          },
          allSelected
        ))}
      </div>

      <div style="margin-top: 16px;">
        <p><strong>Production sensors</strong> (power fed back to the grid)</p>
        ${Array.from({ length: count }, (_, i) => this._renderEntityDropdown(
          `Production L${i + 1}`,
          this._productionEntities[i] ?? '',
          (val) => {
            const copy = [...this._productionEntities];
            copy[i] = val;
            this._productionEntities = copy;
          },
          allSelected
        ))}
      </div>

      ${this._triedContinueStep3 ? this._renderEntityErrors() : nothing}

      ${renderButton(
        this.hass,
        () => { this._triedContinueStep3 = false; this._step = Step.PhasesAndCapacity; },
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${renderButton(
        this.hass,
        () => this._continueToValidation(),
        true,
        undefined,
        false
      )}
    `;
  }

  private _renderEntityDropdown(
    label: string,
    selected: string,
    onChange: (val: string) => void,
    allSelected: Set<string>
  ) {
    const hasPowerGroup = this._sensorEntities.some(e => e.isPower);
    return html`
      <div style="margin: 8px 0;">
        <label style="font-size: 0.875em; color: var(--secondary-text-color);">${label}</label>
        <select
          .value=${selected}
          @change=${(e) => onChange(e.target.value)}
          style="width: 100%; padding: 8px; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--card-background-color); color: var(--primary-text-color); font-size: 0.95em;"
        >
          <option value="">Select a sensor...</option>
          ${hasPowerGroup ? html`<optgroup label="Power sensors">
            ${this._sensorEntities
              .filter(e => e.isPower)
              .map(e => html`
                <option
                  value=${e.id}
                  ?selected=${e.id === selected}
                  ?disabled=${e.id !== selected && allSelected.has(e.id)}
                >${e.name} (${e.id})</option>
              `)}
          </optgroup>` : nothing}
          <optgroup label="${hasPowerGroup ? 'Other sensors' : 'Sensors'}">
            ${this._sensorEntities
              .filter(e => !e.isPower)
              .map(e => html`
                <option
                  value=${e.id}
                  ?selected=${e.id === selected}
                  ?disabled=${e.id !== selected && allSelected.has(e.id)}
                >${e.name} (${e.id})</option>
              `)}
          </optgroup>
        </select>
      </div>
    `;
  }

  private _getAllSelectedEntities(): Set<string> {
    const all = [
      ...this._consumptionEntities,
      ...this._productionEntities,
    ].filter(e => e !== '');
    return new Set(all);
  }

  private _hasDuplicateEntities(): boolean {
    const all = [
      ...this._consumptionEntities,
      ...this._productionEntities,
    ].filter(e => e !== '');
    return new Set(all).size !== all.length;
  }

  private _allEntitiesFilled(): boolean {
    const count = this._phases ?? 1;
    return (
      this._consumptionEntities.filter(e => e !== '').length === count &&
      this._productionEntities.filter(e => e !== '').length === count &&
      !this._hasDuplicateEntities()
    );
  }

  private _hasEmptyEntities(): boolean {
    const count = this._phases ?? 1;
    return (
      this._consumptionEntities.filter(e => e !== '').length < count ||
      this._productionEntities.filter(e => e !== '').length < count
    );
  }

  private _renderEntityErrors() {
    const errors = [];
    if (this._hasEmptyEntities()) {
      errors.push('Please select a sensor for each field.');
    }
    if (this._hasDuplicateEntities()) {
      errors.push('Each sensor can only be selected once.');
    }
    if (errors.length === 0) return nothing;
    return html`${errors.map(
      e => html`<ha-alert alert-type="error">${e}</ha-alert>`
    )}`;
  }

  private _continueToValidation() {
    this._triedContinueStep3 = true;
    if (this._hasEmptyEntities() || this._hasDuplicateEntities()) return;
    this._validationResults = {};
    this._validationDone = false;
    this._step = Step.Validation;
    this._runEntityValidation();
  }

  // ── Step 4: Entity Validation ───────────────────────────────────────

  private _renderValidation() {
    const allEntities = [
      ...this._consumptionEntities,
      ...this._productionEntities,
    ];

    return html`
      ${this._validationDone
        ? this._renderValidationResult()
        : html`
          <p>Verifying that your sensors are reporting data...</p>
          <p style="font-size: 0.875em; color: var(--secondary-text-color);">
            <strong>Tip:</strong> To make sure there is activity on your grid connection,
            try turning on an appliance like a kettle or oven.
          </p>
        `
      }

      <div style="margin: 16px 0;">
        ${allEntities.map(entity => {
          const result = this._validationResults[entity];
          const icon = result === true
            ? '✅'
            : result === false
              ? '❓'
              : '⏳';
          return html`
            <div style="padding: 4px 0; font-family: var(--code-font-family, monospace); font-size: 0.875em;">
              ${icon} ${entity}
            </div>
          `;
        })}
      </div>

      ${this._validationDone
        ? this._renderValidationButtons()
        : renderSpinner(this.hass)
      }
    `;
  }

  private _renderValidationResult() {
    const allOk = Object.values(this._validationResults).every(v => v === true);
    if (allOk) {
      return html`<ha-alert alert-type="success">All sensors are working correctly.</ha-alert>`;
    }
    return html`
      <ha-alert alert-type="warning">
        Some sensors did not respond within 30 seconds.
        This could mean the entity ID is incorrect, or the sensor is not reporting
        data at this time. For production sensors, this can be normal if there is
        currently no or little solar production — the meter may report 0 continuously,
        which does not generate a state change.
      </ha-alert>
    `;
  }

  private _renderValidationButtons() {
    const allOk = Object.values(this._validationResults).every(v => v === true);
    if (allOk) {
      return html`
        ${this._saving ? renderSpinner(this.hass) : html`
          ${this._saveError ? html`<ha-alert alert-type="error">${this._saveError}</ha-alert>` : nothing}
          ${renderButton(this.hass, () => this._save(), true, this.hass.localize('ui.common.save'))}
        `}
      `;
    }
    return html`
      ${renderButton(
        this.hass,
        () => { this._step = Step.Entities; },
        false,
        'Back to edit',
        false,
        'back',
        true
      )}
      ${this._saving ? renderSpinner(this.hass) : html`
        ${this._saveError ? html`<ha-alert alert-type="error">${this._saveError}</ha-alert>` : nothing}
        ${renderButton(this.hass, () => this._save(), true, 'Save anyway')}
      `}
    `;
  }

  private async _runEntityValidation() {
    this._validationRunning = true;

    // Initialise all as undefined (pending)
    const allEntities = [
      ...this._consumptionEntities,
      ...this._productionEntities,
    ];
    for (const e of allEntities) {
      this._validationResults[e] = undefined;
    }
    this._validationResults = { ...this._validationResults };

    // Subscribe to progress events
    const unsubProgress = await this.hass.connection.subscribeEvents<HassEvent>(
      (event: HassEvent) => {
        const entity = event.data.entity;
        if (entity && entity in this._validationResults) {
          this._validationResults = {
            ...this._validationResults,
            [entity]: true,
          };
        }
      },
      'test_grid_entities.progress'
    );

    try {
      const result = await callFunction(
        this.hass,
        'test_grid_entities',
        {
          consumption_entities: this._consumptionEntities,
          production_entities: this._productionEntities,
        },
        35 * 1000
      );

      // Mark failed entities
      const failed: string[] = result.failed ?? [];
      for (const entity of failed) {
        this._validationResults = {
          ...this._validationResults,
          [entity]: false,
        };
      }
      // Mark any remaining undefined as success (in case progress events were missed)
      for (const entity of allEntities) {
        if (this._validationResults[entity] === undefined) {
          this._validationResults = {
            ...this._validationResults,
            [entity]: result.success ?? false,
          };
        }
      }
    } catch (e) {
      // Timeout or error — mark all pending as failed
      for (const entity of allEntities) {
        if (this._validationResults[entity] === undefined) {
          this._validationResults = {
            ...this._validationResults,
            [entity]: false,
          };
        }
      }
    } finally {
      unsubProgress();
      this._validationRunning = false;
      this._validationDone = true;
    }
  }

  // ── Save ────────────────────────────────────────────────────────────

  private async _save() {
    this._saving = true;
    this._saveError = '';

    try {
      const result = await callFunction(
        this.hass,
        'save_grid_connection_settings',
        {
          phases: this._phases,
          capacity_per_phase: parseInt(this._capacityPerPhase, 10),
          consumption_entities: this._consumptionEntities,
          production_entities: this._productionEntities,
        }
      );

      if (result.error) {
        this._saveError = result.error;
        this._saving = false;
        return;
      }

      this.closeDialog();
    } catch (e) {
      this._saveError = 'Failed to save settings. Please try again.';
      this._saving = false;
    }
  }

  static styles = [
    styles,
    css`
      .error {
        color: var(--error-color);
        font-size: 0.875em;
        margin-top: 4px;
      }
      details.hint {
        margin-top: 8px;
        font-size: 0.875em;
        color: var(--secondary-text-color);
      }
      details.hint summary {
        cursor: pointer;
        color: var(--primary-color);
      }
      details.hint p {
        margin: 4px 0 0 0;
        line-height: 1.4;
      }
    `,
  ];
}
