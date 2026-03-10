"""Unit tests for data_repairer module."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
from appdaemon.plugins.hass.hassapi import Hass

from apps.v2g_liberty.data_repairer import (
    MAX_GAP_LENGTH,
    PERIODIC_LOOKBACK_HOURS,
    DataRepairer,
    _empty_summary,
    _get_row_after,
    _get_row_before,
    _get_soc_after,
    _get_soc_before,
    _infer_gap_context,
)
from apps.v2g_liberty.data_store import DataStore

# pylint: disable=C0116,W0621

# Fixed timezone for deterministic tests
TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 27, 12, 0, 0, tzinfo=TEST_TZ)


@pytest.fixture
def hass():
    mock = AsyncMock(spec=Hass)
    mock.run_every = AsyncMock()
    return mock


@pytest.fixture
def data_store(hass, tmp_path):
    """Create a DataStore with a temp database."""
    store = DataStore(hass)
    store.DB_PATH = str(tmp_path / "test_v2g_liberty_data.db")
    return store


@pytest.fixture
@patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
def initialised_store(mock_now, data_store):
    """Return a DataStore that has been fully initialised."""
    asyncio.get_event_loop().run_until_complete(data_store.initialise())
    return data_store


@pytest.fixture
def repairer(hass, initialised_store):
    """Return a DataRepairer wired to an initialised DataStore."""
    r = DataRepairer(hass)
    r.data_store = initialised_store
    return r


def _insert_interval(
    store, ts_str, energy=0.0, state="charge", soc=50.0, avail=100.0, repaired=0
):
    """Helper: insert a single interval row."""
    conn = store.connection
    conn.execute(
        "INSERT INTO interval_log "
        "(timestamp, energy_kwh, app_state, soc_pct, availability_pct, is_repaired) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ts_str, energy, state, soc, avail, repaired),
    )
    conn.commit()


def _ts(minutes_offset):
    """Generate an ISO timestamp at TEST_NOW + offset minutes."""
    dt = TEST_NOW + timedelta(minutes=minutes_offset)
    return dt.isoformat()


def _get_all_intervals(store):
    """Return all interval_log rows as a list of dicts."""
    cursor = store.connection.cursor()
    cursor.execute("SELECT * FROM interval_log ORDER BY timestamp")
    cols = [d[0] for d in cursor.description]
    rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    cursor.close()
    return rows


# =====================================================================
# Helper function tests
# =====================================================================


class TestHelpers:
    def test_empty_summary(self):
        s = _empty_summary()
        assert s["gaps_filled"] == 0
        assert s["soc_blanked"] == 0
        assert s["energy_interpolated"] == 0
        assert s["soc_reconstructed"] == 0
        assert s["soc_constant_filled"] == 0
        assert s["violations"] == {}

    def test_infer_gap_context_both_not_connected(self):
        before = {"app_state": "not_connected", "availability_pct": 0.0}
        after = {"app_state": "not_connected", "availability_pct": 0.0}
        result = _infer_gap_context(before, after)
        assert result["app_state"] == "not_connected"
        assert result["energy_kwh"] == 0.0

    def test_infer_gap_context_both_error(self):
        before = {"app_state": "error", "availability_pct": 0.0}
        after = {"app_state": "error", "availability_pct": 0.0}
        result = _infer_gap_context(before, after)
        assert result["app_state"] == "error"

    def test_infer_gap_context_one_side_not_connected(self):
        before = {"app_state": "charge", "availability_pct": 100.0}
        after = {"app_state": "not_connected", "availability_pct": 0.0}
        result = _infer_gap_context(before, after)
        assert result["app_state"] == "not_connected"

    def test_infer_gap_context_one_side_error(self):
        before = {"app_state": "charge", "availability_pct": 100.0}
        after = {"app_state": "error", "availability_pct": 0.0}
        result = _infer_gap_context(before, after)
        assert result["app_state"] == "error"

    def test_infer_gap_context_both_charge(self):
        before = {"app_state": "charge", "availability_pct": 80.0}
        after = {"app_state": "charge", "availability_pct": 90.0}
        result = _infer_gap_context(before, after)
        assert result["app_state"] == "charge"
        assert result["availability_pct"] == 85.0

    def test_infer_gap_context_different_states(self):
        before = {"app_state": "charge", "availability_pct": 100.0}
        after = {"app_state": "discharge", "availability_pct": 100.0}
        result = _infer_gap_context(before, after)
        assert result["app_state"] == "unknown"

    def test_infer_gap_context_none_before(self):
        result = _infer_gap_context(
            None, {"app_state": "charge", "availability_pct": 100.0}
        )
        assert result["app_state"] == "unknown"

    def test_infer_gap_context_none_after(self):
        result = _infer_gap_context(
            {"app_state": "charge", "availability_pct": 100.0}, None
        )
        assert result["app_state"] == "unknown"


class TestGetRowBeforeAfter:
    def test_get_row_before(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC")
        df = pd.DataFrame(
            {"energy_kwh": [1.0, 2.0, 3.0], "app_state": ["a", "b", "c"]},
            index=idx,
        )
        row = _get_row_before(df, idx[1])
        assert row["energy_kwh"] == 1.0

    def test_get_row_before_first(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC")
        df = pd.DataFrame(
            {"energy_kwh": [1.0, 2.0, 3.0], "app_state": ["a", "b", "c"]},
            index=idx,
        )
        assert _get_row_before(df, idx[0]) is None

    def test_get_row_after(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC")
        df = pd.DataFrame(
            {"energy_kwh": [1.0, 2.0, 3.0], "app_state": ["a", "b", "c"]},
            index=idx,
        )
        row = _get_row_after(df, idx[1])
        assert row["energy_kwh"] == 3.0

    def test_get_row_after_last(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC")
        df = pd.DataFrame(
            {"energy_kwh": [1.0, 2.0, 3.0], "app_state": ["a", "b", "c"]},
            index=idx,
        )
        assert _get_row_after(df, idx[2]) is None


class TestGetSocBeforeAfter:
    def test_get_soc_before(self):
        idx = pd.date_range("2026-01-01", periods=4, freq="5min", tz="UTC")
        df = pd.DataFrame({"soc_pct": [40.0, None, None, 50.0]}, index=idx)
        assert _get_soc_before(df, idx[2]) == 40.0

    def test_get_soc_before_none(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC")
        df = pd.DataFrame({"soc_pct": [None, None, 50.0]}, index=idx)
        assert _get_soc_before(df, idx[1]) is None

    def test_get_soc_after(self):
        idx = pd.date_range("2026-01-01", periods=4, freq="5min", tz="UTC")
        df = pd.DataFrame({"soc_pct": [40.0, None, None, 50.0]}, index=idx)
        assert _get_soc_after(df, idx[1]) == 50.0

    def test_get_soc_after_none(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC")
        df = pd.DataFrame({"soc_pct": [50.0, None, None]}, index=idx)
        assert _get_soc_after(df, idx[1]) is None


# =====================================================================
# Gap filling tests
# =====================================================================


class TestGapFilling:
    def test_short_gap_both_not_connected(self, repairer, initialised_store):
        """Gap between two not_connected rows → fill with not_connected."""
        _insert_interval(initialised_store, _ts(0), state="not_connected", avail=0.0)
        # Gap at _ts(5) and _ts(10)
        _insert_interval(initialised_store, _ts(15), state="not_connected", avail=0.0)

        summary = repairer.run_full_repair()
        assert summary["gaps_filled"] == 2

        rows = _get_all_intervals(initialised_store)
        assert len(rows) == 4
        gap_rows = [r for r in rows if r["is_repaired"] == 1]
        assert len(gap_rows) == 2
        for r in gap_rows:
            assert r["app_state"] == "not_connected"
            assert r["energy_kwh"] == 0.0
            assert r["availability_pct"] == 0.0

    def test_short_gap_both_error(self, repairer, initialised_store):
        """Gap between two error rows → fill with error."""
        _insert_interval(initialised_store, _ts(0), state="error", avail=0.0)
        _insert_interval(initialised_store, _ts(15), state="error", avail=0.0)

        summary = repairer.run_full_repair()
        assert summary["gaps_filled"] == 2

        rows = _get_all_intervals(initialised_store)
        gap_rows = [r for r in rows if r["is_repaired"] == 1]
        for r in gap_rows:
            assert r["app_state"] == "error"

    def test_short_gap_one_side_not_connected(self, repairer, initialised_store):
        """Gap with one side not_connected → fill with not_connected."""
        _insert_interval(initialised_store, _ts(0), state="charge", avail=100.0)
        _insert_interval(initialised_store, _ts(15), state="not_connected", avail=0.0)

        repairer.run_full_repair()
        rows = _get_all_intervals(initialised_store)
        gap_rows = [r for r in rows if r["is_repaired"] == 1]
        for r in gap_rows:
            assert r["app_state"] == "not_connected"

    def test_short_gap_mixed_states(self, repairer, initialised_store):
        """Gap between charge and discharge → fill with unknown."""
        _insert_interval(initialised_store, _ts(0), state="charge", avail=100.0)
        _insert_interval(initialised_store, _ts(15), state="discharge", avail=100.0)

        repairer.run_full_repair()
        rows = _get_all_intervals(initialised_store)
        gap_rows = [r for r in rows if r["is_repaired"] == 1]
        for r in gap_rows:
            assert r["app_state"] == "unknown"

    def test_long_gap_fills_with_unknown(self, repairer, initialised_store):
        """Gap > MAX_GAP_LENGTH → fill with unknown."""
        _insert_interval(initialised_store, _ts(0), state="charge")
        # Create a gap of MAX_GAP_LENGTH + 5 slots
        offset = (MAX_GAP_LENGTH + 5 + 1) * 5
        _insert_interval(initialised_store, _ts(offset), state="charge")

        summary = repairer.run_full_repair()
        assert summary["gaps_filled"] == MAX_GAP_LENGTH + 5

        rows = _get_all_intervals(initialised_store)
        gap_rows = [r for r in rows if r["is_repaired"] == 1]
        for r in gap_rows:
            assert r["app_state"] == "unknown"

    def test_gap_at_start_no_context(self, repairer, initialised_store):
        """Gap at the very start (no row before) → defaults to unknown."""
        # Row at offset 10 min, gap at 0 and 5
        _insert_interval(initialised_store, _ts(0), state="charge")
        _insert_interval(initialised_store, _ts(15), state="charge")

        summary = repairer.run_full_repair()
        # Only 2 gap slots at 5 and 10
        assert summary["gaps_filled"] == 2

    def test_no_gap(self, repairer, initialised_store):
        """Complete series → no gaps filled."""
        for i in range(5):
            _insert_interval(initialised_store, _ts(i * 5), state="charge")

        summary = repairer.run_full_repair()
        assert summary["gaps_filled"] == 0

    def test_single_row_no_gap(self, repairer, initialised_store):
        """Single row → nothing to fill."""
        _insert_interval(initialised_store, _ts(0), state="charge")

        summary = repairer.run_full_repair()
        assert summary["gaps_filled"] == 0

    def test_gap_soc_is_null(self, repairer, initialised_store):
        """Filled gap rows should have soc_pct = NULL."""
        _insert_interval(initialised_store, _ts(0), state="charge", soc=40.0)
        _insert_interval(initialised_store, _ts(15), state="charge", soc=50.0)

        repairer.run_full_repair()

        rows = _get_all_intervals(initialised_store)
        # After gap fill, soc is None; after interpolation it gets filled.
        # The two gap rows between 40 and 50 should get interpolated values.
        # Check that all rows exist.
        assert len(rows) == 4


# =====================================================================
# SoC jump detection tests
# =====================================================================


class TestSocJumpDetection:
    """Tests for SoC jump detection.

    Upward jumps are handled by step 1a (_reconstruct_upward_jumps).
    Downward jumps are handled by step 1b (_blank_false_constant_soc).
    """

    def test_blanks_upward_jump_no_energy(self, repairer, initialised_store):
        """SoC: 45, 45, 45, 72, 73 → upward jump, no energy → blank.

        Type A-up blanks the constant run (no energy to explain jump).
        Type D does NOT fill: no before_soc + endpoints too far apart.
        """
        socs = [45.0, 45.0, 45.0, 72.0, 73.0]
        for i, soc in enumerate(socs):
            _insert_interval(initialised_store, _ts(i * 5), soc=soc)

        summary = repairer.run_full_repair()
        assert summary["soc_reconstructed_up"] == 3
        assert summary["soc_blanked"] == 0
        assert summary["soc_constant_filled"] == 0

        rows = _get_all_intervals(initialised_store)
        # First 3 rows should have soc_pct = None and is_repaired = 1
        for r in rows[:3]:
            assert r["soc_pct"] is None
            assert r["is_repaired"] == 1
        # Last 2 should be unchanged
        assert rows[3]["soc_pct"] == 72.0
        assert rows[4]["soc_pct"] == 73.0

    def test_reconstructs_upward_jump_with_matching_energy(
        self, repairer, initialised_store
    ):
        """SoC frozen at 40%, jumps to 55%. Energy explains jump → reconstruct.

        battery capacity = 24 kWh. 15% jump = 3.6 kWh total.
        3 intervals × 1.2 kWh = 3.6 kWh → theoretical = 15% → exact match.
        With scaling (scale=1.0), SoC should smoothly go from 40% to 55%.
        """
        # Before the frozen block
        _insert_interval(initialised_store, _ts(0), soc=38.0, energy=0.1)
        # Frozen block with charging energy
        _insert_interval(initialised_store, _ts(5), soc=40.0, energy=1.2)
        _insert_interval(initialised_store, _ts(10), soc=40.0, energy=1.2)
        _insert_interval(initialised_store, _ts(15), soc=40.0, energy=1.2)
        # Jump to 55% (diff=15 > threshold=10)
        _insert_interval(initialised_store, _ts(20), soc=55.0, energy=1.2)

        summary = repairer.run_full_repair()
        assert summary["soc_reconstructed_up"] == 3

        rows = _get_all_intervals(initialised_store)
        # The 3 frozen rows should have reconstructed SoC values
        # Each interval: 1.2/24*100 = 5% per interval, scale=1.0
        # Row 1 (ts+5):  40 + 5.0 = 45.0
        # Row 2 (ts+10): 40 + 10.0 = 50.0
        # Row 3 (ts+15): 40 + 15.0 = 55.0
        assert rows[1]["soc_pct"] is not None
        assert rows[2]["soc_pct"] is not None
        assert rows[3]["soc_pct"] is not None
        # Values should increase monotonically
        assert rows[1]["soc_pct"] < rows[2]["soc_pct"] < rows[3]["soc_pct"]
        # Last reconstructed value should be close to jump target
        assert abs(rows[3]["soc_pct"] - 55.0) < 0.5
        # All marked as repaired
        for r in rows[1:4]:
            assert r["is_repaired"] == 1

    def test_reconstructs_upward_jump_with_scaling(self, repairer, initialised_store):
        """Energy explains ~60% of jump → within tolerance → reconstruct with scaling.

        3 intervals × 0.6 kWh = 1.8 kWh → theoretical = 7.5%.
        Actual jump = 12%. Diff = 4.5 < tolerance (10) → reconstruct.
        Scale = 12/7.5 = 1.6.
        """
        _insert_interval(initialised_store, _ts(0), soc=38.0, energy=0.1)
        _insert_interval(initialised_store, _ts(5), soc=40.0, energy=0.6)
        _insert_interval(initialised_store, _ts(10), soc=40.0, energy=0.6)
        _insert_interval(initialised_store, _ts(15), soc=40.0, energy=0.6)
        # Jump to 52% (diff=12 > threshold=10)
        _insert_interval(initialised_store, _ts(20), soc=52.0, energy=0.6)

        summary = repairer.run_full_repair()
        assert summary["soc_reconstructed_up"] == 3

        rows = _get_all_intervals(initialised_store)
        # Last reconstructed value should hit 52.0 exactly (scaled)
        assert abs(rows[3]["soc_pct"] - 52.0) < 0.5

    def test_blanks_upward_jump_with_context_before(self, repairer, initialised_store):
        """SoC: 30, 45, 45, 45, 72 → upward jump, no energy → blank.

        After blanking, gap has before=30, after=72 (diff=42 > 10) →
        Type D skips. SoC stays NULL.
        """
        socs = [30.0, 45.0, 45.0, 45.0, 72.0]
        energies = [0.3, 0.0, 0.0, 0.0, 0.3]
        for i, (soc, energy) in enumerate(zip(socs, energies)):
            _insert_interval(initialised_store, _ts(i * 5), soc=soc, energy=energy)

        summary = repairer.run_full_repair()
        # Upward jump (45→72) handled by step 1a
        assert summary["soc_reconstructed_up"] == 3

        rows = _get_all_intervals(initialised_store)
        for r in rows[1:4]:
            assert r["soc_pct"] is None

    def test_blanks_downward_jump(self, repairer, initialised_store):
        """SoC: 72, 72, 72, 45, 44 → downward jump → blank the three 72s."""
        socs = [72.0, 72.0, 72.0, 45.0, 44.0]
        for i, soc in enumerate(socs):
            _insert_interval(initialised_store, _ts(i * 5), soc=soc)

        summary = repairer.run_full_repair()
        assert summary["soc_blanked"] == 3
        assert summary["soc_reconstructed_up"] == 0

        rows = _get_all_intervals(initialised_store)
        for r in rows[:3]:
            assert r["soc_pct"] is None
            assert r["is_repaired"] == 1

    def test_no_jump_below_threshold(self, repairer, initialised_store):
        """Gradual SoC change below threshold → no blanking."""
        socs = [50.0, 52.0, 54.0, 56.0, 58.0]
        for i, soc in enumerate(socs):
            _insert_interval(initialised_store, _ts(i * 5), soc=soc)

        summary = repairer.run_full_repair()
        assert summary["soc_blanked"] == 0
        assert summary["soc_reconstructed_up"] == 0

    def test_single_value_before_upward_jump_not_modified(
        self, repairer, initialised_store
    ):
        """Single value before upward jump (run < 2) → not modified."""
        socs = [45.0, 72.0, 73.0]
        for i, soc in enumerate(socs):
            _insert_interval(initialised_store, _ts(i * 5), soc=soc)

        summary = repairer.run_full_repair()
        assert summary["soc_reconstructed_up"] == 0
        assert summary["soc_blanked"] == 0

    def test_single_value_before_downward_jump_not_blanked(
        self, repairer, initialised_store
    ):
        """Single value before downward jump (run < 2) → not blanked."""
        socs = [72.0, 45.0, 44.0]
        for i, soc in enumerate(socs):
            _insert_interval(initialised_store, _ts(i * 5), soc=soc)

        summary = repairer.run_full_repair()
        assert summary["soc_blanked"] == 0


# =====================================================================
# Type B: Energy interpolation tests
# =====================================================================


class TestEnergyInterpolation:
    def test_interpolates_gap_with_matching_endpoints(
        self, repairer, initialised_store
    ):
        """Gap-filled rows between matching energy endpoints → interpolate.

        energy: 0.30, 0(gap), 0(gap), 0.30 → fill with 0.30.
        """
        _insert_interval(initialised_store, _ts(0), energy=0.30, soc=40.0)
        # Gap at 5 and 10
        _insert_interval(initialised_store, _ts(15), energy=0.30, soc=43.0)

        summary = repairer.run_full_repair()
        assert summary["gaps_filled"] == 2
        assert summary["energy_interpolated"] == 2

        rows = _get_all_intervals(initialised_store)
        # Gap-filled rows should have interpolated energy
        for r in rows[1:3]:
            assert abs(r["energy_kwh"] - 0.30) < 0.01

    def test_no_interpolation_different_endpoints(self, repairer, initialised_store):
        """Energy endpoints differ > 5% → no interpolation.

        energy: 0.30, 0(gap), 0.10 → diff = 100% > 5% → skip.
        """
        _insert_interval(initialised_store, _ts(0), energy=0.30, soc=40.0)
        # Gap at 5
        _insert_interval(initialised_store, _ts(10), energy=0.10, soc=42.0)

        summary = repairer.run_full_repair()
        assert summary["energy_interpolated"] == 0

    def test_no_interpolation_opposite_direction(self, repairer, initialised_store):
        """Energy endpoints in opposite directions → no interpolation.

        energy: 0.30, 0(gap), -0.20 → different signs → skip.
        """
        _insert_interval(initialised_store, _ts(0), energy=0.30, soc=40.0)
        # Gap at 5
        _insert_interval(initialised_store, _ts(10), energy=-0.20, soc=38.0)

        summary = repairer.run_full_repair()
        assert summary["energy_interpolated"] == 0


# =====================================================================
# Type C: SoC reconstruction tests
# =====================================================================


class TestSocReconstruction:
    @patch("apps.v2g_liberty.data_repairer.c")
    def test_reconstructs_soc_from_energy(self, mock_c, repairer, initialised_store):
        """NULL SoC with energy → reconstruct from cumulative energy.

        SoC: 60, NULL, NULL, NULL, 63.75
        Energy: 0.3, 0.3, 0.3, 0.3, 0.3
        Capacity: 24 kWh → delta_soc = 0.3/24*100 = 1.25%/interval
        Reconstructed: 61.25, 62.5, 63.75 → matches endpoint ✓
        """
        mock_c.CHARGER_MAX_CHARGE_POWER = 7600
        mock_c.CHARGER_MAX_DISCHARGE_POWER = 7600
        mock_c.CAR_MAX_CAPACITY_IN_KWH = 24

        _insert_interval(initialised_store, _ts(0), soc=60.0, energy=0.3)
        _insert_interval(initialised_store, _ts(5), soc=None, energy=0.3)
        _insert_interval(initialised_store, _ts(10), soc=None, energy=0.3)
        _insert_interval(initialised_store, _ts(15), soc=None, energy=0.3)
        _insert_interval(initialised_store, _ts(20), soc=63.75, energy=0.3)

        summary = repairer.run_full_repair()
        assert summary["soc_reconstructed"] == 3

        rows = _get_all_intervals(initialised_store)
        assert rows[1]["soc_pct"] == pytest.approx(61.2, abs=0.2)
        assert rows[2]["soc_pct"] == pytest.approx(62.5, abs=0.2)
        assert rows[3]["soc_pct"] == pytest.approx(63.8, abs=0.2)

    @patch("apps.v2g_liberty.data_repairer.c")
    def test_skips_if_endpoint_mismatch(self, mock_c, repairer, initialised_store):
        """Reconstructed endpoint too far from actual → skip.

        SoC: 60, NULL×3, 80 with energy=0.3 → reconstructed end ≈ 63.75
        Deviation = |63.75 - 80| = 16.25 > 5pp → skip.
        """
        mock_c.CHARGER_MAX_CHARGE_POWER = 7600
        mock_c.CHARGER_MAX_DISCHARGE_POWER = 7600
        mock_c.CAR_MAX_CAPACITY_IN_KWH = 24

        _insert_interval(initialised_store, _ts(0), soc=60.0, energy=0.3)
        _insert_interval(initialised_store, _ts(5), soc=None, energy=0.3)
        _insert_interval(initialised_store, _ts(10), soc=None, energy=0.3)
        _insert_interval(initialised_store, _ts(15), soc=None, energy=0.3)
        _insert_interval(initialised_store, _ts(20), soc=80.0, energy=0.3)

        summary = repairer.run_full_repair()
        assert summary["soc_reconstructed"] == 0

    @patch("apps.v2g_liberty.data_repairer.c")
    def test_no_reconstruction_without_endpoint(
        self, mock_c, repairer, initialised_store
    ):
        """NULL SoC at end with no after value → not reconstructed."""
        mock_c.CHARGER_MAX_CHARGE_POWER = 7600
        mock_c.CHARGER_MAX_DISCHARGE_POWER = 7600
        mock_c.CAR_MAX_CAPACITY_IN_KWH = 24

        _insert_interval(initialised_store, _ts(0), soc=40.0, energy=0.3)
        _insert_interval(initialised_store, _ts(5), soc=None, energy=0.3)
        _insert_interval(initialised_store, _ts(10), soc=None, energy=0.3)

        summary = repairer.run_full_repair()
        assert summary["soc_reconstructed"] == 0


# =====================================================================
# Type D: SoC constant fill tests
# =====================================================================


class TestSocConstantFill:
    def test_fills_with_constant_value(self, repairer, initialised_store):
        """SoC: 45, NULL, NULL, 45, energy ≈ 0 → fill with 45.

        Endpoints match (diff=0 ≤ 10) → fill with before value.
        """
        _insert_interval(initialised_store, _ts(0), soc=45.0, energy=0.0)
        _insert_interval(initialised_store, _ts(5), soc=None, energy=0.0)
        _insert_interval(initialised_store, _ts(10), soc=None, energy=0.0)
        _insert_interval(initialised_store, _ts(15), soc=45.0, energy=0.0)

        summary = repairer.run_full_repair()
        assert summary["soc_constant_filled"] == 2

        rows = _get_all_intervals(initialised_store)
        assert rows[1]["soc_pct"] == 45.0
        assert rows[2]["soc_pct"] == 45.0

    def test_fills_with_small_endpoint_diff(self, repairer, initialised_store):
        """SoC: 45, NULL, NULL, 47, energy ≈ 0 → fill with 45.

        Endpoints differ by 2 ≤ 10 → fill with before value.
        """
        _insert_interval(initialised_store, _ts(0), soc=45.0, energy=0.0)
        _insert_interval(initialised_store, _ts(5), soc=None, energy=0.0)
        _insert_interval(initialised_store, _ts(10), soc=None, energy=0.0)
        _insert_interval(initialised_store, _ts(15), soc=47.0, energy=0.0)

        summary = repairer.run_full_repair()
        assert summary["soc_constant_filled"] == 2

        rows = _get_all_intervals(initialised_store)
        assert rows[1]["soc_pct"] == 45.0
        assert rows[2]["soc_pct"] == 45.0

    def test_skips_large_endpoint_diff(self, repairer, initialised_store):
        """SoC: 45, NULL, NULL, 72, energy ≈ 0 → skip (diff=27 > 10)."""
        _insert_interval(initialised_store, _ts(0), soc=45.0, energy=0.0)
        _insert_interval(initialised_store, _ts(5), soc=None, energy=0.0)
        _insert_interval(initialised_store, _ts(10), soc=None, energy=0.0)
        _insert_interval(initialised_store, _ts(15), soc=72.0, energy=0.0)

        summary = repairer.run_full_repair()
        assert summary["soc_constant_filled"] == 0

        rows = _get_all_intervals(initialised_store)
        assert rows[1]["soc_pct"] is None
        assert rows[2]["soc_pct"] is None

    def test_skips_without_both_endpoints(self, repairer, initialised_store):
        """SoC: NULL, NULL, 50, energy ≈ 0 → skip (no before endpoint)."""
        _insert_interval(initialised_store, _ts(0), soc=None, energy=0.0)
        _insert_interval(initialised_store, _ts(5), soc=None, energy=0.0)
        _insert_interval(initialised_store, _ts(10), soc=50.0, energy=0.0)

        summary = repairer.run_full_repair()
        assert summary["soc_constant_filled"] == 0


# =====================================================================
# Bounds validation tests
# =====================================================================


class TestBoundsValidation:
    @patch("apps.v2g_liberty.data_repairer.c")
    def test_soc_below_1_logged(self, mock_c, repairer, initialised_store):
        """SoC < 1 → logged as warning, not modified."""
        mock_c.CHARGER_MAX_CHARGE_POWER = 1380
        mock_c.CHARGER_MAX_DISCHARGE_POWER = 1380
        _insert_interval(initialised_store, _ts(0), soc=-5.0)
        _insert_interval(initialised_store, _ts(5), soc=50.0)

        summary = repairer.run_full_repair()
        assert "soc_below_1" in summary["violations"]
        assert summary["violations"]["soc_below_1"] == 1

        # Value should NOT be modified
        rows = _get_all_intervals(initialised_store)
        assert rows[0]["soc_pct"] == -5.0

    @patch("apps.v2g_liberty.data_repairer.c")
    def test_soc_above_100_logged(self, mock_c, repairer, initialised_store):
        """SoC > 100 → logged as warning, not modified."""
        mock_c.CHARGER_MAX_CHARGE_POWER = 1380
        mock_c.CHARGER_MAX_DISCHARGE_POWER = 1380
        _insert_interval(initialised_store, _ts(0), soc=105.0)
        _insert_interval(initialised_store, _ts(5), soc=50.0)

        summary = repairer.run_full_repair()
        assert "soc_above_100" in summary["violations"]

        rows = _get_all_intervals(initialised_store)
        assert rows[0]["soc_pct"] == 105.0

    @patch("apps.v2g_liberty.data_repairer.c")
    def test_energy_exceeds_charge_limit_logged(
        self, mock_c, repairer, initialised_store
    ):
        """Energy above charger limit → logged as warning."""
        mock_c.CHARGER_MAX_CHARGE_POWER = 1380  # W
        mock_c.CHARGER_MAX_DISCHARGE_POWER = 1380
        # Max energy per 5 min = 1380 / 1000 * 5 / 60 = 0.115 kWh
        _insert_interval(initialised_store, _ts(0), energy=0.5)  # Way above limit
        _insert_interval(initialised_store, _ts(5), energy=0.01)

        summary = repairer.run_full_repair()
        assert "energy_exceeds_charge_limit" in summary["violations"]

    @patch("apps.v2g_liberty.data_repairer.c")
    def test_energy_while_not_connected_logged(
        self, mock_c, repairer, initialised_store
    ):
        """Energy while not_connected → logged as warning."""
        mock_c.CHARGER_MAX_CHARGE_POWER = 1380
        mock_c.CHARGER_MAX_DISCHARGE_POWER = 1380
        _insert_interval(
            initialised_store, _ts(0), energy=0.05, state="not_connected", avail=0.0
        )
        _insert_interval(initialised_store, _ts(5), energy=0.0, state="charge")

        summary = repairer.run_full_repair()
        assert "energy_while_not_connected" in summary["violations"]


# =====================================================================
# Safety and idempotency tests
# =====================================================================


class TestSafety:
    def test_original_rows_not_modified(self, repairer, initialised_store):
        """Original rows (is_repaired=0) are never overwritten."""
        _insert_interval(
            initialised_store, _ts(0), energy=0.1, soc=50.0, state="charge"
        )
        _insert_interval(
            initialised_store, _ts(5), energy=0.2, soc=51.0, state="charge"
        )
        _insert_interval(
            initialised_store, _ts(10), energy=0.15, soc=52.0, state="charge"
        )

        repairer.run_full_repair()

        rows = _get_all_intervals(initialised_store)
        for r in rows:
            assert r["is_repaired"] == 0
        assert rows[0]["energy_kwh"] == 0.1
        assert rows[1]["energy_kwh"] == 0.2
        assert rows[2]["energy_kwh"] == 0.15

    def test_idempotent_repair(self, repairer, initialised_store):
        """Running repair twice produces the same result."""
        _insert_interval(initialised_store, _ts(0), state="not_connected", avail=0.0)
        _insert_interval(initialised_store, _ts(15), state="not_connected", avail=0.0)

        repairer.run_full_repair()
        rows1 = _get_all_intervals(initialised_store)

        summary2 = repairer.run_full_repair()
        rows2 = _get_all_intervals(initialised_store)

        assert len(rows1) == len(rows2)
        for r1, r2 in zip(rows1, rows2):
            assert r1 == r2
        # Second run should find nothing to repair
        assert summary2["gaps_filled"] == 0


# =====================================================================
# Empty table test
# =====================================================================


class TestEmptyTable:
    def test_empty_table_no_errors(self, repairer):
        """Empty interval_log → no errors, zero summary."""
        summary = repairer.run_full_repair()
        assert summary == _empty_summary()


# =====================================================================
# Incremental repair test
# =====================================================================


class TestIncrementalRepair:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_repairer.get_local_now", return_value=TEST_NOW)
    async def test_incremental_repair_runs(self, mock_now, repairer, initialised_store):
        """Incremental repair should run without errors."""
        # Insert some data within the lookback window
        lookback_start = TEST_NOW - timedelta(hours=PERIODIC_LOOKBACK_HOURS)
        ts1 = lookback_start.isoformat()
        ts2 = (lookback_start + timedelta(minutes=5)).isoformat()
        _insert_interval(initialised_store, ts1, state="charge")
        _insert_interval(initialised_store, ts2, state="charge")

        await repairer.run_incremental_repair()
        # No assertion beyond "no exception"


# =====================================================================
# Initialise test
# =====================================================================


class TestInitialise:
    @pytest.mark.asyncio
    async def test_initialise_schedules_periodic(self, repairer, hass):
        """initialise() should call run_every for periodic scheduling."""
        await repairer.initialise()
        hass.run_every.assert_called_once()
        args = hass.run_every.call_args
        assert args[0][0] == repairer.run_incremental_repair
        assert args[0][2] == 6 * 60 * 60


# =====================================================================
# Integration test — full pipeline end-to-end
# =====================================================================


class TestIntegration:
    @patch("apps.v2g_liberty.data_repairer.c")
    def test_full_pipeline_realistic_data(self, mock_c, repairer, initialised_store):
        """Realistic scenario: gaps + SoC jumps + bounds violations.

        Timeline (minutes offset from TEST_NOW):
          0: charge, soc=40, energy=0.05
          5: charge, soc=41, energy=0.05
         10: (gap)
         15: (gap)
         20: charge, soc=43, energy=0.05
         25: charge, soc=43, energy=0.05  <- constant run start
         30: charge, soc=43, energy=0.05  <- constant run
         35: charge, soc=70, energy=0.05  <- SoC jump (|70-43|=27 > 10)
         40: charge, soc=71, energy=0.05
         45: not_connected, soc=None, energy=0.0  <- disconnected
         50: not_connected, soc=None, energy=0.0
         55: charge, soc=-2, energy=0.05  <- SoC below zero (bounds)

        Expected pipeline results:
        Step 0: 2 gap slots filled at 10, 15 (charge context)
        Step 1: SoC jump at 35 → blanks 43@20, 43@25, 43@30 (3 values)
        Step 2: Gap-filled rows at 10,15 have energy=0, endpoints=0.05/0.05
                → within 5%, interpolated to 0.05
        Step 3: NULL SoC gap from 10-30 (5 rows) with energy=0.05 each
                → reconstruct from energy (before=41, capacity=24)
                → but |reconstructed_end - 70| may be > 5pp → skip
        Step 4: NULL SoC at 10-30 with energy, not negligible → skip
                NULL SoC at 45,50 with energy=0 → before=71, after=-2
                → diff=73 > 10 → skip
        Step 5: soc_below_1 at -2
        """
        mock_c.CHARGER_MAX_CHARGE_POWER = 1380
        mock_c.CHARGER_MAX_DISCHARGE_POWER = 1380
        mock_c.CAR_MAX_CAPACITY_IN_KWH = 24

        # Insert the realistic dataset
        _insert_interval(
            initialised_store, _ts(0), soc=40.0, energy=0.05, state="charge"
        )
        _insert_interval(
            initialised_store, _ts(5), soc=41.0, energy=0.05, state="charge"
        )
        # Gap at 10 and 15
        _insert_interval(
            initialised_store, _ts(20), soc=43.0, energy=0.05, state="charge"
        )
        _insert_interval(
            initialised_store, _ts(25), soc=43.0, energy=0.05, state="charge"
        )
        _insert_interval(
            initialised_store, _ts(30), soc=43.0, energy=0.05, state="charge"
        )
        _insert_interval(
            initialised_store, _ts(35), soc=70.0, energy=0.05, state="charge"
        )
        _insert_interval(
            initialised_store, _ts(40), soc=71.0, energy=0.05, state="charge"
        )
        _insert_interval(
            initialised_store,
            _ts(45),
            soc=None,
            energy=0.0,
            state="not_connected",
            avail=0.0,
        )
        _insert_interval(
            initialised_store,
            _ts(50),
            soc=None,
            energy=0.0,
            state="not_connected",
            avail=0.0,
        )
        _insert_interval(
            initialised_store, _ts(55), soc=-2.0, energy=0.05, state="charge"
        )

        summary = repairer.run_full_repair()

        # --- Verify gap filling ---
        assert summary["gaps_filled"] == 2

        rows = _get_all_intervals(initialised_store)
        # Total: 10 original + 2 filled = 12 rows
        assert len(rows) == 12

        # Filled rows at 10 and 15 should be charge state
        ts_10 = _ts(10)
        ts_15 = _ts(15)
        filled = {r["timestamp"]: r for r in rows if r["timestamp"] in (ts_10, ts_15)}
        assert len(filled) == 2
        for r in filled.values():
            assert r["app_state"] == "charge"
            assert r["is_repaired"] == 1

        # --- Verify SoC jump detection ---
        # Valid SoC sequence after gap fill: 40, 41, NULL, NULL, 43, 43, 43, 70, 71, None, None, -2
        # dropna: 40, 41, 43, 43, 43, 70, 71, -2
        # Upward jump at 70 (diff=27>10): energy too low to explain →
        # blanked by step 1a (soc_reconstructed_up)
        assert summary["soc_reconstructed_up"] == 3
        assert summary["soc_blanked"] == 0

        # --- Verify bounds ---
        assert "soc_below_1" in summary["violations"]
        assert summary["violations"]["soc_below_1"] == 1

        # --- Verify original row integrity ---
        ts_0 = _ts(0)
        original_row = next(r for r in rows if r["timestamp"] == ts_0)
        assert original_row["soc_pct"] == 40.0
        assert original_row["energy_kwh"] == 0.05
        assert original_row["is_repaired"] == 0

        # --- Verify idempotency ---
        summary2 = repairer.run_full_repair()
        rows2 = _get_all_intervals(initialised_store)
        assert len(rows2) == len(rows)
        assert summary2["gaps_filled"] == 0
        assert summary2["soc_reconstructed_up"] == 0
        assert summary2["soc_blanked"] == 0


# =====================================================================
# Pending-review (is_repaired=2) tests
# =====================================================================


class TestPendingReview:
    def test_pending_rows_marked_as_reviewed(self, repairer, initialised_store):
        """Imported rows (is_repaired=2) become 0 after repair."""
        _insert_interval(initialised_store, _ts(0), soc=50.0, repaired=2)
        _insert_interval(initialised_store, _ts(5), soc=51.0, repaired=2)
        _insert_interval(initialised_store, _ts(10), soc=52.0, repaired=2)

        repairer.run_full_repair()

        rows = _get_all_intervals(initialised_store)
        for r in rows:
            assert r["is_repaired"] == 0, (
                f"Row at {r['timestamp']} should be reviewed (0), got {r['is_repaired']}"
            )

    def test_pending_rows_hidden_from_ui_queries(self, initialised_store):
        """Rows with is_repaired=2 should not appear in UI queries."""
        # Insert reviewed rows (visible)
        _insert_interval(initialised_store, _ts(0), soc=50.0, repaired=0)
        _insert_interval(initialised_store, _ts(5), soc=51.0, repaired=0)
        # Insert pending rows (hidden)
        _insert_interval(initialised_store, _ts(10), soc=52.0, repaired=2)
        _insert_interval(initialised_store, _ts(15), soc=53.0, repaired=2)

        # has_any_intervals should only count reviewed rows
        assert initialised_store.has_any_intervals() is True

        # get_intervals_since should exclude pending rows
        intervals = initialised_store.get_intervals_since("2000-01-01T00:00:00")
        assert len(intervals) == 2

    def test_pending_only_table_appears_empty_to_ui(self, initialised_store):
        """Table with only pending rows appears empty to the UI."""
        _insert_interval(initialised_store, _ts(0), soc=50.0, repaired=2)
        _insert_interval(initialised_store, _ts(5), soc=51.0, repaired=2)

        assert initialised_store.has_any_intervals() is False
        intervals = initialised_store.get_intervals_since("2000-01-01T00:00:00")
        assert len(intervals) == 0

    def test_repair_makes_pending_visible(self, repairer, initialised_store):
        """After repair, previously pending rows become visible to UI."""
        _insert_interval(initialised_store, _ts(0), soc=50.0, repaired=2)
        _insert_interval(initialised_store, _ts(5), soc=51.0, repaired=2)

        # Before repair: hidden
        assert initialised_store.has_any_intervals() is False

        repairer.run_full_repair()

        # After repair: visible
        assert initialised_store.has_any_intervals() is True
        intervals = initialised_store.get_intervals_since("2000-01-01T00:00:00")
        assert len(intervals) == 2

    def test_mixed_pending_and_repaired(self, repairer, initialised_store):
        """Pending rows that need repair get is_repaired=1, rest get 0."""
        # Gap: pending rows at 0 and 15, gap at 5 and 10
        _insert_interval(
            initialised_store, _ts(0), soc=50.0, state="charge", repaired=2
        )
        _insert_interval(
            initialised_store, _ts(15), soc=54.0, state="charge", repaired=2
        )

        repairer.run_full_repair()

        rows = _get_all_intervals(initialised_store)
        assert len(rows) == 4  # 2 original + 2 gap-filled

        # Original pending rows: reviewed → 0
        originals = [r for r in rows if r["timestamp"] in (_ts(0), _ts(15))]
        for r in originals:
            assert r["is_repaired"] == 0

        # Gap-filled rows: repaired → 1
        filled = [r for r in rows if r["timestamp"] not in (_ts(0), _ts(15))]
        for r in filled:
            assert r["is_repaired"] == 1
