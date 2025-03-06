import { css, html, nothing } from 'lit';
import { customElement, query, state } from 'lit/decorators';

import { callFunction } from './util/appdaemon';
import { renderLoadbalancerInfo, isLoadbalancerEnabled } from './util/render';

import {
  InputText,
  renderDialogHeader,
  renderInputBoolean,
  renderInputNumber,
  renderInputText,
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

  @query(`[test-id='${entityIds.chargerHostUrl}']`) private _chargerHostField;
  @query(`[test-id='${entityIds.chargerPort}']`) private _chargerPortField;

  private _maxAvailablePower: string;

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._chargerHost = defaultState(
      this.hass.states[entityIds.chargerHostUrl],
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
    await this.updateComplete;
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const header = tp('header');
    const content =
      this._hasTriedToConnect && this._isConnected()
        ? this._renderChargerDetails()
        : this._renderConnectionDetails();
    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${renderDialogHeader(this.hass, header)}
      >
        ${content}
      </ha-dialog>
    `;
  }

  private _isConnected() {
    return this._chargerConnectionStatus === ConnectionStatus.Connected;
  }

  private _renderConnectionDetails() {
    const chargerHostState = this.hass.states[entityIds.chargerHostUrl];
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
        InputText.IpAddress,
        this._chargerHost,
        chargerHostState,
        evt => (this._chargerHost = evt.target.value)
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
        ? html`
            <ha-circular-progress
              test-id="progress"
              size="small"
              indeterminate
              slot="primaryAction"
            ></ha-circular-progress>
          `
        : html`
            <mwc-button
              test-id="continue"
              @click=${this._continue}
              slot="primaryAction"
            >
              ${this.hass.localize('ui.common.continue')}
            </mwc-button>
          `}
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
      <mwc-button test-id="save" @click=${this._save} slot="primaryAction">
        ${this.hass.localize('ui.common.save')}
      </mwc-button>
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
    this.closeDialog();
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
