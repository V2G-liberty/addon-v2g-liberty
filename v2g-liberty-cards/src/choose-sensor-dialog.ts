// Side-step dialog: pick a sensor for one role (redesign, Fase 4).
//
// The candidate list is filtered on device_class / unit / state_class, never on
// entity id text. Entities already used by another role stay in the list but are
// marked "already in use". Switching to "All sensors" drops the filter and
// labels each unsuitable candidate with what it actually measures; picking one
// then needs a second OK (a stated-consequence override).
//
// Copy lives in strings.json / nl.json under settings.grid-connection.choose-sensor.

import { html, css, nothing, TemplateResult } from 'lit';
import { customElement, state, property } from 'lit/decorators';
import { HassEntity } from 'home-assistant-js-websocket';

import { DialogBase } from './dialog-base';
import {
  renderButton,
  renderDialogHeader,
  renderHaInput,
  isNewHaDialogAPI,
} from './util/render';
import { styles } from './card.styles';
import {
  RoleDefinition,
  candidatesFor,
  friendlyName,
  fitsRole,
} from './grid-connection-status';
import { unsafeHTML } from 'lit/directives/unsafe-html';
import { partial } from './util/translate';

const tp = partial('settings.grid-connection.choose-sensor');

export const tagName = 'v2g-liberty-choose-sensor-dialog';

export interface ChooseSensorDialogParams {
  definition: RoleDefinition;
  /** Human label for what is being configured, e.g. "Consumption phase 2 · power". */
  subtitle: string;
  /** Entity ids already used by other roles. */
  inUse: string[];
  /** Prefilled search terms, derived from the role. */
  searchTerms?: string[];
  onSelect: (entityId: string) => void;
}

@customElement(tagName)
export class ChooseSensorDialog extends DialogBase {
  @property({ attribute: false }) private _params!: ChooseSensorDialogParams;
  @state() private _query = '';
  @state() private _unfiltered = false;
  @state() private _selected: string | null = null;
  @state() private _warnedUnsuitable = false;
  @state() private _triedConfirmEmpty = false;

  public async showDialog(params: ChooseSensorDialogParams): Promise<void> {
    super.showDialog();
    this._params = params;
    this._query = (params.searchTerms ?? []).join(' ');
    this._selected = params.definition.entityId;
    this._unfiltered = false;
    this._warnedUnsuitable = false;
    this._triedConfirmEmpty = false;
  }

  render(): TemplateResult | typeof nothing {
    if (!this.isOpen) return nothing;
    const newApi = isNewHaDialogAPI(this.hass);
    const title = tp('title');
    const isPower = this._params.definition.role === 'power';

    return html`
      <ha-dialog
        open
        @closed=${this.closeDialog}
        .headerTitle=${newApi ? title : undefined}
        .headerSubtitle=${newApi ? this._params.subtitle : undefined}
        .heading=${newApi ? undefined : renderDialogHeader(this.hass, title)}
      >
        <div class="content">
          ${newApi
            ? nothing
            : html`<p class="subtitle">${this._params.subtitle}</p>`}

          ${renderHaInput({
            value: this._query,
            onChange: (e: any) => (this._query = e.target?.value ?? ''),
            label: tp('search-label'),
            style: 'width: 100%',
            testId: 'sensor-search',
            suffix: html`
              ${this._query
                ? html`<button
                    class="icon-btn"
                    title=${tp('clear-title')}
                    @click=${() => (this._query = '')}
                  >
                    ✕
                  </button>`
                : nothing}
              <span class="info" title=${tp('search-info')}>
                <ha-icon icon="mdi:information-outline"></ha-icon>
              </span>
            `,
          })}

          <div class="filters">
            <button
              class="chip ${this._unfiltered ? '' : 'active'}"
              @click=${() => (this._unfiltered = false)}
            >
              ${isPower ? tp('filter-power') : tp('filter-meter')}
            </button>
            <button
              class="chip ${this._unfiltered ? 'active' : ''}"
              @click=${() => (this._unfiltered = true)}
            >
              ${tp('filter-all')}
            </button>
          </div>

          <div class="list">
            ${this._visibleCandidates().map(stateObj =>
              this._renderCandidate(stateObj)
            )}
            ${this._visibleCandidates().length === 0
              ? html`<div class="empty">${tp('empty')}</div>`
              : nothing}
          </div>

          ${this._triedConfirmEmpty && !this._selected
            ? html`<ha-alert alert-type="error">${tp('select-first')}</ha-alert>`
            : nothing}
          ${this._renderUnsuitableWarning()} ${this._renderHelp(isPower)}
        </div>

        ${renderButton(this.hass, () => this.closeDialog(), false, tp('cancel'))}
        ${renderButton(this.hass, () => this._confirm(), true, tp('ok'))}
      </ha-dialog>
    `;
  }

  private _visibleCandidates(): HassEntity[] {
    const role = this._params.definition.role;
    const all = this._unfiltered
      ? Object.values(this.hass.states)
          .filter(s => s.entity_id.startsWith('sensor.'))
          // Fitting sensors (the right unit/class for this role) first, the
          // rest below; alphabetical by friendly name within each group.
          .sort((a, b) => {
            const aFits = fitsRole(a, role) ? 0 : 1;
            const bFits = fitsRole(b, role) ? 0 : 1;
            if (aFits !== bFits) return aFits - bFits;
            return friendlyName(a).localeCompare(friendlyName(b));
          })
      : candidatesFor(this.hass, role, this._params.inUse);

    const terms = this._query
      .toLowerCase()
      .split(/\s+/)
      .filter(term => term.length > 0);

    return all
      .filter(stateObj => {
        if (terms.length === 0) return true;
        const haystack =
          `${friendlyName(stateObj)} ${stateObj.entity_id}`.toLowerCase();
        return terms.every(term => haystack.includes(term));
      })
      .slice(0, 100);
  }

