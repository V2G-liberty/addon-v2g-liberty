"""Processor for transforming raw FlexMeasures price data into UI-ready format."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


class PriceProcessor:
    """
    Transforms raw price data from FlexMeasures into formatted data points for UI display.

    Responsibilities:
    - Apply VAT and markup to raw prices
    - Filter out None values
    - Detect first negative price in the future
    - Add trailing point for step-line chart display
    """

    def __init__(self, price_resolution_minutes: int):
        """
        Initialise the PriceProcessor.

        Args:
            price_resolution_minutes: The resolution of price data in minutes (e.g., 15 or 30)
        """
        self.price_resolution_minutes = price_resolution_minutes

    def process_prices(
        self,
        raw_prices: List[Optional[float]],
        start: datetime,
        now: datetime,
        vat_factor: float,
        markup_per_kwh: float,
        end_of_fixed_prices_dt: Optional[datetime] = None,
    ) -> Tuple[List[Dict[str, any]], Optional[Dict[str, any]], int, Optional[str]]:
        """
        Process raw price data into UI-ready format.

        Args:
            raw_prices: List of raw price values (may contain None)
            start: Start datetime for the price data
            now: Current datetime
            vat_factor: VAT multiplication factor (e.g., 1.21 for 21% VAT)
            markup_per_kwh: Fixed markup to add per kWh
            end_of_fixed_prices_dt: Datetime marking the boundary between fixed (EPEX day-ahead)
                                    prices and forecast prices. None for non-EPEX providers.

        Returns:
            Tuple containing:
            - price_points: List of dicts with 'time' and 'price' keys for chart display
            - first_negative_price: Dict with 'time' and 'price' of first future negative price,
                                   or None if no negative prices
            - none_count: Number of None values encountered
            - end_of_fixed_prices_iso: ISO format string of end_of_fixed_prices_dt, or None
        """
        price_points = []
        first_negative_price = None
        none_count = 0
        last_price = None
        last_dt = None

        for i, price in enumerate(raw_prices):
            if price is None:
                none_count += 1
                continue

            dt = start + timedelta(minutes=(i * self.price_resolution_minutes))
            net_price = round((price + markup_per_kwh) * vat_factor, 2)

            data_point = {"time": dt.isoformat(), "price": net_price}
            price_points.append(data_point)

            # Detect first negative price in the future
            if first_negative_price is None and net_price < 0 and dt > now:
                first_negative_price = {"time": dt, "price": net_price}

            last_price = net_price
            last_dt = dt

        # Add trailing point to extend step-line chart to end of last period
        if last_price is not None and last_dt is not None:
            trailing_point = {
                "time": (
                    last_dt + timedelta(minutes=self.price_resolution_minutes)
                ).isoformat(),
                "price": last_price,
            }
            price_points.append(trailing_point)

        # Convert EFP datetime to ISO string for JSON serialisation
        end_of_fixed_prices_iso = (
            end_of_fixed_prices_dt.isoformat() if end_of_fixed_prices_dt else None
        )

        return price_points, first_negative_price, none_count, end_of_fixed_prices_iso
