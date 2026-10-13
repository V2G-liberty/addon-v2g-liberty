"""Utility module to handle sending notifications to users"""

from typing import Optional, Callable
from appdaemon.exceptions import ServiceException
from appdaemon.plugins.hass.hassapi import Hass
from .event_bus import EventBus

from . import constants as c
from .log_wrapper import get_class_method_logger
import uuid


class Notifier:
    """
    Utility class to handle sending notifications to users:
    + App notifications via HA companion app
    + Persistent notifications via HA

    """

    event_bus: EventBus = None
    hass: Hass = None
    recipients: list
    _notification_callbacks: dict = {}

    ADMIN_MISSING_MEMO_ID = "admin_mobile_missing"

    ########################################################################
    #                         INITIALISATIO METHOD                         #
    ########################################################################

    def __init__(self, hass: Hass, event_bus: EventBus):
        self.hass = hass
        self.__log = get_class_method_logger(module_name="notifier_util")
        self.recipients = []
        # None until the first check, so that one always posts or dismisses.
        self._admin_missing_reported: bool | None = None

        self.event_bus = event_bus
        # not used yet
        # self.event_bus.add_event_listener("notify_user", self.notify_user)
        # self.event_bus.add_event_listener("clear_notification", self.clear_notification)

        # Fired in UI / custom_card
        self.hass.listen_event(self._send_test_notification, "send_test_notification")

        # Fired when user clicks a button on an actionable notification
        self.hass.listen_event(
            self.__handle_notification_action, event="mobile_app_notification_action"
        )

        # MOVED: Don't call _get_recipients() here - will be called in initialize()
        self.__log(
            "Completed initialization of Notifier (recipients will be loaded in initialize())"
        )

    async def initialize(self):
        """Async initialization - must be called after __init__"""
        self.__log("Initializing Notifier (async)")
        self._get_recipients()
        # AppDaemon's own service list never drops a removed service, so a phone
        # that leaves Home Assistant while the app runs is followed here.
        await self.hass.listen_event(self.__handle_service_change, "service_registered")
        await self.hass.listen_event(self.__handle_service_change, "service_removed")
        self.__log("Completed async initialization of Notifier")

    ########################################################################
    #                            PUBLIC METHODS                            #
    ########################################################################

    async def notify_user(
        self,
        message: str,
        title: Optional[str] = None,
        tag: Optional[str] = None,
        critical: bool = False,
        send_to_all: bool = False,
        ttl: Optional[int] = 0,
        actions: Optional[list] = None,
        callback: Optional[Callable] = None,
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
                Send with high priority to Admin only. Always delivered and sound is played.
                Use with caution. Defaults to False.
            send_to_all (bool, optional):
                Send to all users (can't be combined with critical), default = only send to Admin.
                Defaults to False.
            ttl (Optional[int], optional):
                Time to live in seconds, after that the message will be cleared. 0 = do not clear.
                A tag is required. Defaults to 0.
            actions (list, optional):
                A list of dicts with action and title strings. Defaults to None.
            callback (Callable, optional):
                An async(!) callable that will be called with the selected action name when the user responds.
                Defaults to None. A tag is needed for this to work.
        """

        if not self.recipients:
            self.__log(
                "No registered devices to notify, cancel notification.", level="WARNING"
            )
            return

        # Use abbreviation to make more room for title itself.
        title = title if title else "V2G Liberty"

        if tag is None:
            tag = str(uuid.uuid4())

        if callback is not None:
            self._register_notification_callback(tag, callback)

        notification_data = {"tag": tag}
        if actions:
            # Prepend the tag to each action for later matching with callback
            notification_data["actions"] = [
                {"action": f"{tag}~{action['action']}", "title": action["title"]}
                for action in actions
            ]

        message = message + " [" + c.HA_NAME + "]"

        if send_to_all and not critical:
            to_notify = list(self.recipients)
            await self.__send_to_each(to_notify, title, message, notification_data)
        else:
            # Meant for the administrator; critical trumps send_to_all.
            to_notify = await self.__notify_admin(
                title, message, notification_data, critical
            )

        if ttl is not None and ttl > 0 and tag and not critical:
            # Remove the notification after a time-to-live.
            # A tag is required for clearing.
            # Critical notifications should never auto clear.
            await self.hass.run_in(
                self._clear_notification_after_ttl,
                delay=ttl,
                recipients=to_notify,
                tag=tag,
            )

    async def __notify_admin(
        self, title: str, message: str, notification_data: dict, critical: bool
    ) -> list:
        """Send to the administrator's phone, or to all other phones when Home
        Assistant does not know that phone (any more) or none is set.

        Returns the phones it was sent to.
        """
        admin = c.ADMIN_MOBILE_NAME
        if self.__admin_is_known():
            data = notification_data
            if critical:
                self.__log(f"Critical! Send to: ['{admin}'].")
                # A copy: the shared config must not collect tags and actions.
                data = {**(c.PRIORITY_NOTIFICATION_CONFIG or {}), **notification_data}
            outcome = await self.__send(admin, title, message, data)
            if outcome != "not_found":
                if outcome == "ok":
                    self.__report_admin_missing(False)
                return [admin]
        # A notification for the administrator must not vanish because one
        # phone is gone. The others get it at normal priority: the critical
        # settings are the administrator's platform's, not theirs.
        if admin:
            self.__report_admin_missing(True)
        others = [r for r in self.recipients if r != admin]
        self.__log(
            f"Administrator phone '{admin}' not available, sending to: {others}.",
            level="WARNING",
        )
        await self.__send_to_each(others, title, message, notification_data)
        return others

    async def __send_to_each(
        self, recipients: list, title: str, message: str, data: dict
    ):
        for recipient in recipients:
            await self.__send(recipient, title, message, data)

    async def __send(self, recipient: str, title: str, message: str, data: dict) -> str:
        """Send to one phone, and say how that went: "ok", "not_found" (Home
        Assistant or AppDaemon does not know the phone) or "failed".

        Awaited, so a failure is seen here: without the await AppDaemon runs the
        call as a separate task, and its error only reached the error log.
        """
        self.__log(
            f"Notifying '{recipient}' with message: '{message[0:15]}...' data: {data}."
        )
        try:
            result = await self.hass.call_service(
                "notify/mobile_app_" + recipient,
                title=title,
                message=message,
                data=data,
            )
        except ServiceException:
            self.__log(
                f"Could not notify '{recipient}': unknown phone.", level="WARNING"
            )
            return "not_found"
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.__log(f"Could not notify '{recipient}': {e}.", level="WARNING")
            return "failed"
        if isinstance(result, dict) and result.get("success") is False:
            error = result.get("error") or {}
            self.__log(
                f"Could not notify '{recipient}': {error or result}.", level="WARNING"
            )
            return "not_found" if error.get("code") == "not_found" else "failed"
        return "ok"

    def __admin_is_known(self) -> bool:
        return bool(c.ADMIN_MOBILE_NAME) and c.ADMIN_MOBILE_NAME in self.recipients

    def check_admin_mobile(self):
        """Report a configured administrator phone that Home Assistant does not
        know (any more), or clear that report. Called at start-up, after the
        administrator setting is saved, and when phones come and go. No phone
        set at all is not reported here: the settings screen already shows it.
        """
        if not c.ADMIN_MOBILE_NAME:
            self.__report_admin_missing(False)
            return
        self.__report_admin_missing(not self.__admin_is_known())

    def __report_admin_missing(self, missing: bool):
        if missing == self._admin_missing_reported:
            return
        self._admin_missing_reported = missing
        if not missing:
            self.dismiss_sticky_memo(self.ADMIN_MISSING_MEMO_ID)
            return
        self.__log(
            f"Administrator phone '{c.ADMIN_MOBILE_NAME}' is not known in "
            f"Home Assistant.",
            level="WARNING",
        )
        self.post_sticky_memo(
            title="Administrator phone not found",
            message=(
                f"The administrator phone '{c.ADMIN_MOBILE_NAME}' is no longer "
                "known in Home Assistant. Notifications for the administrator, "
                "critical ones included, now go to all phones.<br/>"
                "Choose another administrator phone in the V2G Liberty settings."
            ),
            memo_id=self.ADMIN_MISSING_MEMO_ID,
        )

    async def __handle_service_change(self, event_name, data, kwargs):
        """Follow phones that are added to or removed from Home Assistant."""
        if data.get("domain") != "notify":
            return
        service = data.get("service", "")
        if not service.startswith("mobile_app_"):
            return
        name = service.removeprefix("mobile_app_")
        if event_name == "service_registered":
            if name in self.recipients:
                return
            self.recipients = sorted([*self.recipients, name])
        else:
            if name not in self.recipients:
                return
            self.recipients = [r for r in self.recipients if r != name]
        self.__log(f"{event_name} '{name}': recipients now {self.recipients}.")
        self.check_admin_mobile()

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

    async def _clear_notification_after_ttl(self, kwargs: dict):
        """Scheduled (``run_in``) callback that clears a notification after its ttl.

        AppDaemon passes the scheduling kwargs as a single positional dict, so the
        tag and recipients are read from it here. Scheduling ``clear_notification``
        directly bound that whole dict to its ``tag`` parameter, so the ttl clear
        never matched the real tag and the notification was never removed.
        """
        self.clear_notification(
            tag=kwargs.get("tag"),
            recipients=kwargs.get("recipients"),
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
            self.__log(f"Failed. Exception: '{e}'.", level="WARNING")

    def dismiss_sticky_memo(self, memo_id: str):
        """
        Dismiss a sticky memo (persistent notification) previously posted with
        post_sticky_memo, identified by memo_id.

        Args:
            memo_id (str):
                The id used when the memo was created via post_sticky_memo.
        """
        try:
            self.hass.call_service(
                service="persistent_notification/dismiss",
                notification_id=memo_id,
            )
        except Exception as e:
            self.__log(f"Failed. Exception: '{e}'.", level="WARNING")

    ########################################################################
    #                       PRIVATE (UTILITY) FUNCTIONS                    #
    ########################################################################

    def _register_notification_callback(self, tag: str, callback: Callable):
        self.__log(f"Callback '{callback}' registered under tag '{tag}'.")
        self._notification_callbacks[tag] = callback

    async def __handle_notification_action(self, event_name, data, kwargs):
        """To be called when the user takes action on a actionable notification.

        :param event_name: always the same: mobile_app_notification_action
        :param data: holds the action, do not use a tilde (~) in this string
        :param kwargs: not used
        :return: nothing
        """

        action_data = data["action"]

        if action_data is None or action_data == "":
            self.__log("Aborting: action_data is empty.")
            return

        action_parts = str(action_data).split("~")
        tag = action_parts[0]
        action = action_parts[1]
        self.__log(f"Retrieved tag: '{tag}' and action: '{action}' from action_data.")

        if tag in self._notification_callbacks:
            try:
                callback = self._notification_callbacks[tag]
                await callback(action)  # Call the callback with the action name
                del self._notification_callbacks[tag]  # Clean up the callback
            except Exception as e:
                self.__log(
                    f"Could not call callback '{callback}', exception: '{e}'.",
                    level="WARNING",
                )

    async def _cb_test_notification(self, user_action, *args):
        loud_alarm = user_action == "test_notification_loud_alarm"
        self.__log(
            f"test notification action: '{user_action}', loud_alarm={loud_alarm}."
        )
        self.hass.fire_event("send_test_notification.result", loud_alarm=loud_alarm)

    async def _send_test_notification(self, event, data, kwargs):
        user_actions = [
            {
                "action": "test_notification_loud_alarm",
                "title": data["notificationLoudAlarmLabel"],
            },
            {
                "action": "test_notification_soft_or_no_sound",
                "title": data["notificationSoftOrNoSoundLabel"],
            },
        ]
        await self.notify_user(
            message=data["notificationMessage"],
            title=data["notificationTitle"],
            tag="test_notification",
            critical=True,
            send_to_all=False,
            ttl=None,
            actions=user_actions,
            callback=self._cb_test_notification,
        )

    def _get_recipients(self):
        """Get list of mobile devices for notifications.
        Called during async initialization when Home Assistant domains are available.
        """
        self.__log("getting recipients")
        # TODO: Add a listener for changes in registered devices (smartphones with HA installed)?

        try:
            # Service "mobile_app_" seems more reliable than using get_trackers,
            # as these names do not always match with the service.
            for service in self.hass.list_services():
                if service["service"].startswith("mobile_app_"):
                    self.recipients.append(
                        service["service"].replace("mobile_app_", "")
                    )
        except Exception as e:
            self.__log(
                f"Failed to list services: {e}. Will retry later.", level="WARNING"
            )
            return

        if len(self.recipients) == 0:
            message = (
                "No mobile devices (e.g. phone, tablet, etc.) have been registered in "
                "Home Assistant for notifications.<br/>It is highly recommended to do so. "
                "Please install the HA companion app on your mobile device and connect it to "
                "Home Assistant. Then restart Home Assistant and the V2G Liberty add-on."
            )
            self.__log(f"Configuration warning: {message}.")
            # TODO: Research if showing this only to admin users is possible.
            try:
                self.post_sticky_memo(
                    title="Configuration warning",
                    message=message,
                    memo_id="notification_config_error",
                )
            except Exception as e:
                self.__log(f"Failed to post sticky memo: {e}", level="WARNING")
        else:
            self.recipients = list(set(self.recipients))  # Remove duplicates
            self.recipients.sort()
            self.__log(f"recipients for notifications: {self.recipients}.")

        self.__log("Completed get recipients")
