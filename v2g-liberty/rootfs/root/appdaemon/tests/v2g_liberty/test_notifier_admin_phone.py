"""A notification for the administrator does not vanish when that phone is gone.

Everything critical, and everything not sent to all, goes to the
administrator's phone only. In production that setting pointed for six weeks
to a phone that had left Home Assistant: every such notification failed, the
error only reached the error log, and nobody knew. Now such a notification
goes to the other phones (at normal priority), a memo in Home Assistant says
what is wrong, and phones that come and go are followed while the app runs.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from appdaemon.exceptions import ServiceException
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.notifier_util import Notifier

CRITICAL_CONFIG = {"push": {"sound": {"critical": 1}}}


def _critical_config():
    """A fresh copy, so a test that changes it cannot hide that."""
    return {"push": {"sound": {"critical": 1}}}


OK = {"success": True}
NOT_FOUND = {
    "success": False,
    "error": {"code": "not_found", "message": "Service notify.x not found."},
}
TIMED_OUT = {"success": False}


class ServiceResult:
    def __init__(self, result):
        self.result = result

    def __await__(self):
        yield from ()
        return self.result


@pytest.fixture
def notifier(monkeypatch):
    monkeypatch.setattr(c, "ADMIN_MOBILE_NAME", "admin_phone")
    monkeypatch.setattr(c, "PRIORITY_NOTIFICATION_CONFIG", _critical_config())
    monkeypatch.setattr(c, "HA_NAME", "Home")
    hass = MagicMock(spec=Hass)
    hass.run_in = AsyncMock()
    # Called without await in __init__ and with await in initialize().
    hass.listen_event = MagicMock(return_value=ServiceResult(None))
    hass.list_services = MagicMock(return_value=[])
    instance = Notifier(hass=hass, event_bus=MagicMock(spec=EventBus))
    instance.recipients = ["admin_phone", "jane", "john"]
    instance.results = {}  # phone -> result or exception for call_service

    def call_service(service, **kwargs):
        phone = service.removeprefix("notify/mobile_app_")
        outcome = instance.results.get(phone, OK)
        if isinstance(outcome, Exception):
            raise outcome
        return ServiceResult(outcome)

    hass.call_service = MagicMock(side_effect=call_service)
    return instance


def _sent(notifier):
    """(phone, data) for every notification, without the memo calls."""
    return [
        (call.args[0].removeprefix("notify/mobile_app_"), call.kwargs.get("data"))
        for call in notifier.hass.call_service.call_args_list
        if call.args and call.args[0].startswith("notify/mobile_app_")
    ]


def _phones(notifier):
    return [phone for phone, _data in _sent(notifier)]


def _memo_calls(notifier, service):
    return [
        call
        for call in notifier.hass.call_service.call_args_list
        if call.kwargs.get("service") == service
    ]


async def _critical(notifier):
    await notifier.notify_user(message="Charger unusable", tag="t", critical=True)


@pytest.mark.asyncio
async def test_a_known_administrator_gets_it_alone_and_loud(notifier):
    await _critical(notifier)

    assert _sent(notifier) == [
        ("admin_phone", {**CRITICAL_CONFIG, "tag": "t"}),
    ]


@pytest.mark.asyncio
async def test_an_unknown_administrator_phone_sends_it_to_all_others(notifier):
    """The production case: the setting names a phone Home Assistant does not
    have. Normal priority: the critical settings are for the admin's platform."""
    notifier.recipients = ["jane", "john"]

    await _critical(notifier)

    assert _sent(notifier) == [("jane", {"tag": "t"}), ("john", {"tag": "t"})]
    memo = _memo_calls(notifier, "persistent_notification/create")
    assert len(memo) == 1
    assert "admin_phone" in memo[0].kwargs["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [NOT_FOUND, ServiceException("default", "notify", "mobile_app_admin_phone", [])],
    ids=["home-assistant-does-not-know-it", "appdaemon-does-not-know-it"],
)
async def test_a_phone_that_turns_out_to_be_gone_sends_it_to_the_others(
    notifier, failure
):
    """Known in the list, but gone when it is used: the delivery itself says so."""
    notifier.results["admin_phone"] = failure

    await _critical(notifier)

    assert _phones(notifier) == ["admin_phone", "jane", "john"]
    assert len(_memo_calls(notifier, "persistent_notification/create")) == 1


