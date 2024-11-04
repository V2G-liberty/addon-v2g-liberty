from datetime import datetime, timedelta
import pytz

TZ = pytz.timezone("Australia/Sydney")

GET_PRICES_TIME = "13:32:21"  # When to start check for prices.
GET_EMISSIONS_TIME = "13:45:26"  # When to start check for emissions.
TRY_UNTIL = "11:22:33"  # If not successful retry every x minutes until this time (the next day)


class TestIsNowBetween():
    def init(self):
        # TESTING
        test_cases = [
            ("15:00:00", "13:30:00", "18:30:00", True),
            ("17:13:53", "13:32:21", "23:59:59", True),
            ("19:00:00", "13:30:00", "00:00:00", True),
            ("10:00:00", "13:30:00", "23:59:59", False),
            ("10:00:00", "13:30:00", "15:30:00", False),
            ("19:00:00", "13:30:00", "15:30:00", False),
            ("12:00:00", "13:30:00", "11:30:00", False),
            ("01:00:00", "13:30:00", "11:30:00", True),
            (None, "13:32:21", "11:22:33", True),
        ]

        for now_time, start_time, end_time, expected in test_cases:
            result = is_local_now_between(start_time = start_time, end_time = end_time, now_time = now_time)
            print(f"Test: {start_time=}, {end_time=}, {now_time=}, assert: {result == expected}.")

        result = is_local_now_between(start_time = GET_PRICES_TIME, end_time = TRY_UNTIL)
        print(f"Test: start_time={GET_PRICES_TIME}, end_time={TRY_UNTIL}, assert: {result == True}.")


def is_local_now_between(start_time: str, end_time: str, now_time: str = None) -> bool:
    # Get today's date
    today_date = datetime.today().date()

    if now_time is None:
        now = get_local_now()
    else:
        time_obj = datetime.strptime(now_time, "%H:%M:%S").time()
        now = TZ.localize(datetime.combine(today_date, time_obj))

    time_obj = datetime.strptime(start_time, "%H:%M:%S").time()
    start_dt = TZ.localize(datetime.combine(today_date, time_obj))

    time_obj = datetime.strptime(end_time, "%H:%M:%S").time()
    end_dt = TZ.localize(datetime.combine(today_date, time_obj))

    # Comparisons
    if end_dt < start_dt:
        # self.log(f"is_local_now_between, end_dt < start_dt ...")
        # Start and end time backwards, so it spans midnight.
        # Let's start by assuming end_dt is wrong and should be tomorrow.
        # This will be true if we are currently after start_dt
        end_dt += timedelta(days=1)
        if now < start_dt and now < end_dt:
            # Well, it's complicated, we crossed into a new day and things changed.
            # Now all times have shifted relative to the new day, so we need to look at it differently
            # If both times are now in the future, we now actually need to set start_dt and end_dt back a day.
            start_dt -= timedelta(days=1)
            end_dt -= timedelta(days=1)

    result = (start_dt <= now <= end_dt)
    print(f"is_local_now_between start: {start_dt.isoformat()}, end: {end_dt.isoformat()},"
          f" now: {now.isoformat()} => result: {result}.")
    return result

def get_local_now():
    return TZ.localize(datetime.now())

if __name__ == '__main__':
    TestIsNowBetween().init()
