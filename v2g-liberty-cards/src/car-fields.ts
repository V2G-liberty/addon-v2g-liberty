/**
 * The car's values: what they are called, how they are shown and what the
 * backend accepts. Shared by the car card and the car settings dialog, so a
 * limit or a label exists in exactly one place on this side.
 *
 * The limits mirror v2g_globals.CAR_VALUE_SETTINGS (which mirrors the Home
 * Assistant package, pinned by a python test). Deliberately duplicated here:
 * the dialog gives immediate feedback, the backend is the truth and refuses
 * anything outside these bounds.
 */
export interface CarField {
  /** Key in the get_car_settings answer. */
  key: string;
  /** Key in the save_car_settings payload (the backend's own naming). */
  payloadKey: string;
  /** Translation key under settings.car.fields. */
  label: string;
  /** Translation key of the long explanation, under settings.dialogs. */
  help: string;
  icon: string;
  suffix: string;
  min: number;
  max: number;
}

/** Battery and consumption; shown on the details page of the dialog. */
export const CAR_DETAIL_FIELDS: CarField[] = [
  {
    key: 'capacity_kwh',
    payloadKey: 'capacity_kwh',
    label: 'capacity',
    help: 'car-battery-usable-capacity.description',
    icon: 'mdi:battery-high',
    suffix: 'kWh',
    min: 10,
    max: 200,
  },
  {
    key: 'roundtrip_efficiency',
    payloadKey: 'efficiency',
    label: 'efficiency',
    help: 'roundtrip-efficiency.description',
    icon: 'mdi:arrow-u-left-bottom',
    suffix: '%',
    min: 50,
    max: 100,
  },
  {
    key: 'consumption_wh_per_km',
    payloadKey: 'consumption_wh_km',
    label: 'consumption',
    help: 'car-energy-consumption.description',
    icon: 'mdi:gauge-low',
    suffix: 'Wh/km',
    min: 100,
    max: 400,
  },
];

/** The limits the schedule respects; shown on the limits page. */
export const CAR_LIMIT_FIELDS: CarField[] = [
  {
    key: 'min_soc_percent',
    payloadKey: 'min_soc',
    label: 'min-soc',
    help: 'car-battery-lower-charge-limit.description',
    icon: 'mdi:chart-bell-curve-cumulative',
    suffix: '%',
    min: 10,
    max: 55,
  },
  {
    key: 'max_soc_percent',
    payloadKey: 'max_soc',
    label: 'max-soc',
    help: 'car-battery-upper-charge-limit.description',
    icon: 'mdi:chart-sankey',
    suffix: '%',
    min: 60,
    max: 95,
  },
  {
    key: 'allowed_duration_above_max_soc_hrs',
    payloadKey: 'allowed_duration_above_max',
    label: 'allowed-duration',
    help: 'allowed-duration-above-max.description',
    icon: 'mdi:timer-lock-outline',
    suffix: 'h',
    min: 1,
    max: 12,
  },
];

export const CAR_FIELDS: CarField[] = [...CAR_DETAIL_FIELDS, ...CAR_LIMIT_FIELDS];

/** The maximum name length the backend stores. */
export const CAR_NAME_MAX_LENGTH = 40;

/** What get_car_settings answers with. */
export interface CarSettings {
  name: string;
  ev_id: string;
  configured: boolean;
  identifies_car: boolean;
  [value: string]: string | number | boolean;
}

/** Whether a value is a whole number within the field's limits. */
export function isCarValueValid(field: CarField, raw: string): boolean {
  const value = Number(raw);
  return (
    raw !== null &&
    `${raw}`.trim() !== '' &&
    Number.isFinite(value) &&
    Number.isInteger(value) &&
    value >= field.min &&
    value <= field.max
  );
}
