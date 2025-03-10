### V2G Liberty constants ###

######################## Feature Settings Card (FSC) ########################
# Many of these constants do not have to be set here but should be
# passed to the (re-) init method of a specific class. This is mentioned
# a comment marked by FSC (Feature Settings Card).
#############################################################################


# For showing dates in UI
# FSC: Now in 3 modules, should be used wider.
DATE_TIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"

# Timezone, untyped here..
# FSC: Used in 4 modules. Keep here.
TZ = ""

# FSC: Only used in v2g_liberty module, move there.
HA_NAME: str = ""

# FSC: Next 2, Only used in V2G Liberty module, move there.
NOTIFICATION_RECIPIENTS: list = []
ADMIN_MOBILE_NAME: str = ""

# FSC, only used here
ADMIN_MOBILE_PLATFORM: str = ""

# FSC: holds knowledge of HA specifics, based on previous, move to v2g_liberty module and
# pass previous to that module
PRIORITY_NOTIFICATION_CONFIG: str = ""

# FSC: Next 3 only used in get_fm_data, move there
# FM in some cases returns gross prices that need conversion for the UI.
# VAT and Markup are initialised with "no effect value".
USE_VAT_AND_MARKUP: bool = False
ENERGY_PRICE_VAT: int = 0
# Usually a markup per kWh for transport and sustainability
ENERGY_PRICE_MARKUP_PER_KWH: int = 0


# USER PREFERENCE
# See remark for charger constants
# Battery protection boundaries
# A hard setting that is always respected (and used for Max_Charge_Now when
# car is connected with a SoC below this value)
# Defaults to 20 (to be safe)
# FSC: Used in several modules, keep here for now.
CAR_MIN_SOC_IN_PERCENT: int = 20
# Derived from above setting and CAR_MAX_CAPACITY_IN_KWH
# FSC: Used in fm_client only, move there.
CAR_MIN_SOC_IN_KWH: float = 0

# A 'soft' setting, that is respected during normal cycling but is ignored when
# a calendar item requires a higher SoC.
# Defaults to 80% (to be safe)
# FSC: Used only in v2g_liberty module, move there? See next...
CAR_MAX_SOC_IN_PERCENT: int = 80
# Derived from above setting and CAR_MAX_CAPACITY_IN_KWH
# FSC: Used in v2g_liberty and fm_client module, keep here?
CAR_MAX_SOC_IN_KWH: float = 0

# A practical absolute max. capacity of the car battery in percent.
# E.g. a Quasar + Nissan Leaf will never charge higher than 97%.
# FSC: Used only in v2g_liberty module, move there.
CAR_MAX_CAPACITY_IN_PERCENT: int = 97

# Car consumption per km. Defaults to the Nissan Leaf average.
# FSC: Used only in v2g_liberty module, move there.
CAR_CONSUMPTION_WH_PER_KM: int = 175
CAR_MAX_RANGE_IN_KM: int = 1

# Average distance travelled for every hour that a calendar item lasts
# E.g. with value of 20km and a duration of 2 hours estimated distance is 40km
# From this we derive an average usage.
# TODO: Maybe make this a user setting?
# FSC: Not used anywhere yet...
KM_PER_HOUR_OF_CALENDAR_ITEM: int = 20

# Assumed car consumption during a calendar event per time_interval, calculate as follows:
# (KM_PER_HOUR_OF_CALENDAR_ITEM * CAR_CONSUMPTION_WH_PER_KM / 1000) / (60 / FM_EVENT_RESOLUTION_IN_MINUTES)
# FSC: Expected to be only used in fm_client. Move this and previous there.
USAGE_PER_EVENT_TIME_INTERVAL: float = None

# Duration in hours, defaults to 4 should be between 2 and 12 hours
# FSC: Used in v2g_liberty and fm_client module, keep here?
ALLOWED_DURATION_ABOVE_MAX_SOC: int = 4

# FSC: Used in v2g_liberty and amber (why not Octopus?) module, keep here?
OPTIMISATION_MODE: str = "price"

# FSC: Used in amber and Octopus module, keep here?
ELECTRICITY_PROVIDER: str = "nl_generic"
# FSC: Used in get_fm_data, Octopus (why not Amber?) module, keep here?
EMISSIONS_UOM: str = "kg/MWh"  # For some ELECTRICITY_PROVIDER-s this can be %
# For some ELECTRICITY_PROVIDER-s this can be different, e.g. GBP or AUD.
# FSC: Used in get_fm_data, Octopus and Amber module, keep here?
CURRENCY: str = "EUR"
# FSC: A fixed value for Octopus and Amber modules, move there.
PRICE_RESOLUTION_MINUTES: int = 60  # For some ELECTRICITY_PROVIDER-s this can be 30

# FlexMeasures settings

# This represents how often schedules should refresh. Keep at this setting.
# FSC: Used many modules, keep here
FM_EVENT_RESOLUTION_IN_MINUTES: int = 5

# Should be timedelta but do not want to import that here, see globals.
# FSC: Used many modules, keep here
EVENT_RESOLUTION: object