  private _renderCandidate(stateObj: HassEntity) {
    const role = this._params.definition.role;
    const used = this._params.inUse.includes(stateObj.entity_id);
    const misfit = !fitsRole(stateObj, role);
    const selected = this._selected === stateObj.entity_id;
    const unit =
      (stateObj.attributes as { unit_of_measurement?: string })
        .unit_of_measurement ?? '';

    return html`
      <div
        class="candidate ${selected ? 'selected' : ''} ${used ? 'used' : ''}"
        @click=${() => this._select(stateObj.entity_id)}
      >
        <div class="labels">
          <div class="name">${friendlyName(stateObj)}</div>
          <div class="entity-id">${stateObj.entity_id}</div>
        </div>
        ${used
          ? html`<span class="note">${tp('already-in-use')}</span>`
          : misfit && this._unfiltered
            ? html`<span class="warn">${tp('measures', { unit: unit || '?' })}</span>`
            : nothing}
      </div>
    `;
  }

  private _renderUnsuitableWarning() {
    if (!this._warnedUnsuitable || !this._selected) return nothing;
    const stateObj = this.hass.states[this._selected];
    if (stateObj && fitsRole(stateObj, this._params.definition.role)) {
      return nothing;
    }
    const unit =
      (stateObj?.attributes as { unit_of_measurement?: string })
        ?.unit_of_measurement ?? tp('unsuitable-fallback-unit');
    return html`
      <ha-alert alert-type="warning" title=${tp('unsuitable-title')}>
        ${tp('unsuitable-body', { unit })}
      </ha-alert>
    `;
  }

  private _renderHelp(isPower: boolean) {
    return html`
      <details class="help">
        <summary>${tp('help-summary')}</summary>
        <div class="help-body">
          ${isPower
            ? html`
                <p>${unsafeHTML(tp('help-power-1'))}</p>
                <p>${tp('help-power-2')}</p>
              `
            : html`
                <p>${unsafeHTML(tp('help-meter-1'))}</p>
                <p>${tp('help-meter-2')}</p>
              `}
        </div>
      </details>
    `;
  }

  private _select(entityId: string) {
    this._selected = entityId;
    this._warnedUnsuitable = false;
    this._triedConfirmEmpty = false;
  }

  private _confirm() {
    if (!this._selected) {
      this._triedConfirmEmpty = true;
      return;
    }
    const stateObj = this.hass.states[this._selected];
    const suitable =
      stateObj && fitsRole(stateObj, this._params.definition.role);
    if (!suitable && !this._warnedUnsuitable) {
      this._warnedUnsuitable = true; // first OK explains; second OK proceeds
      return;
    }
    this._params.onSelect(this._selected);
    this.closeDialog();
  }

  static styles = [
    styles,
    css`
      .subtitle {
        color: var(--secondary-text-color);
        margin: 0 0 12px;
      }
      .icon-btn {
        background: none;
        border: none;
        cursor: pointer;
        color: var(--secondary-text-color);
        font-size: 1em;
        padding: 0 4px;
      }
      .info {
        color: var(--secondary-text-color);
        --mdc-icon-size: 20px;
        display: inline-flex;
        align-items: center;
        cursor: help;
      }
      .filters {
        display: flex;
        gap: 8px;
        margin: 12px 0;
      }
      .chip {
        border: 1px solid var(--divider-color);
        border-radius: 16px;
        padding: 4px 12px;
        background: none;
        color: var(--primary-text-color);
        cursor: pointer;
        font-size: 0.85em;
      }
      .chip.active {
        border-color: var(--primary-color);
        color: var(--primary-color);
        background: color-mix(in srgb, var(--primary-color) 12%, transparent);
      }
      .list {
        border: 1px solid var(--divider-color);
        border-radius: 12px;
        overflow: hidden auto;
        max-height: 320px;
      }
      .candidate {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 10px 14px;
        cursor: pointer;
        border-top: 1px solid var(--divider-color);
      }
      .candidate:first-child {
        border-top: none;
      }
      .candidate.selected {
        background: color-mix(in srgb, var(--primary-color) 10%, transparent);
      }
      .candidate.used {
        opacity: 0.5;
      }
      .labels {
        flex: 1;
        min-width: 0;
      }
      .name {
        font-size: 0.95em;
      }
      .entity-id {
        font-family: var(--code-font-family, monospace);
        font-size: 0.7em;
        color: var(--secondary-text-color);
      }
      .note {
        font-size: 0.8em;
        color: var(--secondary-text-color);
      }
      .warn {
        font-size: 0.8em;
        color: var(--warning-color);
      }
      .empty {
        padding: 16px;
        color: var(--secondary-text-color);
      }
      .help {
        margin-top: 12px;
        font-size: 0.85em;
        color: var(--secondary-text-color);
      }
      .help summary {
        cursor: pointer;
        color: var(--primary-color);
      }
    `,
  ];
}
