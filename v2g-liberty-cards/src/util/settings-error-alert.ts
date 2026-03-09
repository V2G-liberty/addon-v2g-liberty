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

/**
 * Connection-status sensors to check after the corresponding settings category
 * has been configured (i.e. the requiresInitId entity is 'on'). If the sensor
 * state is non-empty and not 'Successfully connected', a connection error is
 * shown in the alert dialog alongside any unconfigured-settings items.
 */
const CONNECTION_STATUS_SENSORS = [
  {
    sensorId: entityIds.fmConnectionStatus,
    requiresInitId: entityIds.scheduleSettingsInitialised,
  },
  {
    sensorId: entityIds.calendarAccountConnectionStatus,
    requiresInitId: entityIds.calendarSettingsInitialised,
  },
];

const tp = partial('settings-alert-dialog');

function hasConnectionErrors(hass: HomeAssistant): boolean {
  return CONNECTION_STATUS_SENSORS.some(({ sensorId, requiresInitId }) => {
    if (hass.states[requiresInitId]?.state !== 'on') return false;
    const state = hass.states[sensorId]?.state;
    return !!state && state !== 'Successfully connected';
  });
}

/**
 * Returns a list of uninitialized entity IDs as an HTML list.
 * @param hass Home Assistant instance
 */
export function renderUninitializedEntitiesList(hass: HomeAssistant) {
  const entities = REQUIRED_ENTITY_IDS
    .map(id => hass.states[id])
    .filter(Boolean);

  const uninitializedEntities = entities.filter(entity => entity?.state === 'off');

  const connectionErrorSensors = CONNECTION_STATUS_SENSORS.filter(({ sensorId, requiresInitId }) => {
    if (hass.states[requiresInitId]?.state !== 'on') return false;
    const state = hass.states[sensorId]?.state;
    return !!state && state !== 'Successfully connected';
  });

  if (uninitializedEntities.length === 0 && connectionErrorSensors.length === 0) {
    return nothing;
  }

  return html`
    <ul>
      ${uninitializedEntities.map(entity => {
        const localizedName = tp(`entity_names.${entity.entity_id}`);
        return html`<li>${localizedName}</li>`;
      })}
      ${connectionErrorSensors.map(({ sensorId }) => {
        const localizedName = tp(`entity_names.${sensorId}`);
        return html`<li>${localizedName}</li>`;
      })}
    </ul>
  `;
}

/**
 * Checks if any of the required entities are uninitialized, or if any
 * connection-status sensors indicate a login/connection failure.
 * @param hass Home Assistant instance
 * @returns True if any entity is uninitialized or any connection has failed.
 */
export function hasUninitializedEntities(hass: HomeAssistant): boolean {
  const entities = REQUIRED_ENTITY_IDS
    .map(id => hass.states[id])
    .filter(Boolean);

  return entities.some(entity => entity?.state === 'off') || hasConnectionErrors(hass);
}
