import { html, css, LitElement } from 'lit';
import { customElement, state } from 'lit/decorators';
import { HomeAssistant, LovelaceCardConfig } from 'custom-card-helpers';
import { HassEvent } from 'home-assistant-js-websocket';

import { renderButton } from './util/render';
import { styles } from './card.styles';
import { callFunction } from './util/appdaemon';
import { showGridConnectionSettingsDialog } from './show-dialogs';
import {
  RoleDefinition,
  statusOf,
  aggregateStatus,
} from './grid-connection-status';
import { powerRoles, meterRoles } from './grid-connection-roles';
import { partial } from './util/translate';

const tp = partial('settings.grid-connection');

@customElement('v2g-liberty-grid-connection-settings-card')
export class GridConnectionSettingsCard extends LitElement {
  @state() private _isConfigured = false;
  @state() private _phases: number | null = null;
  @state() private _capacityPerPhase: number | null = null;
  @state() private _consumptionEntities: string[] = [];
  @state() private _productionEntities: string[] = [];
  @state() private _consumptionRegisters: string[] = [];
  @state() private _productionRegisters: string[] = [];
  @state() private _loading = true;

  private _hass!: HomeAssistant;
  private _unsubscribe: (() => void) | null = null;

  setConfig(_config: LovelaceCardConfig) {}

  set hass(hass: HomeAssistant) {
    const old = this._hass;
    this._hass = hass;
    if (!old) {
      this._loadSettings();
      this._subscribeToSaveEvents();
      return;
    }
    // Re-render only when a configured entity's state object changed (including
    // it appearing or disappearing), so the status dot / Problem state stays
    // current without re-rendering on every hass tick.
    const ids = [
      ...this._consumptionEntities,
      ...this._productionEntities,
      ...this._consumptionRegisters,
      ...this._productionRegisters,
    ].filter(Boolean);
    if (ids.some(id => old.states[id] !== hass.states[id])) {
      this.requestUpdate();
    }
  }

