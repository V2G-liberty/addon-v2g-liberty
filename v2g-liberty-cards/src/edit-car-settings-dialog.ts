import { css, html, nothing } from 'lit';
import { customElement, state } from 'lit/decorators';

import { callFunction } from './util/appdaemon';
import {
  renderButton,
  renderDialogHeader,
  renderHaInput,
  renderSpinner,
  isNewHaDialogAPI,
} from './util/render';
import { partial } from './util/translate';
import { styles } from './card.styles';
import { DialogBase } from './dialog-base';
import {
  CarField,
  CarSettings,
  CAR_DETAIL_FIELDS,
  CAR_LIMIT_FIELDS,
  CAR_FIELDS,
  CAR_NAME_MAX_LENGTH,
  isCarValueValid,
} from './car-fields';

export const tagName = 'v2g-liberty-edit-car-settings-dialog';

const tp = partial('settings.car-dialog');
const tc = partial('settings.common');
const tf = partial('settings.car.fields');
const th = partial('settings.dialogs');

/** What reading the car's ID came back with; mirrors the backend's reasons. */
type IdState =
  | 'idle'
  | 'reading'
  | 'ok'
  | 'no_car'
  | 'no_id'
  | 'read_failed'
  | 'unsupported';

const enum Page {
  CarId = '1-car-id',
  Details = '2-car-details',
  Limits = '3-scheduling-limits',
}

/**
 * The one way into the car settings. Collects everything and saves once, at
 * the end: nothing is stored while the user is still walking through the
 * flow, so backing out leaves the stored car exactly as it was.
 *
 * Entity-free: every value is loaded from get_car_settings and written back
 * through save_car_settings. No DialogBase FM gate -- car values never touch
 * an asset on the Smart schedule server.
 */
@customElement(tagName)
class EditCarSettingsDialog extends DialogBase {
  @state() private _loading = true;
  @state() private _loadFailed = false;
  @state() private _page: Page = Page.Details;

  @state() private _name = '';
  @state() private _values: { [key: string]: string } = {};

  @state() private _isEdit = false;
  @state() private _identifiesCar = false;
  @state() private _storedEvId = '';
  /** An ID read in this session, accepted but not yet saved. */
  @state() private _pendingEvId: string | null = null;
  @state() private _idState: IdState = 'idle';
  @state() private _readEvId = '';
  /** Whether this session opened on the ID step, so page 2 can go back to it. */
  @state() private _startedOnIdStep = false;

  @state() private _hasTriedToContinue = false;
  @state() private _saving = false;
  @state() private _saveError: string | null = null;

  /**
   * Which dialog session a pending answer belongs to. The element is reused,
   * so a slow read from a previous opening must not fill in fields here.
   */
  private _openToken = 0;

  public async showDialog(): Promise<void> {
    super.showDialog();
    const token = ++this._openToken;

    this._loading = true;
    this._loadFailed = false;
    this._page = Page.Details;
    this._name = '';
    this._values = {};
    this._isEdit = false;
    this._identifiesCar = false;
    this._storedEvId = '';
    this._pendingEvId = null;
    this._idState = 'idle';
    this._readEvId = '';
    this._startedOnIdStep = false;
    this._hasTriedToContinue = false;
    this._saving = false;
    this._saveError = null;

    await this._load(token);
    await this.updateComplete;
  }

  private async _load(token: number): Promise<void> {
    try {
      const data = (await callFunction(
        this.hass,
        'get_car_settings'
      )) as CarSettings;
      if (token !== this._openToken) return;
      this._name = data.name ?? '';
      this._values = Object.fromEntries(
        CAR_FIELDS.map(field => [field.key, `${data[field.key] ?? ''}`])
      );
      this._isEdit = !!data.configured;
      this._identifiesCar = !!data.identifies_car;
      this._storedEvId = data.ev_id ?? '';
      // A charger that identifies cars needs an ID, and a fresh install has
      // none: ask for it first, because it can only be read while the car is
      // plugged in.
      this._startedOnIdStep = this._identifiesCar && !this._storedEvId;
      this._page = this._startedOnIdStep ? Page.CarId : Page.Details;
      this._loadFailed = false;
    } catch (e) {
      if (token !== this._openToken) return;
      // Without the current values a save would overwrite the stored car with
      // invented defaults, so show no form at all.
      console.error('Failed to load car settings', e);
      this._loadFailed = true;
    }
    if (token === this._openToken) this._loading = false;
  }

