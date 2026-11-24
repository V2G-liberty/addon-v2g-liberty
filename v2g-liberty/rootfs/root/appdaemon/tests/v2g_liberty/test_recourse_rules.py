"""The contract for FlexMeasures' recourse rules: recourse/rules.py against
the fixtures in fixtures/fm/.

The fixtures are the agreed examples (design revision 3). The parser must
read every valid one and refuse every malformed shape with a RulesError, and
respond() must give the setpoints the scenario catalogue expects.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from apps.v2g_liberty.recourse.rules import (
    Band,
    DeviceRules,
    RulesError,
    SlotRule,
    parse_rules,
    respond,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fm"
CET = timezone(timedelta(hours=1))
CHARGER = 65
NOON = datetime(2026, 12, 10, 12, 0, tzinfo=CET)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def rules_of(name: str) -> DeviceRules:
    rules = parse_rules(fixture(name), sensor_id=CHARGER)
    assert rules is not None
    return rules


def band(*ranges) -> Band:
    return Band(tuple(ranges))


def rule(p_w=4400.0, gain=-1.0, ranges=((1400.0, 7400.0),), reference_w=-4400.0):
    return SlotRule(
        start=NOON, p_w=p_w, reference_w=reference_w, gain=gain, band=band(*ranges)
    )


def assert_band(actual: Band, *expected):
    """Compare a band's ranges with a tolerance: the fixtures are MW at full
    precision and the parser multiplies by a million."""
    assert len(actual.ranges) == len(expected)
    for (lo, hi), (want_lo, want_hi) in zip(actual.ranges, expected, strict=True):
        assert (lo is None) == (want_lo is None)
        assert (hi is None) == (want_hi is None)
        if lo is not None:
            assert lo == pytest.approx(want_lo)
        if hi is not None:
            assert hi == pytest.approx(want_hi)


class TestParseFixtures:
    def test_a_schedule_without_response_has_no_rules(self):
        schedule = fixture("schedule_response_without_response")
        assert parse_rules(schedule, sensor_id=CHARGER) is None

    def test_the_four_variants_are_read_in_watts(self):
        rules = rules_of("schedule_response_with_response")
        assert rules.sensor_id == CHARGER
        assert rules.start == NOON
        assert rules.resolution == timedelta(minutes=5)
        single, listed, follow, discharging = rules.rules

        assert single.start == NOON
        assert single.p_w == pytest.approx(4400.0)
        assert single.reference_w == pytest.approx(-4400.0)
        assert single.gain == -1.0
        assert_band(single.band, (1400.0, 7400.0))

        assert listed.start == NOON + timedelta(minutes=5)
        assert_band(listed.band, (-7400.0, -1400.0), (0.0, 0.0), (1400.0, 7400.0))

        assert follow.gain == 0.0
        assert follow.p_w == 0.0
        assert follow.reference_w == pytest.approx(1200.0)
        assert_band(follow.band, (0.0, 0.0))

        assert discharging.p_w == pytest.approx(-5900.0)
        assert_band(discharging.band, (-7400.0, -1400.0))

    def test_a_null_side_is_no_bound(self):
        (only,) = rules_of("schedule_response_null_band").rules
        assert_band(only.band, (None, -1400.0))

    def test_the_night_follows_the_plan(self):
        rules = rules_of("schedule_response_night_k0")
        assert len(rules.rules) == 4
        for slot in rules.rules:
            assert slot.gain == 0.0
            assert slot.p_w == pytest.approx(7400.0)
            assert_band(slot.band, (7400.0, 7400.0))

    def test_a_target_raises_the_lower_bound_towards_the_deadline(self):
        rules = rules_of("schedule_response_band_target")
        lower_bounds = [slot.band.ranges[-1][0] for slot in rules.rules]
        assert lower_bounds == pytest.approx([1400.0, 3000.0, 4500.0, 7400.0])
        assert all(slot.gain == -1.0 for slot in rules.rules)

    def test_without_a_forecast_the_reference_is_zero(self):
        rules = rules_of("schedule_response_reference_zero")
        assert [slot.reference_w for slot in rules.rules] == [0.0, 0.0, 0.0, 0.0]
        assert [slot.gain for slot in rules.rules] == [-1.0, 0.0, -1.0, 0.0]

    def test_a_collapsed_band_is_read_as_such(self):
        rules = rules_of("schedule_response_band_collapsed")
        for slot in rules.rules:
            assert slot.gain == -1.0
            assert_band(slot.band, (7400.0, 7400.0))

    def test_the_opt_in_request_documents_the_new_fields(self):
        """The trigger we will send once a server has the feature. Not sent in
        0.9.1; the fixture pins the shape."""
        request = fixture("trigger_request_opt_in")
        context = request["flex-context"]
        assert context["reference-sensor"] == {"sensor": 4711}
        assert context["response-order"] == [{"sensor": CHARGER}]
        assert context["retrigger-on-band-exit"] is True
        assert context["reference-at-start"] == "0.4 kW"
        (charger,) = request["flex-model"]
        assert len(charger["operation-modes"]) == 3
        assert charger["response"]["response-time"] == "PT150S"

    def test_the_repr_is_short(self):
        rules = rules_of("schedule_response_with_response")
        assert repr(rules) == (
            "DeviceRules(sensor_id=65, start=2026-12-10T12:00:00+01:00, "
            "resolution=5 min, slots=4)"
        )


def _schedule(**changes) -> dict:
    schedule = fixture("schedule_response_with_response")
    schedule.update(changes)
    return schedule


def _first_rule(**changes) -> dict:
    schedule = fixture("schedule_response_with_response")
    schedule["response"][0].update(changes)
    return schedule


class TestRefusedShapes:
    @pytest.mark.parametrize(
        ("schedule", "reason"),
        [
            (_schedule(response={"reference": 0.0}), "not a list"),
            (_schedule(values=None), "no values"),
            (_schedule(values=[], response=[]), "no values"),
            (fixture("schedule_response_invalid_length"), "1 rules for 4 values"),
            (_schedule(unit="kWh"), "unknown unit 'kWh'"),
            (_schedule(unit=None), "unknown unit None"),
            (_schedule(start=None), "start and duration"),
            (_schedule(duration="twenty minutes"), "start and duration"),
            (_schedule(values=[None, 0.0044, 0.0, -0.0059]), "slot 0: value"),
            (_first_rule(reference=None), "slot 0: reference is not a number"),
            (_first_rule(reference=True), "slot 0: reference is not a number"),
            (_first_rule(gain="-1"), "slot 0: gain is not a number"),
            (_first_rule(band=[]), "slot 0: band is not a non-empty list"),
            (_first_rule(band="wide"), "slot 0: band is not a non-empty list"),
            (_first_rule(band=[[0.0014, 0.0074, 0.0]]), "range 0 is not a [min, max]"),
            (_first_rule(band=[[0.0074, 0.0014]]), "range 0 has its min above its max"),
            (_first_rule(band=[[0.0, 0.002], [0.001, 0.0074]]), "not ascending"),
            (_first_rule(band=[[0.0014, 0.0074], [0.0, 0.0]]), "not ascending"),
            (_first_rule(band=[[None, 0.0], [None, 0.0074]]), "not ascending"),
        ],
    )
    def test_is_refused_with_the_reason(self, schedule, reason):
        with pytest.raises(RulesError, match=re.escape(reason)):
            parse_rules(schedule, sensor_id=CHARGER)

    def test_a_rule_that_is_not_an_object_is_refused(self):
        schedule = _schedule()
        schedule["response"][2] = 0.0
        with pytest.raises(RulesError, match="slot 2: the rule is not an object"):
            parse_rules(schedule, sensor_id=CHARGER)

    def test_a_rule_without_a_key_is_refused(self):
        schedule = _schedule()
        del schedule["response"][1]["gain"]
        del schedule["response"][1]["band"]
        with pytest.raises(RulesError, match="slot 1: the rule lacks gain, band"):
            parse_rules(schedule, sensor_id=CHARGER)


class TestBand:
    DEAD_ZONE = band((-7400.0, -1400.0), (0.0, 0.0), (1400.0, 7400.0))

    @pytest.mark.parametrize(
        ("power_w", "inside"),
        [
            (1400.0, True),
            (7400.0, True),
            (0.0, True),
            (-3000.0, True),
            (700.0, False),
            (-700.0, False),
            (7401.0, False),
        ],
    )
    def test_contains(self, power_w, inside):
        assert self.DEAD_ZONE.contains(power_w) is inside

    def test_a_null_side_is_unbounded(self):
        open_below = band((None, -1400.0))
        assert open_below.contains(-50_000.0)
        assert not open_below.contains(0.0)
        assert open_below.nearest(500.0, plan_w=-5900.0) == -1400.0

    def test_inside_the_band_the_power_is_returned_as_is(self):
        assert self.DEAD_ZONE.nearest(3000.0, plan_w=4400.0) == 3000.0

    @pytest.mark.parametrize(
        ("power_w", "expected"),
        [(9000.0, 7400.0), (-9000.0, -7400.0), (1000.0, 1400.0), (300.0, 0.0)],
    )
    def test_outside_the_band_the_nearest_edge_is_returned(self, power_w, expected):
        assert self.DEAD_ZONE.nearest(power_w, plan_w=4400.0) == expected

    def test_in_the_middle_of_the_dead_zone_the_plan_side_wins(self):
        assert self.DEAD_ZONE.nearest(700.0, plan_w=4400.0) == 1400.0
        assert self.DEAD_ZONE.nearest(700.0, plan_w=-4400.0) == 0.0
        assert self.DEAD_ZONE.nearest(-700.0, plan_w=-4400.0) == -1400.0

    def test_a_band_without_edges_allows_everything(self):
        assert band((None, None)).nearest(123_456.0, plan_w=0.0) == 123_456.0


class TestRespond:
    def test_follow_the_plan_ignores_the_residual(self):
        """A3, C4: a cheap night, gain 0 and band [[p, p]]. A heat-pump cycle
        changes the load, not the price per kWh."""
        night = rule(p_w=7400.0, gain=0.0, ranges=((7400.0, 7400.0),))
        assert respond(night, residual_w=2000.0) == 7400.0
        assert respond(night, residual_w=-2000.0) == 7400.0

    def test_a_shortfall_of_sun_is_absorbed_down_to_the_band(self):
        """B1: 4.4 kW planned on a 4.4 kW surplus. Clouds leave 1 kW surplus
        (residual +3 kW): charge 1.4 kW. More clouds: the dead zone is left
        for the nearest edge, idle. Extra sun: charge harder, to 7.4 kW."""
        sunny = rule(ranges=((0.0, 0.0), (1400.0, 7400.0)))
        assert respond(sunny, residual_w=3000.0) == 1400.0
        assert respond(sunny, residual_w=4000.0) == 0.0
        assert respond(sunny, residual_w=-2000.0) == 6400.0
        assert respond(sunny, residual_w=-5000.0) == 7400.0

    def test_a_rising_lower_bound_makes_the_charger_import_for_the_target(self):
        """B2: with a target the band's lower bound rises; a shortfall of sun
        is absorbed only down to that bound."""
        late = rule(ranges=((3000.0, 7400.0),))
        assert respond(late, residual_w=3000.0) == 3000.0

    def test_a_collapsed_band_behaves_as_follow_the_plan(self):
        """D2: low state of charge and a target soon, gain -1 but band
        [[p, p]]."""
        forced = rule(p_w=7400.0, ranges=((7400.0, 7400.0),))
        assert respond(forced, residual_w=5000.0) == 7400.0
        assert respond(forced, residual_w=-5000.0) == 7400.0

    def test_a_null_side_lets_the_device_go_beyond_the_plan(self):
        """The expensive evening with an open lower side: when the house
        imports more than planned the car discharges harder, with no bound
        from the planner; when it exports more, the car eases off but never
        charges."""
        evening = rule(p_w=-5900.0, reference_w=2100.0, ranges=((None, -1400.0),))
        assert respond(evening, residual_w=2000.0) == -7900.0
        assert respond(evening, residual_w=20_000.0) == -25_900.0
        assert respond(evening, residual_w=-3000.0) == -2900.0
        assert respond(evening, residual_w=-6000.0) == -1400.0

    def test_the_middle_of_the_dead_zone_goes_to_the_plan_side(self):
        """E1: a setpoint of 0.7 kW is as far from idle as from the minimum
        charge power; the plan was charging, so the minimum wins."""
        sunny = rule(ranges=((-7400.0, -1400.0), (0.0, 0.0), (1400.0, 7400.0)))
        assert respond(sunny, residual_w=3700.0) == 1400.0


class TestRuleAt:
    def test_the_slot_that_contains_the_moment(self):
        rules = rules_of("schedule_response_with_response")
        assert rules.rule_at(NOON) is rules.rules[0]
        assert rules.rule_at(NOON + timedelta(minutes=7)) is rules.rules[1]
        assert rules.rule_at(NOON + timedelta(minutes=19, seconds=59)) is rules.rules[3]

    def test_outside_the_schedule_there_is_no_rule(self):
        rules = rules_of("schedule_response_with_response")
        assert rules.rule_at(NOON - timedelta(seconds=1)) is None
        assert rules.rule_at(NOON + timedelta(minutes=20)) is None