  private async _subscribeToSaveEvents() {
    this._unsubscribe = await this._hass.connection.subscribeEvents<HassEvent>(
      () => this._loadSettings(),
      'save_grid_connection_settings.result'
    );
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = null;
    }
  }

  private async _loadSettings() {
    this._loading = true;
    try {
      const data = await callFunction(this._hass, 'get_grid_connection_settings');
      this._phases = data.phases ?? null;
      this._capacityPerPhase = data.capacity_per_phase ?? null;
      this._consumptionEntities = data.consumption_entities ?? [];
      this._productionEntities = data.production_entities ?? [];
      this._consumptionRegisters = data.consumption_registers ?? [];
      this._productionRegisters = data.production_registers ?? [];
      this._isConfigured = this._consumptionEntities.length > 0;
    } catch (e) {
      console.error('Failed to load grid connection settings', e);
      this._isConfigured = false;
    }
    this._loading = false;
  }

  private _roles(): RoleDefinition[] {
    return [
      ...powerRoles(
        this._phases ?? 1,
        this._consumptionEntities,
        this._productionEntities
      ),
      ...meterRoles(this._consumptionRegisters, this._productionRegisters),
    ];
  }

  private _state(): 'ok' | 'problem' | 'incomplete' | 'not_set' {
    if (!this._isConfigured) return 'not_set';
    return aggregateStatus(this._roles().map(r => statusOf(this._hass, r)));
  }

  private _roleLabel(role: RoleDefinition): string {
    const p = role.key.match(/^(consumption|production)_l(\d+)$/);
    if (p) {
      return tp(p[1] === 'consumption' ? 'role.consumption' : 'role.production', {
        n: p[2],
      });
    }
    const m = role.key.match(/^(import|export)_t(\d+)$/);
    if (m) {
      return tp(m[1] === 'import' ? 'role.import' : 'role.export', { n: m[2] });
    }
    return role.key;
  }

  render() {
    if (this._loading) {
      return html`<ha-card header=${tp('title')}>
        <div class="card-content"><ha-spinner></ha-spinner></div>
      </ha-card>`;
    }

    const state = this._state();
    // The status dot reads the state without reading the alert: green = active,
    // red = a problem, amber = not fully set up, outlined = nothing set.
    const dotClass =
      state === 'problem'
        ? 'problem'
        : state === 'incomplete'
          ? 'incomplete'
          : state === 'not_set'
            ? 'not-set'
            : 'ok';

    return html`
      <ha-card>
        <div class="gc-header">
          <span class="dot ${dotClass}"></span>
          <span>${tp('title')}</span>
        </div>
        ${state === 'not_set'
          ? this._renderNotSetUp()
          : state === 'problem'
            ? this._renderProblem()
            : state === 'incomplete'
              ? this._renderIncomplete()
              : this._renderActive()}
      </ha-card>
    `;
  }

  private _renderActive() {
    const roles = this._roles();
    const reporting = roles.filter(
      r => statusOf(this._hass, r) === 'ok'
    ).length;
    const phaseLabel = tp(this._phases === 1 ? 'card.phase-1' : 'card.phase-3');
    return html`
      <div class="card-content">
        <ha-alert alert-type="success">
          ${tp('card.active-alert', { reporting, total: roles.length })}
        </ha-alert>
        <p>
          ${tp('card.active-summary', {
            phase: phaseLabel,
            capacity: this._capacityPerPhase ?? '',
          })}
        </p>
      </div>
      <div class="card-actions">
        ${renderButton(
          this._hass,
          () => this._openDialog(),
          true,
          this._hass.localize('ui.common.edit')
        )}
      </div>
    `;
  }

  private _renderProblem() {
    const failing = this._roles().find(r => {
      const s = statusOf(this._hass, r);
      return s === 'stale' || s === 'wrong_type';
    });
    return html`
      <div class="card-content">
        <ha-alert alert-type="error" title=${tp('card.problem-title')}>
          ${failing
            ? tp('card.problem-alert', { role: this._roleLabel(failing) })
            : tp('card.problem-alert-generic')}
        </ha-alert>
      </div>
      <div class="card-actions">
        ${renderButton(this._hass, () => this._openDialog(), true, tp('card.fix'))}
      </div>
    `;
  }

  private _renderIncomplete() {
    const roles = this._roles();
    const missing = roles.filter(r => statusOf(this._hass, r) === 'not_set');
    const set = roles.length - missing.length;
    const names = missing.map(m => this._roleLabel(m)).join(', ');
    return html`
      <div class="card-content">
        <ha-alert alert-type="warning" title=${tp('card.incomplete-title')}>
          ${tp('card.incomplete-alert', {
            set,
            total: roles.length,
            names,
          })}
        </ha-alert>
      </div>
      <div class="card-actions">
        ${renderButton(this._hass, () => this._openDialog(), true, tp('card.fix'))}
      </div>
    `;
  }

  private _renderNotSetUp() {
    return html`
      <div class="card-content">
        <p>${tp('card.not-set-up')}</p>
      </div>
      <div class="card-actions">
        ${renderButton(this._hass, () => this._openDialog(), true, tp('card.set-up'))}
      </div>
    `;
  }

  private _openDialog() {
    showGridConnectionSettingsDialog(this);
  }

  static styles = [
    styles,
    css`
      .gc-header {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 12px 16px 0 16px;
        font-size: 1.2em;
        font-weight: 500;
      }
      .dot {
        width: 12px;
        height: 12px;
        border-radius: 50%;
        flex: 0 0 auto;
      }
      .dot.ok {
        background: var(--success-color, #4caf50);
      }
      .dot.problem {
        background: var(--error-color, #f44336);
      }
      .dot.incomplete {
        background: var(--warning-color, #ff9800);
      }
      .dot.not-set {
        background: transparent;
        border: 2px solid var(--disabled-text-color, var(--secondary-text-color));
      }
    `,
  ];
}
