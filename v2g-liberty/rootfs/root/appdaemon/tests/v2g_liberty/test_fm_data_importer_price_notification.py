"""The "prices received" confirmation is sent once, also when the check runs
twice at the same moment. The flag that says a push notification went out
was cleared only after the confirmation had been awaited, so a second
concurrent check still saw it and confirmed again."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from apps.v2g_liberty.fm_data_importer import FlexMeasuresDataImporter
from apps.v2g_liberty.notifier_util import Notifier


async def _yield_once(*_args, **_kwargs):
    """Stands in for notify_user: gives the other task its turn, as the real
    call does."""
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_the_prices_received_confirmation_is_sent_once():
    notifier = MagicMock(spec=Notifier)
    notifier.notify_user = AsyncMock(side_effect=_yield_once)
    importer = FlexMeasuresDataImporter(MagicMock(), notifier)
    importer.v2g_main_app = MagicMock()
    importer.v2g_main_app.set_price_is_up_to_date = AsyncMock()
    importer.entsoe_data_is_up_to_date = True
    importer._critical_price_notification_sent = True
    check = importer._FlexMeasuresDataImporter__check_if_prices_are_up_to_date_again

    await asyncio.gather(check(), check())

    assert notifier.notify_user.await_count == 1
    assert importer._critical_price_notification_sent is False
