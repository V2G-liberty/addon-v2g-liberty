### V2G Liberty constants ###

# For showing dates in UI
DATE_TIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"
# Timezone, untyped here..
TZ = ""

HA_NAME: str = ""

NOTIFICATION_RECIPIENTS: list = []
ADMIN_MOBILE_NAME: str = ""
ADMIN_MOBILE_PLATFORM: str = ""
PRIORITY_NOTIFICATION_CONFIG: str = ""

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
CAR_MIN_SOC_IN_PERCENT: int = 20
# Derived from above setting and CAR_MAX_CAPACITY_IN_KWH
CAR_MIN_SOC_IN_KWH: float = 0

# A 'soft' setting, that is respected during normal cycling but is ignored when
# a calendar item requires a higher SoC.
# Defaults to 80% (to be safe)
CAR_MAX_SOC_IN_PERCENT: int = 80
# Derived from above setting and CAR_MAX_CAPACITY_IN_KWH
CAR_MAX_SOC_IN_KWH: float = 0

# A practical absolute max. capacity of the car battery in percent.
# E.g. a Quasar + Nissan Leaf will never charge higher than 97%.
CAR_MAX_CAPACITY_IN_PERCENT: int = 97

# Car consumption per km. Defaults to the Nissan Leaf average.
CAR_CONSUMPTION_WH_PER_KM: int = 175

# Average distance travelled for every hour that a calendar item lasts
# E.g. with value of 20km and a duration of 2 hours estimated distance is 40km
# From this we derive an average usage.
# TODO: Maybe make this a user setting?
KM_PER_HOUR_OF_CALENDAR_ITEM: int = 20

# Assumed car consumption during a calendar event per time_interval, calculate as follows:
# (KM_PER_HOUR_OF_CALENDAR_ITEM * CAR_CONSUMPTION_WH_PER_KM / 1000) / (60 / FM_EVENT_RESOLUTION_IN_MINUTES)
USAGE_PER_EVENT_TIME_INTERVAL: float = None

# Duration in hours, defaults to 12 should be between 2 and 36 hours
ALLOWED_DURATION_ABOVE_MAX_SOC: int = 12

OPTIMISATION_MODE: str = "price"
ELECTRICITY_PROVIDER: str = "nl_generic"
EMISSIONS_UOM: str = "kg/MWh"    # For some ELECTRICITY_PROVIDER-s this can be %
CURRENCY: str = "EUR"            # For some ELECTRICITY_PROVIDER-s this can be different, e.g. GBP or AUD.
PRICE_RESOLUTION_MINUTES: int = 60  # For some ELECTRICITY_PROVIDER-s this can be 30

# FlexMeasures settings

# This represents how often schedules should refresh. Keep at this setting.
FM_EVENT_RESOLUTION_IN_MINUTES: int = 5
EVENT_RESOLUTION: object # Should be timedelta but do not want to import that here, see globals.

# CONSTANTS for FM URL's
FM_BASE_URL = "https://seita.energy"
# Based on ELECTRICITY_PROVIDER
FM_OPTIMISATION_CONTEXT: dict = {}

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
# These don't have defaults.
# They should asap be overwritten by v2g_globals with value from HA settings entities
FM_ACCOUNT_USERNAME: str = ""
FM_ACCOUNT_PASSWORD: str = ""

# Sensor entity for sending and id for retrieving data to/from FM
FM_ACCOUNT_POWER_SENSOR_ID: int = 0
FM_ACCOUNT_AVAILABILITY_SENSOR_ID: int = 0
FM_ACCOUNT_SOC_SENSOR_ID: int = 0
FM_ACCOUNT_COST_SENSOR_ID: int = 0

# Sensors for optimisation context, also in case prices are self_provided (e.g. au_amber_electric)
# Sensor entity for sending and id for retrieving data to/from FM
FM_PRICE_PRODUCTION_SENSOR_ID: int = 0
FM_PRICE_CONSUMPTION_SENSOR_ID: int = 0
FM_EMISSIONS_SENSOR_ID: int = 0
UTILITY_CONTEXT_DISPLAY_NAME: int = 0


# The sensor (or entity) id's to which the third party integration
# writes the Consumption- and Production Price (Forecasts)
HA_OWN_CONSUMPTION_PRICE_ENTITY_ID: str = ""
HA_OWN_PRODUCTION_PRICE_ENTITY_ID: str = ""

# Octopus Energy Agile contracts related settings:
OCTOPUS_IMPORT_CODE: str = ""
OCTOPUS_EXPORT_CODE: str = ""
GB_DNO_REGION: str = ""

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
