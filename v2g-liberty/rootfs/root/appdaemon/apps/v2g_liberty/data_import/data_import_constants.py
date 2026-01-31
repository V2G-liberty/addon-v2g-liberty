"""Constants for the data_import module.

These constants are used across the data_import module for scheduling
price, emission, and cost data retrieval from FlexMeasures.
"""

# When to start checking for prices. Price data is typically available just after
# 13:00 when data can be retrieved from its original source (ENTSO-E) but sometimes
# there is a delay of several hours.
GET_PRICES_TIME: str = "13:35:51"

# If not successful, retry every CHECK_RESOLUTION_SECONDS until this time (the next day).
TRY_UNTIL: str = "11:22:33"

# When to check if price data is up to date, and if not notify the user.
CHECK_DATA_STATUS_TIME: str = "18:34:52"

# Delay between checks when no data was found (in seconds).
CHECK_RESOLUTION_SECONDS: int = 30 * 60

# End of day time string for time range comparisons.
END_OF_DAY_TIME: str = "23:59:59"
