// Grid connection configuration flow (redesign).
//
// Five steps: Intro → Connection → Power → Meter readings → Done. Storage is
// unchanged (the four settings lists); roles are derived for rendering only.
// Rows and status come from grid-connection-status / -sensor-row / -roles; the
// per-row "Change / Choose sensor" opens the choose-sensor side-step.
//
// Buttons are never disabled to block an action (except the shared FM
// reachability gate on the intro): pressing on with something missing shows an
// ha-alert saying what. Copy lives in strings.json / nl.json under
// settings.grid-connection.

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
import { unsafeHTML } from 'lit/directives/unsafe-html';
import { partial } from './util/translate';

const tp = partial('settings.grid-connection');

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
        return tp('header.connection');
      case Step.Power:
        return tp('header.power');
      case Step.Meter:
        return tp('header.meter');
      case Step.Done:
        return tp('header.done');
      default:
        return tp('title');
    }
  }

  // ── Step 1: Intro ───────────────────────────────────────────────────

  private _renderIntro() {
    return html`
      ${this._renderFmGate(tp('fm-gate-subject'))}

      <p>${unsafeHTML(tp('intro.p1'))}</p>
      <p>${unsafeHTML(tp('intro.p2'))}</p>

      <div class="requirements-box">
        <div class="requirements-header">${tp('intro.req-header')}</div>
        <div class="requirement-item">
          <ha-icon icon="mdi:meter-electric" class="requirement-icon"></ha-icon>
          <div>
            <strong>${tp('intro.req-smart-meter-title')}</strong><br />
            ${tp('intro.req-smart-meter-desc')}
          </div>
        </div>
        <div class="requirement-item">
          <ha-icon icon="mdi:cable-data" class="requirement-icon"></ha-icon>
          <div>
            <strong>${tp('intro.req-cable-title')}</strong><br />
            ${tp('intro.req-cable-desc')}
          </div>
        </div>
        <div class="requirement-item">
          <ha-icon icon="mdi:home-assistant" class="requirement-icon"></ha-icon>
          <div>
            <strong>${tp('intro.req-integration-title')}</strong><br />
            ${tp('intro.req-integration-desc')}
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
            <strong>${tp('connection.phases-question')}</strong>
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
              <strong>${tp('connection.phase-1-title')}</strong><br />
              <span class="phase-subtitle">${tp('connection.phase-1-sub')}</span>
            </div>
          </div>
          <div
            class="phase-card ${this._phases === 3 ? 'selected' : ''}"
            @click=${() => this._selectPhases(3)}
          >
            ${renderRadioIndicator(this._phases === 3)}
            <div>
              <strong>${tp('connection.phase-3-title')}</strong><br />
              <span class="phase-subtitle">${tp('connection.phase-3-sub')}</span>
            </div>
          </div>
        </div>
        ${this._triedContinueConnection && this._phases === null
          ? html`<div class="error">${tp('connection.phases-error')}</div>`
          : nothing}
        <details class="hint">
          <summary>${tp('connection.phases-hint-summary')}</summary>
          <p>${tp('connection.phases-hint-body')}</p>
        </details>
      </div>

      <div style="margin-top: 16px;">
        <div class="section-head">
          <p style="margin: 0;"><strong>${tp('connection.capacity-label')}</strong></p>
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
          <summary>${tp('connection.capacity-hint-summary')}</summary>
          <p>${tp('connection.capacity-hint-body')}</p>
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
          ? tp('connection.continue-anyway')
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
      return html`<div class="error">
        ${tp('connection.capacity-error-empty')}
      </div>`;
    }
    if (this._capacityPerPhase !== '' && !this._isCapacityValid()) {
      return html`<div class="error">
        ${tp('connection.capacity-error-range')}
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
        <p style="margin: 0;"><strong>${tp('sensors-heading')}</strong></p>
        <p class="muted">${tp('power.intro')}</p>
      </div>

      ${noCandidates
        ? this._renderNotRecognised(tp('power.not-recognised-what'))
        : nothing}

      <div class="group-head">
        <span class="gh-title"><strong>${tp('power.consumption-title')}</strong> <span class="muted">${tp('power.consumption-sub')}</span></span>
        <span class="gh-live">LIVE</span>
      </div>
      ${consumption.map(role =>
        renderSensorRow(this.hass, role, {
          onChoose: def => this._openChooseSensor(def),
        })
      )}

      <div class="group-head" style="margin-top: 16px;">
        <span class="gh-title"><strong>${tp('power.production-title')}</strong> <span class="muted">${tp('power.production-sub')}</span></span>
        <span class="gh-live">LIVE</span>
      </div>
      ${production.map(role =>
        renderSensorRow(this.hass, role, {
          onChoose: def => this._openChooseSensor(def),
        })
      )}

      ${this._triedContinuePower && this._powerIncomplete()
        ? html`<ha-alert alert-type="error" style="margin-top: 12px;">
            ${tp('power.incomplete-alert')}
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
        <p style="margin: 0;"><strong>${tp('sensors-heading')}</strong></p>
        <p class="muted">${tp('meter.intro')}</p>
      </div>

      ${noCandidates
        ? this._renderNotRecognised(tp('meter.not-recognised-what'))
        : nothing}

      <div class="group-head"><strong>${tp('meter.tariff-1')}</strong></div>
      ${rows(tariff1)}
      <div class="group-head" style="margin-top: 16px;"><strong>${tp('meter.tariff-2')}</strong></div>
      ${rows(tariff2)}

      ${this._triedContinueMeter && this._metersIncomplete()
        ? html`<ha-alert alert-type="error" style="margin-top: 12px;">
            ${tp('meter.incomplete-alert')}
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
            ${tp('done.all-set', { count: linked })}
          </ha-alert>`
        : html`<ha-alert alert-type="warning">
            ${tp('done.incomplete-warning')}
          </ha-alert>`}

      <div class="summary-group">${tp('header.connection')}</div>
      <div class="summary-row"><span>${tp('done.phases-label')}</span><span>${tp('done.phases-value', { smart_count: this._phases ?? 0 })}</span></div>
      <div class="summary-row"><span>${tp('done.capacity-label')}</span><span>${this._capacityPerPhase} A</span></div>

      <div class="summary-group">${tp('header.power')}</div>
      ${powerList.map(role => this._summaryRow(role))}

      <div class="summary-group">${tp('header.meter')}</div>
      ${meterList.map(role => this._summaryRow(role, false))}

      <p class="muted" style="margin-top: 12px;">
        ${tp('done.keeps-monitoring')}
      </p>

      ${this._saveError
        ? html`<ha-alert alert-type="error" class="save-error">
            ${this._saveError}
          </ha-alert>`
        : nothing}
      ${this._triedSave && !complete
        ? html`<ha-alert alert-type="error">
            ${tp('done.save-incomplete')}
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
        this._saving ? tp('done.saving') : this.hass.localize('ui.common.save'),
        this._saving
      )}
    `;
  }

  private _summaryRow(role: RoleDefinition, showStatus = true) {
    const label = role.entityId ?? tp('done.no-sensor');
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
      const label =
        dir === 'consumption'
          ? tp('picker.consumption')
          : tp('picker.production');
      // Prefill with terms that actually appear in entity names: the direction
      // and the phase as "l1"/"l2"/"l3" (matches "L1" and "..._l1"). Avoid the
      // word "phase" — many integrations do not use it. The ✕ clears it.
      return {
        subtitle: tp('picker.power-subtitle', { direction: label, phase }),
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
      const direction =
        dir === 'import' ? tp('picker.import') : tp('picker.export');
      return {
        subtitle: tp('picker.meter-subtitle', { tariff, direction }),
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
      <ha-alert alert-type="warning" title=${tp('detect.title')}>
        ${tp('detect.body', { what })}
        <div style="margin-top: 8px;">
          <button class="link" @click=${() => this._searchAgain()}>
            ${tp('detect.search-again')}
          </button>
          <a
            href="/config/integrations"
            target="_blank"
            rel="noopener"
            style="margin-left: 12px; color: var(--primary-color);"
          >
            ${tp('detect.open-integrations')}
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
      ${tp('badge.auto-detected')}
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
      .map(p => p.name ?? tp('solar-warning.unnamed'));
  }

  private _renderSolarPanelWarning() {
    if (!this._phaseChangeConfirmed) return nothing;
    const affected = this._panelsThatWillBecomeInconsistent();
    if (affected.length === 0) return nothing;
    return html`
      <ha-alert
        alert-type="warning"
        title=${affected.length === 1
          ? tp('solar-warning.title-one')
          : tp('solar-warning.title-many')}
        style="margin-top: 16px;"
      >
        <p style="margin: 0 0 8px 0;">${tp('solar-warning.body-intro')}</p>
        <ul style="margin: 0 0 8px 16px; padding: 0;">
          ${affected.map(n => html`<li>${n}</li>`)}
        </ul>
        <p style="margin: 0;">${tp('solar-warning.body-outro')}</p>
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
        this._saveError = tp('save-error.fm', { error: result.fm_error });
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
      this._saveError = tp('save-error.unreachable');
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
        /* Match the rows' horizontal padding so columns line up. */
        padding: 0 4px;
      }
      .gh-title {
        flex: 1;
        min-width: 0;
      }
      .gh-live {
        flex: 0 0 auto;
        width: 28px;
        text-align: center;
        white-space: nowrap;
        /* Sit centred above the 28px status column: the row's action column
           is 108px wide with a 12px gap before the status icon. */
        margin-right: 120px;
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
