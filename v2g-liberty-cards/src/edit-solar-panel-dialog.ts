import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HassEvent } from 'home-assistant-js-websocket';

import { callFunction } from './util/appdaemon';
import { renderButton, renderDialogHeader, renderSpinner, isNewHaDialogAPI } from './util/render';
import { styles } from './card.styles';
import { DialogBase } from './dialog-base';

export const tagName = 'v2g-liberty-edit-solar-panel-dialog';

export interface SolarPanelDialogParams {
  /** Existing panel to edit. When omitted, the dialog adds a new panel. */
  panel?: {
    id: string;
    name: string;
    phases: 1 | 3;
    connected_to_phase?: 1 | 2 | 3;
    peak_power_wp?: number;
    power_entity_id: string;
    curtailable?: boolean;
    curtail_entity_id?: string;
  };
}

const enum Step {
  Intro = 'intro',
  Basic = 'basic',
  PowerEntity = 'power_entity',
  ConnectedToPhase = 'connected_to_phase',
  GridNotConfigured = 'grid_not_configured',
}

@customElement(tagName)
export class EditSolarPanelDialog extends DialogBase {
  @state() private _step: Step = Step.Intro;

  // Form state
  @state() private _editingId: string | null = null;
  @state() private _name: string = '';
  @state() private _peakPowerWp: string = '';
  @state() private _phases: 1 | 3 | null = null;
  @state() private _connectedToPhase: 1 | 2 | 3 | null = null;
  @state() private _powerEntityId: string = '';

  // Curtailable fields are deferred (see plan arch decision "PV curtailable
  // niet naar FM"); preserved verbatim across edits so existing data isn't
  // dropped on save.
  private _curtailablePreserved: boolean | undefined;
  private _curtailEntityIdPreserved: string | undefined;

  // Grid context (from get_grid_connection_settings)
  @state() private _gridPhases: number | null = null;

  // Normalised names of *other* solar panels — populated at dialog open
  // and used for the inline duplicate-name check on step 2. Empty when
  // editing the only panel, or before the panel list has been fetched.
  @state() private _otherPanelNames: Set<string> = new Set();

  // Power entity ids already claimed by other panels — used to disable
  // those entries in the step-3 dropdown so the same sensor can never be
  // chosen for two panels.
  @state() private _otherPanelEntities: Set<string> = new Set();

  // Inline entity validation
  @state() private _entityStatus: { [entityId: string]: boolean | undefined } = {};
  private _entityListeners: { [entityId: string]: any } = {};

  // Step-2 / step-3 validation flags
  @state() private _triedContinueBasic: boolean = false;
  @state() private _triedSave: boolean = false;
  @state() private _triedContinueEntity: boolean = false;
  @state() private _triedContinuePhase: boolean = false;

  // Save state
  @state() private _saving: boolean = false;
  @state() private _saveError: string = '';
  @state() private _saveConfirmed: boolean = false;

  // Delete state (edit-mode only)
  @state() private _deleting: boolean = false;
  @state() private _deleteConfirmed: boolean = false;
  @state() private _deleteError: string = '';

  // Sensor entity list (built once per open)
  private _sensorEntities: { id: string; name: string; isPower: boolean }[] = [];

