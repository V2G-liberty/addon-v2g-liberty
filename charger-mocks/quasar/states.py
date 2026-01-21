"""
Wallbox Quasar Charger State Definitions

This module defines the possible states for the Wallbox Quasar charger.
These states are read from the CHARGER_STATE_REGISTER (537).

For V2G Liberty development: https://github.com/V2G-liberty/addon-v2g-liberty
"""

# Charger State Definitions
# Key: State code (register value)
# Value: Human-readable state description
CHARGER_STATES = {
    0: 'No car connected',
    1: 'Charging',
    2: 'Connected: waiting for car demand',
    3: 'Connected: controlled by Wallbox App',
    4: 'Connected: not charging (paused)',
    5: 'Connected: end of schedule',
    6: 'No car connected and charger locked',
    7: 'Error',
    8: 'Connected: In queue by Power Sharing',
    9: 'Error: Unconfigured Power Sharing System',
    10: 'Connected: In queue by Power Boost (Home uses all available power)',
    11: 'Discharging'
}

# State Code Constants for Programmatic Use
STATE_DISCONNECTED = 0
STATE_CHARGING = 1
STATE_WAITING_FOR_CAR_DEMAND = 2
STATE_APP_CONTROLLED = 3
STATE_PAUSED = 4
STATE_SCHEDULE_ENDED = 5
STATE_LOCKED = 6
STATE_ERROR = 7
STATE_POWER_SHARING_QUEUE = 8
STATE_POWER_SHARING_UNCONFIGURED = 9
STATE_POWER_BOOST_QUEUE = 10
STATE_DISCHARGING = 11
