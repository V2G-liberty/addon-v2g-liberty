import { html, css, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';
import { renderDialogHeader, renderButton, renderSpinner, isNewHaDialogAPI, renderHaInput, renderSelectOptionWithLabel } from './util/render';
import { partial } from './util/translate';
import { DialogBase } from './dialog-base';
import { callFunction } from './util/appdaemon';

export const tagName = 'reset-database-dialog';
const tp = partial('data-table.reset-dialog');

@customElement(tagName)
export class ResetDatabaseDialog extends DialogBase {
  @state() private _mode: 'reimport' | 'full' = 'reimport';
  @state() private _confirmText = '';
  @state() private _isResetting = false;
  @state() private _error = '';
  @state() private _success = false;

  static styles = css`
    .option-detail {
      margin-top: 4px;
      font-size: 13px;
      color: var(--secondary-text-color);
      line-height: 1.4;
    }
    .duration-note {
      margin: 16px 0 0 0;
      font-size: 13px;
      color: var(--secondary-text-color);
      line-height: 1.4;
    }
    .duration-note strong {
      font-weight: 600;
    }
    .confirm-section {
      margin: 12px 0 0 0;
    }
  `;

  public async showDialog(): Promise<void> {
    this._mode = 'reimport';
    this._confirmText = '';
    this._isResetting = false;
    this._error = '';
    this._success = false;
    super.showDialog();
  }

  private get _canConfirm(): boolean {
    if (this._isResetting) return false;
    if (this._mode === 'reimport') return true;
    const v = this._confirmText.trim().toLowerCase();
    return v === 'yes' || v === 'ja';
  }

  private async _doReset() {
    this._isResetting = true;
    this._error = '';
    try {
      await callFunction(this.hass, 'reset_database', { mode: this._mode }, 60000);
      this._success = true;
    } catch {
      this._error = tp('error');
    } finally {
      this._isResetting = false;
    }
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const header = tp('header');
    const _isNew = isNewHaDialogAPI(this.hass);

    if (this._success) {
      return html`
        <ha-dialog open @closed=${this.closeDialog}
          .heading=${_isNew ? null : renderDialogHeader(this.hass, header)}
          .headerTitle=${_isNew ? header : null}>
          <ha-alert alert-type="success">${tp('success')}</ha-alert>
          ${renderButton(this.hass, () => this.closeDialog(), true, tp('close'))}
        </ha-dialog>
      `;
    }

    return html`
      <ha-dialog open @closed=${this.closeDialog}
        .heading=${_isNew ? null : renderDialogHeader(this.hass, header)}
        .headerTitle=${_isNew ? header : null}>

        ${renderSelectOptionWithLabel(
          'reimport',
          html`
            <strong>${tp('reimport-label')}</strong>
            <div class="option-detail">${tp('reimport-explanation')}</div>
          `,
          this._mode === 'reimport',
          evt => { this._mode = evt.target.value; }
        )}

        ${renderSelectOptionWithLabel(
          'full',
          html`
            <strong>${tp('full-label')}</strong>
            <div class="option-detail">${tp('full-explanation')}</div>
          `,
          this._mode === 'full',
          evt => { this._mode = evt.target.value; }
        )}

        ${this._mode === 'full' ? html`
          <div class="confirm-section">
            <ha-alert alert-type="warning">
              ${tp('full-warning')}
            </ha-alert>
            <p style="margin-top: 12px; font-weight: 500;">
              ${tp('confirm-prompt')}
            </p>
            ${renderHaInput({
              value: this._confirmText,
              onChange: (e: any) => {
                this._confirmText = e.target.value;
              },
              placeholder: tp('confirm-placeholder'),
              style: 'width: 100%',
            })}
          </div>
        ` : nothing}

        <ha-markdown class="duration-note" breaks .content=${tp('duration-note')}></ha-markdown>

        ${this._error ? html`
          <ha-alert alert-type="error" style="margin-top: 12px;">
            ${this._error}
          </ha-alert>
        ` : nothing}

        ${this._isResetting
          ? renderSpinner(this.hass)
          : renderButton(
              this.hass,
              () => this._doReset(),
              true,
              tp('confirm-button'),
              !this._canConfirm
            )}
      </ha-dialog>
    `;
  }
}