  public async showDialog(params: SolarPanelDialogParams = {}): Promise<void> {
    super.showDialog();

    // Reset — skip the intro page when editing an existing panel.
    this._step = params.panel ? Step.Basic : Step.Intro;
    this._editingId = null;
    this._name = '';
    this._peakPowerWp = '';
    this._phases = null;
    this._connectedToPhase = null;
    this._powerEntityId = '';
    this._curtailablePreserved = undefined;
    this._curtailEntityIdPreserved = undefined;
    this._gridPhases = null;
    this._entityStatus = {};
    this._cleanupEntityListeners();
    this._triedContinueBasic = false;
    this._triedSave = false;
    this._triedContinueEntity = false;
    this._triedContinuePhase = false;
    this._saving = false;
    this._saveError = '';
    this._saveConfirmed = false;
    this._deleting = false;
    this._deleteConfirmed = false;
    this._deleteError = '';

    // Load grid context (needed to limit phases dropdown + decide step 4).
    // If the grid is not configured, take over the dialog immediately with
    // a blocking explanation (plan task 29): solar panel config depends on
    // grid phases, so there's nothing useful the user can do here yet.
    try {
      const grid = await callFunction(this.hass, 'get_grid_connection_settings');
      this._gridPhases = grid.phases ?? null;
      if (grid.configured !== true) {
        this._step = Step.GridNotConfigured;
      }
    } catch (e) {
      this._step = Step.GridNotConfigured;
    }

    // Pre-fill on edit
    if (params.panel) {
      this._editingId = params.panel.id;
      this._name = params.panel.name ?? '';
      this._peakPowerWp =
        params.panel.peak_power_wp != null ? String(params.panel.peak_power_wp) : '';
      this._phases = params.panel.phases;
      this._connectedToPhase = params.panel.connected_to_phase ?? null;
      this._powerEntityId = params.panel.power_entity_id ?? '';
      this._curtailablePreserved = params.panel.curtailable;
      this._curtailEntityIdPreserved = params.panel.curtail_entity_id;
    }

    // Load other panels' names + power entity ids for the inline
    // uniqueness checks (name on step 2, sensor on step 3). The backend
    // remains the source of truth (its checks are the safety net), but
    // inline feedback is friendlier than discovering the clash only on
    // Save. On failure we leave the sets empty — backend will still reject.
    try {
      const data = await callFunction(this.hass, 'get_solar_panels');
      const panels = (data.solar_panels ?? []) as {
        id: string;
        name: string;
        power_entity_id?: string;
      }[];
      const others = panels.filter((p) => p.id !== this._editingId);
      this._otherPanelNames = new Set(
        others
          .map((p) => (p.name ?? '').trim().toLowerCase())
          .filter((n) => n !== '')
      );
      this._otherPanelEntities = new Set(
        others.map((p) => p.power_entity_id ?? '').filter((e) => e !== '')
      );
    } catch (e) {
      this._otherPanelNames = new Set();
      this._otherPanelEntities = new Set();
    }

    this._buildSensorEntityList();
    if (this._powerEntityId) {
      this._startListeningEntity(this._powerEntityId);
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
      } catch (e) {
        /* ignore */
      }
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
      const isPower = deviceClass === 'power' || ['W', 'kW', 'MW'].includes(unit);
      const name = stateObj.attributes.friendly_name || entityId;
      this._sensorEntities.push({ id: entityId, name, isPower });
    }
    this._sensorEntities.sort((a, b) => {
      if (a.isPower !== b.isPower) return a.isPower ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const isNew = isNewHaDialogAPI(this.hass);
    const header = this._editingId ? 'Edit solar panel' : 'Add solar panel';
    let content;

    switch (this._step) {
      case Step.Intro:
        content = this._renderIntro();
        break;
      case Step.Basic:
        content = this._renderBasic();
        break;
      case Step.PowerEntity:
        content = this._renderPowerEntity();
        break;
      case Step.ConnectedToPhase:
        content = this._renderConnectedToPhase();
        break;
      case Step.GridNotConfigured:
        content = this._renderGridNotConfigured();
        break;
    }

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${isNew ? null : renderDialogHeader(this.hass, header)}
        .headerTitle=${isNew ? header : null}
      >
        ${content}
      </ha-dialog>
    `;
  }

  // ── Step 1: Introduction ────────────────────────────────────────────

  // ── Guard step: grid connection not configured ──────────────────────

  private _renderGridNotConfigured() {
    return html`
      <ha-alert
        alert-type="error"
        title="Grid connection not configured"
      >
        Solar panel settings depend on your grid connection (number of
        phases, capacity). Please configure the grid connection first via
        its settings card, then come back here to add your panels.
      </ha-alert>
      ${renderButton(
        this.hass,
        () => this.closeDialog(),
        true,
        this.hass.localize('ui.common.close'),
        false,
        'close'
      )}
    `;
  }

  // ── Step 1: Introduction ────────────────────────────────────────────

  private _renderIntro() {
    return html`
      <p>
        By monitoring your solar panels, V2G Liberty learns your generation
        patterns. Over time, this leads to <strong>better predictions</strong>
        and <strong>smarter schedules</strong> that match how much energy your
        panels are likely to produce.
      </p>
      <p>
        <strong>For Dutch users:</strong> this is a valuable preparation for the
        end of "saldering" (net metering).
      </p>

      <div class="requirements-box">
        <div class="requirements-header">What you need</div>
        <div class="requirement-item">
          <ha-icon icon="mdi:solar-power" class="requirement-icon"></ha-icon>
          <div>
            <strong>A solar inverter</strong><br />
            With a Home Assistant integration that exposes a power sensor for
            the inverter's current production.
          </div>
        </div>
        <div class="requirement-item">
          <ha-icon icon="mdi:information-outline" class="requirement-icon"></ha-icon>
          <div>
            <strong>The inverter details</strong><br />
            Peak power (Wp) and how many phases the inverter is connected to.
            You can add multiple inverters one at a time.
          </div>
        </div>
      </div>

      ${renderButton(
        this.hass,
        () => {
          this._step = Step.Basic;
        },
        true
      )}
    `;
  }

  // ── Step 2: Basic fields (name, Wp, phases) ─────────────────────────

  private _renderBasic() {
    const phasesAllowed = this._allowedPhaseOptions();
    return html`
      <div>
        <label class="field-label" for="panel-name">Name</label>
        <ha-textfield
          id="panel-name"
          .value=${this._name}
          @input=${(e: any) => {
            this._name = e.target.value;
          }}
          placeholder="e.g. South roof"
        ></ha-textfield>
        ${this._renderNameError()}
      </div>

      <div style="margin-top: 16px;">
        <label class="field-label" for="panel-wp">Peak power (Wp)</label>
        <ha-textfield
          id="panel-wp"
          type="text"
          inputmode="numeric"
          .value=${this._peakPowerWp}
          @input=${(e: any) => {
            this._peakPowerWp = e.target.value;
          }}
          suffix="Wp"
          style="width: 160px;"
        ></ha-textfield>
        ${this._renderWpError()}
      </div>

      <div style="margin-top: 16px;">
        <p style="margin: 0 0 8px 0;"><strong>Inverter phases</strong></p>
        <div class="phase-cards">
          ${phasesAllowed.includes(1)
            ? html`
                <div
                  class="phase-card ${this._phases === 1 ? 'selected' : ''}"
                  @click=${() => this._selectPhases(1)}
                >
                  <ha-radio
                    .checked=${this._phases === 1}
                    name="solar-phases"
                    value="1"
                    @change=${() => this._selectPhases(1)}
                  ></ha-radio>
                  <div>
                    <strong>1 phase</strong><br />
                    <span class="phase-subtitle">Single-phase inverter</span>
                  </div>
                </div>
              `
            : nothing}
          ${phasesAllowed.includes(3)
            ? html`
                <div
                  class="phase-card ${this._phases === 3 ? 'selected' : ''}"
                  @click=${() => this._selectPhases(3)}
                >
                  <ha-radio
                    .checked=${this._phases === 3}
                    name="solar-phases"
                    value="3"
                    @change=${() => this._selectPhases(3)}
                  ></ha-radio>
                  <div>
                    <strong>3 phases</strong><br />
                    <span class="phase-subtitle">Three-phase inverter</span>
                  </div>
                </div>
              `
            : nothing}
        </div>
        ${this._gridPhases === 1
          ? html`<div class="hint-note">
              Only 1-phase available because your grid connection is 1-phase.
            </div>`
          : nothing}
        ${this._triedContinueBasic && this._phases === null
          ? html`<div class="error">Please select the number of phases.</div>`
          : nothing}
      </div>

      ${this._deleteConfirmed ? this._renderDeleteConfirmationAlert() : nothing}
      ${this._renderStep2Footer()}
    `;
  }

  private _renderDeleteConfirmationAlert() {
    return html`
      <ha-alert
        alert-type="warning"
        title="Delete this panel?"
        style="margin-top: 32px;"
      >
        Local registration of
        <strong>${this._name.trim() || '(unnamed)'}</strong>
        will be removed. The asset in FlexMeasures is left in place (an
        administrator can clean it up if needed); historical data stays
        available there under the same id.
        ${this._deleteError
          ? html`<p class="error" style="margin-top:8px;">
              ${this._deleteError}
            </p>`
          : nothing}
      </ha-alert>
    `;
  }

  private _renderStep2Footer() {
    if (this._deleting) {
      return renderSpinner(this.hass);
    }
    if (this._deleteConfirmed) {
      // Confirmation flow takes over the footer — Cancel cancels the
      // delete (not the dialog), Yes deletes (red, destructive variant).
      const slot = isNewHaDialogAPI(this.hass) ? 'footer' : 'primaryAction';
      return html`
        ${renderButton(
          this.hass,
          () => {
            this._deleteConfirmed = false;
            this._deleteError = '';
          },
          false,
          this.hass.localize('ui.common.cancel'),
          false,
          'cancel-delete'
        )}
        <ha-button
          @click=${() => this._handleDelete()}
          slot=${slot}
          appearance="filled"
          variant="danger"
          test-id="confirm-delete"
          size="small"
          style="width: auto;"
        >
          Yes, delete
        </ha-button>
      `;
    }
    // Normal edit-mode footer: Delete… on the left, Cancel + Continue on
    // the right. The trailing ellipsis on the Delete label signals that
    // a confirmation step follows.
    const slot = isNewHaDialogAPI(this.hass) ? 'footer' : 'secondaryAction';
    return html`
      ${this._editingId
        ? html`<ha-button
            @click=${() => {
              this._deleteConfirmed = true;
              this._deleteError = '';
            }}
            slot=${slot}
            appearance="outlined"
            variant="secondary"
            test-id="delete"
            size="small"
            style="width: auto; margin-right: auto;"
          >
            Delete this panel…
          </ha-button>`
        : nothing}
      ${this._editingId
        ? renderButton(
            this.hass,
            () => this.closeDialog(),
            false,
            this.hass.localize('ui.common.cancel'),
            false,
            'cancel'
          )
        : renderButton(
            this.hass,
            () => {
              this._triedContinueBasic = false;
              this._step = Step.Intro;
            },
            false,
            this.hass.localize('ui.common.back'),
            false,
            'back',
            true
          )}
      ${renderButton(this.hass, () => this._continueFromBasic(), true)}
    `;
  }

  private async _handleDelete() {
    if (!this._editingId) return;
    this._deleting = true;
    this._deleteError = '';
    try {
      const result = await callFunction(this.hass, 'delete_solar_panel', {
        id: this._editingId,
      });
      if (result.error) {
        this._deleteError = result.error;
        this._deleting = false;
        return;
      }
      this.closeDialog();
    } catch (e) {
      this._deleteError = 'Failed to delete the solar panel. Please try again.';
      this._deleting = false;
    }
  }

  private _allowedPhaseOptions(): number[] {
    if (this._gridPhases === 1) return [1];
    return [1, 3];
  }

  private _selectPhases(phases: 1 | 3) {
    this._phases = phases;
    // Clear connected_to_phase if no longer relevant
    if (!this._needsConnectedToPhase()) {
      this._connectedToPhase = null;
    }
  }

  private _isWpValid(): boolean {
    // Required. Use Number() (not parseFloat) so "500abc" → NaN instead of 500.
    if (this._peakPowerWp.trim() === '') return false;
    const v = Number(this._peakPowerWp);
    return Number.isInteger(v) && v >= 500 && v <= 15000;
  }

  private _isNameDuplicate(): boolean {
    const norm = this._name.trim().toLowerCase();
    return norm !== '' && this._otherPanelNames.has(norm);
  }

  private _renderNameError() {
    if (this._name.trim() === '') {
      return this._triedContinueBasic
        ? html`<div class="error">Please enter a name.</div>`
        : nothing;
    }
    if (this._isNameDuplicate()) {
      return html`<div class="error">
        A solar panel named '${this._name.trim()}' already exists. Please
        choose a different name.
      </div>`;
    }
    return nothing;
  }

  private _renderWpError() {
    if (this._peakPowerWp.trim() === '') {
      return this._triedContinueBasic
        ? html`<div class="error">Please enter a peak power.</div>`
        : nothing;
    }
    if (!this._isWpValid()) {
      return html`<div class="error">
        Must be a whole number between 500 and 15000 Wp.
      </div>`;
    }
    return nothing;
  }

  private _continueFromBasic() {
    this._triedContinueBasic = true;
    if (this._name.trim() === '') return;
    if (this._isNameDuplicate()) return;
    if (!this._isWpValid()) return;
    if (this._phases === null) return;
    this._step = Step.PowerEntity;
  }

  // ── Step 3: Power entity ────────────────────────────────────────────

  private _renderPowerEntity() {
    const hasPowerGroup = this._sensorEntities.some((e) => e.isPower);
    const status = this._powerEntityId
      ? this._entityStatus[this._powerEntityId]
      : undefined;
    const statusIcon = this._powerEntityId
      ? status === true
        ? html`<ha-icon
            icon="mdi:check-circle"
            style="color: var(--success-color, #4caf50); --mdc-icon-size: 20px;"
          ></ha-icon>`
        : html`<ha-spinner size="small"></ha-spinner>`
      : nothing;

    return html`
      <p>
        <strong>Power sensor</strong> — the Home Assistant entity that reports
        the current production of this inverter.
      </p>
      <div style="display: flex; align-items: center; gap: 8px; margin-top: 8px;">
        <select
          .value=${this._powerEntityId}
          @change=${(e: any) => this._selectPowerEntity(e.target.value)}
          style="flex: 1; min-width: 0; padding: 8px;
                 border: 1px solid var(--divider-color); border-radius: 4px;
                 background: var(--card-background-color);
                 color: var(--primary-text-color); font-size: 0.95em;"
        >
          <option value="">Select a sensor...</option>
          ${hasPowerGroup
            ? html`<optgroup label="Power sensors">
                ${this._sensorEntities
                  .filter((e) => e.isPower)
                  .map((e) => this._renderSensorOption(e))}
              </optgroup>`
            : nothing}
          <optgroup label="${hasPowerGroup ? 'Other sensors' : 'Sensors'}">
            ${this._sensorEntities
              .filter((e) => !e.isPower)
              .map((e) => this._renderSensorOption(e))}
          </optgroup>
        </select>
        <span
          style="width: 28px; text-align: center; display: flex;
                 align-items: center; justify-content: center;"
        >
          ${statusIcon}
        </span>
      </div>
      ${this._triedContinueEntity && this._powerEntityId === ''
        ? html`<ha-alert alert-type="error"
            >Please select a power sensor.</ha-alert
          >`
        : nothing}
      ${this._renderPendingWarning()}

      ${renderButton(
        this.hass,
        () => {
          this._triedContinueEntity = false;
          this._step = Step.Basic;
        },
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${this._renderEntityNextOrSave()}
    `;
  }

  private _renderEntityNextOrSave() {
    // If a phase selection step is needed, this is a Continue button;
    // otherwise the save flow lives here.
    if (this._needsConnectedToPhase()) {
      return renderButton(
        this.hass,
        () => this._continueFromEntity(),
        true,
        this.hass.localize('ui.common.continue')
      );
    }
    return this._renderSaveButton(() => this._continueFromEntity());
  }

  private _renderSensorOption(e: { id: string; name: string }) {
    // Disable entities already claimed by another panel (mirrors the
    // grid-dialog pattern). The currently selected entity is never
    // disabled even if it's somehow in the set, so the dropdown stays
    // self-consistent.
    const isUsedByOther =
      this._otherPanelEntities.has(e.id) && e.id !== this._powerEntityId;
    const label = isUsedByOther
      ? `${e.name} (${e.id}) — used by another panel`
      : `${e.name} (${e.id})`;
    return html`
      <option
        value=${e.id}
        ?selected=${e.id === this._powerEntityId}
        ?disabled=${isUsedByOther}
      >
        ${label}
      </option>
    `;
  }

  private _selectPowerEntity(entityId: string) {
    const old = this._powerEntityId;
    if (old) this._stopListeningEntity(old);
    this._powerEntityId = entityId;
    if (entityId) this._startListeningEntity(entityId);
  }

  private _renderPendingWarning() {
    if (
      !this._triedSave ||
      this._powerEntityId === '' ||
      this._entityStatus[this._powerEntityId] === true
    ) {
      return nothing;
    }
    const currentState = this.hass.states[this._powerEntityId]?.state ?? '(none)';
    return html`
      <ha-alert
        alert-type="warning"
        title="Sensor has not reported a usable value yet"
      >
        <p style="margin: 0 0 8px 0;">
          The selected sensor
          <code>${this._powerEntityId}</code> is currently reporting
          <strong>${currentState}</strong>, and has not sent a usable
          numeric value since this dialog was opened.
        </p>
        <p style="margin: 0 0 8px 0;">Likely causes:</p>
        <ul style="margin: 0 0 8px 16px; padding: 0;">
          <li>The entity ID is wrong or the integration is broken.</li>
          <li>
            The sensor is reporting <code>unknown</code> or
            <code>unavailable</code> — usually fixed by reloading the
            integration in Home Assistant.
          </li>
          <li>
            The sensor genuinely hasn't reported anything yet (rare,
            but possible right after a fresh install).
          </li>
        </ul>
        <p style="margin: 0;">
          Clicking <strong>Save anyway</strong> will save with the
          current configuration. V2G Liberty will start collecting data
          as soon as the sensor reports a numeric value; until then no
          PV data will be sent to FlexMeasures for this panel.
        </p>
      </ha-alert>
    `;
  }

  private _continueFromEntity() {
    this._triedContinueEntity = true;
    if (this._powerEntityId === '') return;
    if (this._needsConnectedToPhase()) {
      this._step = Step.ConnectedToPhase;
    } else {
      this._handleSave();
    }
  }

  private _needsConnectedToPhase(): boolean {
    return this._phases === 1 && this._gridPhases === 3;
  }

  // ── Step 4: connected_to_phase ──────────────────────────────────────

  private _renderConnectedToPhase() {
    return html`
      <p>
        <strong>Which phase is this 1-phase inverter connected to?</strong>
      </p>
      <p class="hint-note">
        Your grid connection is 3-phase, so a 1-phase inverter sits on one of
        the three phases. Pick the one your installer wired it to.
      </p>
      <div class="phase-cards" style="margin-top: 12px;">
        ${[1, 2, 3].map(
          (n) => html`
            <div
              class="phase-card ${this._connectedToPhase === n ? 'selected' : ''}"
              @click=${() => {
                this._connectedToPhase = n as 1 | 2 | 3;
              }}
            >
              <ha-radio
                .checked=${this._connectedToPhase === n}
                name="connected-to-phase"
                value=${String(n)}
                @change=${() => {
                  this._connectedToPhase = n as 1 | 2 | 3;
                }}
              ></ha-radio>
              <div><strong>L${n}</strong></div>
            </div>
          `
        )}
      </div>
      ${this._triedContinuePhase && this._connectedToPhase === null
        ? html`<div class="error">Please select a phase.</div>`
        : nothing}

      ${renderButton(
        this.hass,
        () => {
          this._triedContinuePhase = false;
          this._step = Step.PowerEntity;
        },
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${this._renderSaveButton(() => this._triggerSaveFromPhaseStep())}
    `;
  }

  private _triggerSaveFromPhaseStep() {
    this._triedContinuePhase = true;
    if (this._connectedToPhase === null) return;
    this._handleSave();
  }

  // ── Inline entity validation (state_changed subscription) ───────────

  private _startListeningEntity(entityId: string) {
    if (this._entityListeners[entityId]) return;

    // Initial state check: if the entity already has a valid numeric value
    // right now (including 0 — a working sensor reporting no production
    // counts as healthy), mark as ✓ immediately. Avoids a forever-spinner
    // for sensors that report continuously without firing state_changed.
    const initiallyValid = this._isStateValid(
      this.hass.states[entityId]?.state
    );
    this._entityStatus = {
      ...this._entityStatus,
      [entityId]: initiallyValid ? true : undefined,
    };

    // Still listen — the sensor may transition from
    // unknown/unavailable to a valid value during the dialog session.
    const unsub = this.hass.connection.subscribeEvents<HassEvent>(
      (event: HassEvent) => {
        const data = event.data as any;
        if (data.entity_id !== entityId) return;
        if (!this._isStateValid(data.new_state?.state)) return;
        this._entityStatus = { ...this._entityStatus, [entityId]: true };
      },
      'state_changed'
    );
    unsub.then((unsubFn) => {
      this._entityListeners[entityId] = unsubFn;
    });
  }

  private _isStateValid(state: string | undefined | null): boolean {
    if (state == null || state === '' || state === 'unknown' || state === 'unavailable') {
      return false;
    }
    return !isNaN(parseFloat(state));
  }

  private _stopListeningEntity(entityId: string) {
    const unsub = this._entityListeners[entityId];
    if (unsub) {
      try {
        if (typeof unsub === 'function') unsub();
      } catch (e) {
        /* ignore */
      }
      delete this._entityListeners[entityId];
    }
    const copy = { ...this._entityStatus };
    delete copy[entityId];
    this._entityStatus = copy;
  }

  // ── Save ────────────────────────────────────────────────────────────

  private _renderSaveButton(onSave: () => void) {
    if (this._saving) {
      return renderSpinner(this.hass);
    }
    const label = this._saveConfirmed
      ? 'Save anyway'
      : this.hass.localize('ui.common.save');
    return html`
      ${this._saveError
        ? html`<ha-alert alert-type="error">${this._saveError}</ha-alert>`
        : nothing}
      ${renderButton(this.hass, onSave, true, label)}
    `;
  }

  private async _handleSave() {
    this._triedSave = true;
    const isPending = this._entityStatus[this._powerEntityId] !== true;
    if (isPending && !this._saveConfirmed) {
      // First Save click while still pending → show warning + "Save anyway".
      this._saveConfirmed = true;
      return;
    }
    await this._save();
  }

  private async _save() {
    this._saving = true;
    this._saveError = '';

    const payload: { [key: string]: any } = {
      name: this._name.trim(),
      phases: this._phases,
      power_entity_id: this._powerEntityId,
    };
    if (this._editingId) {
      payload.id = this._editingId;
    }
    if (this._peakPowerWp !== '') {
      payload.peak_power_wp = Number(this._peakPowerWp);
    }
    if (this._needsConnectedToPhase() && this._connectedToPhase != null) {
      payload.connected_to_phase = this._connectedToPhase;
    }
    // Preserve curtailable / curtail_entity_id verbatim across edits — the
    // UI for these fields is deferred (see plan arch decision "PV curtailable
    // niet naar FM"), but we must not silently drop data that already exists.
    if (this._curtailablePreserved !== undefined) {
      payload.curtailable = this._curtailablePreserved;
    }
    if (this._curtailEntityIdPreserved !== undefined) {
      payload.curtail_entity_id = this._curtailEntityIdPreserved;
    }

    try {
      const result = await callFunction(this.hass, 'save_solar_panel', payload);
      if (result.fm_error) {
        // FM-side rejection. Lit preserves the form state, ensure_* is
        // idempotent on retry, so clicking Save again resends the same
        // payload and recovers cleanly once FM is available.
        this._saveError = `FlexMeasures error: ${result.fm_error}`;
        this._saving = false;
        return;
      }
      if (result.error) {
        this._saveError = result.error;
        this._saving = false;
        return;
      }
      this.closeDialog();
    } catch (e) {
      this._saveError = 'Failed to save the solar panel. Please try again.';
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
      .field-label {
        display: block;
        font-size: 0.875em;
        color: var(--secondary-text-color);
        margin-bottom: 4px;
      }
      .hint-note {
        font-size: 0.85em;
        color: var(--secondary-text-color);
        margin-top: 4px;
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
        transition:
          border-color 0.2s,
          background 0.2s;
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
    `,
  ];
}
