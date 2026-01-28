"""Tests for EnergyProcessor class."""

import pytest
from datetime import datetime, timedelta
import pytz
from apps.v2g_liberty.get_fm_data.processors.energy_processor import (
    EnergyProcessor,
    EnergyStats,
)


@pytest.fixture
def energy_processor():
    """Create an EnergyProcessor instance with 5-minute resolution."""
    return EnergyProcessor(event_resolution_minutes=5)


class TestEnergyProcessorInitialisation:
    """Test EnergyProcessor initialisation."""

    def test_initialisation_with_resolution(self):
        """Test EnergyProcessor initialisation with specified resolution."""
        processor = EnergyProcessor(event_resolution_minutes=5)
        assert processor.event_resolution_minutes == 5

    def test_initialisation_different_resolution(self):
        """Test EnergyProcessor initialisation with different resolution."""
        processor = EnergyProcessor(event_resolution_minutes=15)
        assert processor.event_resolution_minutes == 15


class TestCalculateEnergyStats:
    """Test calculate_energy_stats method."""

    def test_calculate_energy_stats_charging_only(self, energy_processor):
        """Test energy calculation with only charging (positive power)."""
        # 12 * 5 minutes = 1 hour of data
        # Power in MW, all positive (charging)
        power_values = [0.012] * 12  # 12 kW constant charging for 1 hour
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        emission_cache = {}  # No emissions data

        stats = energy_processor.calculate_energy_stats(
            power_values, start, emission_cache
        )

        # Energy: 0.012 MW * 12 periods * (1000 kW/MW) / (60 min / 5 min) = 12 kWh
        assert stats.total_charged_energy_kwh == 12
        assert stats.total_discharged_energy_kwh == 0
        assert stats.net_energy_kwh == 12

        # No emissions data
        assert stats.total_emissions_kg == 0.0
        assert stats.total_saved_emissions_kg == 0.0
        assert stats.net_emissions_kg == 0.0

        # Time: 12 * 5 = 60 minutes = 1 hour
        assert stats.total_charge_time == "00d 01h 00m"
        assert stats.total_discharge_time == "00d 00h 00m"

    def test_calculate_energy_stats_discharging_only(self, energy_processor):
        """Test energy calculation with only discharging (negative power)."""
        # 12 * 5 minutes = 1 hour of data
        # Power in MW, all negative (discharging)
        power_values = [-0.012] * 12  # 12 kW constant discharging for 1 hour
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        emission_cache = {}  # No emissions data

        stats = energy_processor.calculate_energy_stats(
            power_values, start, emission_cache
        )

        # Energy: -0.012 MW * 12 periods * (1000 kW/MW) / (60 min / 5 min) = -12 kWh
        assert stats.total_charged_energy_kwh == 0
        assert stats.total_discharged_energy_kwh == -12
        assert stats.net_energy_kwh == -12

        # No emissions data
        assert stats.total_emissions_kg == 0.0
        assert stats.total_saved_emissions_kg == 0.0
        assert stats.net_emissions_kg == 0.0

        # Time: 12 * 5 = 60 minutes = 1 hour
        assert stats.total_charge_time == "00d 00h 00m"
        assert stats.total_discharge_time == "00d 01h 00m"

    def test_calculate_energy_stats_mixed(self, energy_processor):
        """Test energy calculation with mixed charging and discharging."""
        # Mixed positive and negative power values
        power_values = [
            0.012,
            0.012,
            0.012,  # 15 min charging
            -0.012,
            -0.012,  # 10 min discharging
            0.012,
            0.012,
            0.012,
            0.012,  # 20 min charging
            -0.012,
            -0.012,
            -0.012,
        ]  # 15 min discharging
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        emission_cache = {}

        stats = energy_processor.calculate_energy_stats(
            power_values, start, emission_cache
        )

        # Charging: 7 periods * 0.012 MW * 1000 / 12 = 7 kWh
        assert stats.total_charged_energy_kwh == 7
        # Discharging: 5 periods * -0.012 MW * 1000 / 12 = -5 kWh
        assert stats.total_discharged_energy_kwh == -5
        # Net: 7 + (-5) = 2 kWh
        assert stats.net_energy_kwh == 2

        # Charge time: 7 * 5 = 35 minutes
        assert stats.total_charge_time == "00d 00h 35m"
        # Discharge time: 5 * 5 = 25 minutes
        assert stats.total_discharge_time == "00d 00h 25m"

    def test_calculate_energy_stats_with_emissions(self, energy_processor):
        """Test energy calculation with emission intensities."""
        power_values = [
            0.012,
            0.012,
            0.012,  # Charging (15 min)
            -0.012,
            -0.012,
            -0.012,
        ]  # Discharging (15 min)
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)

        # Emission cache: 100 kg/MWh for first 15 min, 200 kg/MWh for second 15 min
        # Note: Emission cache only has entries at 15-minute intervals
        # Power values are at 5-minute intervals, so only timestamps that match
        # emission timestamps will find a value; others default to 0
        emission_cache = {
            start: 100.0,  # Matches index 0 only (00:00)
            start + timedelta(minutes=15): 200.0,  # Matches index 3 only (00:15)
        }

        stats = energy_processor.calculate_energy_stats(
            power_values, start, emission_cache
        )

        # Charging emissions: Only index 0 matches (indices 1,2 default to 0)
        # 1 period * 0.012 MW * 100 kg/MWh / 12 = 0.1 kg
        assert stats.total_emissions_kg == 0.1

        # Discharging emissions (saved): Only index 3 matches (indices 4,5 default to 0)
        # 1 period * -0.012 MW * 200 kg/MWh / 12 = -0.2 kg
        assert stats.total_saved_emissions_kg == -0.2

        # Net emissions: 0.1 + (-0.2) = -0.1 kg (net saving)
        assert stats.net_emissions_kg == -0.1

    def test_calculate_energy_stats_with_none_values(self, energy_processor):
        """Test that None values are skipped in calculations."""
        power_values = [0.012, None, 0.012, None, -0.012, None]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        emission_cache = {}

        stats = energy_processor.calculate_energy_stats(
            power_values, start, emission_cache
        )

        # Only 3 non-None values: 2 charging, 1 discharging
        assert stats.total_charged_energy_kwh == 2  # 2 * 0.012 * 1000 / 12 = 2
        assert stats.total_discharged_energy_kwh == -1  # 1 * -0.012 * 1000 / 12 = -1
        assert stats.net_energy_kwh == 1

        # Time: 2 * 5 = 10 min charging, 1 * 5 = 5 min discharging
        assert stats.total_charge_time == "00d 00h 10m"
        assert stats.total_discharge_time == "00d 00h 05m"

    def test_calculate_energy_stats_empty_list(self, energy_processor):
        """Test calculation with empty power list."""
        power_values = []
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        emission_cache = {}

        stats = energy_processor.calculate_energy_stats(
            power_values, start, emission_cache
        )

        assert stats.total_charged_energy_kwh == 0
        assert stats.total_discharged_energy_kwh == 0
        assert stats.net_energy_kwh == 0
        assert stats.total_emissions_kg == 0.0
        assert stats.total_saved_emissions_kg == 0.0
        assert stats.net_emissions_kg == 0.0
        assert stats.total_charge_time == "00d 00h 00m"
        assert stats.total_discharge_time == "00d 00h 00m"

    def test_calculate_energy_stats_all_none(self, energy_processor):
        """Test calculation with all None values."""
        power_values = [None, None, None]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        emission_cache = {}

        stats = energy_processor.calculate_energy_stats(
            power_values, start, emission_cache
        )

        assert stats.total_charged_energy_kwh == 0
        assert stats.total_discharged_energy_kwh == 0
        assert stats.net_energy_kwh == 0
        assert stats.total_charge_time == "00d 00h 00m"
        assert stats.total_discharge_time == "00d 00h 00m"

    def test_calculate_energy_stats_missing_emission_data(self, energy_processor):
        """Test that missing emission data defaults to 0."""
        power_values = [0.012, 0.012, 0.012]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        # Emission cache doesn't have data for these timestamps
        emission_cache = {
            datetime(2026, 1, 27, 0, 0, 0, tzinfo=pytz.UTC): 100.0  # Different date
        }

        stats = energy_processor.calculate_energy_stats(
            power_values, start, emission_cache
        )

        # Energy is calculated normally
        assert stats.total_charged_energy_kwh == 3

        # But emissions default to 0 (no matching data in cache)
        assert stats.total_emissions_kg == 0.0


