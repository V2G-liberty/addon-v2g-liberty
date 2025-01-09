import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';

import { callFunction } from './util/appdaemon';
import { renderDialogHeader, renderSelectOption } from './util/render';
import { styles } from './card.styles';
import { t } from './util/translate';
import { DialogBase } from './dialog-base';

export const tagName = 'edit-inputselect-dialog';

@customElement(tagName)
class EditInputNumberDialog extends DialogBase {
  @state() private _params?: any;
  @state() private _value: string;

  public async showDialog(params: any): Promise<void> {
    super.showDialog();
    this._params = params;
    this._value = this.hass.states[this._params.entity_id].state;
    await this.updateComplete;
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const stateObj = this.hass.states[this._params.entity_id];
    const header = this._params.header;
    const name = t(stateObj.entity_id) || stateObj.attributes.friendly_name;
    const description = this._params.description;

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${renderDialogHeader(this.hass, header)}
      >
        <div>
          <span class="name">${name}</span>
          <ha-icon .icon="${stateObj.attributes.icon}"></ha-icon>
        </div>
        <div>
          ${stateObj.attributes.options.map(option =>
            renderSelectOption(
              option,
              option === this._value,
              evt => (this._value = evt.target.value)
            )
          )}
        </div>
        <ha-markdown breaks .content=${description}></ha-markdown>
        <mwc-button @click=${this._save} slot="primaryAction">
          ${this.hass.localize('ui.common.save')}
        </mwc-button>
      </ha-dialog>
    `;
  }

  private async _save(): Promise<void> {
    const result = await callFunction(this.hass, 'save_setting', {
      entity: this._params.entity_id,
      value: this._value,
    });
    this.closeDialog();
  }
  static styles = [
    styles,
    css`
      .name {
        font-weight: bold;
      }
    `
  ];

}
