"""Recovery probe: notices when a charger that was given up on comes back."""

from collections.abc import Awaitable, Callable

from ..timer_utils import cancel_timer_silent, set_recurring_timer


class RecoveryProbe:
    """Periodically re-checks a charger after an un-recoverable error.

    Escalation stops the polling, so nothing would otherwise notice the charger
    returning: recovery used to be a manual restart of charger and app. The
    probe runs ``check`` every ``interval_seconds``; the first True cancels the
    probe and runs ``on_recovered``. Whatever ``check`` raises counts as "not
    yet" -- the transport raises on a dead connection, and that is the expected
    state while probing.

    One probe at a time. The handle is claimed before the first await, so two
    escalations landing together (the Modbus and the error-state timer can)
    cannot both arm a timer and orphan one.
    """

    def __init__(
        self,
        hass,
        log,
        interval_seconds: int,
        check: Callable[[], Awaitable[bool]],
        on_recovered: Callable[[], Awaitable[None]],
    ):
        self._hass = hass
        self._log = log
        self._interval = interval_seconds
        self._check = check
        self._on_recovered = on_recovered
        self._timer_handle: str | None = None

    @property
    def is_armed(self) -> bool:
        return self._timer_handle is not None

    async def arm(self) -> None:
        if self._timer_handle is not None:
            self._log("Recovery probe already armed.", level="DEBUG")
            return
        # Claim the slot before the await below; a second escalation arriving
        # meanwhile sees the claim and backs off.
        self._timer_handle = ""
        try:
            # AppDaemon (4.5) schedules a recurring timer's first run at
            # now + interval + N for a "now+N" start, so "now" is what gives
            # the first check after exactly one interval.
            handle = await set_recurring_timer(
                self._hass,
                None,
                self._tick,
                start="now",
                interval=self._interval,
            )
        except Exception as ex:  # pylint: disable=broad-exception-caught
            # Release the claim so a later escalation can try again.
            self._timer_handle = None
            self._log(f"Could not arm the recovery probe: {ex}", level="WARNING")
            return
        if self._timer_handle is None:
            # Cancelled while the timer was being created (a manual recovery
            # landed in between): do not keep a timer nobody asked for.
            await cancel_timer_silent(self._hass, handle)
            return
        self._timer_handle = handle
        self._log(f"Recovery probe armed, checking every {self._interval}s.")

    async def cancel(self) -> None:
        if self._timer_handle is None:
            return
        await cancel_timer_silent(self._hass, self._timer_handle)
        self._timer_handle = None

    async def _tick(self, kwargs=None) -> None:
        try:
            healthy = await self._check()
        except Exception as ex:  # pylint: disable=broad-exception-caught
            self._log(f"Recovery probe: charger not back yet ({ex}).")
            return
        if not healthy:
            self._log("Recovery probe: charger reachable but still reporting an error.")
            return
        await self.cancel()
        await self._on_recovered()
