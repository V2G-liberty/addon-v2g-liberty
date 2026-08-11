import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEvent } from 'home-assistant-js-websocket';

import { callFunction } from './util/appdaemon';
import {
  renderButton,
  renderDialogHeader,
  renderSpinner,
  isNewHaDialogAPI,
  renderHaInput,
  renderRadioIndicator,
} from './util/render';
import { styles } from './card.styles';
import { DialogBase } from './dialog-base';

export const tagName = 'v2g-liberty-edit-grid-connection-settings-dialog';

const enum Step {
  Intro = 'intro',
  PhasesAndCapacity = 'phases_and_capacity',
  Entities = 'entities',
}

@customElement(tagName)
export class EditGridConnectionSettingsDialog extends DialogBase {
  @state() private _step: Step = Step.Intro;
  @state() private _phases: number | null = null;
  @state() private _capacityPerPhase: string = '';
  @state() private _consumptionEntities: string[] = [];
  @state() private _productionEntities: string[] = [];

  // Cumulative meter registers (total_increasing energy). Index 0 = tariff 1,
  // index 1 = tariff 2. Optional and summed downstream; empty = feature off.
  // The common NL case is two tariffs (or a single total register in slot 0).
  @state() private _consumptionRegisters: string[] = ['', ''];
  @state() private _productionRegisters: string[] = ['', ''];

  // Inline entity validation state (per entity: true=ok, undefined=pending)
  @state() private _entityStatus: { [entityId: string]: boolean | undefined } = {};
  // Entities that reported a negative value (likely a bidirectional/net or
  // wrong sensor, since consumption/production should be directional).
  @state() private _entityNegative: { [entityId: string]: boolean } = {};
  private _entityListeners: { [entityId: string]: any } = {};

  // Auto-detection state
  @state() private _autoDetected: boolean = false;

  // Form validation state
  @state() private _triedContinueStep2: boolean = false;
  @state() private _triedSave: boolean = false;
  // Set on the first Continue click of step 2 when the new phases would
  // make existing solar panels inconsistent. While true, the Continue
  // button reads "Continue anyway" and the warning is visible. Reset
  // whenever the user changes the phase selection so a different choice
  // requires its own acknowledgement.
  @state() private _phaseChangeConfirmed: boolean = false;

  // Saving state
  @state() private _saving: boolean = false;
  @state() private _saveError: string = '';
  @state() private _saveConfirmed: boolean = false;

  // Existing solar panels (loaded at open) so the dialog can warn the user
  // when a phases change would leave one or more panels inconsistent with
  // the new grid configuration. The dialog never auto-fixes panels — see
  // plan task 30a.
  @state() private _existingSolarPanels: {
    name: string;
    phases: number;
    connected_to_phase?: number;
  }[] = [];

  // Available sensor entities for dropdowns
  private _sensorEntities: {
    id: string;
    name: string;
    isPower: boolean;
    isEnergyRegister: boolean;
  }[] = [];

  // Pad/trim a register list to exactly two slots (tariff 1, tariff 2). A
  // single total register lands in slot 0; any 3rd/4th tariff is dropped
  // (rare outside NL — the total register covers those meters).
  private _padTo2(arr: string[]): string[] {
    return [arr[0] ?? '', arr[1] ?? ''];
  }

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._step = Step.Intro;
    this._phases = null;
    this._capacityPerPhase = '';
    this._consumptionEntities = [];
    this._productionEntities = [];
    this._consumptionRegisters = ['', ''];
    this._productionRegisters = ['', ''];
    this._entityStatus = {};
    this._entityNegative = {};
    this._cleanupEntityListeners();
    this._autoDetected = false;
    this._triedContinueStep2 = false;
    this._triedSave = false;
    this._phaseChangeConfirmed = false;
    this._saving = false;
    this._saveError = '';
    this._saveConfirmed = false;

