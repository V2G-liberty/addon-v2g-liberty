"""The drivers tell the fuse guard where their dead zone ends.

A limit the guard lowers to a value under this minimum is one the charger
cannot follow, so the guard rounds it to 0 W; these tests pin what each
driver reports.
"""

from unittest.mock import AsyncMock, MagicMock

from apps.v2g_liberty.chargers.evtec_bidipro import EVtecBiDiProClient
from apps.v2g_liberty.chargers.wallbox_quasar_1 import WallboxQuasar1Client


def _hass():
    hass = MagicMock()
    for m in ("set_state", "run_every", "run_in", "get_state", "cancel_timer"):
        setattr(hass, m, AsyncMock())
    return hass


def test_the_quasar_accepts_nothing_under_six_amperes_in_either_direction():
    quasar = WallboxQuasar1Client(_hass(), MagicMock(), MagicMock())

    assert quasar.min_charge_power_w == 1380
    assert quasar.min_discharge_power_w == 1380


def test_the_bidipro_reports_its_charge_floor_once_the_window_is_known():
    evtec = EVtecBiDiProClient(_hass(), MagicMock(), MagicMock())
    assert evtec.min_charge_power_w == 0  # window not read yet

    evtec._MCE_UPPER_LIMIT.current_value = 10000.0
    evtec._MCE_LOWER_LIMIT.current_value = 1400.0

    assert evtec.min_charge_power_w == 1400
    assert evtec.min_discharge_power_w == 0  # no discharge minimum is known


def test_the_bidipro_trusts_no_floor_without_a_sane_window():
    evtec = EVtecBiDiProClient(_hass(), MagicMock(), MagicMock())
    evtec._MCE_UPPER_LIMIT.current_value = 0.0  # car not charge-ready
    evtec._MCE_LOWER_LIMIT.current_value = 1400.0

    assert evtec.min_charge_power_w == 0
