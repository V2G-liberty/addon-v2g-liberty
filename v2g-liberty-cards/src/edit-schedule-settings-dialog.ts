import { mdiCheck } from '@mdi/js';
import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';

import { callFunction } from './util/appdaemon';
import {
  InputText,
  renderDialogHeader,
  renderInputBoolean,
  renderInputPassword,
  renderInputSelect,
  renderInputText,
} from './util/render';
import { partial, t } from './util/translate';
import { defaultState, DialogBase } from './dialog-base';
import * as entityIds from './entity-ids';

export const tagName = 'edit-schedule-settings-dialog';
const tp = partial('settings.schedule');

enum ConnectionStatus {
  Connected = 'Successfully connected',
  Connecting = 'Trying to connect...',
  Failed = 'Failed to connect',
  TimedOut = 'Timed out',
}

@customElement(tagName)
class EditScheduleSettingsDialog extends DialogBase {
  @state() private _fmAccountUsername: string;
  @state() private _fmAccountPassword: string;
  @state() private _fmUseOtherServer: string;
  @state() private _fmHostUrl: string;
  @state() private _fmAsset: string;
  @state() private _fmConnectionStatus: string;
  @state() private _hasTriedToConnect: boolean;
  @state() private _hasTriedToSave: boolean;

  private _fmAssets;

  public async showDialog(): Promise<void> {
    super.showDialog();

    this._fmAccountUsername = defaultState(
      this.hass.states[entityIds.fmAccountUsername],
      ''
    );
    this._fmAccountPassword = defaultState(
      this.hass.states[entityIds.fmAccountPassword],
      ''
    );
    this._fmUseOtherServer = this.hass.states[entityIds.fmUseOtherServer].state;
    this._fmHostUrl = defaultState(this.hass.states[entityIds.fmHostUrl], 'https://seita.energy');
    this._fmAsset = this.hass.states[entityIds.fmAsset].state;

    this._fmConnectionStatus = '';
    this._hasTriedToConnect = false;
    this._hasTriedToSave = false;
    await this.updateComplete;
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const header = tp('header');
    const content =
      this._hasTriedToConnect && this._isConnected()
        ? this._renderAssetDetails()
        : this._renderAccountDetails();

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
    return this._fmConnectionStatus === ConnectionStatus.Connected;
  }

  private _isBusyConnecting() {
    return this._fmConnectionStatus === ConnectionStatus.Connecting;
  }

  private _renderAccountDetails() {
    const description = tp('account-description');
    const fmAccountUsernameState =
      this.hass.states[entityIds.fmAccountUsername];
    const fmAccountPasswordState =
      this.hass.states[entityIds.fmAccountPassword];
    const fmUseOtherServerState = this.hass.states[entityIds.fmUseOtherServer];
    const fmHostUrlState = this.hass.states[entityIds.fmHostUrl];
    const isUsingOtherServer = this._fmUseOtherServer === 'on';

    const useOtherServerChanged = evt =>
      (this._fmUseOtherServer = evt.target.checked ? 'on' : 'off');

    return html`
      ${this._renderConnectionError()}
      <ha-markdown breaks .content=${description}></ha-markdown>
      ${renderInputText(
        InputText.EMail,
        this._fmAccountUsername,
        fmAccountUsernameState,
        evt => (this._fmAccountUsername = evt.target.value)
      )}
      ${renderInputPassword(
        this._fmAccountPassword,
        fmAccountPasswordState,
        evt => (this._fmAccountPassword = evt.target.value)
      )}
      ${renderInputBoolean(
        isUsingOtherServer,
        fmUseOtherServerState,
        useOtherServerChanged
      )}
      ${isUsingOtherServer
        ? renderInputText(
            InputText.URL,
            this._fmHostUrl,
            fmHostUrlState,
            evt => (this._fmHostUrl = evt.target.value)
          )
        : nothing}
      ${this._isBusyConnecting()
        ? html`
            <ha-circular-progress
              size="small"
              indeterminate
              slot="primaryAction"
            ></ha-circular-progress>
          `
        : html`
            <mwc-button @click=${this._continue} slot="primaryAction">
              ${this.hass.localize('ui.common.continue')}
            </mwc-button>
          `}
    `;
  }

