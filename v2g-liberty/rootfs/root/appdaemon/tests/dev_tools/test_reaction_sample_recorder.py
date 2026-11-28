"""The reaction sample recorder: one CSV row per ``reaction_sample`` event.
Built with ``object.__new__`` to skip ``hass.Hass.__init__``, like the
emulator tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from dev_tools.reaction_sample_recorder import HEADER, ReactionSampleRecorder

CET = timezone(timedelta(hours=1))
T0 = datetime(2026, 12, 10, 12, 0, tzinfo=CET)


class FakeBus:
    """Enough of the EventBus to count listeners and deliver an event."""

    def __init__(self):
        self.listeners = []

    def add_event_listener(self, event, f):
        assert event == "reaction_sample"
        self.listeners.append(f)

    def remove_event_listener(self, event, f):
        assert event == "reaction_sample"
        self.listeners.remove(f)

    def emit(self, **kwargs):
        for f in list(self.listeners):
            f(**kwargs)


def make_recorder(path, bus=None, **args):
    recorder = object.__new__(ReactionSampleRecorder)
    recorder.args = {"path": str(path), **args}
    recorder.log = MagicMock()
    app = MagicMock()
    app.event_bus = MagicMock() if bus is None else bus
    recorder.get_app = MagicMock(return_value=app)
    recorder.initialize()
    return recorder


def warnings_of(recorder):
    return [
        call.args[0]
        for call in recorder.log.call_args_list
        if call.kwargs.get("level") == "WARNING"
    ]


def test_it_listens_on_the_apps_event_bus(tmp_path):
    bus = MagicMock()
    recorder = make_recorder(tmp_path / "s.csv", bus)
    recorder.get_app.assert_called_once_with("v2g-app")
    bus.add_event_listener.assert_called_once_with(
        "reaction_sample", recorder.on_sample
    )
    assert warnings_of(recorder) == []


def test_each_sample_is_a_row_under_one_header(tmp_path):
    """With the rule in force on every row (plan, react, reference, the
    band's outer ends); a field an older engine does not send stays empty."""
    path = tmp_path / "s.csv"
    recorder = make_recorder(path)
    recorder.on_sample(
        time=T0,
        phase=1,
        deviation_w=3000,
        raw_w=1400,
        setpoint_w=1400,
        plan_w=4400,
        meter_w=3000,
        charger_w=4400,  # not recorded
        react=1.0,
        reference_w=-4400,
        band_lo_w=1400,
        band_hi_w=7400,
    )
    recorder.on_sample(
        time=T0 + timedelta(seconds=1),
        phase=3,
        deviation_w=5000,
        raw_w=-600,
        setpoint_w=1400,
    )
    assert path.read_text().splitlines() == [
        HEADER,
        "2026-12-10T12:00:00.000+01:00,3000,1400,1400,1,4400,1,-4400,1400,7400",
        "2026-12-10T12:00:01.000+01:00,5000,-600,1400,3,,,,,",
    ]
    assert HEADER.split(",")[5:] == ["plan", "react", "reference", "band_lo", "band_hi"]


def test_a_file_with_the_old_columns_is_rotated_aside_at_start(tmp_path):
    """Rows under a header must match it: a file an older recorder started
    goes to .1, and the new rows start a new file with the new header."""
    path = tmp_path / "s.csv"
    old_rows = (
        "time,surprise,raw_setpoint,clipped_setpoint,phase\n"
        "2026-12-10T11:59:59.000+01:00,3000,1400,1400,1\n"
    )
    path.write_text(old_rows)
    recorder = make_recorder(path)
    recorder.on_sample(time=T0, deviation_w=0, raw_w=4400, setpoint_w=4400)
    assert (tmp_path / "s.csv.1").read_text() == old_rows
    assert path.read_text().splitlines()[0] == HEADER
    assert any("other columns" in call.args[0] for call in recorder.log.call_args_list)


def test_a_file_with_the_current_columns_is_kept_at_start(tmp_path):
    path = tmp_path / "s.csv"
    path.write_text(HEADER + "\n2026-12-10T11:59:59.000+01:00,0,4400,4400,1,,,,,\n")
    make_recorder(path).on_sample(time=T0, deviation_w=0, raw_w=4400, setpoint_w=4400)
    assert not (tmp_path / "s.csv.1").exists()
    assert len(path.read_text().splitlines()) == 3


def test_a_removed_file_comes_back_with_its_header(tmp_path):
    """Deleting the file to start afresh, with the app running, is fine."""
    path = tmp_path / "s.csv"
    recorder = make_recorder(path)
    recorder.on_sample(time=T0, deviation_w=0, raw_w=4400, setpoint_w=4400)
    path.unlink()
    recorder.on_sample(time=T0, deviation_w=0, raw_w=4400, setpoint_w=4400)
    lines = path.read_text().splitlines()
    assert lines[0] == HEADER
    assert len(lines) == 2


def test_an_unwritable_path_warns_once_and_gives_up(tmp_path):
    recorder = make_recorder(tmp_path / "missing" / "s.csv")
    recorder.on_sample(time=T0, deviation_w=0, raw_w=0, setpoint_w=0)
    recorder.on_sample(time=T0, deviation_w=0, raw_w=0, setpoint_w=0)
    warnings = warnings_of(recorder)
    assert len(warnings) == 1
    assert "Could not write" in warnings[0]


def test_without_an_event_bus_it_warns_and_records_nothing(tmp_path):
    recorder = object.__new__(ReactionSampleRecorder)
    recorder.args = {"path": str(tmp_path / "s.csv")}
    recorder.log = MagicMock()
    recorder.get_app = MagicMock(return_value=MagicMock(spec=[]))  # no event_bus
    recorder.initialize()
    assert len(warnings_of(recorder)) == 1


def test_a_restart_of_the_recorder_leaves_one_listener(tmp_path):
    """AppDaemon restarts the recorder on its own when its config or module
    changes, while v2g-app and its bus live on: terminate() must take the
    old listener off, or every sample is written twice."""
    path = tmp_path / "s.csv"
    bus = FakeBus()
    first = make_recorder(path, bus)
    first.terminate()
    second = make_recorder(path, bus)
    assert bus.listeners == [second.on_sample]
    bus.emit(time=T0, phase=1, deviation_w=0, raw_w=4400, setpoint_w=4400)
    assert len(path.read_text().splitlines()) == 2  # header and one row


def test_terminate_without_a_bus_is_harmless(tmp_path):
    recorder = object.__new__(ReactionSampleRecorder)
    recorder.args = {"path": str(tmp_path / "s.csv")}
    recorder.log = MagicMock()
    recorder.get_app = MagicMock(return_value=MagicMock(spec=[]))
    recorder.initialize()
    recorder.terminate()


def test_the_file_is_rotated_at_max_bytes_and_the_oldest_dropped(tmp_path):
    """Like a rotating log: the full file becomes .1, older ones shift up,
    at most max_files are kept; the live file starts afresh with its header,
    under the same name, so a follower keeps following."""
    bus = FakeBus()
    path = tmp_path / "s.csv"
    recorder = make_recorder(path, bus, max_bytes=150, max_files=2)
    for n in range(12):  # a header and a row are ~100 bytes together
        bus.emit(
            time=T0 + timedelta(seconds=n),
            deviation_w=-5000 - n,
            raw_w=5000,
            setpoint_w=5000,
            phase=1,
        )
    rotated = sorted(p.name for p in tmp_path.iterdir())
    assert rotated == ["s.csv", "s.csv.1", "s.csv.2"]  # never a .3
    for name in rotated:
        lines = (tmp_path / name).read_text().splitlines()
        assert lines[0] == HEADER
        assert len(lines) >= 2
    # The newest rows are in the live file, the oldest kept are in .2, and
    # the very oldest were dropped with the rotation beyond max_files.
    newest = path.read_text().splitlines()[-1]
    assert newest.endswith(",-5011,5000,5000,1,,,,,")
    assert any("Rotated" in call.args[0] for call in recorder.log.call_args_list)
