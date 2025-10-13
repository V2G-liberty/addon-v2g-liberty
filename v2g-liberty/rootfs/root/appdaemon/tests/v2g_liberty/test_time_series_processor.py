import math
from datetime import datetime, timezone
import pytest
import pandas as pd
from apps.v2g_liberty.time_series_processor import _weighted_avg, weighted_resample_5t


# Helper to create DataFrame with datetime index
def _make_df(values, start="2025-08-28 14:00:00", freq="1min"):
    index = pd.date_range(start=start, periods=len(values), freq=freq)
    # Make sure the index is timezone aware and UTC
    if index.tz is None:
        index = index.tz_localize("UTC")
    else:
        index = index.tz_convert("UTC")
    return pd.DataFrame({"value": values}, index=index)


@pytest.mark.parametrize(
    "values,expected",
    [
        ([3, 3, 3, 6, 6], 4.2),
        ([None, 3, None, 6, None], 4.5),  # last valid value lasts until interval end
        ([None, None, None], None),
        ([42], 42.0),
    ],
)
def test_weighted_avg(values, expected):
    df = _make_df(values)
    # Use interval_end as last timestamp + 1 min to simulate 5-min interval duration
    interval_end = df.index[-1] + pd.Timedelta(minutes=1)
    result = _weighted_avg(df["value"], interval_end=interval_end)
    if expected is None:
        assert result is None
    else:
        assert pytest.approx(result) == expected

def test_weighted_resample_5t_valueerrors():
    # 1. Empty df
    empty_df = pd.DataFrame(columns=["value"])
    with pytest.raises(ValueError, match="df must not be empty"):
        weighted_resample_5t(
            empty_df,
            dt_start=datetime(2025, 8, 28, 14, 0, tzinfo=timezone.utc),
            dt_end=datetime(2025, 8, 28, 14, 5, tzinfo=timezone.utc),
        )

    # 2. dt_end <= dt_start
    df = _make_df([1, 2, 3])

    with pytest.raises(ValueError, match="must be at least 5 min. after"):
        weighted_resample_5t(
            df,
            dt_start=datetime(2025, 8, 28, 14, 15, tzinfo=timezone.utc),
            dt_end=datetime(2025, 8, 28, 14, 0, tzinfo=timezone.utc),
        )

    # 3. Timezone mismatch (This is the only case where we won't use UTC)
    tz_df = df.copy()  # _make_df returns timezone aware index.
    naive_start = datetime(2025, 8, 28, 14, 0)  # naive datetime (no tz)
    naive_end = datetime(2025, 8, 28, 14, 5)

    # We will now pass a timezone-aware `dt_start`/`dt_end` but with a mismatch:
    with pytest.raises(ValueError, match="must be timezone-aware"):
        weighted_resample_5t(tz_df, dt_start=naive_start, dt_end=naive_end)

    # 4. dt_start or dt_end not 5-min aligned
    misaligned_start = datetime(2025, 8, 28, 14, 2, tzinfo=timezone.utc)
    aligned_end = datetime(2025, 8, 28, 14, 5, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="rounded to 5 minutes"):
        weighted_resample_5t(df, dt_start=misaligned_start, dt_end=aligned_end)

    # 5. df.index start (14:00) after dt_end
    df_range = df.copy()
    start = datetime(2025, 8, 28, 12, 0, tzinfo=timezone.utc)
    end = datetime(2025, 8, 28, 12, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="range time series must start before dt_end"):
        weighted_resample_5t(df_range, dt_start=start, dt_end=end)


def test_weighted_resample_5t_simple():
    # Step-change data
    values = [3, 6]
    # Localize the index to UTC
    index = pd.to_datetime(["2025-08-28 13:58:21", "2025-08-28 14:01:21"]).tz_localize(
        "UTC"
    )
    df = pd.DataFrame({"value": values}, index=index)

    # Define start and end explicitly (5-min aligned), and make them timezone-aware in UTC
    dt_start = datetime(2025, 8, 28, 14, 0, 0, tzinfo=timezone.utc)
    dt_end = datetime(2025, 8, 28, 14, 5, 0, tzinfo=timezone.utc)

    result = weighted_resample_5t(df, dt_start=dt_start, dt_end=dt_end)

    # The returned index should start at dt_start and end before dt_end
    assert result.index[0] == pd.Timestamp(dt_start)

    # Compare computed weighted average
    assert pytest.approx(result.iloc[0, 0]) == 5.19


