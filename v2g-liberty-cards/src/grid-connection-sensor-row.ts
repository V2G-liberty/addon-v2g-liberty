// One sensor row for the grid connection flow (redesign, Fase 2).
//
// Layout: [badge] [friendly name / entity id] [LIVE status] [Change / Choose].
// Rendered as a plain helper (not a custom element) so it lives inside the
// dialog's light DOM and re-renders with the dialog when `hass` updates — that
// is what makes the status "live" (spinner → green check) without a separate
// subscription.
//
// The entity id is never the only label: friendly name first, id underneath in
// monospace. Step 4 (meter readings) passes `showStatus: false` — nothing
// monitors a cumulative register, so no status is shown there.

import { html, nothing, TemplateResult } from 'lit';
import { HomeAssistant } from 'custom-card-helpers';

import {
  RoleDefinition,
  SensorStatus,
  statusOf,
  friendlyName,
} from './grid-connection-status';
import { partial } from './util/translate';

const tp = partial('settings.grid-connection');

export interface SensorRowOptions {
  /** Show the LIVE status column. Default true; step 4 sets it false. */
  showStatus?: boolean;
  /** Called when the user activates Change / Choose sensor for this row. */
  onChoose: (definition: RoleDefinition) => void;
}

/** A single status icon — reused by the row and the Done summary. */
export function renderStatusIcon(status: SensorStatus): TemplateResult | symbol {
  switch (status) {
    case 'ok':
      return html`<ha-icon
        icon="mdi:check-circle"
        title=${tp('status.reporting')}
        style="color: var(--success-color, #4caf50); --mdc-icon-size: 20px;"
      ></ha-icon>`;
    case 'waiting':
      return html`<ha-spinner
        size="small"
        title=${tp('status.waiting')}
      ></ha-spinner>`;
    case 'wrong_type':
      return html`<ha-icon
        icon="mdi:alert-circle"
        title=${tp('status.wrong-type')}
        style="color: var(--error-color, #f44336); --mdc-icon-size: 20px;"
      ></ha-icon>`;
    case 'stale':
      return html`<ha-icon
        icon="mdi:alert-circle"
        title=${tp('status.stale')}
        style="color: var(--error-color, #f44336); --mdc-icon-size: 20px;"
      ></ha-icon>`;
    default:
      // not_set / unmonitored: nothing to show.
      return nothing;
  }
}

export function renderSensorRow(
  hass: HomeAssistant,
  definition: RoleDefinition,
  opts: SensorRowOptions
): TemplateResult {
  const showStatus = opts.showStatus ?? true;
  const status = statusOf(hass, definition);
  const stateObj = definition.entityId
    ? hass.states[definition.entityId]
    : undefined;
  const notSet = status === 'not_set';

  return html`
    <div
      style="display: flex; align-items: center; gap: 12px; padding: 10px 4px; border-top: 1px solid var(--divider-color);"
    >
      <span
        style="flex: 0 0 auto; min-width: 28px; text-align: center; font-size: 0.8em; color: var(--secondary-text-color);"
        >${definition.badge}</span
      >
      <div style="flex: 1; min-width: 0;">
        ${notSet
          ? html`<div style="color: var(--secondary-text-color); font-style: italic;">
              ${tp('done.no-sensor')}
            </div>`
          : html`
              <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                ${stateObj ? friendlyName(stateObj) : definition.entityId}
              </div>
              <div
                style="font-family: var(--code-font-family, monospace); font-size: 0.75em; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
              >
                ${definition.entityId}
              </div>
            `}
      </div>
      ${showStatus
        ? html`<span
            style="flex: 0 0 auto; width: 28px; display: flex; align-items: center; justify-content: center;"
            >${renderStatusIcon(status)}</span
          >`
        : nothing}
      <button
        style="flex: 0 0 auto; width: 108px; text-align: right; white-space: nowrap; background: none; border: none; padding: 4px 0; color: var(--primary-color); cursor: pointer; font-size: 0.95em;"
        @click=${() => opts.onChoose(definition)}
      >
        ${notSet ? tp('row.choose') : tp('row.change')}
      </button>
    </div>
  `;
}
