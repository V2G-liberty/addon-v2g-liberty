// Administrator
export const adminSettingsInitialised =
  'input_boolean.admin_settings_initialised';
export const adminMobileName = 'input_text.admin_mobile_name';
export const adminMobilePlatform = 'input_select.admin_mobile_platform';

// Car
// Whether the car is fully configured. Derived by the backend (a configured
// car without an ID is not finished on a charger that identifies cars) and
// published as a runtime sensor: no helper in the package, so no Home
// Assistant restart is needed to pick it up.
export const carSettingsInitialised = 'sensor.car_settings_initialised';
// Written by the add-on at the end of every start-up. A card that could not
// reach the add-on can watch this to know it is worth asking again.
export const appRebootedAt = 'sensor.last_reboot_at';
// The car's values themselves are no longer entities: they come from
// get_car_settings. One input_number per installation could never hold the
// capacity of a second car.

// Car reservation calendar
export const calendarSettingsInitialised =
  'input_boolean.calendar_settings_initialised';
export const carCalendarSource = 'input_text.car_calendar_source';
// CalDAV
export const calendarAccountUrl = 'input_text.calendar_account_init_url';
export const calendarAccountUsername = 'input_text.calendar_account_username';
export const calendarAccountPassword = 'input_text.calendar_account_password';
export const calendarAccountConnectionStatus =
  'sensor.calendar_account_connection_status';
export const carCalendarName = 'input_text.car_calendar_name';
// Home Assistant local Integration
export const integrationCalendarEntityName =
  'input_text.integration_calendar_entity_name';

// Charger
export const chargerSettingsInitialised =
  'input_boolean.charger_settings_initialised';
export const chargerHostname = 'input_text.charger_host_url';
export const chargerPort = 'input_number.charger_port';
export const chargerConnectionStatus = 'sensor.charger_connection_status';
export const chargerMaxAvailablePower =
  'sensor.charger_max_available_power';
export const useReducedMaxChargePower =
  'input_boolean.use_reduced_max_charge_power';
export const chargerMaxChargingPower =
  'input_number.charger_max_charging_power';
export const chargerMaxDischargingPower =
  'input_number.charger_max_discharging_power';
export const quasarLoadBalancerLimit =
  'sensor.quasar_loadbalancer_limit';
export const chargerType = 'input_text.charger_type';

// Electricity contract
export const electricityContractSettingsInitialised =
  'input_boolean.electricity_contract_settings_initialised';
export const electricityContract = 'input_select.electricity_provider';
// nl_generic only:
export const energyPriceVat = 'input_number.energy_price_vat';
export const energyPriceMarkup = 'input_number.energy_price_markup_per_kwh';
// au_amber_electric only:
export const ownConsumptionPriceEntityId =
  'input_text.own_consumption_price_entity_id';
export const ownProductionPriceEntityId =
  'input_text.own_production_price_entity_id';
// gb_octopus_energy only:
export const octopusImportCode = 'input_text.octopus_import_code';
export const octopusExportCode = 'input_text.octopus_export_code';
export const gbDnoRegion = 'input_select.gb_dno_region';

// Optimisation
export const optimisationMode = 'input_select.optimisation_mode';
// The scheduling limits moved to the car: they belong to the car, not to the
// installation, and different cars can have different limits.

// Homepage stats
export const chargedTodayKwh = 'sensor.v2g_liberty_charged_today_kwh';
export const chargeCostToday = 'sensor.v2g_liberty_charge_cost_today';
export const dischargedTodayKwh = 'sensor.v2g_liberty_discharged_today_kwh';
export const dischargeRevenueToday =
  'sensor.v2g_liberty_discharge_revenue_today';

// Schedule
export const scheduleSettingsInitialised =
  'input_boolean.schedule_settings_initialised';
export const fmAccountUsername = 'input_text.fm_account_username';
export const fmAccountPassword = 'input_text.fm_account_password';
export const fmUseOtherServer = 'input_boolean.fm_show_option_to_change_url';
export const fmHostUrl = 'input_text.fm_host_url';
export const fmConnectionStatus = 'sensor.fm_connection_status';
export const fmAsset = 'input_text.fm_asset';