class TestFormatDuration:
    """Test _format_duration method."""

    def test_format_duration_zero(self, energy_processor):
        """Test formatting zero duration."""
        assert energy_processor._format_duration(0) == "00d 00h 00m"

    def test_format_duration_minutes_only(self, energy_processor):
        """Test formatting durations less than 1 hour."""
        assert energy_processor._format_duration(5) == "00d 00h 05m"
        assert energy_processor._format_duration(59) == "00d 00h 59m"

    def test_format_duration_hours_and_minutes(self, energy_processor):
        """Test formatting durations with hours and minutes."""
        assert energy_processor._format_duration(65) == "00d 01h 05m"
        assert energy_processor._format_duration(125) == "00d 02h 05m"

    def test_format_duration_days_hours_minutes(self, energy_processor):
        """Test formatting durations with days, hours, and minutes."""
        assert energy_processor._format_duration(1505) == "01d 01h 05m"
        assert energy_processor._format_duration(86735) == "60d 05h 35m"

    def test_format_duration_exact_hour(self, energy_processor):
        """Test formatting exact hour durations."""
        assert energy_processor._format_duration(60) == "00d 01h 00m"
        assert energy_processor._format_duration(120) == "00d 02h 00m"

    def test_format_duration_exact_day(self, energy_processor):
        """Test formatting exact day durations."""
        assert energy_processor._format_duration(1440) == "01d 00h 00m"
        assert energy_processor._format_duration(2880) == "02d 00h 00m"


