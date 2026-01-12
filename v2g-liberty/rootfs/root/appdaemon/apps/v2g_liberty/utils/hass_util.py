"""Utility functions for working with Home Assistant / AppDaemon."""


def cancel_timer_silently(hass, timer_id: str) -> None:
    """Utility function to silently cancel a timer.

    The "silent" flag in cancel_timer does not work properly and the
    logs get flooded with useless warnings. This function provides a
    workaround by checking if the timer is running before canceling.

    Args:
        hass: Home Assistant API object (AppDaemon Hass instance)
        timer_id: timer_handle to cancel, can be None or empty string

    Returns:
        None
    """
    if timer_id in [None, ""]:
        return
    if hass.timer_running(timer_id):
        silent = True  # Does not really work, but we try anyway
        hass.cancel_timer(timer_id, silent)
