"""Records the reaction engine's verdict per meter sample to a CSV a probe
can follow live.

Dev-only: stripped from the add-on image with the rest of dev_tools. The
recorder listens to ``reaction_sample`` on V2G Liberty's event bus, reached
through the app object, and appends one row per sample::

    time,surprise,raw_setpoint,clipped_setpoint,phase

``time`` is ISO 8601 with offset and milliseconds (when the engine evaluated
the sample), the powers are W, ``phase`` is the phase whose sample it was.
The first four column names are those of Seita's probe, which follows the
file with ``--deviation-csv FILE --surprise-unit W --follow`` and ignores
the rest; the engine's own name for the second column is deviation. Each
row is flushed, and the file is opened per row, so a file removed to start
afresh comes back with its header at the next sample. A path that cannot be
written gives one warning and no further attempts.

A three-phase meter gives a row per phase per second, the other two phases
still at their previous values, so keep the last phase for one complete row
per second. About 1 MB per hour, only while the engine has rules, and never
cleaned up: delete or rotate the file after a test. The row is written on
the event loop, in the engine's own publish call; a slow disk shows up as a
slow engine, acceptable for a dev tool. In the dev container
``/config/logs`` is the repository's ``v2g-liberty/logs/`` folder.
"""

import appdaemon.plugins.hass.hassapi as hass

EVENT = "reaction_sample"
HEADER = "time,surprise,raw_setpoint,clipped_setpoint,phase"
DEFAULT_PATH = "/config/logs/reaction_samples.csv"
V2G_APP = "v2g-app"


class ReactionSampleRecorder(hass.Hass):
    def initialize(self):
        self._path = str(self.args.get("path", DEFAULT_PATH))
        self._failed = False
        self._bus = getattr(self.get_app(V2G_APP), "event_bus", None)
        if self._bus is None:
            self.log(f"{V2G_APP} has no event bus; not recording.", level="WARNING")
            return
        self._bus.add_event_listener(EVENT, self.on_sample)
        self.log(f"Recording reaction samples to {self._path}.")

    def terminate(self):
        """AppDaemon restarts this app on its own when its config or module
        changes, while v2g-app and its bus live on: the old listener must go,
        or every sample is written twice."""
        if self._bus is not None:
            self._bus.remove_event_listener(EVENT, self.on_sample)
            self._bus = None

    def on_sample(self, time, deviation_w, raw_w, setpoint_w, phase="", **_):
        """One row, flushed. The event's other fields are not recorded."""
        if self._failed:
            return
        row = (
            f"{time.isoformat(timespec='milliseconds')},"
            f"{deviation_w},{raw_w},{setpoint_w},{phase}\n"
        )
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                if f.tell() == 0:
                    f.write(HEADER + "\n")
                f.write(row)
        except OSError as e:
            self._failed = True
            self.log(
                f"Could not write {self._path}: {e}; not recording this run.",
                level="WARNING",
            )
