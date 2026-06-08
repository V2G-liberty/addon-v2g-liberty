import { css, html, nothing } from 'lit';
import { customElement, query, state } from 'lit/decorators';
import { HassEvent } from 'home-assistant-js-websocket';

import { callFunction } from './util/appdaemon';
import {
  renderLoadbalancerInfo,
  isLoadbalancerEnabled,
  renderSpinner,
  renderButton,
  InputText,
  renderDialogHeader,
  renderInputBoolean,
  renderInputNumber,
  renderInputText,
  isNewHaDialogAPI,
} from './util/render';
import { partial } from './util/translate';
import { styles } from './card.styles';
import { defaultState, DialogBase } from './dialog-base';
import * as entityIds from './entity-ids';

export const tagName = 'edit-charger-settings-dialog';
const tp = partial('settings.charger');

const enum ConnectionStatus {
  Connected = 'Successfully connected',
  Connecting = 'Trying to connect...',
  Failed = 'Failed to connect',
  TimedOut = 'Timed out',
}

@customElement(tagName)
class EditChargerSettingsDialog extends DialogBase {
  @state() private _chargerHost: string;
  @state() private _chargerPort: string;
  @state() private _useReducedMaxPower: string;
  @state() private _chargerMaxChargingPower: string;
  @state() private _chargerMaxDischargingPower: string;
  @state() private _chargerConnectionStatus: string;
  @state() private _hasTriedToConnect: boolean;
  @state() private _quasarLoadBalancerLimit: string;

  // Phase step state
  @state() private _showPhaseStep: boolean = false;
  @state() private _gridPhases: number | null = null;
  @state() private _chargerPhases: number = 1; // TODO: derive from charger type in branch 359
  @state() private _selectedPhase: number | number[] | null = null;
  @state() private _triedSavePhase: boolean = false;
  @state() private _savingPhase: boolean = false;
  @state() private _detecting: boolean = false;
  @state() private _detectStep: string = '';
  @state() private _detectError: string = '';
  @state() private _detectSuccess: string = '';

  @query(`[test-id='${entityIds.chargerHostname}']`) private _chargerHostField;
  @query(`[test-id='${entityIds.chargerPort}']`) private _chargerPortField;

  private _maxAvailablePower: string;

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._chargerHost = defaultState(
      this.hass.states[entityIds.chargerHostname],
      ''
    );
    this._chargerPort = defaultState(
      this.hass.states[entityIds.chargerPort],
      '502'
    );
    this._chargerConnectionStatus = '';
    this._useReducedMaxPower =
      this.hass.states[entityIds.useReducedMaxChargePower].state;
    this._quasarLoadBalancerLimit = this.hass.states[entityIds.quasarLoadBalancerLimit].state;
    this._hasTriedToConnect = false;
    this._showPhaseStep = false;
    this._triedSavePhase = false;
    this._savingPhase = false;
    this._detecting = false;
    this._detectStep = '';
    this._detectError = '';
    this._detectSuccess = '';

    // Load grid and charger phase info
    try {
      const gridData = await callFunction(this.hass, 'get_grid_connection_settings');
      this._gridPhases = gridData.configured ? (gridData.phases ?? null) : null;
    } catch (e) {
      this._gridPhases = null;
    }
    try {
      const phaseData = await callFunction(this.hass, 'get_charger_phase');
      this._selectedPhase = phaseData.connected_to_phase ?? null;
    } catch (e) {
      this._selectedPhase = null;
    }

    await this.updateComplete;
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const header = tp('header');
    const _isNew = isNewHaDialogAPI(this.hass);
    const content = this._showPhaseStep
      ? this._renderPhaseStep()
      : this._hasTriedToConnect && this._isConnected()
        ? this._renderChargerDetails()
        : this._renderConnectionDetails();
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

  private _isConnected() {
    return this._chargerConnectionStatus === ConnectionStatus.Connected;
  }

