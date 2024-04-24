### V2G Liberty constants ###

# Date 2024-04-24 Configuration via UI instead of secrets.yaml
V2G_LIBERTY_VERSION: str = "0.1.0"

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
FM_BASE_API_URL = FM_BASE_URL + "/api/"
FM_API_VERSION = "v3_0"

# URL for checking if API is alive
# https://flexmeasures.seita.nl/api/ops/ping
FM_PING_URL = FM_BASE_API_URL + "ops/ping"

# URL for authentication on FM
# https://flexmeasures.seita.nl/api/requestAuthToken
FM_AUTHENTICATION_URL = FM_BASE_API_URL + "requestAuthToken"

# URL for retrieval of the schedules
# https://flexmeasures.seita.nl/api/v3_0/sensors/XX/schedules/trigger
# https://flexmeasures.seita.nl/api/v3_0/sensors/XX/schedules/SI
# Where XX is the sensor_id and SI is the schedule_id
FM_SCHEDULE_URL = FM_BASE_API_URL + FM_API_VERSION + "/sensors/"
FM_SCHEDULE_SLUG = "/schedules/"
FM_SCHEDULE_TRIGGER_SLUG = FM_SCHEDULE_SLUG + "trigger"

# URL for getting data for the chart:
# https://flexmeasures.seita.nl/api/dev/sensor/XX/chart_data/
# Where XX is the sensor_id
FM_GET_DATA_URL = FM_BASE_API_URL + "dev/sensor/"
FM_GET_DATA_SLUG = "/chart_data/"

# URL for sending metering data to FM:
# https://flexmeasures.seita.nl/api/v3_0/sensors/data
FM_SET_DATA_URL = FM_BASE_API_URL + FM_API_VERSION + "/sensors/data"

# Utility context
# The utility (or electricity provider) are represented by different sensor's.
# These sensor's determine to which signal the schedules are optimised.
# These are the also used for fetching data from FM to show in the graph.
# ToDo: Add EPEX NO and Emissions NO sensors
DEFAULT_UTILITY_CONTEXTS = {
    "nl_generic": {"consumption-sensor": 14, "production-sensor": 14, "emissions-sensor": 27, "display-name": "EPEX Day ahead NL"},
    "nl_anwb_energie": {"consumption-sensor": 60, "production-sensor": 71, "emissions-sensor": 27, "display-name": "ANWB Energie"},
    "nl_next_energy": {"consumption-sensor": 90, "production-sensor": 91, "emissions-sensor": 27, "display-name": "NextEnergy"},
    "nl_tibber": {"consumption-sensor": 58, "production-sensor": 70, "emissions-sensor": 27, "display-name": "Tibber"},
    "no_generic": {"consumption-sensor": 14, "production-sensor": 14, "emissions-sensor": 27,  "display-name": "EPEX Day ahead NO"}
}

# FM ACCOUNT CONSTANTS
# These don't have defaults. To allow a "not-initialised state we use str instead of int.
# They should asap be overwritten by v2g_globals with value from HA settings entities
FM_PRICE_PRODUCTION_SENSOR_ID: str = ""
FM_PRICE_CONSUMPTION_SENSOR_ID: str = ""
FM_EMISSIONS_SENSOR_ID: str = ""
UTILITY_CONTEXT_DISPLAY_NAME: str = ""

FM_ACCOUNT_POWER_SENSOR_ID: str = ""
FM_ACCOUNT_AVAILABILITY_SENSOR_ID: str = ""
FM_ACCOUNT_SOC_SENSOR_ID: str = ""
FM_ACCOUNT_COST_SENSOR_ID: str = ""

FM_ACCOUNT_USERNAME: str = ""
FM_ACCOUNT_PASSWORD: str = ""

# CHARGER CONSTANTS
# ToDo:
# These are set by v2g_globals, should be moved there...
# Some have defaults here that should asap be overwritten by v2g_globals with value from secrets.yaml

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
