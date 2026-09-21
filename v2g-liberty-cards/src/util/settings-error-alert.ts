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

// States that carry no information about the connection. V2G Liberty writes
// these sensors with set_state, so Home Assistant does not restore them: after
// an HA restart they are 'unknown', then '' for a few seconds, until the app
// rewrites them. Reading that as "not connected" made the blocking dialog open
// on every HA restart -- and by the time it rendered, the sensor had moved on
// and the list was empty.
const NO_INFORMATION_STATES = ['', 'unknown', 'unavailable'];

/**
 * The connection sensors that report an actual error, for a settings category
 * that has been configured. One implementation, used both to decide whether to
 * warn and to render what is wrong, so the two can never disagree.
 */
function connectionErrorSensors(hass: HomeAssistant) {
  return CONNECTION_STATUS_SENSORS.filter(({ sensorId, requiresInitId }) => {
    if (hass.states[requiresInitId]?.state !== 'on') return false;
    const state = hass.states[sensorId]?.state;
    if (!state || NO_INFORMATION_STATES.includes(state)) return false;
    return state !== 'Successfully connected';
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

  const errorSensors = connectionErrorSensors(hass);

  if (uninitializedEntities.length === 0 && errorSensors.length === 0) {
    return nothing;
  }

  return html`
    <ul>
      ${uninitializedEntities.map(entity => {
        const localizedName = tp(`entity_names.${entity.entity_id}`);
        return html`<li>${localizedName}</li>`;
      })}
      ${errorSensors.map(({ sensorId }) => {
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

  return (
    entities.some(entity => entity?.state === 'off') ||
    connectionErrorSensors(hass).length > 0
  );
}
