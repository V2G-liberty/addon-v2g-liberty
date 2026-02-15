// Administrator
export const adminSettingsInitialised =
  'input_boolean.admin_settings_initialised';
export const adminMobileName = 'input_text.admin_mobile_name';
export const adminMobilePlatform = 'input_select.admin_mobile_platform';

// Car
export const usableCapacity = 'input_number.car_max_capacity_in_kwh';
export const roundtripEfficiency =
  'input_number.charger_plus_car_roundtrip_efficiency';
export const carEnergyConsumption = 'input_number.car_consumption_wh_per_km';

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
export const lowerChargeLimit = 'input_number.car_min_soc_in_percent';
export const upperChargeLimit = 'input_number.car_max_soc_in_percent';
export const allowedDurationAboveMax =
  'input_number.allowed_duration_above_max_soc_in_hrs';

// Schedule
export const scheduleSettingsInitialised =
  'input_boolean.schedule_settings_initialised';
export const fmAccountUsername = 'input_text.fm_account_username';
export const fmAccountPassword = 'input_text.fm_account_password';
export const fmUseOtherServer = 'input_boolean.fm_show_option_to_change_url';
export const fmHostUrl = 'input_text.fm_host_url';
export const fmConnectionStatus = 'sensor.fm_connection_status';
export const fmAsset = 'input_text.fm_asset';
