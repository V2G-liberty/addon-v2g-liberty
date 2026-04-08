"""Async timer helpers for AppDaemon 4.

AppDaemon 4 turned ``run_in``, ``run_at``, ``run_daily``, ``timer_running``
and ``cancel_timer`` into async coroutines that must be awaited. Calling
them synchronously silently discards the coroutine, which means timers
are never created and cancels never happen — the original cause of the
``set_next_action`` watchdog leak.

These helpers centralise the correct ``await`` usage and the
"cancel previous, schedule new" pattern so every module uses the same
solid implementation.
"""

from typing import Any, Awaitable, Callable, Optional


async def cancel_timer_silent(hass, timer_id: Optional[str]) -> None:
    """Cancel an AppDaemon timer if it is still running.

    Accepts ``None`` and empty-string handles as a no-op so callers do not
    have to guard. Swallows any exception from the underlying AppDaemon
    call to avoid noisy logs when a timer was already cleaned up.
    """
    if not timer_id:
        return
    try:
        running = await hass.timer_running(timer_id)
    except Exception:  # pylint: disable=broad-exception-caught
        # AppDaemon's timer API can raise undocumented exceptions when the
        # timer was already cleaned up. Treat as "not running" — we don't
        # want to crash the caller over a missing timer.
        running = False
    if not running:
        return
    try:
        await hass.cancel_timer(timer_id, silent=True)
    except Exception:  # pylint: disable=broad-exception-caught
        # The "silent" flag in AppDaemon does not always suppress warnings,
        # and the call itself can raise on stale handles. Swallow on purpose.
        pass


async def set_oneshot_timer(
    hass,
    current_handle: Optional[str],
    callback: Callable[..., Awaitable[Any]],
    delay: float,
    **kwargs: Any,
) -> str:
    """Schedule a one-shot timer, cancelling any previous handle first.

    Returns the new (real) timer handle. Use this instead of the
    ``if handle: cancel; handle = run_in(...)`` pattern so the timer
    bookkeeping cannot leak coroutines.

    Args:
        hass: AppDaemon Hass instance with the async timer API.
        current_handle: Existing timer handle to cancel first, or ``None``
            / empty string on the first call.
        callback: Async callable that AppDaemon will invoke after ``delay``
            seconds.
        delay: Delay in seconds.
        **kwargs: Forwarded to ``hass.run_in`` (and ultimately to the
            callback as keyword arguments).
    """
    await cancel_timer_silent(hass, current_handle)
    return await hass.run_in(callback, delay=delay, **kwargs)


async def set_recurring_timer(
    hass,
    current_handle: Optional[str],
    callback: Callable[..., Awaitable[Any]],
    start: Any,
    interval: float,
    **kwargs: Any,
) -> str:
    """Schedule a recurring timer, cancelling any previous handle first.

    Returns the new (real) timer handle.

    Args:
        hass: AppDaemon Hass instance.
        current_handle: Existing timer handle to cancel first, or ``None``
            / empty string on the first call.
        callback: Async callable invoked on each tick.
        start: First-fire time, forwarded to ``hass.run_every`` (e.g. the
            string ``"now"`` or a datetime).
        interval: Tick interval in seconds.
        **kwargs: Forwarded to ``hass.run_every``.
    """
    await cancel_timer_silent(hass, current_handle)
    return await hass.run_every(callback, start, interval, **kwargs)


async def set_daily_timer(
    hass,
    current_handle: Optional[str],
    callback: Callable[..., Awaitable[Any]],
    start: Any,
    **kwargs: Any,
) -> str:
    """Schedule a daily-recurring timer, cancelling any previous handle first.

    Returns the new (real) timer handle.

    Args:
        hass: AppDaemon Hass instance.
        current_handle: Existing timer handle to cancel first, or ``None``
            / empty string on the first call.
        callback: Async callable invoked once per day.
        start: ``datetime.time`` or ISO time string at which to fire,
            forwarded to ``hass.run_daily``.
        **kwargs: Forwarded to ``hass.run_daily``.
    """
    await cancel_timer_silent(hass, current_handle)
    return await hass.run_daily(callback, start=start, **kwargs)
