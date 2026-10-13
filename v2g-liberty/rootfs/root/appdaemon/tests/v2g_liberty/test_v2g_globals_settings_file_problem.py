"""A settings file that will not load leaves the app running on factory
defaults. A user cannot reach /data to see that, so the only way they can tell
it from a fresh install is if the app says so -- and the two failures need them
to do different things."""

from unittest.mock import Mock

import pytest
from apps.v2g_liberty.settings_manager import SettingsManager
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals


@pytest.fixture
def globals_instance():
    instance = object.__new__(V2GLibertyGlobals)
    instance._V2GLibertyGlobals__log = Mock()
    instance.notifier = Mock()
    instance.notifier.post_sticky_memo = Mock()
    instance.v2g_settings = Mock()
    instance.v2g_settings.file_problem = SettingsManager.FILE_OK
    instance.v2g_settings.set_aside_path = ""
    return instance


def _report(instance):
    return instance._V2GLibertyGlobals__report_settings_file_problem()


def test_a_healthy_file_says_nothing(globals_instance):
    _report(globals_instance)
    globals_instance.notifier.post_sticky_memo.assert_not_called()


def test_a_set_aside_file_asks_the_user_to_set_up_again(globals_instance):
    globals_instance.v2g_settings.file_problem = SettingsManager.FILE_SET_ASIDE
    globals_instance.v2g_settings.set_aside_path = "/data/settings.json.corrupt-x"

    _report(globals_instance)

    message = globals_instance.notifier.post_sticky_memo.call_args.kwargs["message"]
    # Name the file: it is the only handle support has on what was lost.
    assert "/data/settings.json.corrupt-x" in message
    assert "set it up again" in message


def test_an_unreadable_file_asks_for_a_restart(globals_instance):
    """Here the file was left where it is, so setting up again would be wasted
    work -- nothing would be saved. A restart is the way out."""
    globals_instance.v2g_settings.file_problem = SettingsManager.FILE_UNREADABLE

    _report(globals_instance)

    message = globals_instance.notifier.post_sticky_memo.call_args.kwargs["message"]
    assert "restarted" in message
    assert "set it up again" not in message


def test_it_lands_where_nothing_has_to_be_configured(globals_instance):
    """notify_user pushes to the registered mobile apps, and after a file is
    set aside there are none -- the recipients lived in the settings too."""
    globals_instance.v2g_settings.file_problem = SettingsManager.FILE_UNREADABLE

    _report(globals_instance)

    globals_instance.notifier.notify_user.assert_not_called()
    assert globals_instance.notifier.post_sticky_memo.call_args.kwargs["memo_id"]