    // Load existing settings if configured
    try {
      const data = await callFunction(this.hass, 'get_grid_connection_settings');
      if (data.consumption_entities?.length > 0) {
        this._phases = data.phases;
        this._capacityPerPhase = String(data.capacity_per_phase ?? '');
        this._consumptionEntities = data.consumption_entities;
        this._productionEntities = data.production_entities;
      }
      if (data.consumption_registers?.length > 0) {
        this._consumptionRegisters = this._padTo2(data.consumption_registers);
      }
      if (data.production_registers?.length > 0) {
        this._productionRegisters = this._padTo2(data.production_registers);
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
        if (detected.consumption_registers?.length > 0) {
          this._autoDetected = true;
          this._consumptionRegisters = this._padTo2(detected.consumption_registers);
        }
        if (detected.production_registers?.length > 0) {
          this._autoDetected = true;
          this._productionRegisters = this._padTo2(detected.production_registers);
        }
      } catch (e) {
        // Auto-detect failed, no problem — user fills in manually
      }
    }

    this._buildSensorEntityList();

    // Load existing solar panels so we can warn the user when their new
    // phases choice would invalidate any of them (plan task 30a). Failure
    // is non-fatal — we just won't warn.
    try {
      const sp = await callFunction(this.hass, 'get_solar_panels');
      this._existingSolarPanels = (sp.solar_panels ?? []) as {
        name: string;
        phases: number;
        connected_to_phase?: number;
      }[];
    } catch (e) {
      this._existingSolarPanels = [];
    }

