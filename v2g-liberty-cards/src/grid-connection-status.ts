// Status model for the grid connection settings (redesign, Fase 1).
//
// Design: `.private/UX design/Grid connection flow/README.md` — "Status
// vocabulary". Everything here is derived from `hass.states`; there is no
// backend service that watches whether a cumulative meter reading keeps
// increasing, so meter-reading roles deliberately have no waiting/stale status
// (they report `unmonitored` once set).
//
// Filtering is on attributes (device_class, unit, state_class), never on the
// entity id text: Home Assistant generates entity ids from the default name in
// the user's language, so a Dutch install yields
// `sensor.slimme_meter_stroomverbruik_fase_l2`.

import { HomeAssistant } from 'custom-card-helpers';
import { HassEntity } from 'home-assistant-js-websocket';

export type SensorStatus =
  | 'ok' // reported at least once
  | 'waiting' // selected, nothing reported yet
  | 'not_set' // no entity selected
  | 'wrong_type' // unit / device class does not fit the role
  | 'stale' // reported nothing for a long time
  | 'unmonitored'; // selected, but nothing watches it (meter readings)

export type SensorRole = 'power' | 'meter_reading';

/** How long without a state change before a power sensor counts as stale. */
export const STALE_AFTER_MS = 24 * 60 * 60 * 1000;

/**
 * One configurable sensor slot. A light internal helper only — it is never
 * stored or transported; the card derives roles from the existing settings
 * lists (consumption/production entities and registers).
 */
export interface RoleDefinition {
  /** Stable key, used for the strings.json copy. */
  key: string;
  role: SensorRole;
  /** Short badge, e.g. 'L1', '↓', '↑'. */
  badge: string;
  entityId: string | null;
}

/** Does this entity's unit / class fit the role it is being used for? */
export function fitsRole(stateObj: HassEntity, role: SensorRole): boolean {
  const attrs = stateObj.attributes as {
    unit_of_measurement?: string;
    device_class?: string;
    state_class?: string;
  };
  const unit = (attrs.unit_of_measurement ?? '').toLowerCase();

  if (role === 'power') {
    return attrs.device_class === 'power' && (unit === 'w' || unit === 'kw');
  }
  return (
    attrs.device_class === 'energy' &&
    (unit === 'wh' || unit === 'kwh' || unit === 'mwh') &&
    attrs.state_class === 'total_increasing'
  );
}

export function statusOf(
  hass: HomeAssistant,
  definition: RoleDefinition
): SensorStatus {
  if (!definition.entityId) return 'not_set';

  const stateObj = hass.states[definition.entityId] as HassEntity | undefined;
  if (!stateObj) return 'wrong_type'; // entity gone or disabled

  if (!fitsRole(stateObj, definition.role)) return 'wrong_type';

  if (definition.role === 'meter_reading') {
    // A standing-still reading is normal: only the active tariff moves, and
    // nothing monitors a cumulative register, so there is no live status.
    return 'unmonitored';
  }

  if (stateObj.state === 'unknown' || stateObj.state === 'unavailable') {
    return 'waiting';
  }

  const changed = new Date(stateObj.last_changed).getTime();
  if (Number.isFinite(changed) && Date.now() - changed > STALE_AFTER_MS) {
    return 'stale';
  }

  return 'ok';
}

export function friendlyName(stateObj: HassEntity): string {
  return (
    (stateObj.attributes as { friendly_name?: string }).friendly_name ??
    stateObj.entity_id
  );
}

/**
 * Candidate entities for a role, filtered on attributes only. Entities already
 * used by another role stay in the list (sorted last) so the caller can show
 * them dimmed as "already in use" rather than hiding them.
 */
export function candidatesFor(
  hass: HomeAssistant,
  role: SensorRole,
  inUse: string[] = []
): HassEntity[] {
  return Object.values(hass.states)
    .filter(stateObj => stateObj.entity_id.startsWith('sensor.'))
    .filter(stateObj => fitsRole(stateObj, role))
    .sort((a, b) => {
      const aUsed = inUse.includes(a.entity_id) ? 1 : 0;
      const bUsed = inUse.includes(b.entity_id) ? 1 : 0;
      if (aUsed !== bUsed) return aUsed - bUsed;
      return friendlyName(a).localeCompare(friendlyName(b));
    });
}

/** Roll a set of role statuses up into one card-level status. */
export function aggregateStatus(
  statuses: SensorStatus[]
): 'ok' | 'problem' | 'incomplete' | 'not_set' {
  if (statuses.length === 0 || statuses.every(s => s === 'not_set')) {
    return 'not_set';
  }
  if (statuses.some(s => s === 'stale' || s === 'wrong_type')) return 'problem';
  if (statuses.some(s => s === 'not_set' || s === 'waiting')) return 'incomplete';
  return 'ok';
}
