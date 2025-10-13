import { html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { navigate } from 'custom-card-helpers';
import { renderDialogHeader, renderButton } from './util/render';
import { partial } from './util/translate';
import { renderUninitializedEntitiesList } from './util/settings-error-alert';
import { DialogBase } from './dialog-base';

export const tagName = 'settings-error-alert-dialog';
const tp = partial('settings-alert-dialog');

@customElement(tagName)
export class SettingsErrorAlertDialog extends DialogBase {
  @state() private _initialisationEntities: any[] = [];
  @state() private _hasUnInitialisedEntities: boolean = false;

  public async showDialog(): Promise<void> {
    super.showDialog();
    await this.updateComplete;
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const _navigateToSettings = () => {
      navigate(this.hass, '/lovelace-yaml/settings', true);
      this.closeDialog();
    };

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${renderDialogHeader(this.hass, tp('header'))}
      >
        <ha-alert alert-type="error">
          ${tp('error')}
        </ha-alert>
        <br/>
        <p>${tp('message')}</p>
        <p>${renderUninitializedEntitiesList(this.hass)}</p>
        <p>${tp('cta')}</p>
        ${renderButton(
          this.hass,
          _navigateToSettings,
          true,
          tp('go_to_settings')
        )}

      </ha-dialog>
    `;
  }
}
