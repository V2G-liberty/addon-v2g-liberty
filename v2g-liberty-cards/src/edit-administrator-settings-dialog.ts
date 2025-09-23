import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';

import { callFunction } from './util/appdaemon';
import { renderDialogHeader, renderInputSelect, renderButton } from './util/render';
import { partial } from './util/translate';
import { styles } from './card.styles';
import { defaultState, DialogBase } from './dialog-base';
import * as entityIds from './entity-ids';

export const tagName = 'edit-administrator-settings-dialog';
const tp = partial('settings.administrator');

@customElement(tagName)
class EditAdministratorSettingsDialog extends DialogBase {
  @state() private _mobileName: string;
  @state() private _mobilePlatform: string;
  @state() private _hasTriedToSave: boolean;

  public async showDialog(): Promise<void> {
    super.showDialog();
    this._mobileName = defaultState(
      this.hass.states[entityIds.adminMobileName],
      ''
    );
    this._mobilePlatform = defaultState(
      this.hass.states[entityIds.adminMobilePlatform],
      ''
    );
    this._hasTriedToSave = false;
    await this.updateComplete;
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const mobileNameState = this.hass.states[entityIds.adminMobileName];
    const mobilePlatformState = this.hass.states[entityIds.adminMobilePlatform];

    const mobileApps = Object.keys(this.hass.services['notify'])
      .filter(service => /^mobile_app_/.test(service))
      .map(service => service.replace(/^mobile_app_/, ''));

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${renderDialogHeader(this.hass, tp('header'))}
      >
        <p><ha-markdown breaks .content=${tp('sub-header')}></ha-markdown></p>
        ${renderInputSelect(
          this._mobileName,
          mobileNameState,
          evt => (this._mobileName = evt.target.value),
          mobileApps
        )}
        ${this._renderError(this._mobileName)}
        ${renderInputSelect(
          this._mobilePlatform,
          mobilePlatformState,
          evt => (this._mobilePlatform = evt.target.value)
        )}
        ${this._renderError(this._mobilePlatform)}
        ${renderButton(
          this.hass,
          this._save,
          true,
          this.hass.localize('ui.common.save')
        )}
      </ha-dialog>
    `;
  }

  private _renderError(value) {
    return this._hasTriedToSave && !value
      ? html` <div class="error">${tp('error')}</div> `
      : nothing;
  }

  private async _save(): Promise<void> {
    this._hasTriedToSave = true;
    if (!this._mobileName || !this._mobilePlatform) return;

    const result = await callFunction(
      this.hass,
      'save_administrator_settings',
      {
        mobileName: this._mobileName,
        mobilePlatform: this._mobilePlatform,
      }
    );
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