  private _renderConnectionDetails() {
    const chargerHostState = this.hass.states[entityIds.chargerHostname];
    const chargerPortState = this.hass.states[entityIds.chargerPort];
    const portDescription = tp('connection-details.port-description');
    const _isLoadBalancerEnabled = isLoadbalancerEnabled(this._quasarLoadBalancerLimit)

    return html`
      ${this._renderConnectionError()}
      ${_isLoadBalancerEnabled
        ? nothing
        : html`
          <ha-markdown breaks .content="${tp('connection-details.description')}"></ha-markdown><br/>
        `
      }
      ${renderInputText(
        InputText.Hostname,
        this._chargerHost,
        chargerHostState,
        evt => (this._chargerHost = evt.target.value),
        tp('invalid-host-error'),
        "text",
        this.hass
      )}
      ${this._renderInvalidHostError()}
      ${renderInputNumber(
        this._chargerPort,
        chargerPortState,
        evt => (this._chargerPort = evt.target.value),
        '[0-9]+'
      )}
      ${this._renderInvalidPortError()}
      ${_isLoadBalancerEnabled
        ? nothing
        : html`
          <ha-markdown breaks .content=${portDescription}></ha-markdown><br/>
        `
      }
      ${renderLoadbalancerInfo(_isLoadBalancerEnabled)}
      ${this._isBusyConnecting()
        ? renderSpinner(this.hass)
        : renderButton(
          this.hass,
          this._continue,
          true,
          this.hass.localize('ui.common.continue')
        )}
    `;
  }

  private _renderConnectionError() {
    const hasConnectionError =
      this._chargerConnectionStatus == ConnectionStatus.Failed ||
      this._chargerConnectionStatus == ConnectionStatus.TimedOut;
    return hasConnectionError
      ? html`
          <p>
            <ha-alert test-id="connection-error" alert-type="error">
              ${tp('connection-error')}
            </ha-alert>
          </p>
        `
      : nothing;
  }

  private _renderInvalidHostError() {
    return !this._hasTriedToConnect || this._isChargerHostValid()
      ? nothing
      : html`<div test-id="invalid-host" class="invalid">
          ${tp('invalid-host-error')}
        </div>`;
  }

  private _isChargerHostValid() {
    return (
      !this._chargerHostField ||
      (this._chargerHost && this._chargerHostField.checkValidity())
    );
  }

  private _renderInvalidPortError() {
    return !this._hasTriedToConnect || this._isChargerPortValid()
      ? nothing
      : html`<div test-id="invalid-port" class="invalid">
          ${tp('invalid-port-error')}
        </div>`;
  }

  private _isChargerPortValid() {
    return (
      !this._chargerPortField ||
      (this._chargerPort && this._chargerPortField.checkValidity())
    );
  }

  private _isBusyConnecting() {
    return this._chargerConnectionStatus === ConnectionStatus.Connecting;
  }

  private _renderChargerDetails() {
    const description = tp('charger-details.description', {
      value: this._maxAvailablePower,
    });
    const useReducedMaxPowerState =
      this.hass.states[entityIds.useReducedMaxChargePower];
    const isUsingReducedMaxPower = this._useReducedMaxPower === 'on';
    const _isLoadBalancerEnabled = isLoadbalancerEnabled(this._quasarLoadBalancerLimit)

    return html`
      <ha-alert alert-type="success">${tp('connection-success')}</ha-alert>
      <ha-markdown breaks .content=${description}></ha-markdown>
      ${renderInputBoolean(
        isUsingReducedMaxPower,
        useReducedMaxPowerState,
        evt => (this._useReducedMaxPower = evt.target.checked ? 'on' : 'off')
      )}
      ${isUsingReducedMaxPower ? this._renderReducedMaxPower() : nothing}
      ${renderLoadbalancerInfo(_isLoadBalancerEnabled)}
      ${renderButton(
        this.hass,
        this._save,
        true,
        this.hass.localize('ui.common.continue'),
        false,
        'continue'
      )}
    `;
  }