    await this.updateComplete;
  }

  public closeDialog(): void {
    this._cleanupEntityListeners();
    super.closeDialog();
  }

  private _cleanupEntityListeners() {
    for (const unsub of Object.values(this._entityListeners)) {
      try {
        if (typeof unsub === 'function') unsub();
      } catch (e) { /* ignore */ }
    }
    this._entityListeners = {};
  }

  private _buildSensorEntityList() {
    const states = this.hass.states;
    this._sensorEntities = [];
    for (const entityId of Object.keys(states)) {
      if (!entityId.startsWith('sensor.')) continue;
      const stateObj = states[entityId];
      const deviceClass = stateObj.attributes.device_class ?? '';
      const unit = stateObj.attributes.unit_of_measurement ?? '';
      const stateClass = stateObj.attributes.state_class ?? '';
      const isPower =
        deviceClass === 'power' ||
        ['W', 'kW', 'MW'].includes(unit);
      // Cumulative meter register: an energy sensor whose value only ever
      // increases (the meter's kWh total per tariff).
      const isEnergyRegister =
        deviceClass === 'energy' &&
        stateClass === 'total_increasing' &&
        ['Wh', 'kWh', 'MWh'].includes(unit);
      const name =
        stateObj.attributes.friendly_name || entityId;
      this._sensorEntities.push({ id: entityId, name, isPower, isEnergyRegister });
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
      <p>By monitoring your grid connection, the system learns your household energy
      patterns. Over time, this leads to <strong>better predictions</strong> and
      <strong>smarter schedules</strong> that fit your specific situation.</p>

      <p><strong>For Dutch users:</strong> this is a valuable preparation for the
      end of "saldering" (net metering). Once net metering ends, a grid connection
      configuration will be required.</p>

      <div class="requirements-box">
        <div class="requirements-header">What you need*</div>
        <div class="requirement-item">
          <ha-icon icon="mdi:meter-electric" class="requirement-icon"></ha-icon>
          <div>
            <strong>Smart meter</strong><br/>
            Capable of reporting power usage per phase in real-time.
          </div>
        </div>
        <div class="requirement-item">
          <ha-icon icon="mdi:cable-data" class="requirement-icon"></ha-icon>
          <div>
            <strong>P1 cable</strong><br/>
            A USB P1 port cable or similar to connect the meter.
          </div>
        </div>
        <div class="requirement-item">
          <ha-icon icon="mdi:home-assistant" class="requirement-icon"></ha-icon>
          <div>
            <strong>Home Assistant integration</strong><br/>
            A functional integration that exposes meter data as sensor entities
            (e.g. a DSMR integration).
          </div>
        </div>
        <div class="requirements-footer">
          * Typical setup. Other setups are possible, as long as usage and production
          can be read from HA sensors.
        </div>
      </div>

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
      <div>
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
          <p style="margin: 0;"><strong>How many phases does your grid connection have?</strong></p>
          ${this._autoDetected && this._phases !== null
            ? html`<span class="auto-detected-badge">
                <ha-icon icon="mdi:auto-fix" style="--mdc-icon-size: 14px;"></ha-icon>
                Auto-detected
              </span>`
            : nothing
          }
        </div>
        <div class="phase-cards">
          <div
            class="phase-card ${this._phases === 1 ? 'selected' : ''}"
            @click=${() => { this._selectPhases(1); }}
          >
            ${renderRadioIndicator(this._phases === 1)}
            <div>
              <strong>1 phase</strong><br/>
              <span class="phase-subtitle">Small apartment connection</span>
            </div>
          </div>
          <div
            class="phase-card ${this._phases === 3 ? 'selected' : ''}"
            @click=${() => { this._selectPhases(3); }}
          >
            ${renderRadioIndicator(this._phases === 3)}
            <div>
              <strong>3 phases</strong><br/>
              <span class="phase-subtitle">Standard connection</span>
            </div>
          </div>
        </div>
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
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
          <p style="margin: 0;"><strong>Capacity per phase (ampere)</strong></p>
          ${this._autoDetected && this._capacityPerPhase !== ''
            ? html`<span class="auto-detected-badge">
                <ha-icon icon="mdi:auto-fix" style="--mdc-icon-size: 14px;"></ha-icon>
                Auto-detected
              </span>`
            : nothing
          }
        </div>
        ${renderHaInput({
          value: this._capacityPerPhase,
          onChange: (e: any) => {
            this._capacityPerPhase = e.target.value;
          },
          type: 'number',
          inputmode: 'numeric',
          min: 6,
          max: 80,
          suffix: 'A',
          testId: 'capacity-per-phase',
          style: 'width: 120px;',
        })}
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
      ${this._renderSolarPanelWarning()}
      ${renderButton(
        this.hass,
        () => this._continueToEntities(),
        true,
        this._phaseChangeConfirmed
          ? 'Continue anyway'
          : this.hass.localize('ui.common.continue')
      )}
    `;
  }

  private _selectPhases(phases: 1 | 3) {
    // Any change in the phase choice invalidates a previous
    // "Continue anyway" acknowledgement — re-warn for the new selection.
    if (this._phases !== phases) {
      this._phaseChangeConfirmed = false;
    }
    this._phases = phases;
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

    // Soft warning: would the new phase choice make existing solar panels
    // inconsistent? First Continue click reveals the warning; the second
    // (now "Continue anyway") actually moves to the next step. Nothing on
    // the panels is changed — they get flagged on the solar panels card.
    if (
      this._panelsThatWillBecomeInconsistent().length > 0 &&
      !this._phaseChangeConfirmed
    ) {
      this._phaseChangeConfirmed = true;
      return;
    }

    // Initialise entity arrays to correct length if needed
    const count = this._phases;
    if (this._consumptionEntities.length !== count) {
      this._consumptionEntities = new Array(count).fill('');
    }
    if (this._productionEntities.length !== count) {
      this._productionEntities = new Array(count).fill('');
    }

    this._triedSave = false;
    this._saveConfirmed = false;
    this._step = Step.Entities;

    // Start listening for already-selected entities
    for (const entityId of [...this._consumptionEntities, ...this._productionEntities]) {
      if (entityId) {
        this._startListeningEntity(entityId);
      }
    }
  }

  // ── Step 3: Entity Selection (with inline validation) ───────────────

  private _renderEntities() {
    const count = this._phases ?? 1;
    const allSelected = this._getAllSelectedEntities();

    return html`
      ${this._renderPowerHelp()}
      <div>
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
          <p style="margin: 0; flex: 1;"><strong>Consumption sensors</strong> (grid power drawn from the grid)</p>
          ${this._autoDetected && this._consumptionEntities.some(e => e !== '')
            ? html`<span class="auto-detected-badge">
                <ha-icon icon="mdi:auto-fix" style="--mdc-icon-size: 14px;"></ha-icon>
                Auto-detected
              </span>`
            : nothing
          }
          <span class="column-header">Active</span>
        </div>
        ${Array.from({ length: count }, (_, i) => this._renderEntityDropdown(
          `Consumption phase ${i + 1} (L${i + 1})`,
          this._consumptionEntities[i] ?? '',
          (val) => {
            const old = this._consumptionEntities[i];
            if (old) this._stopListeningEntity(old);
            const copy = [...this._consumptionEntities];
            copy[i] = val;
            this._consumptionEntities = copy;
            if (val) this._startListeningEntity(val);
          },
          allSelected
        ))}
      </div>

      <div style="margin-top: 24px;">
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
          <p style="margin: 0; flex: 1;"><strong>Production sensors</strong> (power fed back to the grid)</p>
          ${this._autoDetected && this._productionEntities.some(e => e !== '')
            ? html`<span class="auto-detected-badge">
                <ha-icon icon="mdi:auto-fix" style="--mdc-icon-size: 14px;"></ha-icon>
                Auto-detected
              </span>`
            : nothing
          }
          <span class="column-header">Active</span>
        </div>
        ${Array.from({ length: count }, (_, i) => this._renderEntityDropdown(
          `Production phase ${i + 1} (L${i + 1})`,
          this._productionEntities[i] ?? '',
          (val) => {
            const old = this._productionEntities[i];
            if (old) this._stopListeningEntity(old);
            const copy = [...this._productionEntities];
            copy[i] = val;
            this._productionEntities = copy;
            if (val) this._startListeningEntity(val);
          },
          allSelected
        ))}
      </div>

      ${this._renderRegisters()}

      ${this._triedSave ? this._renderEntityErrors() : nothing}
      ${this._renderNegativeWarning()}
      ${this._renderSaveWarning()}
      ${this._saveError
        ? html`<div class="error save-error" role="alert">${this._saveError}</div>`
        : nothing}

      ${renderButton(
        this.hass,
        () => { this._cleanupEntityListeners(); this._triedSave = false; this._step = Step.PhasesAndCapacity; },
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${this._saving
        ? renderSpinner(this.hass)
        : renderButton(
            this.hass,
            () => this._handleSave(),
            true,
            this._saveConfirmed ? 'Save anyway' : this.hass.localize('ui.common.save')
          )
      }
    `;
  }

  private _renderEntityDropdown(
    label: string,
    selected: string,
    onChange: (val: string) => void,
    allSelected: Set<string>
  ) {
    const hasPowerGroup = this._sensorEntities.some(e => e.isPower);
    const status = selected ? this._entityStatus[selected] : undefined;
    const isNegative = selected ? this._entityNegative[selected] : false;
    const statusIcon = !selected
      ? nothing
      : isNegative
        ? html`<ha-icon icon="mdi:alert" title="This sensor reported a negative value" style="color: var(--warning-color, #ff9800); --mdc-icon-size: 20px;"></ha-icon>`
        : status === true
          ? html`<ha-icon icon="mdi:check-circle" style="color: var(--success-color, #4caf50); --mdc-icon-size: 20px;"></ha-icon>`
          : html`<ha-spinner size="small"></ha-spinner>`;

    return html`
      <div style="margin: 8px 0;">
        <label style="font-size: 0.875em; color: var(--secondary-text-color);">${label}</label>
        <div style="display: flex; align-items: center; gap: 8px;">
          <select
            .value=${selected}
            @change=${(e) => onChange(e.target.value)}
            style="flex: 1; min-width: 0; padding: 8px; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--card-background-color); color: var(--primary-text-color); font-size: 0.95em;"
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
          <span style="width: 28px; text-align: center; display: flex; align-items: center; justify-content: center;">${statusIcon}</span>
        </div>
      </div>
    `;
  }

  // ── Cumulative energy meter registers (optional) ────────────────────

  private _renderRegisterDropdown(
    selected: string,
    onChange: (val: string) => void,
    allSelected: Set<string>
  ) {
    const registers = this._sensorEntities.filter(e => e.isEnergyRegister);
    return html`
      <select
        .value=${selected}
        @change=${(e) => onChange(e.target.value)}
        style="width: 100%; min-width: 0; padding: 8px; border: 1px solid var(--divider-color); border-radius: 4px; background: var(--card-background-color); color: var(--primary-text-color); font-size: 0.95em;"
      >
        <option value="">Select a sensor...</option>
        ${registers.map(e => html`
          <option
            value=${e.id}
            ?selected=${e.id === selected}
            ?disabled=${e.id !== selected && allSelected.has(e.id)}
          >${e.name} (${e.id})</option>
        `)}
      </select>
    `;
  }

  private _renderIntegrationHint() {
    return html`
      <p style="margin: 4px 0;">
        <strong>If the sensor you need is missing from the list, it may still
        need to be enabled on the meter integration</strong> (Settings →
        Devices &amp; Services → your meter → Entities → show disabled
        entities).
      </p>
    `;
  }

  private _renderPowerHelp() {
    const count = this._phases ?? 1;
    return html`
      <details style="margin: 4px 0 12px; font-size: 0.85em; color: var(--secondary-text-color);">
        <summary style="cursor: pointer;">Help — which sensors do I pick?</summary>
        <div style="margin-top: 6px;">
          <p style="margin: 4px 0;">
            Pick the <strong>power</strong> sensors (in W or kW) for each phase:
            how much the connection draws from the grid (consumption) and feeds
            back (production). Select one per phase (L1..L${count}).
          </p>
          ${this._renderIntegrationHint()}
        </div>
      </details>
    `;
  }

  private _renderRegisterHelp() {
    return html`
      <details style="margin: 4px 0; font-size: 0.85em; color: var(--secondary-text-color);">
        <summary style="cursor: pointer;">What are these? (optional)</summary>
        <div style="margin-top: 6px;">
          <p style="margin: 4px 0;">
            These are the meter's <strong>cumulative</strong> import/export
            registers — the ever-increasing kWh totals, one per tariff. V2G
            Liberty uses their per-interval increase to send the exact
            whole-connection energy to FlexMeasures. Leave them empty to skip
            this; it is optional.
          </p>
          <p style="margin: 4px 0;">
            Only sensors of device class <em>energy</em> with state class
            <em>total_increasing</em> are listed.
          </p>
          ${this._renderIntegrationHint()}
        </div>
      </details>
    `;
  }

  private _renderRegisters() {
    const allSelected = this._getAllSelectedEntities();
    const hasRegisters = this._sensorEntities.some(e => e.isEnergyRegister);
    const anyDetected =
      this._autoDetected &&
      [...this._consumptionRegisters, ...this._productionRegisters].some(
        e => e !== ''
      );

    const th =
      'text-align: left; font-size: 0.8em; color: var(--secondary-text-color); font-weight: normal; padding: 4px;';

    return html`
      <div style="margin-top: 24px;">
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
          <p style="margin: 0; flex: 1;"><strong>Energy meter registers</strong> (cumulative import/export, optional)</p>
          ${anyDetected
            ? html`<span class="auto-detected-badge">
                <ha-icon icon="mdi:auto-fix" style="--mdc-icon-size: 14px;"></ha-icon>
                Auto-detected
              </span>`
            : nothing}
        </div>
        ${this._renderRegisterHelp()}
        ${hasRegisters
          ? html`
            <table style="width: 100%; border-collapse: collapse; margin-top: 8px;">
              <thead>
                <tr>
                  <th style=${th}></th>
                  <th style=${th}>Consumption (import)</th>
                  <th style=${th}>Production (export)</th>
                </tr>
              </thead>
              <tbody>
                ${[0, 1].map(i => html`
                  <tr>
                    <td style="padding: 4px; font-size: 0.875em; color: var(--secondary-text-color); white-space: nowrap;">Tariff ${i + 1}</td>
                    <td style="padding: 4px;">
                      ${this._renderRegisterDropdown(
                        this._consumptionRegisters[i] ?? '',
                        (v) => this._setRegister('c', i, v),
                        allSelected
                      )}
                    </td>
                    <td style="padding: 4px;">
                      ${this._renderRegisterDropdown(
                        this._productionRegisters[i] ?? '',
                        (v) => this._setRegister('p', i, v),
                        allSelected
                      )}
                    </td>
                  </tr>
                `)}
              </tbody>
            </table>`
          : html`<p style="font-size: 0.85em; color: var(--secondary-text-color); margin: 8px 0;">
              No cumulative energy sensors were found. See the note above.
            </p>`}
      </div>
    `;
  }

  private _startListeningEntity(entityId: string) {
    if (this._entityListeners[entityId]) return; // already listening
    this._entityStatus = { ...this._entityStatus, [entityId]: undefined }; // pending
    // Fresh subscription: clear any stale negative flag for this entity.
    const negReset = { ...this._entityNegative };
    delete negReset[entityId];
    this._entityNegative = negReset;

    // Subscribe to state changes for this entity
    const unsub = this.hass.connection.subscribeEvents<HassEvent>(
      (event: HassEvent) => {
        const data = event.data as any;
        if (data.entity_id !== entityId) return;
        const newState = data.new_state?.state;
        if (newState == null || newState === '' || newState === 'unknown' || newState === 'unavailable') return;
        // Numeric check
        const value = parseFloat(newState);
        if (isNaN(value)) return;
        this._entityStatus = { ...this._entityStatus, [entityId]: true };
        // Consumption/production should be directional (>= 0). A negative value
        // points at a bidirectional/net or wrong sensor; flag it (sticky).
        if (value < 0) {
          this._entityNegative = { ...this._entityNegative, [entityId]: true };
        }
      },
      'state_changed'
    );
    unsub.then(unsubFn => {
      this._entityListeners[entityId] = unsubFn;
    });
  }

  private _stopListeningEntity(entityId: string) {
    const unsub = this._entityListeners[entityId];
    if (unsub) {
      try {
        if (typeof unsub === 'function') unsub();
      } catch (e) { /* ignore */ }
      delete this._entityListeners[entityId];
    }
    const copy = { ...this._entityStatus };
    delete copy[entityId];
    this._entityStatus = copy;
    const negCopy = { ...this._entityNegative };
    delete negCopy[entityId];
    this._entityNegative = negCopy;
  }

  private _getAllSelectedEntities(): Set<string> {
    const all = [
      ...this._consumptionEntities,
      ...this._productionEntities,
      ...this._consumptionRegisters,
      ...this._productionRegisters,
    ].filter(e => e !== '');
    return new Set(all);
  }

  private _setRegister(side: 'c' | 'p', i: number, val: string) {
    if (side === 'c') {
      const copy = [...this._consumptionRegisters];
      copy[i] = val;
      this._consumptionRegisters = copy;
    } else {
      const copy = [...this._productionRegisters];
      copy[i] = val;
      this._productionRegisters = copy;
    }
  }

  private _hasDuplicateEntities(): boolean {
    const all = [
      ...this._consumptionEntities,
      ...this._productionEntities,
    ].filter(e => e !== '');
    return new Set(all).size !== all.length;
  }

  private _hasEmptyEntities(): boolean {
    const count = this._phases ?? 1;
    return (
      this._consumptionEntities.filter(e => e !== '').length < count ||
      this._productionEntities.filter(e => e !== '').length < count
    );
  }

  private _hasPendingEntities(): boolean {
    const all = [
      ...this._consumptionEntities,
      ...this._productionEntities,
    ].filter(e => e !== '');
    return all.some(e => this._entityStatus[e] !== true);
  }

  // Meter registers are required, not optional. Per direction the selection is
  // only complete when it is either a single cumulative TOTAL register (1.8.0 /
  // 2.8.0 — already sums the tariffs), or BOTH tariff registers. A single
  // tariff register leaves the other tariff's energy uncounted, so it does not
  // count as complete.
  private _isTotalRegister(entityId: string): boolean {
    // The detector marks totals with "total" in the entity id
    // (e.g. ..._energy_consumption_total). Tariff registers carry "tarif".
    return /total/i.test(entityId);
  }

  private _directionIncomplete(registers: string[]): boolean {
    const filled = registers.filter(e => e !== '');
    if (filled.length === 0) return true;
    if (filled.some(e => this._isTotalRegister(e))) return false;
    return filled.length < 2;
  }

  private _hasMissingRegisters(): boolean {
    return (
      this._directionIncomplete(this._consumptionRegisters) ||
      this._directionIncomplete(this._productionRegisters)
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
    if (this._hasMissingRegisters()) {
      errors.push(
        'For both import and export, select a total meter register or both ' +
          'tariff 1 and tariff 2 (cumulative kWh).'
      );
    }
    if (errors.length === 0) return nothing;
    return html`${errors.map(
      e => html`<ha-alert alert-type="error">${e}</ha-alert>`
    )}`;
  }

  private _renderNegativeWarning() {
    const selected = this._getAllSelectedEntities();
    const hasNegative = Object.keys(this._entityNegative).some(
      e => this._entityNegative[e] && selected.has(e)
    );
    if (!hasNegative) return nothing;

    return html`
      <ha-alert alert-type="warning" title="A sensor reported a negative value">
        A selected grid sensor reported a negative value. These fields each need
        a sensor that reports only one direction and stays zero or positive: one
        for power drawn from the grid, and one for power fed back to the grid. A
        negative value means the sensor measures both directions at once (a net
        value) — for example a CT clamp that also sees the car feeding back.
        <p>
          <strong>Tip:</strong> V2G Liberty does not support a single net sensor.
          If your meter offers separate sensors for power drawn from and fed back
          to the grid, select those; if not, please contact V2G Liberty to
          request support.
        </p>
      </ha-alert>
    `;
  }

  private _renderSaveWarning() {
    if (
      !this._triedSave ||
      this._hasEmptyEntities() ||
      this._hasDuplicateEntities() ||
      this._hasMissingRegisters()
    ) {
      return nothing;
    }
    if (!this._hasPendingEntities()) return nothing;

    return html`
      <ha-alert alert-type="warning" title="Some sensors have not responded yet">
        This could mean the entity ID is incorrect, or the sensor is not reporting
        data at this time. For production sensors, this can be normal if there is
        currently no or little solar production — the meter may report 0 continuously,
        which does not generate a state change.
      </ha-alert>
    `;
  }

  // ── Solar panel consistency warning (plan task 30a) ─────────────────

  private _panelsThatWillBecomeInconsistent(): string[] {
    if (this._phases === null) return [];
    const newPhases = this._phases;
    return this._existingSolarPanels
      .filter((p) => {
        if (typeof p.phases !== 'number') return true; // unknown counts as broken
        if (p.phases > newPhases) return true;
        if (
          p.phases === 1 &&
          newPhases === 3 &&
          p.connected_to_phase !== 1 &&
          p.connected_to_phase !== 2 &&
          p.connected_to_phase !== 3
        ) {
          return true;
        }
        return false;
      })
      .map((p) => p.name ?? '(unnamed)');
  }

  private _renderSolarPanelWarning() {
    if (!this._phaseChangeConfirmed) return nothing;
    const affected = this._panelsThatWillBecomeInconsistent();
    if (affected.length === 0) return nothing;
    const list = affected.map((n) => html`<li>${n}</li>`);
    return html`
      <ha-alert
        alert-type="warning"
        title="This change will break ${affected.length === 1
          ? 'a solar panel'
          : 'solar panels'}"
        style="margin-top: 16px;"
      >
        <p style="margin: 0 0 8px 0;">
          The new phase count no longer matches the configuration of:
        </p>
        <ul style="margin: 0 0 8px 16px; padding: 0;">${list}</ul>
        <p style="margin: 0;">
          Continue anyway is allowed — the affected panel(s) will be flagged
          on the solar panels card so you can edit them afterwards. Nothing
          on the panels is changed automatically.
        </p>
      </ha-alert>
    `;
  }

  // ── Save ────────────────────────────────────────────────────────────

  private async _handleSave() {
    this._triedSave = true;

    // Block if empty, duplicate, or required registers missing
    if (
      this._hasEmptyEntities() ||
      this._hasDuplicateEntities() ||
      this._hasMissingRegisters()
    )
      return;

    // If some entities still pending and not yet confirmed
    if (this._hasPendingEntities() && !this._saveConfirmed) {
      this._saveConfirmed = true; // next click will be "Save anyway"
      return;
    }

    await this._save();
  }

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
          consumption_registers: this._consumptionRegisters.filter(e => e !== ''),
          production_registers: this._productionRegisters.filter(e => e !== ''),
        }
      );

      if (result.fm_error) {
        // FM-side rejection (provisioning failed). Lit preserves the form
        // state; ensure_* is idempotent so clicking Save again resends the
        // same payload and recovers cleanly once FM is available. Back/Cancel
        // closes without saving.
        this._saveError = `FlexMeasures error: ${result.fm_error}`;
        this._saving = false;
        this._saveConfirmed = false;
        return;
      }

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
      .save-error {
        margin-top: 12px;
        font-weight: 500;
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
      .phase-cards {
        display: flex;
        gap: 12px;
      }
      .phase-card {
        flex: 1;
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 12px;
        border: 1px solid var(--divider-color);
        border-radius: 12px;
        cursor: pointer;
        transition: border-color 0.2s, background 0.2s;
      }
      .phase-card:hover {
        border-color: var(--primary-color);
      }
      .phase-card.selected {
        border-color: var(--primary-color);
        border-width: 2px;
        background: color-mix(in srgb, var(--primary-color) 5%, transparent);
      }
      .phase-subtitle {
        font-size: 0.85em;
        color: var(--secondary-text-color);
      }
      .column-header {
        font-size: 0.75em;
        font-weight: 600;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.05em;
        width: 28px;
        text-align: center;
        flex-shrink: 0;
      }
      .auto-detected-badge {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        font-size: 0.75em;
        font-weight: 600;
        color: #2e7d32;
        background: #e8f5e9;
        padding: 2px 8px;
        border-radius: 12px;
        white-space: nowrap;
      }
      .requirements-box {
        border: 1px solid var(--divider-color);
        border-radius: 12px;
        padding: 16px;
        margin: 16px 0;
        background: var(--card-background-color);
      }
      .requirements-header {
        text-transform: uppercase;
        font-size: 0.75em;
        font-weight: 600;
        color: var(--secondary-text-color);
        letter-spacing: 0.05em;
        margin-bottom: 12px;
      }
      .requirement-item {
        display: flex;
        align-items: flex-start;
        gap: 12px;
        padding: 8px 0;
      }
      .requirement-icon {
        color: var(--primary-color);
        background: color-mix(in srgb, var(--primary-color) 10%, transparent);
        border-radius: 50%;
        padding: 8px;
        flex-shrink: 0;
        --mdc-icon-size: 24px;
      }
      .requirement-item div {
        font-size: 0.9em;
        line-height: 1.4;
      }
      .requirements-footer {
        margin-top: 12px;
        font-size: 0.8em;
        color: var(--secondary-text-color);
        font-style: italic;
      }
    `,
  ];
}
