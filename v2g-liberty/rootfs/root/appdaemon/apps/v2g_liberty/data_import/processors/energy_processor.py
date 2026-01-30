"""Processor for calculating energy and emission statistics from power data."""

import math
from datetime import datetime, timedelta
from typing import Dict, NamedTuple, Optional


class EnergyStats(NamedTuple):
    """Energy and emission statistics for a time period."""

    total_charged_energy_kwh: int  # Total energy charged (positive, in kWh)
    total_discharged_energy_kwh: int  # Total energy discharged (negative, in kWh)
    net_energy_kwh: int  # Net energy (charged + discharged)
    total_emissions_kg: float  # Total emissions from charging (kg CO2)
    total_saved_emissions_kg: float  # Total emissions saved from discharging (kg CO2)
    net_emissions_kg: float  # Net emissions (total + saved)
    total_charge_time: str  # Formatted charge duration
    total_discharge_time: str  # Formatted discharge duration


class EnergyProcessor:
    """
    Calculates energy and emission statistics from power data.

    Responsibilities:
    - Process power values and calculate charged/discharged energy
    - Calculate emissions based on emission intensities
    - Convert power (MW over 5-min periods) to energy (kWh)
    - Convert emissions (MW * kg/MWh over 5-min) to mass (kg)
    - Format durations for display
    """

    def __init__(self, event_resolution_minutes: int):
        """
        Initialise the EnergyProcessor.

        Args:
            event_resolution_minutes: The resolution of power data in minutes (typically 5)
        """
        self.event_resolution_minutes = event_resolution_minutes

    def calculate_energy_stats(
        self,
        power_values: list[Optional[float]],
        start: datetime,
        emission_cache: Dict[datetime, float],
    ) -> EnergyStats:
        """
        Calculate energy and emission statistics from power values.

        Args:
            power_values: List of power values in MW (negative = discharge, positive = charge)
            start: Start datetime for the power data
            emission_cache: Dict mapping datetime to emission intensity (kg/MWh)

        Returns:
            EnergyStats containing calculated energy and emission values
        """
        total_charged_energy = 0.0  # Sum of MW over 5-min periods
        total_discharged_energy = 0.0  # Sum of MW over 5-min periods
        total_emissions = 0.0  # Sum of MW * kg/MWh over 5-min periods
        total_saved_emissions = 0.0  # Sum of MW * kg/MWh over 5-min periods
        total_minutes_charged = 0
        total_minutes_discharged = 0

        for i, power in enumerate(power_values):
            if power is None:
                continue

            power_float = float(power)
            timestamp = start + timedelta(minutes=(i * self.event_resolution_minutes))

            # Look up emission intensity for this timestamp
            # Emissions have 15-min resolution, power has 5-min resolution
            # so emission will match every 3 power values
            emission_intensity = emission_cache.get(timestamp, 0)

            if power_float < 0:
                # Discharging (V2G/V2H)
                total_discharged_energy += power_float
                total_minutes_discharged += self.event_resolution_minutes
                # Calculate saved emissions (negative power * positive emission)
                total_saved_emissions += power_float * emission_intensity
            elif power_float > 0:
                # Charging
                total_charged_energy += power_float
                total_minutes_charged += self.event_resolution_minutes
                # Calculate emissions from charging
                total_emissions += power_float * emission_intensity

        # Convert power (MW over 5-min periods) to energy (kWh)
        # Formula: MW * (minutes/60) * 1000 kW/MW = kWh
        # Simplified: MW * 1000 / (60/minutes) = kWh
        energy_conversion_factor = 1000 / (60 / self.event_resolution_minutes)
        total_charged_energy_kwh = int(
            round(total_charged_energy * energy_conversion_factor, 0)
        )
        total_discharged_energy_kwh = int(
            round(total_discharged_energy * energy_conversion_factor, 0)
        )

        # Convert emissions (MW * kg/MWh over 5-min periods) to mass (kg)
        # Formula: MW * kg/MWh * (minutes/60) = kg
        # Simplified: (MW * kg/MWh) / (60/minutes) = kg
        emission_conversion_factor = 1 / (60 / self.event_resolution_minutes)
        total_emissions_kg = round(total_emissions * emission_conversion_factor, 1)
        total_saved_emissions_kg = round(
            total_saved_emissions * emission_conversion_factor, 1
        )

        # Calculate net values
        net_energy_kwh = total_charged_energy_kwh + total_discharged_energy_kwh
        net_emissions_kg = total_emissions_kg + total_saved_emissions_kg

        # Format durations
        charge_time_str = self._format_duration(total_minutes_charged)
        discharge_time_str = self._format_duration(total_minutes_discharged)

        return EnergyStats(
            total_charged_energy_kwh=total_charged_energy_kwh,
            total_discharged_energy_kwh=total_discharged_energy_kwh,
            net_energy_kwh=net_energy_kwh,
            total_emissions_kg=total_emissions_kg,
            total_saved_emissions_kg=total_saved_emissions_kg,
            net_emissions_kg=net_emissions_kg,
            total_charge_time=charge_time_str,
            total_discharge_time=discharge_time_str,
        )

    def _format_duration(self, duration_in_minutes: int) -> str:
        """
        Format a duration in minutes for presentation in UI.

        Args:
            duration_in_minutes: Duration in minutes (e.g., 86735)

        Returns:
            Formatted string (e.g., "01d 05h 35m")
        """
        minutes_in_day = 60 * 24
        days = math.floor(duration_in_minutes / minutes_in_day)
        hours = math.floor((duration_in_minutes - days * minutes_in_day) / 60)
        minutes = duration_in_minutes - days * minutes_in_day - hours * 60
        return f"{days:02d}d {hours:02d}h {minutes:02d}m"
