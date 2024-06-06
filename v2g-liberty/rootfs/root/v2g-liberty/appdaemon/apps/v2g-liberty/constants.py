### V2G Liberty constants ###

# Date 2024-06-03 Bug fix #25 Sometimes settings get lost
V2G_LIBERTY_VERSION: str = "0.1.5"
# TODO: Get version number from the add-on itself.

# For showing dates in UI
DATE_TIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"
# Timezone, untyped here..
TZ = ""

NOTIFICATION_RECIPIENTS: list = []
ADMIN_MOBILE_NAME: str = ""
ADMIN_MOBILE_PLATFORM: str = ""
PRIORITY_NOTIFICATION_CONFIG: str = ""

# FM in some cases returns gross prices that need conversion for the UI.
# VAT and Markup are initialised with "no effect value".
ENERGY_PRICE_VAT: int = 0
# Usually a markup per kWh for transport and sustainability
ENERGY_PRICE_MARKUP_PER_KWH: int = 0

# USER PREFERENCE
# See remark for charger constants
# Battery protection boundaries
# A hard setting that is always respected (and used for Max_Charge_Now when
# car is connected with a SoC below this value)
# Defaults to 20 (to be safe)
CAR_MIN_SOC_IN_PERCENT: int = 20
# Derived from above setting and CAR_MAX_CAPACITY_IN_KWH
CAR_MIN_SOC_IN_KWH: float = 0

# A 'soft' setting, that is respected during normal cycling but is ignored when
# a calendar item requires a higher SoC.
# Defaults to 80% (to be safe)
CAR_MAX_SOC_IN_PERCENT: int = 80
# Derived from above setting and CAR_MAX_CAPACITY_IN_KWH
CAR_MAX_SOC_IN_KWH: float = 0

# Car consumption per km. Defaults to the Nissan Leaf average.
CAR_CONSUMPTION_WH_PER_KM: int = 175


# Duration in hours, defaults to 12 should be between 2 and 36 hours
ALLOWED_DURATION_ABOVE_MAX_SOC: int = 12

OPTIMISATION_MODE: str = "price"
ELECTRICITY_PROVIDER: str = "nl_generic"

# FlexMeasures settings

# This represents how often schedules should refresh. Keep at this setting.
FM_EVENT_RESOLUTION_IN_MINUTES = 5

# CONSTANTS for FM URL's
FM_BASE_URL = "https://seita.energy"
FM_API_VERSION = "v3_0"

# Set through globals based on FM_BASE_URL
# TODO: Use fm_client so than many of these do not need to be administered here any more.
FM_BASE_API_URL: str = ""
FM_PING_URL: str = ""
FM_AUTHENTICATION_URL: str = ""
FM_SCHEDULE_URL: str = ""
FM_SCHEDULE_SLUG: str = ""
FM_SCHEDULE_TRIGGER_SLUG: str = ""
FM_GET_DATA_URL: str = ""
FM_GET_DATA_SLUG: str = ""
FM_SET_DATA_URL: str = ""

# Utility context
# The utility (or electricity provider) are represented by different sensor's.
# These sensor's determine to which signal the schedules are optimised.
# These are the also used for fetching data from FM to show in the graph.
# ToDo: Add EPEX NO and Emissions NO sensors
DEFAULT_UTILITY_CONTEXTS = {
    "nl_generic": {"consumption-sensor": 14, "production-sensor": 14, "emissions-sensor": 27, "display-name": "EPEX Day ahead NL"},
    "nl_anwb_energie": {"consumption-sensor": 60, "production-sensor": 71, "emissions-sensor": 27, "display-name": "ANWB Energie"},
    "nl_greenchoice": {"consumption-sensor": 129, "production-sensor": 130, "emissions-sensor": 27, "display-name": "Greenchoice"},
    "nl_next_energy": {"consumption-sensor": 90, "production-sensor": 91, "emissions-sensor": 27, "display-name": "NextEnergy"},
    "nl_tibber": {"consumption-sensor": 58, "production-sensor": 70, "emissions-sensor": 27, "display-name": "Tibber"},
    "no_generic": {"consumption-sensor": 14, "production-sensor": 14, "emissions-sensor": 27,  "display-name": "EPEX Day ahead NO"}
}

# FM ACCOUNT CONSTANTS
# These don't have defaults. To allow a "not-initialised state we use str instead of int.
# They should asap be overwritten by v2g_globals with value from HA settings entities
FM_PRICE_PRODUCTION_SENSOR_ID: int = 0
FM_PRICE_CONSUMPTION_SENSOR_ID: int = 0
FM_EMISSIONS_SENSOR_ID: int = 0
UTILITY_CONTEXT_DISPLAY_NAME: int = 0

FM_ACCOUNT_POWER_SENSOR_ID: int = 0
FM_ACCOUNT_AVAILABILITY_SENSOR_ID: int = 0
FM_ACCOUNT_SOC_SENSOR_ID: int = 0
FM_ACCOUNT_COST_SENSOR_ID: int = 0

# TODO: get from flexmeasures.
FM_BASE_ENTITY_ADDRESS_POWER: str = "ea1.2022-03.nl.seita.flexmeasures:fm1."
FM_BASE_ENTITY_ADDRESS_AVAILABILITY: str = "ea1.2022-03.nl.seita.flexmeasures:fm1."
FM_BASE_ENTITY_ADDRESS_SOC: str = "ea1.2022-04.nl.seita.flexmeasures:fm1."

FM_ACCOUNT_USERNAME: str = ""
FM_ACCOUNT_PASSWORD: str = ""

# CHARGER CONSTANTS
# IP address and port for charger modbus communication
CHARGER_HOST_URL: str = ""
# Default port for charger modbus
CHARGER_PORT: int = 502

# Directly derived from CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY, used in flexmeasures client
ROUNDTRIP_EFFICIENCY_FACTOR: float = 0.85
# Defaults to 85, used in settings UI
CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY: int = 85

# Defaults to min current setting of 6A * 230V = 1380W
CHARGER_MAX_CHARGE_POWER: int = 1380
# Defaults to min current setting of 6A * 230V = 1380W
CHARGER_MAX_DISCHARGE_POWER: int = 1380

# CAR CONSTANTS
# See remark for charger constants
# Defaults to 24 (to be safe)
CAR_MAX_CAPACITY_IN_KWH: int = 24

# CALENDAR CONSTANTS
CALENDAR_ACCOUNT_INIT_URL: str = ""
CALENDAR_ACCOUNT_USERNAME: str = ""
CALENDAR_ACCOUNT_PASSWORD: str = ""
CAR_CALENDAR_NAME: str = ""
CAR_CALENDAR_SOURCE: str = ""
INTEGRATION_CALENDAR_ENTITY_NAME: str = ""