  private _renderReducedMaxPower() {
    const reduceMaxPowerDescription = tp(
      'charger-details.reduce-max-power-description'
    );
    const chargerMaxChargingPowerState =
      this.hass.states[entityIds.chargerMaxChargingPower];
    const chargerMaxDischargingPowerState =
      this.hass.states[entityIds.chargerMaxDischargingPower];

    chargerMaxChargingPowerState.attributes.max = this._maxAvailablePower;
    chargerMaxDischargingPowerState.attributes.max = this._maxAvailablePower;

    return html`
      <ha-markdown breaks .content=${reduceMaxPowerDescription}></ha-markdown>
      ${renderInputNumber(
        this._chargerMaxChargingPower,
        chargerMaxChargingPowerState,
        evt => (this._chargerMaxChargingPower = evt.target.value),
        '[0-9]+'
      )}
      ${renderInputNumber(
        this._chargerMaxDischargingPower,
        chargerMaxDischargingPowerState,
        evt => (this._chargerMaxDischargingPower = evt.target.value),
        '[0-9]+'
      )}
    `;
  }

  private async _continue(): Promise<void> {
    this._hasTriedToConnect = true;
    if (!this._isChargerHostValid()) {
      this._chargerHostField.focus();
      return;
    }
    if (!this._isChargerPortValid()) {
      this._chargerPortField.focus();
      return;
    }
    this._chargerPort = `${parseInt(this._chargerPort, 10)}`;
    try {
      this._chargerConnectionStatus = ConnectionStatus.Connecting;
      const result = await callFunction(
        this.hass,
        'test_charger_connection',
        {
          host: this._chargerHost,
          port: this._chargerPort,
        },
        5 * 1000
      );
      this._chargerConnectionStatus = result.msg;
      if (this._isConnected()) {
        this._maxAvailablePower = result.max_available_power;
        this._chargerMaxChargingPower = defaultMaxPower(
          this.hass.states[entityIds.chargerMaxChargingPower],
          this._maxAvailablePower
        );
        this._chargerMaxDischargingPower = defaultMaxPower(
          this.hass.states[entityIds.chargerMaxDischargingPower],
          this._maxAvailablePower
        );
      }

      function defaultMaxPower(stateObj, defaultValue) {
        return parseInt(stateObj.state, 10) === 1380 ||
          stateObj.state === 'unknown'
          ? defaultValue
          : stateObj.state;
      }
    } catch (err) {
      this._chargerConnectionStatus = ConnectionStatus.TimedOut;
    }
  }

  private async _save(): Promise<void> {
    // TODO: Add validation
    const isUsingReducedMaxPower = this._useReducedMaxPower === 'on';
    const args = {
      host: this._chargerHost,
      port: this._chargerPort,
      useReducedMaxChargePower: isUsingReducedMaxPower,
      ...(isUsingReducedMaxPower
        ? {
            maxChargingPower: this._chargerMaxChargingPower,
            maxDischargingPower: this._chargerMaxDischargingPower,
          }
        : {}),
    };
    const result = await callFunction(this.hass, 'save_charger_settings', args);
    // Only show phase step if grid connection is configured
    if (this._gridPhases !== null) {
      this._showPhaseStep = true;
    } else {
      this.closeDialog();
    }
  }

  // ── Phase Step ──────────────────────────────────────────────────────