def test_weighted_resample_5t_multiple_intervals():
    # Data spanning multiple 5-min intervals
    values = [1, 2, 3, 4, 5]
    index = pd.to_datetime(
        [
            "2025-08-28 13:58:00",
            "2025-08-28 14:01:00",
            "2025-08-28 14:03:00",
            "2025-08-28 14:06:00",
            "2025-08-28 14:09:00",
        ]
    )
    df = pd.DataFrame({"value": values}, index=index)

    dt_start = datetime(2025, 8, 28, 14, 0, 0)
    dt_end = datetime(2025, 8, 28, 14, 10, 0)

    result = weighted_resample_5t(df, dt_start=dt_start, dt_end=dt_end)

    # Check index matches 5-min intervals
    expected_index = pd.date_range(start=dt_start, periods=2, freq="5min")
    assert all(result.index == expected_index)

    # Compute expected values manually using _weighted_avg for each interval
    expected_values = [2.2, 4]

    assert all(
        pytest.approx(a) == b
        for a, b in zip(result["value"].iloc[:-1], expected_values)
    )


def test_weighted_resample_5t_single_value():
    # Data spanning multiple 5-min intervals
    values = [2]
    index = pd.to_datetime(
        [
            "2025-08-28 14:05:30",
        ]
    )
    df = pd.DataFrame({"value": values}, index=index)

    dt_start = datetime(2025, 8, 28, 14, 0, 0)
    dt_end = datetime(2025, 8, 28, 14, 10, 0)

    result = weighted_resample_5t(df, dt_start=dt_start, dt_end=dt_end)

    # Check index matches 5-min intervals
    expected_index = pd.date_range(start=dt_start, periods=2, freq="5min")
    assert all(result.index == expected_index)

    assert math.isnan(result["value"].iloc[0])
    assert pytest.approx(result["value"].iloc[1]) == 2


def test_weighted_resample_5t_with_none():
    # Data with some None values
    index = pd.to_datetime(
        [
            "2025-08-28 14:00:00",  # 3
            "2025-08-28 14:01:00",  # None
            "2025-08-28 14:02:00",  # 6
            "2025-08-28 14:07:00",  # None
            "2025-08-28 14:08:00",  # 9
        ]
    )
    values = [3, None, 6, None, 9]
    df = pd.DataFrame({"value": values}, index=index)

    dt_start = datetime(2025, 8, 28, 14, 0)
    dt_end = datetime(2025, 8, 28, 14, 10)

    result = weighted_resample_5t(df, dt_start=dt_start, dt_end=dt_end)

    # Check that the index is exactly 5-min intervals
    expected_index = pd.date_range(start=dt_start, periods=2, freq="5min")
    assert all(result.index == expected_index)

    # Only check non-None intervals
    assert pytest.approx(result["value"].iloc[0]) == 4.8
    assert pytest.approx(result["value"].iloc[1]) == 7.2


def test_weighted_resample_5t_with_all_none():
    # Check that the interval with only None returns None
    index = pd.to_datetime(
        [
            "2025-08-28 14:00:00",
            "2025-08-28 14:04:00",
            "2025-08-28 14:11:00",
        ]
    )
    values = [3, None, 9]

    dt_start = datetime(2025, 8, 28, 14, 0)
    dt_end = datetime(2025, 8, 28, 14, 10)

    df = pd.DataFrame({"value": values}, index=index)
    result = weighted_resample_5t(df, dt_start=dt_start, dt_end=dt_end)
    # Interval 14:05-14:09 contains only None
    assert math.isnan(result["value"].iloc[1])
