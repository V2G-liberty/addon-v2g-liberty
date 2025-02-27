import pytest
from unittest.mock import MagicMock

from apps.load_balancer.load_balancer import LoadBalancer


@pytest.fixture
def hass() -> MagicMock:
    return MagicMock()


@pytest.fixture
def rate_limiter() -> MagicMock:
    mock = MagicMock()
    mock.limit = 230
    mock.set_limit = MagicMock()
    return mock


@pytest.fixture
def load_balancer(hass, rate_limiter) -> LoadBalancer:
    """Setup de LoadBalancer app voor tests."""
    config = {
        "total_power_limit": "10",
        "max_charge_power": "8",
    }
    return LoadBalancer(hass=hass, rate_limiter=rate_limiter, config=config)


def test_reduce_charge_on_high_power(
    load_balancer: LoadBalancer, rate_limiter: MagicMock
):
    rate_limiter.set_limit.reset_mock()

    load_balancer.total_power_changed(30 * 230)
    assert load_balancer.high_timer
    assert not load_balancer.low_timer
    assert not load_balancer.cooldown_timer

    load_balancer.reduce_power({})
    rate_limiter.set_limit.assert_called_once_with(230)
    assert not load_balancer.high_timer
    assert load_balancer.cooldown_timer


def test_reduce_charge_on_high_negative_power(
    load_balancer: LoadBalancer, rate_limiter: MagicMock
):
    rate_limiter.set_limit.reset_mock()

    load_balancer.total_power_changed(-30 * 230)
    assert load_balancer.high_timer
    assert not load_balancer.low_timer
    assert not load_balancer.cooldown_timer

    load_balancer.reduce_power({})
    rate_limiter.set_limit.assert_called_once_with(230)
    assert not load_balancer.high_timer
    assert load_balancer.cooldown_timer


def test_reduce_charge_on_low_power(
    load_balancer: LoadBalancer, rate_limiter: MagicMock
):
    rate_limiter.limit = 230
    rate_limiter.set_limit.reset_mock()

    load_balancer.total_power_changed(3 * 230)
    assert load_balancer.low_timer
    assert not load_balancer.high_timer
    assert not load_balancer.cooldown_timer

    load_balancer.increase_power({})
    rate_limiter.set_limit.assert_called_once_with(int(7.5 * 230))
    assert not load_balancer.low_timer
    assert load_balancer.cooldown_timer


def test_reduce_charge_on_low_negative_power(
    load_balancer: LoadBalancer, rate_limiter: MagicMock
):
    rate_limiter.limit = 230
    rate_limiter.set_limit.reset_mock()

    load_balancer.total_power_changed(-3 * 230)
    assert load_balancer.low_timer
    assert not load_balancer.high_timer
    assert not load_balancer.cooldown_timer

    load_balancer.increase_power({})
    rate_limiter.set_limit.assert_called_once_with(int(7.5 * 230))
    assert not load_balancer.low_timer
    assert load_balancer.cooldown_timer


def test_timers_reset_on_return_to_range(
    load_balancer: LoadBalancer, rate_limiter: MagicMock
):
    rate_limiter.set_limit.reset_mock()

    load_balancer.total_power_changed(3 * 230)
    assert load_balancer.low_timer

    load_balancer.total_power_changed(10 * 230)
    assert not load_balancer.high_timer
    assert not load_balancer.low_timer
    assert not load_balancer.cooldown_timer


def test_no_adjustment_if_within_range(
    load_balancer: LoadBalancer, rate_limiter: MagicMock
):
    rate_limiter.limit = 10 * 230
    rate_limiter.set_limit.reset_mock()

    load_balancer.total_power_changed(10 * 230)
    rate_limiter.set_limit.assert_not_called()
    assert not load_balancer.high_timer
    assert not load_balancer.low_timer
    assert not load_balancer.cooldown_timer
