import pytest
from unittest.mock import MagicMock, call
from apps.v2g_liberty.notifier_util import Notifier
import apps.v2g_liberty.constants as c
from appdaemon.plugins.hass.hassapi import Hass


@pytest.fixture
def mock_hass():
    """Mock the Hass object."""
    hass = MagicMock(spec=Hass)
    hass.log = MagicMock()
    hass.call_service = MagicMock()
    hass.run_in = MagicMock()
    hass.list_services = MagicMock(
        return_value=[
            {"service": "mobile_app_john"},
            {"service": "mobile_app_jane"},
        ]
    )
    return hass


@pytest.fixture
def notifier(mock_hass):
    """Create an instance of Notifier with mocked dependencies."""
    c.ADMIN_MOBILE_NAME = "john"  # Set an admin user for the tests
    c.PRIORITY_NOTIFICATION_CONFIG = {"priority": "high"}
    c.HA_NAME = "HomeAssistant"
    return Notifier(hass=mock_hass)


def test_notify_user_to_admin(notifier, mock_hass):
    """Test notify_user sends notifications to the admin."""
    notifier.notify_user(
        message="Test message",
        title="Test Title",
        tag="test_tag",
        critical=True,
    )

    # Verify the correct notification is sent
    mock_hass.call_service.assert_called_with(
        "notify/mobile_app_john",
        title="V2G-L: Test Title",
        message="Test message [HomeAssistant]",
        data={"priority": "high", "tag": "test_tag"},
    )


def test_notify_user_to_all(notifier, mock_hass):
    """Test notify_user sends notifications to all recipients."""
    notifier.notify_user(
        message="Test message",
        title="Test Title",
        send_to_all=True,
    )

    # Verify notifications are sent to all recipients
    mock_hass.call_service.assert_has_calls(
        [
            call(
                "notify/mobile_app_jane",
                title="V2G-L: Test Title",
                message="Test message [HomeAssistant]",
            ),
            call(
                "notify/mobile_app_john",
                title="V2G-L: Test Title",
                message="Test message [HomeAssistant]",
            ),
        ],
        any_order=True,
    )


def test_clear_notification(notifier, mock_hass):
    """Test clear_notification clears notifications for the given recipients."""
    notifier.clear_notification(tag="test_tag", recipients=["john", "jane"])

    # Verify the clear notification service is called for all recipients
    mock_hass.call_service.assert_has_calls(
        [
            call(
                "notify/mobile_app_john",
                message="clear_notification",
                data={"tag": "test_tag"},
            ),
            call(
                "notify/mobile_app_jane",
                message="clear_notification",
                data={"tag": "test_tag"},
            ),
        ],
        any_order=True,
    )


def test_post_sticky_memo(notifier, mock_hass):
    """Test post_sticky_memo creates a persistent notification."""
    notifier.post_sticky_memo(
        message="Sticky memo test",
        title="Sticky Title",
        memo_id="sticky_id",
    )

    # Verify the persistent notification service is called
    mock_hass.call_service.assert_called_with(
        service="persistent_notification/create",
        title="Sticky Title",
        message="Sticky memo test",
        notification_id="sticky_id",
    )
