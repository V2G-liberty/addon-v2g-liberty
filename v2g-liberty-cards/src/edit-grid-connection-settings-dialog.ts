import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEvent } from 'home-assistant-js-websocket';

import { callFunction } from './util/appdaemon';
import {
  renderButton,
  renderDialogHeader,
  renderSpinner,
  isNewHaDialogAPI,
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

  // Inline entity validation state (per entity: true=ok, undefined=pending)
  @state() private _entityStatus: { [entityId: string]: boolean | undefined } = {};
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
  private _sensorEntities: { id: string; name: string; isPower: boolean }[] = [];

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._step = Step.Intro;
    this._phases = null;
    this._capacityPerPhase = '';
    this._consumptionEntities = [];
    this._productionEntities = [];
    this._entityStatus = {};
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
            <ha-radio
              .checked=${this._phases === 1}
              name="phases"
              value="1"
              @change=${() => { this._selectPhases(1); }}
            ></ha-radio>
            <div>
              <strong>1 phase</strong><br/>
              <span class="phase-subtitle">Small apartment connection</span>
            </div>
          </div>
          <div
            class="phase-card ${this._phases === 3 ? 'selected' : ''}"
            @click=${() => { this._selectPhases(3); }}
          >
            <ha-radio
              .checked=${this._phases === 3}
              name="phases"
              value="3"
              @change=${() => { this._selectPhases(3); }}
            ></ha-radio>
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
        <ha-textfield
          type="number"
          inputmode="numeric"
          .value=${this._capacityPerPhase}
          @change=${(e) => { this._capacityPerPhase = e.target.value; }}
          min="6"
          max="80"
          suffix="A"
          style="width: 120px; --mdc-text-field-text-align: right;"
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

      ${this._triedSave ? this._renderEntityErrors() : nothing}
      ${this._renderSaveWarning()}

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
    const statusIcon = selected
      ? (status === true
          ? html`<ha-icon icon="mdi:check-circle" style="color: var(--success-color, #4caf50); --mdc-icon-size: 20px;"></ha-icon>`
          : html`<ha-spinner size="small"></ha-spinner>`)
      : nothing;

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

  private _startListeningEntity(entityId: string) {
    if (this._entityListeners[entityId]) return; // already listening
    this._entityStatus = { ...this._entityStatus, [entityId]: undefined }; // pending

    // Subscribe to state changes for this entity
    const unsub = this.hass.connection.subscribeEvents<HassEvent>(
      (event: HassEvent) => {
        const data = event.data as any;
        if (data.entity_id !== entityId) return;
        const newState = data.new_state?.state;
        if (newState == null || newState === '' || newState === 'unknown' || newState === 'unavailable') return;
        // Numeric check
        if (isNaN(parseFloat(newState))) return;
        this._entityStatus = { ...this._entityStatus, [entityId]: true };
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

  private _renderSaveWarning() {
    if (!this._triedSave || this._hasEmptyEntities() || this._hasDuplicateEntities()) {
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

    // Block if empty or duplicate
    if (this._hasEmptyEntities() || this._hasDuplicateEntities()) return;

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