class TestEnergyStatsNamedTuple:
    """Test EnergyStats named tuple."""

    def test_energy_stats_creation(self):
        """Test creating an EnergyStats instance."""
        stats = EnergyStats(
            total_charged_energy_kwh=100,
            total_discharged_energy_kwh=-50,
            net_energy_kwh=50,
            total_emissions_kg=10.5,
            total_saved_emissions_kg=-5.2,
            net_emissions_kg=5.3,
            total_charge_time="00d 05h 00m",
            total_discharge_time="00d 02h 30m",
        )

        assert stats.total_charged_energy_kwh == 100
        assert stats.total_discharged_energy_kwh == -50
        assert stats.net_energy_kwh == 50
        assert stats.total_emissions_kg == 10.5
        assert stats.total_saved_emissions_kg == -5.2
        assert stats.net_emissions_kg == 5.3
        assert stats.total_charge_time == "00d 05h 00m"
        assert stats.total_discharge_time == "00d 02h 30m"

    def test_energy_stats_immutability(self):
        """Test that EnergyStats is immutable (NamedTuple property)."""
        stats = EnergyStats(
            total_charged_energy_kwh=100,
            total_discharged_energy_kwh=-50,
            net_energy_kwh=50,
            total_emissions_kg=10.5,
            total_saved_emissions_kg=-5.2,
            net_emissions_kg=5.3,
            total_charge_time="00d 05h 00m",
            total_discharge_time="00d 02h 30m",
        )

        # Attempting to modify should raise AttributeError
        with pytest.raises(AttributeError):
            stats.total_charged_energy_kwh = 200


class TestEnergyProcessorDifferentResolution:
    """Test EnergyProcessor with different time resolutions."""

    def test_calculate_with_15_minute_resolution(self):
        """Test calculation with 15-minute resolution."""
        processor = EnergyProcessor(event_resolution_minutes=15)
        # 4 periods * 15 min = 1 hour
        power_values = [0.012, 0.012, 0.012, 0.012]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        emission_cache = {}

        stats = processor.calculate_energy_stats(power_values, start, emission_cache)

        # Energy: 4 * 0.012 MW * 1000 / (60/15) = 4 * 0.012 * 1000 / 4 = 12 kWh
        assert stats.total_charged_energy_kwh == 12

        # Time: 4 * 15 = 60 minutes = 1 hour
        assert stats.total_charge_time == "00d 01h 00m"
