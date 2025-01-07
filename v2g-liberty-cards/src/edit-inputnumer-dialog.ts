import { css, html, nothing } from 'lit';
import { customElement, query, state } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';

import { callFunction } from './util/appdaemon';
import { renderDialogHeader, renderInputNumber } from './util/render';
import { styles } from './card.styles';
import { t } from './util/translate';
import { DialogBase } from './dialog-base';

export const tagName = 'edit-inputnumber-dialog';

@customElement(tagName)
class EditInputNumberDialog extends DialogBase {
  @state() private _params?: any;
  @state() private _value: string;
  @state() private _isInputValid: boolean;

  @query('#inputField', false) private _inputField;

  public async showDialog(params: any): Promise<void> {
    super.showDialog();
    this._params = params;
    this._value = this.hass.states[this._params.entity_id].state;
    await this.updateComplete;
    this._isInputValid = this._inputField.checkValidity();
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const stateObj = this.hass.states[this._params.entity_id];
    const heading = this._params.header;
    const description = this._params.description;

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${renderDialogHeader(this.hass, heading)}
      >
        ${renderInputNumber(
          this._value,
          stateObj,
          evt => (this._value = evt.target.value)
        )}
        ${this._renderInvalidInputAlert(stateObj)}
        <ha-markdown breaks .content=${description}></ha-markdown>
        <mwc-button @click=${this._save} slot="primaryAction">
          ${this.hass.localize('ui.common.save')}
        </mwc-button>
      </ha-dialog>
    `;
  }

  private _renderInvalidInputAlert(stateObj: HassEntity) {
    const error = t('settings.dialogs.inputnumber.error', {
      min: stateObj.attributes.min,
      max: stateObj.attributes.max,
    });

    return this._isInputValid
      ? nothing
      : html`<ha-alert alert-type="error">${error}</ha-alert>`;
  }

  private async _save(): Promise<void> {
    this._isInputValid = this._inputField.checkValidity();
    if (!this._isInputValid) {
      this._inputField.focus();
      return;
    }
    const result = await callFunction(this.hass, 'save_setting', {
      entity: this._params.entity_id,
      value: this._value,
    });
    this.closeDialog();
  }

  static styles = [
    styles,
    css`
      .select-name {
        font-weight: bold;
      }
    `
  ];
}