  private _renderPhaseStep() {
    const gridPhases = this._gridPhases;
    const chargerPhases = this._chargerPhases;

    // Scenario: 1-phase grid + 3-phase charger → error
    if (gridPhases === 1 && chargerPhases === 3) {
      return html`
        <ha-alert alert-type="error">
          Your 3-phase charger requires a 3-phase grid connection.
          Please check your grid connection settings or charger type.
        </ha-alert>
        ${renderButton(this.hass, () => this.closeDialog(), true, this.hass.localize('ui.common.close'))}
      `;
    }

    // Scenario: 1-phase grid + 1-phase charger → informational
    if (gridPhases === 1 || gridPhases === null) {
      return html`
        <p>Your charger is connected to the only available phase (L1).</p>
        ${this._renderPhaseBackButton()}
        ${renderButton(this.hass, () => this._savePhase(1), true, this.hass.localize('ui.common.save'))}
      `;
    }

    // Scenario: 3-phase grid + 3-phase charger → informational
    if (chargerPhases === 3) {
      return html`
        <p>Your 3-phase charger is connected to all three phases.</p>
        ${this._renderPhaseBackButton()}
        ${renderButton(this.hass, () => this._savePhase([1, 2, 3]), true, this.hass.localize('ui.common.save'))}
      `;
    }

    // Scenario: 3-phase grid + 1-phase charger → manual selection
    return this._renderPhaseSelection();
  }

  private _renderPhaseSelection() {
    return html`
      <p><strong>Which phase is your charger connected to?</strong></p>

      <div class="phase-options">
        ${[1, 2, 3].map(phase => html`
          <div
            class="phase-option ${this._selectedPhase === phase ? 'selected' : ''}"
            @click=${() => { this._selectedPhase = phase; }}
          >
            <ha-radio
              .checked=${this._selectedPhase === phase}
              name="charger-phase"
              value="${phase}"
              @change=${() => { this._selectedPhase = phase; }}
            ></ha-radio>
            <span><strong>Phase ${phase}</strong> (L${phase})</span>
          </div>
        `)}
      </div>

      ${this._triedSavePhase && this._selectedPhase === null
        ? html`<div class="invalid">Please select which phase your charger is connected to.</div>`
        : nothing
      }

      <details class="hint">
        <summary>Not sure?</summary>
        <p>Check the label on your fuse box, or use the automatic detection below.</p>
      </details>

      ${this._renderAutoDetect()}

      ${this._renderPhaseBackButton()}
      ${this._savingPhase
        ? renderSpinner(this.hass)
        : renderButton(
            this.hass,
            () => this._handleSavePhase(),
            true,
            this.hass.localize('ui.common.save')
          )
      }
    `;
  }

  private _renderAutoDetect() {
    if (this._detecting) {
      return html`
        <div class="auto-detect-box">
          <p><strong>Automatic phase detection</strong></p>
          <div style="display: flex; align-items: center; gap: 8px;">
            <ha-spinner size="small"></ha-spinner>
            <span>${this._detectStep || 'Starting...'}</span>
          </div>
        </div>
      `;
    }

    if (this._detectSuccess) {
      return html`
        <div class="auto-detect-box">
          <p><strong>Automatic phase detection</strong></p>
          <ha-alert alert-type="success">${this._detectSuccess}</ha-alert>
        </div>
      `;
    }

    if (this._detectError) {
      return html`
        <div class="auto-detect-box">
          <p><strong>Automatic phase detection</strong></p>
          <div style="margin-bottom: 12px;">
            <ha-alert alert-type="warning">${this._detectError}</ha-alert>
          </div>
          ${renderButton(
            this.hass,
            () => this._startDetection(),
            false,
            'Retry',
          )}
        </div>
      `;
    }

    return html`
      <div class="auto-detect-box">
        <p><strong>Automatic phase detection</strong></p>
        <p style="font-size: 0.875em; color: var(--secondary-text-color);">
          Optionally, the phase can be detected automatically. This briefly
          charges (and discharges for bidirectional chargers) while monitoring
          the grid sensors. The charge mode will be temporarily set to Stop
          during detection. Make sure your car is connected.
        </p>
        ${renderButton(
          this.hass,
          () => this._startDetection(),
          false,
          'Start detection',
        )}
      </div>
    `;
  }

