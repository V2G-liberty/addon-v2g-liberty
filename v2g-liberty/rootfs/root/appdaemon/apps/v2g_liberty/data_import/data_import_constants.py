"""Constants for the data_import module.

These constants are used across the data_import module for scheduling
price, emission, and cost data retrieval from FlexMeasures.
"""

# When to start checking for prices. Price data is typically available just after
# 13:00 when data can be retrieved from its original source (ENTSO-E) but sometimes
# there is a delay of several hours.
GET_PRICES_TIME: str = "13:35:51"

# Delay between checks when no data was found (in seconds).
# Set to 2 hours to match the FM server check frequency for ENTSOE data.
CHECK_RESOLUTION_SECONDS: int = 2 * 60 * 60  # 2 hours

# When to check if price data is up to date, and if not notify the user.
# Just after the second re-try, which is 4 hours after the first check.
CHECK_DATA_STATUS_TIME: str = "17:38:59"

# End of day time string for time range comparisons.
END_OF_DAY_TIME: str = "23:59:59"

# If not successful, retry every CHECK_RESOLUTION_SECONDS until this time (the next day).
TRY_UNTIL: str = "11:22:33"
