import { html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { navigate } from 'custom-card-helpers';
import { renderDialogHeader, renderButton, isNewHaDialogAPI } from './util/render';
import { partial } from './util/translate';
import {
  hasUninitializedEntities,
  renderUninitializedEntitiesList,
} from './util/settings-error-alert';
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

  protected updated() {
    // The dialog keeps receiving state updates while it is open, and it renders
    // its list from the state of this moment. A problem that resolves in the
    // meantime would leave the dialog standing with an empty list -- telling the
    // user to fix something that is no longer wrong. Close instead.
    if (this.isOpen && this.hass && !hasUninitializedEntities(this.hass)) {
      this.closeDialog();
    }
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const _navigateToSettings = () => {
      navigate(this.hass, '/lovelace-yaml/settings', true);
      this.closeDialog();
    };

    const _header = tp('header');
    const _isNew = isNewHaDialogAPI(this.hass);
    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${_isNew ? null : renderDialogHeader(this.hass, _header)}
        .headerTitle=${_isNew ? _header : null}
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
