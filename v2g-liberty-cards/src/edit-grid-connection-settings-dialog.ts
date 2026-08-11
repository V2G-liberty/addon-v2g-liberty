// Grid connection configuration flow (redesign).
//
// Five steps: Intro → Connection → Power → Meter readings → Done. Storage is
// unchanged (the four settings lists); roles are derived for rendering only.
// Rows and status come from grid-connection-status / -sensor-row / -roles; the
// per-row "Change / Choose sensor" opens the choose-sensor side-step.
//
// Buttons are never disabled to block an action (except the shared FM
// reachability gate on the intro): pressing on with something missing shows an
// ha-alert saying what. Copy is inline English for now (moves to strings.json,
// Fase 7.1).

import { css, html, nothing, TemplateResult } from 'lit';
import { customElement, state } from 'lit/decorators';
import { fireEvent } from 'custom-card-helpers';

import { callFunction } from './util/appdaemon';
import {
  renderButton,
  renderDialogHeader,
  isNewHaDialogAPI,
  renderHaInput,
  renderRadioIndicator,
} from './util/render';
import { styles } from './card.styles';
import { DialogBase } from './dialog-base';
import { RoleDefinition, statusOf, candidatesFor } from './grid-connection-status';
import { renderSensorRow, renderStatusIcon } from './grid-connection-sensor-row';
import {
  powerRoles,
  meterRoles,
  roleTarget,
  selectedEntityIds,
} from './grid-connection-roles';
import {
  tagName as chooseSensorTag,
  ChooseSensorDialogParams,
} from './choose-sensor-dialog';

export const tagName = 'v2g-liberty-edit-grid-connection-settings-dialog';

const enum Step {
  Intro = 'intro',
  Connection = 'connection',
  Power = 'power',
  Meter = 'meter',
  Done = 'done',
}

interface SolarPanel {
  name: string;
  phases: number;
  connected_to_phase?: number;
}

@customElement(tagName)
export class EditGridConnectionSettingsDialog extends DialogBase {
  @state() private _step: Step = Step.Intro;
  @state() private _phases: number | null = null;
  @state() private _capacityPerPhase = '';

  // Storage stays the four lists; roles are derived for rendering.
  @state() private _consumptionEntities: string[] = [];
  @state() private _productionEntities: string[] = [];
  @state() private _consumptionRegisters: string[] = ['', ''];
  @state() private _productionRegisters: string[] = ['', ''];

  @state() private _autoDetected = false;

  // Per-step "the user pressed on with something missing" flags.
  @state() private _triedContinueConnection = false;
  @state() private _phaseChangeConfirmed = false;
  @state() private _triedContinuePower = false;
  @state() private _triedContinueMeter = false;
  @state() private _triedSave = false;

  @state() private _saving = false;
  @state() private _saveError = '';

  @state() private _existingSolarPanels: SolarPanel[] = [];

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._step = Step.Intro;
    this._phases = null;
    this._capacityPerPhase = '';
    this._consumptionEntities = [];
    this._productionEntities = [];
    this._consumptionRegisters = ['', ''];
    this._productionRegisters = ['', ''];
    this._autoDetected = false;
    this._triedContinueConnection = false;
    this._phaseChangeConfirmed = false;
    this._triedContinuePower = false;
    this._triedContinueMeter = false;
    this._triedSave = false;
    this._saving = false;
    this._saveError = '';

    // Fresh FlexMeasures reachability probe — the intro gate stays closed until
    // it reports back (Save provisions sensors on FlexMeasures).
    void this._probeFm();