  private _renderConnectionError() {
    const error = tp('connection-error');
    const hasConnectionError =
      this._fmConnectionStatus === ConnectionStatus.Failed;
    return hasConnectionError
      ? html`<ha-alert alert-type="error">${error}</ha-alert>`
      : nothing;
  }

  private _renderAssetDetails() {
    return this._fmAssets?.length === 0
      ? this._renderNoAssets()
      : this._fmAssets?.length === 1
      ? this._renderOneAsset()
      : this._fmAssets?.length > 1
      ? this._renderMultipleAssets()
      : html`ERROR`;
  }

  private _renderNoAssets() {
    return html`
      ${this._renderLoginSuccessful()}
      <ha-alert alert-type="error"> ${tp('no-asset-error')} </ha-alert>
      <mwc-button @click=${this._back} slot="secondaryAction">
        &lt; ${this.hass.localize('ui.common.back')}
      </mwc-button>
    `;
  }

  private _renderLoginSuccessful() {
    return html`
      <div class="success">
        <ha-svg-icon .path=${mdiCheck}></ha-svg-icon>
        <span>Login successful</span>
      </div>
    `;
  }

  private _renderOneAsset() {
    this._fmAsset = this._fmAssets[0].name;
    return html`
      ${this._renderLoginSuccessful()}
      <strong>Asset</strong>
      <div>${this._fmAsset}</div>
      <mwc-button @click=${this._back} slot="secondaryAction">
        &lt; ${this.hass.localize('ui.common.back')}
      </mwc-button>
      <mwc-button @click=${this._save} slot="primaryAction">
        ${this.hass.localize('ui.common.save')}
      </mwc-button>
    `;
  }

  private _renderMultipleAssets() {
    const description = tp('multiple-asset-description');
    const fmAssetState = this.hass.states[entityIds.fmAsset];
    const options = this._fmAssets.map(asset => asset.name);
    return html`
      <p><ha-markdown breaks .content=${description}></ha-markdown></p>
      <strong>Asset</strong>
      ${renderInputSelect(
        this._fmAsset,
        fmAssetState,
        evt => (this._fmAsset = evt.target.value),
        options
      )}
      ${this._renderNoAssetSelectedError()}
      <mwc-button @click=${this._back} slot="secondaryAction">
        &lt; ${this.hass.localize('ui.common.back')}
      </mwc-button>
      <mwc-button @click=${this._save} slot="primaryAction">
        ${this.hass.localize('ui.common.save')}
      </mwc-button>
    `;
  }

  private _renderNoAssetSelectedError() {
    return this._hasTriedToSave && !this._isAssetValid()
      ? html` <div class="error">${tp('no-asset-selected-error')}</div> `
      : nothing;
  }

  private _isAssetValid(): boolean {
    return this._fmAssets.some(asset => asset.name === this._fmAsset);
  }

  private _back(): void {
    this._hasTriedToConnect = false;
  }

  private async _continue(): Promise<void> {
    this._hasTriedToConnect = true;
    // TODO: Add validation
    try {
      const args = this._getConnectionArgs();
      this._fmConnectionStatus = ConnectionStatus.Connecting;
      const result = await callFunction(
        this.hass,
        'test_schedule_connection',
        args,
        5 * 1000
      );
      this._fmAssets = result.assets;
      this._fmConnectionStatus = result.msg;
    } catch (err) {
      this._fmConnectionStatus = ConnectionStatus.TimedOut;
    }
  }

  private async _save(): Promise<void> {
    this._hasTriedToSave = true;
    if (!this._isAssetValid()) {
      return;
    }
    const args = {
      ...this._getConnectionArgs(),
      asset: this._fmAsset,
    };
    const result = await callFunction(
      this.hass,
      'save_schedule_settings',
      args
    );
    this.closeDialog();
  }

  private _getConnectionArgs() {
    const isUsingOtherServer = this._fmUseOtherServer === 'on';
    return {
      host: this._fmHostUrl,
      username: this._fmAccountUsername,
      password: this._fmAccountPassword,
      useOtherServer: isUsingOtherServer,
    };
  }

  static styles = css`
    .name {
      font-weight: bold;
    }
    .error {
      color: var(--error-color);
    }

    .success ha-svg-icon {
      color: var(--success-color);
      padding-right: 2rem;
    }

    .success {
      margin-bottom: 2rem;
      font-size: 1.2rem;
    }
  `;
}
