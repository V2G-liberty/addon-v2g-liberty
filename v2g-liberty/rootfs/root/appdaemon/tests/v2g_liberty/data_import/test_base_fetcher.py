"""Tests for BaseFetcher class."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.data_import.fetchers.base_fetcher import BaseFetcher


@pytest.fixture
def hass():
    """Create a mock Hass instance."""
    hass = AsyncMock(spec=Hass)
    hass.log = MagicMock()
    return hass


@pytest.fixture
def fm_client():
    """Create a mock FlexMeasures client."""
    return AsyncMock()


@pytest.fixture
def base_fetcher(hass, fm_client):
    """Create a BaseFetcher instance."""
    return BaseFetcher(hass, fm_client)


class TestBaseFetcher:
    """Test BaseFetcher class."""

    def test_initialisation(self, hass, fm_client):
        """Test BaseFetcher initialisation."""
        fetcher = BaseFetcher(hass, fm_client)
        assert fetcher.hass == hass
        assert fetcher.fm_client_app == fm_client

    def test_is_client_available_when_client_exists(self, base_fetcher):
        """Test is_client_available returns True when client is set."""
        result = base_fetcher.is_client_available()
        assert result is True

    def test_is_client_available_when_client_is_none(self, hass):
        """Test is_client_available returns False when client is None."""
        fetcher = BaseFetcher(hass, None)
        result = fetcher.is_client_available()
        assert result is False