  private async _startDetection() {
    this._detecting = true;
    this._detectStep = '';
    this._detectError = '';
    this._detectSuccess = '';

    // Subscribe to progress events
    const unsub = await this.hass.connection.subscribeEvents<HassEvent>(
      (event: HassEvent) => {
        const step = event.data.step;
        if (step === 'baseline') this._detectStep = 'Measuring baseline...';
        else if (step === 'charge_test') this._detectStep = 'Charge test...';
        else if (step === 'discharge_test') this._detectStep = 'Discharge test...';
      },
      'charger_phase_detection.progress'
    );

    try {
      const result = await callFunction(
        this.hass,
        'detect_charger_phase',
        {},
        180 * 1000 // 3 min timeout
      );

      if (result.success) {
        this._selectedPhase = result.connected_to_phase;
        this._detectError = '';
        const phase = result.connected_to_phase;
        const label = Array.isArray(phase)
          ? phase.map(p => `L${p}`).join(', ')
          : `L${phase}`;
        this._detectSuccess = `Detected: Phase ${label}`;
      } else {
        this._detectSuccess = '';
        this._detectError = result.error || 'Detection failed. You can select the phase manually.';
      }
    } catch (e) {
      this._detectError = 'Detection timed out. You can select the phase manually.';
    } finally {
      unsub();
      this._detecting = false;
    }
  }

  private _renderPhaseBackButton() {
    return renderButton(
      this.hass,
      () => { this._showPhaseStep = false; this._triedSavePhase = false; },
      false,
      this.hass.localize('ui.common.back'),
      false,
      'back',
      true
    );
  }

  private _handleSavePhase() {
    this._triedSavePhase = true;
    if (this._selectedPhase === null) return;
    this._savePhase(this._selectedPhase);
  }

  private async _savePhase(phase: number | number[]) {
    this._savingPhase = true;
    try {
      await callFunction(this.hass, 'save_charger_phase', {
        connected_to_phase: phase,
      });
      this.closeDialog();
    } catch (e) {
      this._savingPhase = false;
    }
  }



  static styles = [
    styles,
    css`
      .invalid {
        color: var(--error-color);
      }

      .name {
        font-weight: bold;
      }
      .phase-options {
        display: flex;
        gap: 12px;
        margin: 12px 0;
      }
      .phase-option {
        flex: 1;
        display: flex;
        align-items: center;
        gap: 2px;
        padding: 12px 12px 12px 4px;
        border: 1px solid var(--divider-color);
        border-radius: 12px;
        cursor: pointer;
        transition: border-color 0.2s, background 0.2s;
      }
      .phase-option:hover {
        border-color: var(--primary-color);
      }
      .phase-option.selected {
        border-color: var(--primary-color);
        border-width: 2px;
        background: color-mix(in srgb, var(--primary-color) 5%, transparent);
      }
      .hint {
        margin-top: 8px;
        font-size: 0.875em;
        color: var(--secondary-text-color);
      }
      .hint summary {
        cursor: pointer;
        color: var(--primary-color);
      }
      .hint p {
        margin: 4px 0 0 0;
        line-height: 1.4;
      }
      .auto-detect-box {
        margin-top: 16px;
        padding: 12px;
        border: 1px solid var(--divider-color);
        border-radius: 12px;
        background: var(--card-background-color);
      }
      .auto-detect-box p:first-child {
        margin-top: 0;
      }

      // All of these don't seem to reach the hr element...
      // hr,
      // ha-markdown::part(hr),
      // ha-markdown::part(ha-markdown-element) hr,
      // ha-markdown::part(content) hr,
      // ha-dialog ha-markdown hr,
      // ha-markdown ha-markdown-element::part(content) hr,
      // ha-markdown ha-markdown-element hr {
      //   border-width: 2px 0 0 0;
      //   border-style: dotted;
      //   border-color: var(--divider-color);
      // }

    `
  ];

}
