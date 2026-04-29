import { html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';

import { DialogBase } from './dialog-base';
import { styles } from './card.styles';
import { renderButton, isNewHaDialogAPI } from './util/render';

export const tagName = 'v2g-liberty-edit-grid-connection-settings-dialog';

@customElement(tagName)
export class EditGridConnectionSettingsDialog extends DialogBase {

  // Placeholder — full wizard implementation in subsequent tasks (T24–T28)

  render() {
    if (!this.isOpen) return nothing;

    const useNewApi = isNewHaDialogAPI(this.hass);
    const heading = useNewApi ? undefined : html`
      <span slot="heading">Grid connection</span>
    `;
    const headerTitle = useNewApi ? 'Grid connection' : undefined;

    return html`
      <ha-dialog
        open
        .heading=${heading}
        .headerTitle=${headerTitle}
        @closed=${this.closeDialog}
      >
        <div class="card-content">
          <p>Grid connection settings dialog — coming soon.</p>
        </div>
        ${renderButton(this.hass, () => this.closeDialog(), true, 'Close')}
      </ha-dialog>
    `;
  }

  static styles = [styles];
}
