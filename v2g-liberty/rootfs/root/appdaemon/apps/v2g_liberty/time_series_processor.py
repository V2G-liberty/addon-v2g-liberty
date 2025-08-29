"""
Module to resample a time series data frame to a data frame with 5-minute intervals
with a weighted average in that interval.
"""

import datetime as dt
import pandas as pd


def _is_5min_aligned(ts: dt.datetime) -> bool:
    return ts.second == 0 and ts.microsecond == 0 and ts.minute % 5 == 0


def _weighted_avg(
    series: pd.Series, interval_end: pd.Timestamp | None = None
) -> float | None:
    if series.empty:
        return None

    numeric_data = pd.to_numeric(series, errors="coerce")
    valid_data = numeric_data.dropna()

    if valid_data.empty:
        return None

    valid_data = valid_data.sort_index()
    timestamps = valid_data.index.to_series()

    # Determine next timestamps
    next_ts = timestamps.shift(-1)
    # Last value lasts until interval_end if provided, else 0
    if interval_end is not None:
        next_ts.iloc[-1] = interval_end
    else:
        next_ts.iloc[-1] = timestamps.iloc[-1]

    durations = (next_ts - timestamps).dt.total_seconds()
    if durations.sum() == 0:
        return None

    weighted_avg = (valid_data * durations).sum() / durations.sum()
    return weighted_avg


def weighted_resample_5t(
    df: pd.DataFrame, dt_start: dt.datetime, dt_end: dt.datetime
) -> pd.DataFrame:
    """
    Resample a time series data frame to a data frame with 5-minute intervals
    with a weighted average in that interval.

    Parameters
    ----------
    df : DataFrame
        Data frame time series holding the (randomly distributed) values.
        Time serie must have a start before dt_end.
    dt_start : datetime (rounded to 5 min.)
        Start of the returned DataFrame
    dt_end : datetime (rounded to 5 min.)
        End of the returned DataFrame

    Returns
    -------
    DataFrame
        Data frame with timeseries of evenly distributed 5 min. intervals with the
        (duration) weighted average value for the intervals. Starting at start_dt ending at end_dt.
    """
    if df.empty:
        raise ValueError("df must not be empty")

    pd_start = pd.Timestamp(dt_start)
    pd_end = pd.Timestamp(dt_end)
    if df.index.tz != pd_start.tz or df.index.tz != pd_end.tz:
        raise ValueError(
            f"dt_start({pd_start.tz}), dt_end ({pd_end.tz}) and df.index ({df.index.tz}) "
            f"have different timezones"
        )

    if not _is_5min_aligned(dt_start) or not _is_5min_aligned(dt_end):
        raise ValueError("dt_start and dt_end must be rounded to 5 minutes")

    if pd_end < pd_start + pd.Timedelta(minutes=5):
        raise ValueError(
            f"dt_end ({dt_end.isoformat()}) must be at least 5 min. after "
            f"dt_start ({dt_start.isoformat()})"
        )

    df_min = df.index.min()
    if df_min > pd_end:
        raise ValueError("range time series must start before dt_end")

    df = df.copy()
    df.sort_index(inplace=True)

    grid = pd.date_range(start=pd_start, end=pd_end, freq="5min", tz=df.index.tz)

    # Reindex to include grid points
    df_grided = df.reindex(df.index.union(grid))

    # Forward-fill to propagate last value at interval boundaries
    df_grided["value_shifted"] = df_grided["value"].ffill()
    df_grided = df_grided.loc[pd_start:pd_end]
    print(f"Grided:\n{df_grided}")

    # Resample over 5-minute intervals
    def apply_weighted_avg(interval):
        if interval["value"].isna().all():
            return None

        interval_end = interval.index[0] + pd.Timedelta(minutes=5)
        return _weighted_avg(interval["value_shifted"], interval_end=interval_end)

    result = df_grided.resample(
        "5min", origin=pd_start, label="left", closed="left"
    ).apply(apply_weighted_avg)
    result = result[result.index < pd_end]
    result = pd.DataFrame({"value": result})
    print(f"Result:\n{result}")
    return result