# CONSTANTS for FM URL's
# FSC: Used in fm_client only, move there.
FM_BASE_URL = "https://seita.energy"
# Based on ELECTRICITY_PROVIDER
# FSC: Used in fm_client only, move there.
FM_OPTIMISATION_CONTEXT: dict = {}

# Utility context
# The utility (or electricity provider) are represented by different sensor's.
# These sensor's determine to which signal the schedules are optimised.
# These are the also used for fetching data from FM to show in the graph.
# FSC: Used in v2g_globals only, move there.
DEFAULT_UTILITY_CONTEXTS = {
    "nl_generic": {
        "consumption-sensor": 14,
        "production-sensor": 14,
        "emissions-sensor": 27,
        "display-name": "EPEX Day ahead NL",
    },
    "nl_anwb_energie": {
        "consumption-sensor": 60,
        "production-sensor": 71,
        "emissions-sensor": 27,
        "display-name": "ANWB Energie",
    },
    "nl_greenchoice": {
        "consumption-sensor": 129,
        "production-sensor": 130,
        "emissions-sensor": 27,
        "display-name": "Greenchoice",
    },
    "nl_next_energy": {
        "consumption-sensor": 90,
        "production-sensor": 91,
        "emissions-sensor": 27,
        "display-name": "NextEnergy",
    },
    "nl_tibber": {
        "consumption-sensor": 58,
        "production-sensor": 70,
        "emissions-sensor": 27,
        "display-name": "Tibber",
    },
    "no_generic": {
        "consumption-sensor": 14,
        "production-sensor": 14,
        "emissions-sensor": 27,
        "display-name": "EPEX Day ahead NO",
    },
}

# FM ACCOUNT CONSTANTS
# These don't have defaults.
# They should asap be overwritten by v2g_globals with value from HA settings entities
# FSC: Used in fm_client only, move there.
FM_ACCOUNT_USERNAME: str = ""
FM_ACCOUNT_PASSWORD: str = ""

# Sensor entity for sending and id for retrieving data to/from FM
# FSC: Used in fm_client, get_fm_data, data_monitor, keep here.
FM_ACCOUNT_POWER_SENSOR_ID: int = 0

# FSC: Used in data_monitor only, move there.
FM_ACCOUNT_AVAILABILITY_SENSOR_ID: int = 0
FM_ACCOUNT_SOC_SENSOR_ID: int = 0

# FSC: Used in get_fm_data only, move there.
FM_ACCOUNT_COST_SENSOR_ID: int = 0

# Sensors for optimisation context, also in case prices are self_provided (e.g. au_amber_electric)
# Sensor entity for sending and id for retrieving data to/from FM
# FSC: Used in fm_client, get_fm_data, octopus/amber, keep here.
FM_PRICE_PRODUCTION_SENSOR_ID: int = 0
FM_PRICE_CONSUMPTION_SENSOR_ID: int = 0
FM_EMISSIONS_SENSOR_ID: int = 0

# FSC: Used in v2g_liberty only, move there.
UTILITY_CONTEXT_DISPLAY_NAME: int = 0


# The sensor (or entity) id's to which the third party integration
# writes the Consumption- and Production Price (Forecasts)
# FSC: Used in amber module only, move there.
HA_OWN_CONSUMPTION_PRICE_ENTITY_ID: str = ""
HA_OWN_PRODUCTION_PRICE_ENTITY_ID: str = ""

# Octopus Energy Agile contracts related settings:
# FSC: Used in octopus module only, move there.
OCTOPUS_IMPORT_CODE: str = ""
OCTOPUS_EXPORT_CODE: str = ""
GB_DNO_REGION: str = ""

# CHARGER CONSTANTS
# IP address and port for charger modbus communication
# FSC: Used in evse module only, move there.
CHARGER_HOST_URL: str = ""
CHARGER_PORT: int = 502

# Directly derived from CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY, used in flexmeasures client
# FSC: Used in fm_client, v2g_liberty, keep here.
ROUNDTRIP_EFFICIENCY_FACTOR: float = 0.85
# Defaults to 85, used in settings UI
CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY: int = 85

# Defaults to min current setting of 6A * 230V = 1380W
# FSC: Used in fm_client, v2g_liberty, evse_client keep here.
CHARGER_MAX_CHARGE_POWER: int = 1380
CHARGER_MAX_DISCHARGE_POWER: int = 1380

# CAR CONSTANTS
# See remark for charger constants
# Defaults to 24 (to be safe)
# FSC: Used in fm_client, v2g_liberty, keep here.
CAR_MAX_CAPACITY_IN_KWH: int = 24

# CALENDAR CONSTANTS
# FSC: Used in reservations_client only, move there.
CALENDAR_ACCOUNT_INIT_URL: str = ""
CALENDAR_ACCOUNT_USERNAME: str = ""
CALENDAR_ACCOUNT_PASSWORD: str = ""
CAR_CALENDAR_NAME: str = ""
CAR_CALENDAR_SOURCE: str = ""
INTEGRATION_CALENDAR_ENTITY_NAME: str = ""
