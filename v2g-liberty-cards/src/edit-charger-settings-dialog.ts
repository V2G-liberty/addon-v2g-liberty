import { css, html, nothing } from 'lit';
import { customElement, query, state } from 'lit/decorators';

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
  renderSelectOptionWithLabel,
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
const enum ChargerType {
  EVtecBiDiPro10 = 'evtec-bidi-pro-10',
  WallboxQuasar1 = 'wallbox-quasar-1',
}
type DialogPage =
  | '1-select-charger-type'
  | '2-connection-details'
  | '3-power-details';


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
  @state() private _selectedChargerType: ChargerType | null = null;
  @state() private _currentPage: DialogPage = '1-select-charger-type';
  @state() private _isChargerTypeValid: boolean | null = null;

  @query(`[test-id='${entityIds.chargerHostUrl}']`) private _chargerHostField;
  @query(`[test-id='${entityIds.chargerPort}']`) private _chargerPortField;

  private _maxAvailablePower: string;

  private _chargerOptions = [
    {
      value: ChargerType.EVtecBiDiPro10,
      label: tp('evtec-bidi-pro-10'),
      default_port: '5020',
    },
    {
      value: ChargerType.WallboxQuasar1,
      label: tp('wallbox-quasar-1'),
      default_port: '502',
    },
  ];

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._selectedChargerType = this.hass.states[entityIds.chargerType]?.state as ChargerType || null;

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

    const header = this._getDialogHeader();

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${renderDialogHeader(this.hass, header)}
      >
        ${this._currentPage === '1-select-charger-type'
          ? this._renderChargerSelection()
          : this._currentPage === '2-connection-details'
            ? this._renderConnectionDetails()
            : this._renderPowerDetails()}
      </ha-dialog>
    `;
  }

  private _getDialogHeader(): string {
    switch (this._currentPage) {
      case '1-select-charger-type':
        return tp('1-select-charger-type.header');
      case '2-connection-details':
        return `${tp(this._selectedChargerType)}: ${tp('2-connection-details.header')}`;
      case '3-power-details':
        return `${tp(this._selectedChargerType)}: ${tp('3-power-details.header')}`;
      default:
        return tp('header');
    }
  }


  //////////////////////////////////////////////////
  //          Step 1 Charger Selection            //
  //////////////////////////////////////////////////

  private _renderChargerSelection() {
    const showError = this._isChargerTypeValid === false
    return html`
      <ha-markdown breaks .content=${tp('1-select-charger-type.description')}></ha-markdown><br/>
      ${showError ? html`
        <ha-alert alert-type="error">
          ${tp('1-select-charger-type.validation-error')}
        </ha-alert>
      ` : nothing}
      <div class="charger-selection">
        ${this._chargerOptions.map(option =>
          renderSelectOptionWithLabel(
            option.value,
            option.label,
            this._selectedChargerType === option.value,
            () => { this._selectedChargerType = option.value; },
            'selectChargerType'
          )
        )}
      </div>
      ${renderButton(
        this.hass,
        this._goToConnectionDetails,
        true,
        this.hass.localize('ui.common.continue')
      )}
    `;
  }

  private _goToConnectionDetails() {
    console.log('_goToConnectionDetails called, this._selectedChargerType:', this._selectedChargerType);
    if (!this._selectedChargerType || this._selectedChargerType === "no_selection") {
      this._isChargerTypeValid = false;
      this.requestUpdate(); // Force re-render to show the error
      return;
    }
    this._isChargerTypeValid = true;
    this._currentPage = '2-connection-details';
    this._hasTriedToConnect = false;
    this._chargerConnectionStatus = '';
  }


  //////////////////////////////////////////////////
  //         Step 2 Connection Details            //
  //////////////////////////////////////////////////

  private _renderConnectionDetails() {
    const chargerHostState = this.hass.states[entityIds.chargerHostUrl];
    const chargerPortState = this.hass.states[entityIds.chargerPort];
    const portDescription = tp('2-connection-details.port-description', {
      value: this._getDefaultPort(),
    });
    const _isLoadBalancerEnabled = isLoadbalancerEnabled(this._quasarLoadBalancerLimit)

    return html`
      ${this._renderConnectionError()}
      ${html`<ha-markdown breaks .content="${tp('2-connection-details.description.generic')}"></ha-markdown>`}
      ${this._selectedChargerType === 'evtec-bidi-pro-10'
        ? html`<ha-markdown breaks .content="${tp('2-connection-details.description.evtec-bidi-pro-10')}"></ha-markdown>`
        : nothing
      }
      ${this._selectedChargerType === 'wallbox-quasar-1'
        ? html`<ha-markdown breaks .content="${tp('2-connection-details.description.wallbox-quasar-1')}"></ha-markdown>`
        : nothing
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
      ${html`
          <ha-markdown breaks .content=${portDescription}></ha-markdown><br/>
        `
      }
      ${renderLoadbalancerInfo(_isLoadBalancerEnabled)}
      ${renderButton(
        this.hass,
        this._goBackToChargerSelection,
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}

      ${this._isBusyConnecting()
        ? renderSpinner()
        : renderButton(
          this.hass,
          this._goToPowerDetails,
          true,
          this.hass.localize('ui.common.continue')
        )}
    `;
  }


  private _getDefaultPort(): string {
    const selectedOption = this._chargerOptions.find(
      (option) => option.value === this._selectedChargerType
    );
    return selectedOption?.default_port || '502';
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

  private _goBackToChargerSelection() {
    this._currentPage = '1-select-charger-type';
    this._hasTriedToConnect = false;
    this._chargerConnectionStatus = '';
  }

  private _isConnected() {
    return this._chargerConnectionStatus === ConnectionStatus.Connected;
  }


  //////////////////////////////////////////////////
  //            Step 3 Power Details              //
  //////////////////////////////////////////////////

  private _renderPowerDetails() {
    const description = tp('3-power-details.description', {
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
        this._goBackToConnectionDetails,
        false,
        this.hass.localize('ui.common.back'),
        false,
        'back',
        true
      )}

      ${renderButton(
        this.hass,
        this._save,
        true,
        this.hass.localize('ui.common.save'),
        false,
        'save'
      )}
    `;
  }

  private _renderReducedMaxPower() {
    const reduceMaxPowerDescription = tp(
      '3-power-details.reduce-max-power-description'
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

  private _goBackToConnectionDetails() {
    this._currentPage = '2-connection-details';
    this._hasTriedToConnect = false;
    this._chargerConnectionStatus = '';
  }

  private async _goToPowerDetails(): Promise<void> {
    // First validate input
    if (!this._isChargerHostValid()) {
      this._chargerHostField.focus();
      return;
    }
    if (!this._isChargerPortValid()) {
      this._chargerPortField.focus();
      return;
    }
    this._chargerPort = `${parseInt(this._chargerPort, 10)}`;

    // Test the connection
    this._hasTriedToConnect = true;
    try {
      this._chargerConnectionStatus = ConnectionStatus.Connecting;
      const result = await callFunction(
        this.hass,
        'test_charger_connection',
        {
          charger_type: this._selectedChargerType,
          host: this._chargerHost,
          port: this._chargerPort,
        },
        5 * 1000
      );
      this._chargerConnectionStatus = result.msg;

      // If connection success, set variables and navigate.
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
        this._currentPage = '3-power-details';
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
      charger_type: this._selectedChargerType,
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