@pytest.mark.asyncio
async def test_another_failure_does_not_reroute(notifier):
    """A time-out says nothing about the phone being gone; sending to everyone
    then would only duplicate what may already have arrived."""
    notifier.results["admin_phone"] = TIMED_OUT

    await _critical(notifier)

    assert _phones(notifier) == ["admin_phone"]
    assert _memo_calls(notifier, "persistent_notification/create") == []


@pytest.mark.asyncio
async def test_no_administrator_set_sends_it_to_everyone(notifier, monkeypatch):
    """Used to cancel every notification, those for all included."""
    monkeypatch.setattr(c, "ADMIN_MOBILE_NAME", "")

    await notifier.notify_user(message="Prices are late", tag="t")

    assert _phones(notifier) == ["admin_phone", "jane", "john"]
    # Not set at all is for the settings screen to show, not for a memo.
    assert _memo_calls(notifier, "persistent_notification/create") == []


@pytest.mark.asyncio
async def test_the_memo_is_posted_once_and_dismissed_when_the_phone_is_back(
    notifier,
):
    notifier.recipients = ["jane", "john"]
    await _critical(notifier)
    await _critical(notifier)
    assert len(_memo_calls(notifier, "persistent_notification/create")) == 1

    notifier.recipients = ["admin_phone", "jane", "john"]
    await _critical(notifier)

    assert len(_memo_calls(notifier, "persistent_notification/dismiss")) == 1


@pytest.mark.asyncio
async def test_phones_are_followed_while_the_app_runs(notifier):
    handle = notifier._Notifier__handle_service_change
    notifier.check_admin_mobile()  # start-up: admin known, nothing to report

    await handle(
        "service_removed", {"domain": "notify", "service": "mobile_app_admin_phone"}, {}
    )
    assert notifier.recipients == ["jane", "john"]
    assert len(_memo_calls(notifier, "persistent_notification/create")) == 1

    await handle(
        "service_registered",
        {"domain": "notify", "service": "mobile_app_admin_phone"},
        {},
    )
    assert notifier.recipients == ["admin_phone", "jane", "john"]
    # The first check at start-up dismissed (an old memo, if any); this is the
    # second dismiss.
    assert len(_memo_calls(notifier, "persistent_notification/dismiss")) == 2


@pytest.mark.asyncio
async def test_other_services_are_ignored(notifier):
    handle = notifier._Notifier__handle_service_change
    await handle("service_removed", {"domain": "light", "service": "turn_on"}, {})
    await handle("service_removed", {"domain": "notify", "service": "persistent"}, {})

    assert notifier.recipients == ["admin_phone", "jane", "john"]


@pytest.mark.asyncio
async def test_the_shared_critical_config_is_not_changed(notifier):
    """It used to collect each notification's tag and actions."""
    await notifier.notify_user(
        message="Test",
        tag="test_notification",
        critical=True,
        actions=[{"action": "loud", "title": "Loud"}],
    )

    assert c.PRIORITY_NOTIFICATION_CONFIG == _critical_config()


@pytest.mark.asyncio
async def test_send_to_all_is_unchanged(notifier):
    notifier.recipients = ["jane", "john"]  # admin phone gone: irrelevant here

    await notifier.notify_user(message="Car disconnected", tag="t", send_to_all=True)

    assert _phones(notifier) == ["jane", "john"]
    assert _memo_calls(notifier, "persistent_notification/create") == []
