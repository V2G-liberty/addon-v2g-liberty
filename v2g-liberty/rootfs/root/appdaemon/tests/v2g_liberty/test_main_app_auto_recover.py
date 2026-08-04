"""Unit tests for auto-recovery after a charger crash (Fase 2b).

When the EVSE client's recovery probe detects the charger is reachable again, it
calls ``auto_recover_from_charger_crash`` on the main app. That must clear the
communication-fault flag and switch charge_mode back to Automatic (which reuses
the existing manual recovery path), then notify the user.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest

from apps.v2g_liberty.main_app import V2Gliberty


@pytest.fixture
def v2g():
    """Create V2Gliberty with mocked dependencies for recovery handling."""
    hass = AsyncMock()
    hass.log = MagicMock()

    notifier = MagicMock()
    notifier.notify_user = AsyncMock()
    notifier.clear_notification = MagicMock()

    return V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=notifier)


@pytest.mark.asyncio
async def test_auto_recover_clears_fault_and_switches_to_automatic(v2g):
    await v2g.auto_recover_from_charger_crash()

    # Communication-fault flag cleared.
    v2g.hass.set_state.assert_any_await(
        "input_boolean.charger_modbus_communication_fault", state="off"
    )
    # charge_mode → Automatic via the input_boolean (triggers set_active).
    v2g.hass.turn_on.assert_any_await("input_boolean.chargemodeautomatic")


@pytest.mark.asyncio
async def test_auto_recover_notifies_user(v2g):
    await v2g.auto_recover_from_charger_crash()

    v2g.notifier.notify_user.assert_awaited_once()
    kwargs = v2g.notifier.notify_user.await_args.kwargs
    assert kwargs["tag"] == "charger_modbus_crashed"
    assert kwargs["title"] == "Charger recovered"
    assert kwargs["critical"] is False