    // Load existing settings if configured.
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
      // Ignore — start fresh.
    }

    // Auto-detect only when nothing is configured yet.
    if (!this._phases) {
      try {
        const detected = await callFunction(this.hass, 'detect_grid_entities');
        if (
          detected.phases ||
          detected.capacity_per_phase ||
          detected.consumption_entities?.length > 0
        ) {
          this._autoDetected = true;
        }
        if (detected.phases) this._phases = detected.phases;
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
          this._consumptionRegisters = this._padTo2(
            detected.consumption_registers
          );
        }
        if (detected.production_registers?.length > 0) {
          this._autoDetected = true;
          this._productionRegisters = this._padTo2(detected.production_registers);
        }
      } catch (e) {
        // Detection failed — the user fills things in manually.
      }
    }

    // Existing solar panels, to warn when a phase change would invalidate one.
    try {
      const sp = await callFunction(this.hass, 'get_solar_panels');
      this._existingSolarPanels = (sp.solar_panels ?? []) as SolarPanel[];
    } catch (e) {
      this._existingSolarPanels = [];
    }

    await this.updateComplete;
  }

  private _padTo2(arr: string[]): string[] {
    return [arr[0] ?? '', arr[1] ?? ''];
  }

  protected render() {
    if (!this.isOpen) return nothing;
    const isNew = isNewHaDialogAPI(this.hass);
    const header = this._headerFor(this._step);

    let content: TemplateResult;
    switch (this._step) {
      case Step.Intro:
        content = this._renderIntro();
        break;
      case Step.Connection:
        content = this._renderConnection();
        break;
      case Step.Power:
        content = this._renderPower();
        break;
      case Step.Meter:
        content = this._renderMeter();
        break;
      case Step.Done:
        content = this._renderDone();
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

  private _headerFor(step: Step): string {
    switch (step) {
      case Step.Connection:
        return 'Connection';
      case Step.Power:
        return 'Power';
      case Step.Meter:
        return 'Meter readings';
      case Step.Done:
        return 'Done';
      default:
        return 'Grid connection';
    }
  }

  // ── Step 1: Intro ───────────────────────────────────────────────────

  private _renderIntro() {
    return html`
      ${this._renderFmGate('grid connection')}

      <p>
        By monitoring your grid connection, V2G Liberty learns your household
        energy patterns. That leads to <strong>better predictions</strong> and
        <strong>smarter schedules</strong>.
      </p>
      <p>
        <strong>For Dutch users:</strong> a valuable preparation for the end of
        net metering. Once it ends, this configuration will be required.
      </p>

      <div class="requirements-box">
        <div class="requirements-header">What you need</div>
        <div class="requirement-item">
          <ha-icon icon="mdi:meter-electric" class="requirement-icon"></ha-icon>
          <div>
            <strong>Smart meter</strong><br />
            Reports power per phase in real time.
          </div>
        </div>
        <div class="requirement-item">
          <ha-icon icon="mdi:cable-data" class="requirement-icon"></ha-icon>
          <div>
            <strong>P1 cable</strong><br />
            A USB P1 port cable or similar (~€15).
          </div>
        </div>
        <div class="requirement-item">
          <ha-icon icon="mdi:home-assistant" class="requirement-icon"></ha-icon>
          <div>
            <strong>Home Assistant integration</strong><br />
            E.g. the DSMR Smart Meter integration, exposing the meter as sensor
            entities.
          </div>
        </div>
      </div>

      ${renderButton(
        this.hass,
        () => {
          this._step = Step.Connection;
        },
        true,
        null,
        !this._fmReachable
      )}
    `;
  }

  // ── Step 2: Connection (phases + capacity) ──────────────────────────

  private _renderConnection() {
    return html`
      <div>
        <div class="section-head">
          <p style="margin: 0;">
            <strong>How many phases does your grid connection have?</strong>
          </p>
          ${this._autoDetected && this._phases !== null
            ? this._autoBadge()
            : nothing}
        </div>
        <div class="phase-cards">
          <div
            class="phase-card ${this._phases === 1 ? 'selected' : ''}"
            @click=${() => this._selectPhases(1)}
          >
            ${renderRadioIndicator(this._phases === 1)}
            <div>
              <strong>1 phase</strong><br />
              <span class="phase-subtitle">Small connection</span>
            </div>
          </div>
          <div
            class="phase-card ${this._phases === 3 ? 'selected' : ''}"
            @click=${() => this._selectPhases(3)}
          >
            ${renderRadioIndicator(this._phases === 3)}
            <div>
              <strong>3 phases</strong><br />
              <span class="phase-subtitle">Standard connection</span>
            </div>
          </div>
        </div>
        ${this._triedContinueConnection && this._phases === null
          ? html`<div class="error">Please select the number of phases.</div>`
          : nothing}
        <details class="hint">
          <summary>Not sure?</summary>
          <p>
            Check your smart meter integration. Separate L1, L2 and L3 sensors
            mean 3 phases; only L1 means 1 phase.
          </p>
        </details>
      </div>

      <div style="margin-top: 16px;">
        <div class="section-head">
          <p style="margin: 0;"><strong>Capacity per phase (ampere)</strong></p>
          ${this._autoDetected && this._capacityPerPhase !== ''
            ? this._autoBadge()
            : nothing}
        </div>
        ${renderHaInput({
          value: this._capacityPerPhase,
          onChange: (e: any) => (this._capacityPerPhase = e.target.value),
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
          <p>
            On your energy contract or your main fuse — typically 25 A or 35 A.
            Enter the actual value, not a lower safety margin.
          </p>
        </details>
      </div>

      ${renderButton(
        this.hass,
        () => {
          this._triedContinueConnection = false;
          this._step = Step.Intro;
        },
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${this._renderSolarPanelWarning()}
      ${renderButton(
        this.hass,
        () => this._continueToPower(),
        true,
        this._phaseChangeConfirmed
          ? 'Continue anyway'
          : this.hass.localize('ui.common.continue')
      )}
    `;
  }

  private _selectPhases(phases: 1 | 3) {
    if (this._phases !== phases) this._phaseChangeConfirmed = false;
    this._phases = phases;
  }

  private _isCapacityValid(): boolean {
    if (this._capacityPerPhase === '') return false;
    const cap = parseFloat(this._capacityPerPhase);
    return !isNaN(cap) && Number.isInteger(cap) && cap >= 6 && cap <= 80;
  }

  private _renderCapacityError() {
    if (this._capacityPerPhase === '' && this._triedContinueConnection) {
      return html`<div class="error">Please enter the capacity.</div>`;
    }
    if (this._capacityPerPhase !== '' && !this._isCapacityValid()) {
      return html`<div class="error">
        Must be a whole number between 6 and 80.
      </div>`;
    }
    return nothing;
  }

  private _continueToPower() {
    this._triedContinueConnection = true;
    if (this._phases === null || !this._isCapacityValid()) return;

    // Soft warning: would the new phase choice invalidate existing panels?
    if (
      this._panelsThatWillBecomeInconsistent().length > 0 &&
      !this._phaseChangeConfirmed
    ) {
      this._phaseChangeConfirmed = true;
      return;
    }

    // Size the power arrays to the phase count.
    const count = this._phases;
    if (this._consumptionEntities.length !== count) {
      this._consumptionEntities = this._resize(this._consumptionEntities, count);
    }
    if (this._productionEntities.length !== count) {
      this._productionEntities = this._resize(this._productionEntities, count);
    }

    this._triedContinuePower = false;
    this._step = Step.Power;
  }

  private _resize(arr: string[], count: number): string[] {
    const copy = arr.slice(0, count);
    while (copy.length < count) copy.push('');
    return copy;
  }

  // ── Step 3: Power ───────────────────────────────────────────────────

  private _renderPower() {
    const roles = powerRoles(
      this._phases ?? 1,
      this._consumptionEntities,
      this._productionEntities
    );
    const consumption = roles.filter(r => r.key.startsWith('consumption'));
    const production = roles.filter(r => r.key.startsWith('production'));
    const noCandidates = candidatesFor(this.hass, 'power').length === 0;

    return html`
      <div class="sensors-intro">
        <p style="margin: 0;"><strong>Sensors</strong></p>
        <p class="muted">
          To follow what your household uses and returns, V2G Liberty reads the
          power on every phase. These sensors were recognised automatically —
          please check that each one is right before continuing.
        </p>
      </div>

      ${noCandidates ? this._renderNotRecognised('power per phase') : nothing}

      <div class="group-head"><strong>Consumption</strong> <span class="muted">power drawn from the grid</span> <span class="live">LIVE</span></div>
      ${consumption.map(role =>
        renderSensorRow(this.hass, role, {
          onChoose: def => this._openChooseSensor(def),
        })
      )}

      <div class="group-head" style="margin-top: 16px;"><strong>Production</strong> <span class="muted">power fed back to the grid</span> <span class="live">LIVE</span></div>
      ${production.map(role =>
        renderSensorRow(this.hass, role, {
          onChoose: def => this._openChooseSensor(def),
        })
      )}

      ${this._triedContinuePower && this._powerIncomplete()
        ? html`<ha-alert alert-type="error" style="margin-top: 12px;">
            Sensor(s) still missing — choose a sensor for every phase, for both
            consumption and production, to continue.
          </ha-alert>`
        : nothing}

      ${renderButton(
        this.hass,
        () => {
          this._step = Step.Connection;
        },
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${renderButton(this.hass, () => this._continueToMeter(), true)}
    `;
  }

  private _continueToMeter() {
    this._triedContinuePower = true;
    if (this._powerIncomplete()) return;
    this._triedContinueMeter = false;
    this._step = Step.Meter;
  }

  // ── Step 4: Meter readings ──────────────────────────────────────────

  private _renderMeter() {
    const roles = meterRoles(
      this._consumptionRegisters,
      this._productionRegisters
    );
    // roles = [import_t1, import_t2, export_t1, export_t2]
    const tariff1 = [roles[0], roles[2]];
    const tariff2 = [roles[1], roles[3]];
    const noCandidates = candidatesFor(this.hass, 'meter_reading').length === 0;

    const rows = (rs: RoleDefinition[]) =>
      rs.map(role =>
        renderSensorRow(this.hass, role, {
          showStatus: false,
          onChoose: def => this._openChooseSensor(def),
        })
      );

    return html`
      <div class="sensors-intro">
        <p style="margin: 0;"><strong>Sensors</strong></p>
        <p class="muted">
          The cumulative meter readings are what your energy bill is settled on,
          per tariff. There are always two tariffs — with a single tariff,
          tariff 2 simply stays at the same value. These were recognised
          automatically — please check them before continuing.
        </p>
      </div>

      ${noCandidates
        ? this._renderNotRecognised('cumulative kWh readings')
        : nothing}

      <div class="group-head"><strong>Tariff 1</strong></div>
      ${rows(tariff1)}
      <div class="group-head" style="margin-top: 16px;"><strong>Tariff 2</strong></div>
      ${rows(tariff2)}

      ${this._triedContinueMeter && this._metersIncomplete()
        ? html`<ha-alert alert-type="error" style="margin-top: 12px;">
            Sensor(s) still missing — for both import and export, choose a total
            register or both tariff 1 and tariff 2 (cumulative kWh).
          </ha-alert>`
        : nothing}

      ${renderButton(
        this.hass,
        () => {
          this._step = Step.Power;
        },
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${renderButton(this.hass, () => this._continueToDone(), true)}
    `;
  }

  private _continueToDone() {
    this._triedContinueMeter = true;
    if (this._metersIncomplete()) return;
    this._triedSave = false;
    this._step = Step.Done;
  }

  // ── Step 5: Done ────────────────────────────────────────────────────

  private _renderDone() {
    const powerList = powerRoles(
      this._phases ?? 1,
      this._consumptionEntities,
      this._productionEntities
    );
    const meterList = meterRoles(
      this._consumptionRegisters,
      this._productionRegisters
    );
    const linked = selectedEntityIds(powerList, meterList).length;
    const complete = !this._powerIncomplete() && !this._metersIncomplete();

    return html`
      ${complete
        ? html`<ha-alert alert-type="success">
            All set — ${linked} sensors linked.
          </ha-alert>`
        : html`<ha-alert alert-type="warning">
            Not everything is set yet. Go back and complete the missing rows.
          </ha-alert>`}

      <div class="summary-group">Connection</div>
      <div class="summary-row"><span>Phases</span><span>${this._phases} phase${this._phases === 1 ? '' : 's'}</span></div>
      <div class="summary-row"><span>Capacity per phase</span><span>${this._capacityPerPhase} A</span></div>

      <div class="summary-group">Power</div>
      ${powerList.map(role => this._summaryRow(role))}

      <div class="summary-group">Meter readings</div>
      ${meterList.map(role => this._summaryRow(role, false))}

      <p class="muted" style="margin-top: 12px;">
        V2G Liberty keeps monitoring these sensors and warns you as soon as one
        stops reporting.
      </p>

      ${this._saveError
        ? html`<ha-alert alert-type="error" class="save-error">
            ${this._saveError}
          </ha-alert>`
        : nothing}
      ${this._triedSave && !complete
        ? html`<ha-alert alert-type="error">
            Some rows are still missing — go back and complete them before saving.
          </ha-alert>`
        : nothing}

      ${renderButton(
        this.hass,
        () => {
          this._step = Step.Meter;
        },
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}
      ${renderButton(
        this.hass,
        () => this._handleSave(),
        true,
        this._saving ? 'Saving…' : this.hass.localize('ui.common.save'),
        this._saving
      )}
    `;
  }

  private _summaryRow(role: RoleDefinition, showStatus = true) {
    const label = role.entityId ?? 'No sensor selected yet';
    return html`
      <div class="summary-row">
        <span>${role.badge} ${label}</span>
        ${showStatus
          ? renderStatusIcon(statusOf(this.hass, role))
          : nothing}
      </div>
    `;
  }

  // ── Choose-sensor side-step ─────────────────────────────────────────

  private _openChooseSensor(def: RoleDefinition) {
    const { subtitle, searchTerms } = this._pickerContext(def);
    const inUse = selectedEntityIds(
      powerRoles(
        this._phases ?? 1,
        this._consumptionEntities,
        this._productionEntities
      ),
      meterRoles(this._consumptionRegisters, this._productionRegisters)
    ).filter(id => id !== def.entityId);

    const params: ChooseSensorDialogParams = {
      definition: def,
      subtitle,
      inUse,
      searchTerms,
      onSelect: (entityId: string) => this._applyChoice(def, entityId),
    };
    fireEvent(this, 'show-dialog', {
      dialogTag: chooseSensorTag,
      dialogImport: () => Promise.resolve(),
      dialogParams: params,
    });
  }

  private _pickerContext(def: RoleDefinition): {
    subtitle: string;
    searchTerms: string[];
  } {
    const power = def.key.match(/^(consumption|production)_l(\d+)$/);
    if (power) {
      const dir = power[1];
      const phase = power[2];
      const label = dir === 'consumption' ? 'Consumption' : 'Production';
      // Prefill with terms that actually appear in entity names: the direction
      // and the phase as "l1"/"l2"/"l3" (matches "L1" and "..._l1"). Avoid the
      // word "phase" — many integrations do not use it. The ✕ clears it.
      return {
        subtitle: `${label} phase ${phase} · power`,
        searchTerms: [dir, `l${phase}`],
      };
    }
    const meter = def.key.match(/^(import|export)_t(\d+)$/);
    if (meter) {
      const dir = meter[1];
      const tariff = meter[2];
      // DSMR names import as "consumption", export as "production", with the
      // tariff as "tarif_1"/"tarif_2" — so search the direction + the number,
      // not the word "tariff".
      const term = dir === 'import' ? 'consumption' : 'production';
      return {
        subtitle: `Tariff ${tariff} · ${dir} · meter reading`,
        searchTerms: [term, tariff],
      };
    }
    return { subtitle: '', searchTerms: [] };
  }

  private _applyChoice(def: RoleDefinition, entityId: string) {
    const target = roleTarget(def.key);
    if (!target) return;
    const update = (arr: string[]): string[] => {
      const copy = [...arr];
      while (copy.length <= target.index) copy.push('');
      copy[target.index] = entityId;
      return copy;
    };
    switch (target.list) {
      case 'consumption_entities':
        this._consumptionEntities = update(this._consumptionEntities);
        break;
      case 'production_entities':
        this._productionEntities = update(this._productionEntities);
        break;
      case 'consumption_registers':
        this._consumptionRegisters = update(this._consumptionRegisters);
        break;
      case 'production_registers':
        this._productionRegisters = update(this._productionRegisters);
        break;
    }
  }

  // ── Detection variant, badges ───────────────────────────────────────

  private _renderNotRecognised(what: string) {
    return html`
      <ha-alert alert-type="warning" title="Sensors not recognised — or not yet enabled">
        No sensors reporting ${what} were found. Many integrations provide them
        but leave them disabled by default — check Settings → Devices &amp;
        services → your meter → entities and enable them.
        <div style="margin-top: 8px;">
          <button class="link" @click=${() => this._searchAgain()}>
            Search again
          </button>
          <a
            href="/config/integrations"
            target="_blank"
            rel="noopener"
            style="margin-left: 12px; color: var(--primary-color);"
          >
            Open integrations
            <ha-icon icon="mdi:open-in-new" style="--mdc-icon-size: 14px;"></ha-icon>
          </a>
        </div>
      </ha-alert>
    `;
  }

  private async _searchAgain() {
    try {
      const detected = await callFunction(this.hass, 'detect_grid_entities');
      if (detected.consumption_entities?.length > 0) {
        this._consumptionEntities = detected.consumption_entities;
      }
      if (detected.production_entities?.length > 0) {
        this._productionEntities = detected.production_entities;
      }
      if (detected.consumption_registers?.length > 0) {
        this._consumptionRegisters = this._padTo2(detected.consumption_registers);
      }
      if (detected.production_registers?.length > 0) {
        this._productionRegisters = this._padTo2(detected.production_registers);
      }
    } catch (e) {
      // Nothing found — the alert stays; the user can enable and retry.
    }
  }

  private _autoBadge() {
    return html`<span class="auto-detected-badge">
      <ha-icon icon="mdi:auto-fix" style="--mdc-icon-size: 14px;"></ha-icon>
      Auto-detected
    </span>`;
  }

  // ── Validation ──────────────────────────────────────────────────────

  private _powerIncomplete(): boolean {
    return powerRoles(
      this._phases ?? 1,
      this._consumptionEntities,
      this._productionEntities
    ).some(r => !r.entityId);
  }

  private _isTotalRegister(entityId: string): boolean {
    return /total/i.test(entityId);
  }

  private _directionIncomplete(registers: string[]): boolean {
    const filled = registers.filter(e => e !== '');
    if (filled.length === 0) return true;
    if (filled.some(e => this._isTotalRegister(e))) return false;
    return filled.length < 2;
  }

  private _metersIncomplete(): boolean {
    return (
      this._directionIncomplete(this._consumptionRegisters) ||
      this._directionIncomplete(this._productionRegisters)
    );
  }

  // ── Solar panel consistency warning ─────────────────────────────────

  private _panelsThatWillBecomeInconsistent(): string[] {
    if (this._phases === null) return [];
    const newPhases = this._phases;
    return this._existingSolarPanels
      .filter(p => {
        if (typeof p.phases !== 'number') return true;
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
      .map(p => p.name ?? '(unnamed)');
  }

  private _renderSolarPanelWarning() {
    if (!this._phaseChangeConfirmed) return nothing;
    const affected = this._panelsThatWillBecomeInconsistent();
    if (affected.length === 0) return nothing;
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
        <ul style="margin: 0 0 8px 16px; padding: 0;">
          ${affected.map(n => html`<li>${n}</li>`)}
        </ul>
        <p style="margin: 0;">
          Continue anyway is allowed — the affected panel(s) get flagged on the
          solar panels card. Nothing on the panels is changed automatically.
        </p>
      </ha-alert>
    `;
  }

  // ── Save ────────────────────────────────────────────────────────────

  private async _handleSave() {
    this._triedSave = true;
    if (this._powerIncomplete() || this._metersIncomplete()) return;
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
        this._saveError =
          `Could not create the grid sensors in FlexMeasures: ${result.fm_error}. ` +
          `Please check FlexMeasures and try again.`;
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
      this._saveError =
        'Could not reach the add-on. Please check that V2G Liberty is running ' +
        'and try again.';
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
      }
      .muted {
        color: var(--secondary-text-color);
      }
      .section-head {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 8px;
      }
      .sensors-intro {
        border-bottom: 1px solid var(--divider-color);
        padding-bottom: 8px;
        margin-bottom: 12px;
      }
      .sensors-intro .muted {
        font-size: 0.9em;
        line-height: 1.4;
        margin: 4px 0 0 0;
      }
      .group-head {
        display: flex;
        align-items: baseline;
        gap: 8px;
        font-size: 0.95em;
        margin-bottom: 2px;
      }
      .group-head .live {
        margin-left: auto;
        font-size: 0.7em;
        font-weight: 600;
        letter-spacing: 0.05em;
        color: var(--secondary-text-color);
      }
      .summary-group {
        text-transform: uppercase;
        font-size: 0.75em;
        font-weight: 600;
        letter-spacing: 0.05em;
        color: var(--secondary-text-color);
        margin: 16px 0 4px 0;
      }
      .summary-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        padding: 4px 0;
        border-top: 1px solid var(--divider-color);
        font-size: 0.9em;
      }
      .link {
        background: none;
        border: none;
        padding: 0;
        color: var(--primary-color);
        cursor: pointer;
        font-size: 1em;
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
    `,
  ];
}
