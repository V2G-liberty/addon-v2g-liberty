// Bridge between the stored grid connection settings and the role/status model
// (redesign, Fase 3 plumbing).
//
// "Roles" are a light internal helper only — storage stays the four existing
// lists (consumption/production entities and registers). These functions derive
// RoleDefinitions from those lists for rendering, and map a role key back to the
// list + index the user's choice must be written to.

import { RoleDefinition } from './grid-connection-status';

/** Which stored list a role writes to, and at which index. */
export interface RoleTarget {
  list:
    | 'consumption_entities'
    | 'production_entities'
    | 'consumption_registers'
    | 'production_registers';
  index: number;
}

/** Power roles: one consumption and one production role per phase. */
export function powerRoles(
  phases: number,
  consumption: string[],
  production: string[]
): RoleDefinition[] {
  const roles: RoleDefinition[] = [];
  for (let phase = 1; phase <= phases; phase++) {
    roles.push({
      key: `consumption_l${phase}`,
      role: 'power',
      badge: `L${phase}`,
      entityId: consumption[phase - 1] || null,
    });
  }
  for (let phase = 1; phase <= phases; phase++) {
    roles.push({
      key: `production_l${phase}`,
      role: 'power',
      badge: `L${phase}`,
      entityId: production[phase - 1] || null,
    });
  }
  return roles;
}

/**
 * Meter-reading roles: import and export, tariff 1 and 2 (always dual).
 *
 * The rows are grouped by direction (import/export) in the dialog, so the badge
 * is the within-group distinguisher — the tariff number — mirroring how the
 * power roles carry the phase (L1/L2/L3) as their badge.
 */
export function meterRoles(
  consumptionRegisters: string[],
  productionRegisters: string[]
): RoleDefinition[] {
  return [
    {
      key: 'import_t1',
      role: 'meter_reading',
      badge: 'T1',
      entityId: consumptionRegisters[0] || null,
    },
    {
      key: 'import_t2',
      role: 'meter_reading',
      badge: 'T2',
      entityId: consumptionRegisters[1] || null,
    },
    {
      key: 'export_t1',
      role: 'meter_reading',
      badge: 'T1',
      entityId: productionRegisters[0] || null,
    },
    {
      key: 'export_t2',
      role: 'meter_reading',
      badge: 'T2',
      entityId: productionRegisters[1] || null,
    },
  ];
}

/**
 * Map a role key to the stored settings list + index the choice must be written
 * to. Note the two kinds of list: `*_entities` hold per-phase POWER sensors
 * (W/kW), `*_registers` hold cumulative ENERGY meter registers (kWh).
 */
export function roleTarget(key: string): RoleTarget | null {
  const power = key.match(/^(consumption|production)_l(\d+)$/);
  if (power) {
    return {
      list:
        power[1] === 'consumption'
          ? 'consumption_entities'
          : 'production_entities',
      index: Number(power[2]) - 1,
    };
  }
  const meter = key.match(/^(import|export)_t(\d+)$/);
  if (meter) {
    return {
      list:
        meter[1] === 'import'
          ? 'consumption_registers'
          : 'production_registers',
      index: Number(meter[2]) - 1,
    };
  }
  return null;
}

/** All non-empty selected entity ids across the given role lists. */
export function selectedEntityIds(
  ...roleLists: RoleDefinition[][]
): string[] {
  return roleLists
    .flat()
    .map(r => r.entityId)
    .filter((e): e is string => !!e);
}