  protected render() {
    if (!this.isOpen) return nothing;

    const isNew = isNewHaDialogAPI(this.hass);
    const header = this._header();
    const content = this._loading
      ? html`<ha-spinner></ha-spinner>`
      : this._loadFailed
        ? this._renderLoadError()
        : this._page === Page.CarId
          ? this._renderCarIdStep()
          : this._page === Page.Details
            ? this._renderDetails()
            : this._renderLimits();

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .heading=${isNew ? null : renderDialogHeader(this.hass, header)}
        .headerTitle=${isNew ? header : null}
      >
        ${content}
      </ha-dialog>
    `;
  }

  private _header(): string {
    if (!this._isEdit) return tp('add.header');
    return this._name.trim()
      ? tp('edit.header', { name: this._name.trim() })
      : tp('edit.header-unnamed');
  }

  private _renderLoadError() {
    return html`
      <ha-alert alert-type="error">${tp('errors.load-failed')}</ha-alert>
      ${renderButton(
        this.hass,
        () => this.showDialog(),
        true,
        tc('retry')
      )}
    `;
  }

  // ── Step 1: the car's ID ─────────────────────────────────────────────

  // No Close button anywhere in this dialog: the X, Escape and a click outside
  // all close it, and the other settings dialogs do the same.
  private _renderCarIdStep() {
    return html`
      <ha-markdown
        breaks
        .content=${this._isEdit
          ? tp('car-id.explain-edit')
          : tp('car-id.explain')}
      ></ha-markdown>
      ${this._renderIdFeedback()}
      ${this._idState === 'reading'
        ? renderSpinner(this.hass)
        : renderButton(
            this.hass,
            () => this._readCarId(),
            true,
            this._idState === 'idle'
              ? tp('car-id.read')
              : tp('car-id.read-again')
          )}
    `;
  }

  private _renderIdFeedback() {
    switch (this._idState) {
      case 'reading':
        return html`<ha-alert alert-type="info"
          >${tp('car-id.reading')}</ha-alert
        >`;
      case 'ok':
        return html`<ha-alert alert-type="success"
          >${tp('car-id.found', { id: this._readEvId })}</ha-alert
        >`;
      case 'no_car':
        return html`<ha-alert alert-type="warning"
          >${tp('car-id.no-car')}</ha-alert
        >`;
      case 'no_id':
        return html`<ha-alert alert-type="warning"
          >${tp('car-id.no-id')}</ha-alert
        >`;
      case 'read_failed':
        return html`<ha-alert alert-type="error"
          >${tp('car-id.failed')}</ha-alert
        >`;
      default:
        return nothing;
    }
  }

  private async _readCarId(): Promise<void> {
    const token = this._openToken;
    this._idState = 'reading';
    let reason: IdState = 'read_failed';
    let evId = '';
    try {
      const data = await callFunction(
        this.hass,
        'get_connected_car_id',
        {},
        20000
      );
      reason = (data.reason as IdState) ?? 'read_failed';
      evId = `${data.ev_id ?? ''}`;
    } catch (e) {
      // The backend always answers; a throw means it could not be reached.
      console.error('Failed to read the car id', e);
    }
    // The dialog was closed or reopened while we waited.
    if (token !== this._openToken || !this.isOpen) return;

    this._idState = reason;
    this._readEvId = evId;
    if (reason === 'unsupported') {
      // The charger cannot identify cars after all: no ID step at all.
      this._identifiesCar = false;
      this._page = Page.Details;
      return;
    }
    if (reason === 'ok') {
      this._pendingEvId = evId;
      // Nothing is stored yet; Save is what registers this car.
      if (this._page === Page.CarId) this._page = Page.Details;
    }
  }

  // ── Step 2: name, battery and consumption ────────────────────────────

  private _renderDetails() {
    const nameError =
      this._hasTriedToContinue && !this._name.trim()
        ? html`<div class="error">${tp('errors.name-required')}</div>`
        : nothing;

    return html`
      <ha-markdown breaks .content=${tp('details.description')}></ha-markdown>
      ${this._renderIdentificationBlock()}
      <div style="margin-top: 16px;">
        <label class="field-label" for="car-name">${tp('name-label')}</label>
        ${renderHaInput({
          value: this._name,
          onChange: (e: any) =>
            (this._name = `${e.target.value}`.slice(0, CAR_NAME_MAX_LENGTH)),
          id: 'car-name',
          placeholder: tp('name-placeholder'),
          required: true,
        })}
        ${nameError}
      </div>
      ${CAR_DETAIL_FIELDS.map(field => this._renderField(field))}
      ${this._startedOnIdStep
        ? renderButton(
            this.hass,
            () => this._goBackToCarId(),
            false,
            this.hass.localize('ui.common.back'),
            false,
            'back',
            true
          )
        : nothing}
      ${renderButton(
        this.hass,
        () => this._goToLimits(),
        true,
        this.hass.localize('ui.common.continue')
      )}
    `;
  }

  /**
   * In edit mode on a charger that identifies cars: show which car is
   * registered and let the user read the connected one. Reading never stores
   * anything -- only Save does.
   */
  private _renderIdentificationBlock() {
    if (!this._identifiesCar) return nothing;
    const registered = this._pendingEvId || this._storedEvId;
    const isDifferent =
      this._idState === 'ok' &&
      !!this._readEvId &&
      !!this._storedEvId &&
      this._readEvId.toLowerCase() !== this._storedEvId.toLowerCase();

    return html`
      <div class="id-block">
        <p class="id-title">${tp('car-id.header')}</p>
        <p class="id-value">${tp('car-id.registered', { id: registered || '—' })}</p>
        ${this._pendingEvId && this._pendingEvId !== this._storedEvId
          ? html`<ha-alert alert-type="info"
              >${tp('car-id.will-replace', { id: this._pendingEvId })}</ha-alert
            >`
          : nothing}
        ${isDifferent && this._pendingEvId !== this._readEvId
          ? html`
              <ha-alert alert-type="warning">
                ${tp('car-id.different', { id: this._readEvId })}
                <ha-button
                  slot="action"
                  @click=${() => (this._pendingEvId = this._readEvId)}
                >
                  ${tp('car-id.use-connected')}
                </ha-button>
              </ha-alert>
            `
          : nothing}
        ${this._idState !== 'ok' ? this._renderIdFeedback() : nothing}
        ${this._idState === 'reading'
          ? html`<ha-spinner size="small"></ha-spinner>`
          : html`<ha-button
              appearance="plain"
              variant="brand"
              size="s"
              @click=${() => this._readCarId()}
              >${tp('car-id.read-again')}</ha-button
            >`}
      </div>
    `;
  }

  private _goBackToCarId(): void {
    this._hasTriedToContinue = false;
    this._page = Page.CarId;
  }

  private _goToLimits(): void {
    this._hasTriedToContinue = true;
    if (!this._detailsValid()) return;
    this._hasTriedToContinue = false;
    this._page = Page.Limits;
  }

  private _detailsValid(): boolean {
    return (
      !!this._name.trim() &&
      CAR_DETAIL_FIELDS.every(field =>
        isCarValueValid(field, this._values[field.key])
      )
    );
  }

  // ── Step 3: the scheduling limits ────────────────────────────────────

  private _renderLimits() {
    return html`
      <ha-markdown breaks .content=${tp('limits.description')}></ha-markdown>
      ${CAR_LIMIT_FIELDS.map(field => this._renderField(field))}
      ${this._saveError
        ? html`<ha-alert alert-type="error">${this._saveError}</ha-alert>`
        : nothing}
      ${renderButton(
        this.hass,
        () => this._goBackToDetails(),
        false,
        this.hass.localize('ui.common.back'),
        this._saving,
        'back',
        true
      )}
      ${this._saving
        ? renderSpinner(this.hass)
        : renderButton(
            this.hass,
            () => this._save(),
            true,
            this.hass.localize('ui.common.save')
          )}
    `;
  }

  private _goBackToDetails(): void {
    this._hasTriedToContinue = false;
    this._saveError = null;
    this._page = Page.Details;
  }

  // ── A single number field ────────────────────────────────────────────

  private _renderField(field: CarField) {
    const value = this._values[field.key] ?? '';
    const showError =
      this._hasTriedToContinue && !isCarValueValid(field, value);

    return html`
      <div style="margin-top: 16px;">
        <label class="field-label" for="car-${field.key}"
          >${tf(field.label)}</label
        >
        ${renderHaInput({
          value,
          onChange: (e: any) => this._setValue(field, `${e.target.value}`),
          id: `car-${field.key}`,
          testId: `car-${field.key}`,
          type: 'number',
          inputmode: 'numeric',
          min: field.min,
          max: field.max,
          step: 1,
          suffix: field.suffix,
          style: 'width: 220px;',
        })}
        ${showError
          ? html`<div class="error">
              ${tp('errors.out-of-range', { min: field.min, max: field.max })}
            </div>`
          : nothing}
        <!-- One shared name makes these an accordion: opening one closes the
             other. The explanations are long (they were a dialog of their own
             per value before), so several open at once turns this into a page
             the user has to scroll. A browser without exclusive <details>
             simply keeps today's behaviour. -->
        <details class="hint" name="car-help">
          <summary>${tp('learn-more')}</summary>
          <ha-markdown breaks .content=${th(field.help)}></ha-markdown>
        </details>
      </div>
    `;
  }

  private _setValue(field: CarField, value: string): void {
    // Replace the object: Lit compares by reference.
    this._values = { ...this._values, [field.key]: value };
  }

  // ── Saving, once, at the end ─────────────────────────────────────────

  private async _save(): Promise<void> {
    this._hasTriedToContinue = true;
    if (!CAR_LIMIT_FIELDS.every(f => isCarValueValid(f, this._values[f.key]))) {
      return;
    }
    if (!this._detailsValid()) {
      // Something on the previous page is wrong after all; show it there.
      this._page = Page.Details;
      return;
    }

    const args: { [key: string]: string | number } = {
      name: this._name.trim(),
    };
    for (const field of CAR_FIELDS) {
      args[field.payloadKey] = Number(this._values[field.key]);
    }
    // Only send an ID that was read in this session; an empty one keeps the
    // stored ID.
    if (this._pendingEvId) args.ev_id = this._pendingEvId;

    this._saving = true;
    this._saveError = null;
    try {
      const result = await callFunction(
        this.hass,
        'save_car_settings',
        args,
        15000
      );
      if (result?.error) {
        this._saveError = `${result.error}`;
        this._saving = false;
        return;
      }
      this.closeDialog();
    } catch (e) {
      this._saveError = `${e}`;
      this._saving = false;
    }
  }

  static styles = [
    styles,
    css`
      .error {
        color: var(--error-color);
        font-size: 0.875em;
        margin-top: 4px;
      }
      .field-label {
        display: block;
        font-size: 0.875em;
        color: var(--secondary-text-color);
        margin-bottom: 4px;
      }
      .id-block {
        border: 1px solid var(--divider-color);
        border-radius: 12px;
        padding: 12px 16px;
        margin-top: 16px;
      }
      .id-title {
        margin: 0 0 4px 0;
        font-weight: 600;
      }
      .id-value {
        margin: 0 0 8px 0;
        color: var(--secondary-text-color);
      }
      details.hint {
        margin-top: 4px;
        font-size: 0.875em;
        color: var(--secondary-text-color);
      }
      details.hint summary {
        cursor: pointer;
      }
    `,
  ];
}
