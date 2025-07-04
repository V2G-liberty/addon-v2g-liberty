"""Utility module to handle sending notifications to users"""

from typing import Optional

import constants as c
import log_wrapper
from appdaemon.plugins.hass.hassapi import Hass


class Notifier:
    """
    Utility class to handle sending notifications to users:
    + App notifications via HA companion app
    + Persistent notifications via HA

    """

    # TODO: Discuss if notifications via eventbus might be a good idea?
    # event_bus: EventBus = None
    hass: Hass = None
    recipients: list = []

    # def __init__(self, hass: Hass, event_bus: EventBus):

    def __init__(self, hass: Hass):
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)

        # self.event_bus = event_bus
        # self.event_bus.add_event_listener("notify_user", self.notify_user)
        # self.event_bus.add_event_listener("clear_notification", self.clear_notification)

        # TODO: Add listener to see if any devices are (de-) registered
        # self.hass.listen_event(self.entity_registry_listener, "entity_registry_updated")

        # Make __init__() run quick, no need to wait for initialisation
        self.hass.run_in(self._get_recipients, delay=1)
        self.__log("Completed __init__ Notifier")

    async def _get_recipients(self, kwargs):
        # List of all the recipients to notify
        # Warn user about bad config with sticky memo in UI.

        self.__log("getting recipients")
        # TODO: Add a listener for changes in registered devices (smartphones with HA installed)?

        # Service "mobile_app_" seems more reliable than using get_trackers,
        # as these names do not always match with the service.
        for service in self.hass.list_services():
            if service["service"].startswith("mobile_app_"):
                self.recipients.append(service["service"].replace("mobile_app_", ""))

        if len(self.recipients) == 0:
            message = (
                "No mobile devices (e.g. phone, tablet, etc.) have been registered in "
                "Home Assistant for notifications.<br/>It is highly recommended to do so. "
                "Please install the HA companion app on your mobile device and connect it to "
                "Home Assistant. Then restart Home Assistant and the V2G Liberty add-on."
            )
            self.__log(f"Configuration error: {message}.")
            # TODO: Research if showing this only to admin users is possible.
            self.post_sticky_memo(
                title="Configuration error",
                message=message,
                memo_id="notification_config_error",
            )
        else:
            self.recipients = list(set(self.recipients))  # Remove duplicates
            self.recipients.sort()
            self.__log(f"recipients for notifications: {self.recipients}.")

        self.__log("Completed get recipients")

    def notify_user(
        self,
        message: str,
        title: Optional[str] = None,
        tag: Optional[str] = None,
        critical: bool = False,
        send_to_all: bool = False,
        ttl: Optional[int] = 0,
        actions: Optional[list] = None,
    ):
        """
        Method to send notifications to the user. An ADMIN is assumed always to be
        configured and there might be several other users that need to be notified.
        When a new call to this function with the same tag is made, the previous message will be
        overwritten if it still exists.

        Args:
            message (str):
                Message text to be sent.

            title (Optional[str], optional):
                Title of the message. Defaults to None.

            tag (Optional[str], optional):
                ID that can be used to replace or clear a previous message. Defaults to None.

            critical (bool, optional):
                Send with high priority to Admin only. Always delivered and sound is play.
                Use with caution.. Defaults to False.

            send_to_all (bool, optional):
                Send to all users (can't be combined with critical), default = only send to Admin.
                Defaults to False.

            ttl (Optional[int], optional):
                Time to live in seconds, after that the message will be cleared. 0 = do not clear.
                A tag is required. Defaults to 0.

            actions (list, optional):
                A list of dicts with action and title stings. Defaults to None.
        """

        if c.ADMIN_MOBILE_NAME == "" and not self.recipients:
            self.__log(
                "No registered devices to notify, cancel notification.", level="WARNING"
            )
            return

        # All notifications always get sent to admin
        to_notify = [c.ADMIN_MOBILE_NAME]

        # Use abbreviation to make more room for title itself.
        title = "V2G-L: " + title if title else "V2G Liberty"

        notification_data = {}

        # critical trumps send_to_all
        if critical:
            self.__log(f"notify_user: Critical! Send to: {to_notify}.")
            notification_data = c.PRIORITY_NOTIFICATION_CONFIG

        if send_to_all and not critical:
            self.__log(
                f"notify_user: Send to all and not critical! Send to: {to_notify}."
            )
            to_notify = self.recipients

        if tag:
            notification_data["tag"] = tag

        if actions:
            notification_data["actions"] = actions

        message = message + " [" + c.HA_NAME + "]"

        self.__log(
            f"Notifying recipients: {to_notify} with message: '{message[0:15]}...'"
            f"data: {notification_data}."
        )
        for recipient in to_notify:
            service = "notify/mobile_app_" + recipient
            try:
                if notification_data:
                    self.hass.call_service(
                        service, title=title, message=message, data=notification_data
                    )
                else:
                    self.hass.call_service(service, title=title, message=message)
            except Exception as e:
                self.__log(
                    f"notify_user. Could not notify: exception on {recipient}. Exception: {e}."
                )

        if ttl > 0 and tag and not critical:
            # Remove the notification after a time-to-live.
            # A tag is required for clearing.
            # Critical notifications should never auto clear.
            self.hass.run_in(
                self.clear_notification, delay=ttl, recipients=to_notify, tag=tag
            )

    def clear_notification(self, tag: str, recipients: Optional[list] = None):
        """
        Clear a notification sent earlier (or not)

        Args:
        - tag(str): unique id for notification
        - recipients(list): optional list of recipients. If none is given it is assumed the
          notification for all users needs to be removed.
        """
        self.__log(f"Clearing notification. {tag=}, {recipients=}.")

        if tag == "" or tag is None:
            self.__log(f"Cannot clear notification, tag is empty '{tag}'.")
            return

        if recipients is None or recipients == "all":
            recipients = self.recipients

        for recipient in recipients:
            # Clear the notification
            try:
                self.hass.call_service(
                    "notify/mobile_app_" + recipient,
                    message="clear_notification",
                    data={"tag": tag},
                )
            except Exception as e:
                self.__log(
                    f"Could not clear notification: exception on {recipient}. Exception: {e}."
                )

    def post_sticky_memo(
        self, message: str, title: Optional[str] = None, memo_id: Optional[str] = None
    ):
        """
        Post a sticky memo in HA (aka persistant notification) shown in the bottom left menu.

        Args:
            message (str):
                Message text to be presented.

            title (Optional[str], optional):
                Title of the message. Defaults to None.

            memo_id (Optional[str], optional):
                ID that can be used to replace or clear a previous message. Defaults to None.
        """
        try:
            self.hass.call_service(
                service="persistent_notification/create",
                title=title,
                message=message,
                notification_id=memo_id,
            )
        except Exception as e:
            self.__log(f"failed. Exception: '{e}'.", level="WARNING")
