"""Grid & PV sensor emulator for the dev environment.

Creates emulated smart meter and PV inverter sensor entities in Home Assistant.
Updates them every ~10 seconds with realistic values, including:
- Household base load per phase (with time-of-day variation)
- Charger power on the correct phase(s) (read from actual charger state)
- PV production as a sine curve over the day
- Grid production (feed-in) when PV surplus exceeds consumption

These entities can be selected in the grid connection / solar panel settings
dialogs to test the full data pipeline without real hardware.

Dev-only: this app is not included in the production Docker image.
"""

import math
import random
from datetime import datetime

import appdaemon.plugins.hass.hassapi as hass

from v2g_liberty import constants as c


class GridPvEmulator(hass.Hass):
    """AppDaemon app that emulates grid and PV sensor entities."""

    _ENTITY_ATTRS_POWER = {
        "unit_of_measurement": "W",
        "device_class": "power",
        "state_class": "measurement",
    }

    _PAUSE_ENTITY = "input_boolean.emulator_paused"
    _cycle: int = 0  # Alternating offset to ensure state changes

    def initialize(self):
        self._update_interval = int(self.args.get("update_interval", 10))
        self._pv_panels = self.args.get("pv_panels", [])
        self._base_load = self.args.get("base_load", {"l1": 800, "l2": 600, "l3": 400})

        # Create a toggle in HA to pause/resume the emulator
        self.set_state(
            self._PAUSE_ENTITY,
            state="off",
            attributes={"friendly_name": "Pause grid/PV emulator"},
        )

        # Create a fuse threshold entity (DSMR OBIS 1-0:31.4.0)
        fuse_threshold = self.args.get("fuse_threshold", 25)
        self.set_state(
            "sensor.emulated_fuse_threshold_l1",
            state=fuse_threshold,
            attributes={
                "unit_of_measurement": "A",
                "device_class": "current",
                "friendly_name": "Emulated Fuse Threshold L1",
            },
        )

        self.run_every(self._update_sensors, "now", self._update_interval)
        self.log("Grid & PV emulator started")

    def _update_sensors(self, kwargs):
        if self.get_state(self._PAUSE_ENTITY) == "on":
            return

        now = datetime.now()
        # Alternating +1/-1 offset ensures state always changes,
        # even when the rounded value would be identical.
        self._cycle = 1 - self._cycle
        offset = self._cycle  # 0 or 1

        # Calculate components
        base = self._calculate_base_load(now)
        charger = self._calculate_charger_impact()
        pv_per_phase = self._calculate_pv_per_phase(now)

        # Per phase: net = base + charger - pv
        for phase in (1, 2, 3):
            total_consumption = base[phase] + charger[phase]
            pv_on_phase = pv_per_phase[phase]
            net = total_consumption - pv_on_phase

            if net > 0:
                grid_consumption = round(net) + offset
                grid_production = offset
            else:
                grid_consumption = offset
                grid_production = round(abs(net)) + offset

            self._set_sensor(
                f"sensor.emulated_grid_consumption_l{phase}",
                grid_consumption,
                f"Emulated Grid Consumption L{phase}",
            )
            self._set_sensor(
                f"sensor.emulated_grid_production_l{phase}",
                grid_production,
                f"Emulated Grid Production L{phase}",
            )

        # PV sensors (total per panel, not per phase)
        pv_totals = self._calculate_pv_totals(now)
        for i, power in enumerate(pv_totals, start=1):
            self._set_sensor(
                f"sensor.emulated_pv_power_{i}",
                round(power),
                f"Emulated PV Power {i}",
            )

    def _set_sensor(self, entity_id: str, value: float, friendly_name: str):
        self.set_state(
            entity_id,
            state=value,
            attributes={
                **self._ENTITY_ATTRS_POWER,
                "friendly_name": friendly_name,
            },
        )

    def _calculate_base_load(self, now: datetime) -> dict[int, float]:
        """Calculate household base load per phase with time-of-day variation."""
        hour = now.hour + now.minute / 60

        # Time-of-day factor
        if 7 <= hour < 9 or 17 <= hour < 21:
            factor = 1.5  # Morning/evening peak
        elif 23 <= hour or hour < 6:
            factor = 0.5  # Night
        else:
            factor = 1.0

        return {
            1: self._base_load["l1"] * factor + random.uniform(-200, 200),
            2: self._base_load["l2"] * factor + random.uniform(-150, 150),
            3: self._base_load["l3"] * factor + random.uniform(-100, 100),
        }

    def _calculate_charger_impact(self) -> dict[int, float]:
        """Read actual charger power and distribute over the correct phase(s)."""
        impact = {1: 0.0, 2: 0.0, 3: 0.0}

        charger_power_state = self.get_state("sensor.charger_real_charging_power")
        if charger_power_state in (None, "unknown", "unavailable"):
            return impact

        try:
            charger_power = float(charger_power_state)
        except (TypeError, ValueError):
            return impact

        charger_phase = c.CHARGER_CONNECTED_TO_PHASE
        if charger_phase is None:
            # Phase not yet detected, default to L1
            charger_phase = 1

        if isinstance(charger_phase, list):
            # 3-phase charger: distribute evenly
            per_phase = charger_power / len(charger_phase)
            for p in charger_phase:
                impact[p] = per_phase
        else:
            # 1-phase charger: all on that phase
            impact[charger_phase] = charger_power

        return impact

    def _calculate_pv_totals(self, now: datetime) -> list[float]:
        """Calculate total PV production per panel."""
        results = []
        for panel in self._pv_panels:
            peak_wp = panel.get("peak_wp", 5000)
            power = self._pv_power(now, peak_wp)
            results.append(power)
        return results

    def _calculate_pv_per_phase(self, now: datetime) -> dict[int, float]:
        """Calculate PV production distributed over phases."""
        pv = {1: 0.0, 2: 0.0, 3: 0.0}

        for panel in self._pv_panels:
            peak_wp = panel.get("peak_wp", 5000)
            phases = panel.get("phases", 1)
            connected_to = panel.get("connected_to_phase", 1)
            power = self._pv_power(now, peak_wp)

            if phases == 3:
                per_phase = power / 3
                for p in (1, 2, 3):
                    pv[p] += per_phase
            else:
                pv[connected_to] += power

        return pv

    @staticmethod
    def _pv_power(now: datetime, peak_wp: float) -> float:
        """Calculate PV power as a sine curve over the day."""
        hour = now.hour + now.minute / 60

        if hour <= 6 or hour >= 21:
            return 0.0

        # Sine curve: 0 at 6:00, peak at 13:30, 0 at 21:00
        sun_factor = max(0.0, math.sin((hour - 6) / 15 * math.pi))

        # Add some variation (clouds, etc.)
        cloud_factor = 0.8 + random.uniform(0, 0.4)

        return peak_wp * sun_factor * cloud_factor
