"""Unit tests for FMClient ensure_asset and ensure_sensor methods."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from apps.v2g_liberty.fm_client import FMClient


@pytest.fixture
def hass_mock():
    mock = AsyncMock()
    mock.log = MagicMock()
    return mock


@pytest.fixture
def event_bus_mock():
    return MagicMock()


@pytest.fixture
def fm_client_mock():
    """Mock the underlying flexmeasures_client.FlexMeasuresClient."""
    mock = AsyncMock()
    mock.get_assets = AsyncMock(return_value=[])
    mock.get_asset = AsyncMock(return_value={"latitude": 52.1, "longitude": 4.3})
    mock.add_asset = AsyncMock(return_value={"id": 100, "status": 201})
    mock.update_asset = AsyncMock(return_value={})
    mock.get_sensors = AsyncMock(return_value=[])
    mock.add_sensor = AsyncMock(return_value={"id": 200, "status": 201})
    mock.update_sensor = AsyncMock(return_value={})
    mock.get_asset_types = AsyncMock(
        return_value=[
            {"id": 1, "name": "building"},
            {"id": 2, "name": "solar"},
        ]
    )
    mock.get_account = AsyncMock(return_value={"id": 42, "name": "Test Account"})
    return mock


@pytest.fixture
def fm(hass_mock, event_bus_mock, fm_client_mock):
    """Create FMClient with mocked dependencies."""
    with patch("apps.v2g_liberty.fm_client.isodate"):
        client = FMClient(hass_mock, event_bus_mock)
    client.client = fm_client_mock
    client._charger_asset_id = 99  # existing charger asset
    return client


class TestGetAccountId:
    @pytest.mark.asyncio
    async def test_fetches_and_caches(self, fm, fm_client_mock):
        """Account ID is fetched once and cached."""
        result = await fm._get_account_id()
        assert result == 42

        # Second call should not make another API call
        await fm._get_account_id()
        fm_client_mock.get_account.assert_called_once()


class TestGetGenericAssetTypeId:
    @pytest.mark.asyncio
    async def test_finds_known_type(self, fm):
        result = await fm._get_generic_asset_type_id("building")
        assert result == 1

    @pytest.mark.asyncio
    async def test_finds_solar_type(self, fm):
        result = await fm._get_generic_asset_type_id("solar")
        assert result == 2

    @pytest.mark.asyncio
    async def test_raises_for_unknown_type(self, fm):
        with pytest.raises(ValueError, match="Unknown generic_asset_type"):
            await fm._get_generic_asset_type_id("nonexistent")

    @pytest.mark.asyncio
    async def test_caches_results(self, fm, fm_client_mock):
        await fm._get_generic_asset_type_id("building")
        await fm._get_generic_asset_type_id("solar")
        # Only one API call despite two lookups
        fm_client_mock.get_asset_types.assert_called_once()


class TestEnsureAsset:
    @pytest.mark.asyncio
    async def test_creates_new_asset(self, fm, fm_client_mock):
        """Asset is created when not found."""
        fm_client_mock.get_assets.return_value = []

        asset_id = await fm.ensure_asset(
            name="[TEST] Main Connection",
            generic_asset_type="building",
        )

        assert asset_id == 100
        fm_client_mock.add_asset.assert_called_once()
        call_kwargs = fm_client_mock.add_asset.call_args.kwargs
        assert call_kwargs["name"] == "[TEST] Main Connection"
        assert call_kwargs["account_id"] == 42

    @pytest.mark.asyncio
    async def test_finds_existing_asset(self, fm, fm_client_mock):
        """Existing asset is found by name, not created."""
        fm_client_mock.get_assets.return_value = [
            {"id": 55, "name": "[TEST] Main Connection", "sensors": []}
        ]

        asset_id = await fm.ensure_asset(
            name="[TEST] Main Connection",
            generic_asset_type="building",
        )

        assert asset_id == 55
        fm_client_mock.add_asset.assert_not_called()

    @pytest.mark.asyncio
    async def test_updates_attributes_on_existing(self, fm, fm_client_mock):
        """Attributes are updated when asset already exists."""
        fm_client_mock.get_assets.return_value = [
            {"id": 55, "name": "[TEST] Main Connection", "sensors": []}
        ]

        asset_id = await fm.ensure_asset(
            name="[TEST] Main Connection",
            generic_asset_type="building",
            attributes={"phases": 3, "capacity_per_phase": 25},
        )

        assert asset_id == 55
        fm_client_mock.update_asset.assert_called_once_with(
            55, {"attributes": {"phases": 3, "capacity_per_phase": 25}}
        )

    @pytest.mark.asyncio
    async def test_merges_attributes_on_existing(self, fm, fm_client_mock):
        """Existing attributes are preserved (merged) — a PATCH replaces the
        whole attributes object, so a phases/capacity write must not wipe the
        version attributes already on the Main Connection."""
        fm_client_mock.get_assets.return_value = [
            {
                "id": 55,
                "name": "Main Connection",
                "sensors": [],
                "attributes": {
                    "v2g-liberty-version": "1.2.3",
                    "home-assistant-version": "2026.4.1",
                },
            }
        ]

        await fm.ensure_asset(
            name="Main Connection",
            generic_asset_type="building",
            attributes={"phases": 3, "capacity_per_phase": 25},
        )

        fm_client_mock.update_asset.assert_called_once_with(
            55,
            {
                "attributes": {
                    "v2g-liberty-version": "1.2.3",
                    "home-assistant-version": "2026.4.1",
                    "phases": 3,
                    "capacity_per_phase": 25,
                }
            },
        )

    @pytest.mark.asyncio
    async def test_no_update_without_attributes(self, fm, fm_client_mock):
        """No update_asset call when no attributes provided."""
        fm_client_mock.get_assets.return_value = [
            {"id": 55, "name": "[TEST] Main Connection", "sensors": []}
        ]

        await fm.ensure_asset(
            name="[TEST] Main Connection",
            generic_asset_type="building",
        )

        fm_client_mock.update_asset.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_with_parent(self, fm, fm_client_mock):
        """Parent asset ID is passed when creating."""
        fm_client_mock.get_assets.return_value = []

        await fm.ensure_asset(
            name="[TEST] PV Panel 1",
            generic_asset_type="solar",
            parent_asset_id=55,
        )

        call_kwargs = fm_client_mock.add_asset.call_args.kwargs
        assert call_kwargs["parent_asset_id"] == 55

    @pytest.mark.asyncio
    async def test_uses_charger_lat_lon(self, fm, fm_client_mock):
        """Lat/lon from existing charger asset is re-used."""
        fm_client_mock.get_assets.return_value = []
        fm_client_mock.get_asset.return_value = {
            "latitude": 51.999,
            "longitude": 4.483,
        }

        await fm.ensure_asset(
            name="[TEST] Main Connection",
            generic_asset_type="building",
        )

        call_kwargs = fm_client_mock.add_asset.call_args.kwargs
        assert call_kwargs["latitude"] == 51.999
        assert call_kwargs["longitude"] == 4.483

    @pytest.mark.asyncio
    async def test_raises_when_client_not_initialised(self, fm):
        """Raises RuntimeError when client is None."""
        fm.client = None

        with pytest.raises(RuntimeError, match="not initialised"):
            await fm.ensure_asset(name="test", generic_asset_type="building")


class TestEnsureSensor:
    @pytest.mark.asyncio
    async def test_creates_new_sensor(self, fm, fm_client_mock):
        """Sensor is created when not found."""
        fm_client_mock.get_sensors.return_value = []

        sensor_id = await fm.ensure_sensor(
            name="Grid Consumption L1",
            unit="kW",
            asset_id=55,
        )

        assert sensor_id == 200
        fm_client_mock.add_sensor.assert_called_once()
        call_kwargs = fm_client_mock.add_sensor.call_args.kwargs
        assert call_kwargs["name"] == "Grid Consumption L1"
        assert call_kwargs["unit"] == "kW"
        assert call_kwargs["generic_asset_id"] == 55
        assert call_kwargs["event_resolution"] == "PT5M"

    @pytest.mark.asyncio
    async def test_finds_existing_sensor(self, fm, fm_client_mock):
        """Existing sensor is found by name."""
        fm_client_mock.get_sensors.return_value = [
            {"id": 77, "name": "Grid Consumption L1"}
        ]

        sensor_id = await fm.ensure_sensor(
            name="Grid Consumption L1",
            unit="kW",
            asset_id=55,
        )

        assert sensor_id == 77
        fm_client_mock.add_sensor.assert_not_called()

    @pytest.mark.asyncio
    async def test_updates_attributes_on_existing(self, fm, fm_client_mock):
        """Attributes are updated when sensor already exists."""
        fm_client_mock.get_sensors.return_value = [
            {"id": 77, "name": "Grid Consumption L1"}
        ]

        sensor_id = await fm.ensure_sensor(
            name="Grid Consumption L1",
            unit="kW",
            asset_id=55,
            attributes={"consumption_is_positive": True},
        )

        assert sensor_id == 77
        fm_client_mock.add_sensor.assert_not_called()
        fm_client_mock.update_sensor.assert_called_once_with(
            77, {"attributes": {"consumption_is_positive": True}}
        )

    @pytest.mark.asyncio
    async def test_no_update_without_attributes(self, fm, fm_client_mock):
        """No update_sensor call when no attributes provided on existing sensor."""
        fm_client_mock.get_sensors.return_value = [
            {"id": 77, "name": "Grid Consumption L1"}
        ]

        await fm.ensure_sensor(
            name="Grid Consumption L1",
            unit="kW",
            asset_id=55,
        )

        fm_client_mock.update_sensor.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_with_attributes(self, fm, fm_client_mock):
        """Sensor attributes are passed to add_sensor."""
        fm_client_mock.get_sensors.return_value = []

        await fm.ensure_sensor(
            name="Grid Consumption L1",
            unit="kW",
            asset_id=55,
            attributes={"consumption_is_positive": True},
        )

        call_kwargs = fm_client_mock.add_sensor.call_args.kwargs
        assert call_kwargs["attributes"] == {"consumption_is_positive": True}

    @pytest.mark.asyncio
    async def test_creates_without_attributes(self, fm, fm_client_mock):
        """Without attributes, None is passed to add_sensor."""
        fm_client_mock.get_sensors.return_value = []

        await fm.ensure_sensor(
            name="Grid Production L1",
            unit="kW",
            asset_id=55,
        )

        call_kwargs = fm_client_mock.add_sensor.call_args.kwargs
        assert call_kwargs["attributes"] is None

    @pytest.mark.asyncio
    async def test_raises_when_client_not_initialised(self, fm):
        """Raises RuntimeError when client is None."""
        fm.client = None

        with pytest.raises(RuntimeError, match="not initialised"):
            await fm.ensure_sensor(name="test", unit="kW", asset_id=1)


class TestSetAssetAttributes:
    @pytest.mark.asyncio
    async def test_writes_to_explicit_asset_id(self, fm, fm_client_mock):
        """Explicit asset_id targets that asset, not the charger."""
        await fm.set_asset_attributes({"v2g-liberty-version": "1.2.3"}, asset_id=171)

        fm_client_mock.update_asset.assert_called_once_with(
            171, {"attributes": {"v2g-liberty-version": "1.2.3"}}
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_charger_asset_id(self, fm, fm_client_mock):
        """When asset_id is None, falls back to self._charger_asset_id."""
        await fm.set_asset_attributes({"v2g-liberty-version": None})

        fm_client_mock.update_asset.assert_called_once_with(
            99, {"attributes": {"v2g-liberty-version": None}}
        )

    @pytest.mark.asyncio
    async def test_skips_when_no_target_available(self, fm, fm_client_mock):
        """No update call and no raise when both asset_id and charger id are None."""
        fm._charger_asset_id = None

        await fm.set_asset_attributes({"v2g-liberty-version": "1.2.3"})

        fm_client_mock.update_asset.assert_not_called()

    @pytest.mark.asyncio
    async def test_merges_into_existing_attributes(self, fm, fm_client_mock):
        """New keys are merged into the existing attributes — FM replaces the
        whole attributes object on a PATCH, so we read-modify-write."""
        fm_client_mock.get_asset.return_value = {
            "attributes": {"phases": 3, "capacity_per_phase": 25}
        }

        await fm.set_asset_attributes({"v2g-liberty-version": "1.2.3"}, asset_id=171)

        fm_client_mock.get_asset.assert_called_once_with(171, parse_json_fields=True)
        fm_client_mock.update_asset.assert_called_once_with(
            171,
            {
                "attributes": {
                    "phases": 3,
                    "capacity_per_phase": 25,
                    "v2g-liberty-version": "1.2.3",
                }
            },
        )

    @pytest.mark.asyncio
    async def test_sequential_writes_preserve_both_sets(self, fm, fm_client_mock):
        """Two separate writers on the same asset keep both attribute sets —
        a version write must not clobber grid phases/capacity (and vice versa)."""
        state = {"attributes": {}}

        async def fake_get_asset(asset_id, parse_json_fields=None):
            return {"attributes": dict(state["attributes"])}

        async def fake_update_asset(asset_id, updates):
            if "attributes" in updates:
                state["attributes"] = updates["attributes"]
            return {}

        fm_client_mock.get_asset.side_effect = fake_get_asset
        fm_client_mock.update_asset.side_effect = fake_update_asset

        # Writer 1: grid provisioning sets phases/capacity.
        await fm.set_asset_attributes(
            {"phases": 3, "capacity_per_phase": 25}, asset_id=171
        )
        # Writer 2: version attributes on the same asset.
        await fm.set_asset_attributes({"v2g-liberty-version": "1.2.3"}, asset_id=171)

        assert state["attributes"] == {
            "phases": 3,
            "capacity_per_phase": 25,
            "v2g-liberty-version": "1.2.3",
        }


class TestPostSensorData:
    """post_sensor_data treats a 2xx 'failure' from the client as success.

    FlexMeasures returns 202 (Accepted) for sensor-data posts; the
    flexmeasures-client raises ValueError('Request failed with status code 202')
    because it whitelists only one status. The data was accepted, so we must
    not report a failure (which would retry forever and flag FM as down).
    """

    @pytest.mark.asyncio
    async def test_202_is_treated_as_success(self, fm, fm_client_mock):
        fm.set_fm_connection_status = AsyncMock()
        fm_client_mock.post_sensor_data = AsyncMock(
            side_effect=ValueError("Request failed with status code 202")
        )
        result = await fm.post_sensor_data(
            sensor_id=5,
            values=[0.1, 0.2],
            start="2026-01-01T00:00:00+00:00",
            duration="PT10M",
            uom="kW",
        )
        assert result is True
        fm.set_fm_connection_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_200_is_treated_as_success(self, fm, fm_client_mock):
        fm_client_mock.post_sensor_data = AsyncMock(return_value=None)
        result = await fm.post_sensor_data(
            sensor_id=5,
            values=[0.1],
            start="2026-01-01T00:00:00+00:00",
            duration="PT5M",
            uom="kW",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_error_status_is_a_failure(self, fm, fm_client_mock):
        fm.set_fm_connection_status = AsyncMock()
        fm_client_mock.post_sensor_data = AsyncMock(
            side_effect=ValueError("Request failed with status code 500")
        )
        result = await fm.post_sensor_data(
            sensor_id=5,
            values=[0.1],
            start="2026-01-01T00:00:00+00:00",
            duration="PT5M",
            uom="kW",
        )
        assert result is False
        fm.set_fm_connection_status.assert_called_once_with(connected=False)
