import { HomeAssistant } from 'custom-card-helpers';
import { html, nothing } from 'lit';
import * as entityIds from '../entity-ids';
import { partial } from './translate';

const REQUIRED_ENTITY_IDS = [
  entityIds.scheduleSettingsInitialised,
  entityIds.adminSettingsInitialised,
  entityIds.calendarSettingsInitialised,
  entityIds.electricityContractSettingsInitialised,
  entityIds.chargerSettingsInitialised,
];

const tp = partial('settings-alert-dialog');

/**
 * Returns a list of uninitialized entity IDs as an HTML list.
 * @param hass Home Assistant instance
 */
export function renderUninitializedEntitiesList(hass: HomeAssistant) {
  const entities = REQUIRED_ENTITY_IDS
    .map(id => hass.states[id])
    .filter(Boolean);

  const uninitializedEntities = entities.filter(entity => entity?.state === 'off');

  if (uninitializedEntities.length === 0) {
    return nothing;
  }

  return html`
    <ul>
      ${uninitializedEntities.map(entity => {
        const localizedName = tp(`entity_names.${entity.entity_id}`);
        return html`<li>${localizedName}</li>`;
      })}
    </ul>
  `;
}

/**
 * Checks if any of the required entities are uninitialized.
 * @param hass Home Assistant instance
 * @returns True if any entity is uninitialized, false otherwise.
 */
export function hasUninitializedEntities(hass: HomeAssistant): boolean {
  const entities = REQUIRED_ENTITY_IDS
    .map(id => hass.states[id])
    .filter(Boolean);

  return entities.some(entity => entity?.state === 'off');
}
