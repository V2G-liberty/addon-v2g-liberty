"""Processor for transforming raw FlexMeasures emission data for caching and UI display."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


class EmissionProcessor:
    """
    Processes emission intensity data from FlexMeasures for caching and chart display.

    Responsibilities:
    - Filter out None values
    - Cache emission intensities by timestamp for calculations
    - Generate optimised chart data (only changes, recent data only)
    - Scale values for UI display
    - Track latest emission datetime
    """

    def __init__(self, event_resolution_minutes: int):
        """
        Initialise the EmissionProcessor.

        Args:
            event_resolution_minutes: The resolution of emission data in minutes (typically 15)
        """
        self.event_resolution_minutes = event_resolution_minutes

    def process_emissions(
        self,
        raw_emissions: List[Optional[float]],
        start: datetime,
        now: datetime,
        history_hours: int = 5,
    ) -> Tuple[Dict[datetime, float], List[Dict[str, any]], Optional[datetime]]:
        """
        Process raw emission data into cache and chart-ready format.

        Args:
            raw_emissions: List of raw emission intensity values (may contain None)
            start: Start datetime for the emission data
            now: Current datetime
            history_hours: How many hours of history to include in chart (default: 5)

        Returns:
            Tuple containing:
            - emission_cache: Dict mapping datetime to emission intensity (for calculations)
            - chart_points: List of dicts with 'time' and 'emission' keys (optimised for display)
            - latest_emission_dt: Datetime of the latest non-None emission value, or None if all None
        """
        emission_cache = {}
        chart_points = []
        latest_emission_dt = None
        previous_value = None
        show_in_graph_after = now - timedelta(hours=history_hours)

        for i, emission_value in enumerate(raw_emissions):
            if emission_value is None:
                continue

            # Calculate timestamp for this emission value
            emission_start = start + timedelta(
                minutes=i * self.event_resolution_minutes
            )

            # Update latest emission datetime
            latest_emission_dt = emission_start

            # Store in cache for later calculations
            emission_cache[emission_start] = emission_value

            # Add to chart only if:
            # 1. Value has changed from previous (optimisation)
            # 2. Timestamp is within the history window
            if (
                emission_value != previous_value
                and emission_start > show_in_graph_after
            ):
                # Scale for display: divide by 10 and round to integer
                scaled_value = int(round(float(emission_value) / 10, 0))
                data_point = {
                    "time": emission_start.isoformat(),
                    "emission": scaled_value,
                }
                chart_points.append(data_point)

            previous_value = emission_value

        return emission_cache, chart_points, latest_emission_dt
