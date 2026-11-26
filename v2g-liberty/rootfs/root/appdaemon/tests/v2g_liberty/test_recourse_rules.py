"""The contract for FlexMeasures' reaction rules: recourse/rules.py against
the fixtures in fixtures/fm/.

The fixtures are the agreed examples (kick-off document, 24 November 2026).
The parser must read every valid one and refuse every malformed shape with
a InvalidReactionError, and react() must give the setpoints the scenario catalogue
expects.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from apps.v2g_liberty.recourse.rules import (
    Band,
    InvalidReactionError,
    ReactionRule,
    ReactionSchedule,
    parse_reaction,
    react,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fm"
CET = timezone(timedelta(hours=1))
CHARGER = 65
NOON = datetime(2026, 12, 10, 12, 0, tzinfo=CET)
TODAYS_FLEX_CONTEXT_KEYS = {
    "consumption-price",
    "production-price",
    "relax-soc-constraints",
    "relax-capacity-constraints",
    "aggregate-consumption",
    "aggregate-production",
}


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def rules_of(name: str) -> ReactionSchedule:
    rules = parse_reaction(fixture(name), sensor_id=CHARGER)
    assert rules is not None
    return rules


def band(*ranges) -> Band:
    return Band(tuple(ranges))


def rule(p_w=4400.0, react=1.0, ranges=((1400.0, 7400.0),), reference_w=-4400.0):
    return ReactionRule(
        start=NOON, p_w=p_w, reference_w=reference_w, react=react, band=band(*ranges)
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
    def test_a_schedule_without_reaction_has_no_rules(self):
        schedule = fixture("schedule_without_reaction")
        assert parse_reaction(schedule, sensor_id=CHARGER) is None

    def test_the_four_variants_are_read_in_watts(self):
        rules = rules_of("schedule_with_reaction")
        assert rules.sensor_id == CHARGER
        assert rules.start == NOON
        assert rules.resolution == timedelta(minutes=5)
        single, listed, follow, discharging = rules.rules

        assert single.start == NOON
        assert single.p_w == pytest.approx(4400.0)
        assert single.reference_w == pytest.approx(-4400.0)
        assert single.react == 1.0
        assert_band(single.band, (1400.0, 7400.0))

        assert listed.start == NOON + timedelta(minutes=5)
        assert_band(listed.band, (-7400.0, -1400.0), (0.0, 0.0), (1400.0, 7400.0))

        assert follow.react == 0.0
        assert follow.p_w == 0.0
        assert follow.reference_w == pytest.approx(1200.0)
        assert_band(follow.band, (0.0, 0.0))

        assert discharging.p_w == pytest.approx(-5900.0)
        assert_band(discharging.band, (-7400.0, -1400.0))

    def test_a_null_side_is_refused(self):
        """Never served: every device has a power capacity. Refused as the
        client refuses it, so the two parsers agree on the input."""
        reason = "slot 0: band: range 0 lo is not a number but None"
        with pytest.raises(InvalidReactionError, match=re.escape(reason)):
            parse_reaction(fixture("schedule_null_side"), sensor_id=CHARGER)

    def test_the_night_follows_the_plan(self):
        rules = rules_of("schedule_night_k0")
        assert len(rules.rules) == 4
        for slot in rules.rules:
            assert slot.react == 0.0
            assert slot.p_w == pytest.approx(7400.0)
            assert_band(slot.band, (7400.0, 7400.0))

    def test_a_target_raises_the_lower_bound_towards_the_deadline(self):
        rules = rules_of("schedule_band_target")
        lower_bounds = [slot.band.ranges[-1][0] for slot in rules.rules]
        assert lower_bounds == pytest.approx([1400.0, 3000.0, 4500.0, 7400.0])
        # The last slot's band is a single point, and that comes as react 0.
        assert [slot.react for slot in rules.rules] == [1.0, 1.0, 1.0, 0.0]

    def test_without_a_forecast_the_reference_is_zero(self):
        rules = rules_of("schedule_reference_zero")
        assert [slot.reference_w for slot in rules.rules] == [0.0, 0.0, 0.0, 0.0]
        assert [slot.react for slot in rules.rules] == [1.0, 0.0, 1.0, 0.0]

    def test_a_collapsed_band_comes_with_react_zero(self):
        """The planner sets the share to 0 whenever the band leaves no room."""
        rules = rules_of("schedule_band_collapsed")
        for slot in rules.rules:
            assert slot.react == 0.0
            assert_band(slot.band, (7400.0, 7400.0))

    def test_the_opt_in_request_is_todays_plus_one_boolean(self):
        """The trigger we will send once a server has the feature. Not sent in
        0.9.1; the fixture pins the shape: nothing new in the flex-context,
        the operation modes and `react: true` on the device."""
        request = fixture("trigger_request_opt_in")
        assert set(request["flex-context"]) == TODAYS_FLEX_CONTEXT_KEYS
        (charger,) = request["flex-model"]
        assert charger["react"] is True
        assert len(charger["operation-modes"]) == 3
        assert "response" not in charger

    def test_the_repr_is_short(self):
        rules = rules_of("schedule_with_reaction")
        assert repr(rules) == (
            "ReactionSchedule(sensor_id=65, start=2026-12-10T12:00:00+01:00, "
            "resolution=5 min, slots=4)"
        )


def _schedule(**changes) -> dict:
    schedule = fixture("schedule_with_reaction")
    schedule.update(changes)
    return schedule


def _first_rule(**changes) -> dict:
    schedule = fixture("schedule_with_reaction")
    schedule["reaction"][0].update(changes)
    return schedule


class TestRefusedShapes:
    @pytest.mark.parametrize(
        ("schedule", "reason"),
        [
            (_schedule(reaction={"reference": 0.0}), "reaction is not a list"),
            (_schedule(values=None), "no values"),
            (_schedule(values=[], reaction=[]), "no values"),
            (fixture("schedule_invalid_length"), "1 rules for 4 values"),
            (_schedule(unit="kWh"), "unknown unit 'kWh'"),
            (_schedule(unit=None), "unknown unit None"),
            (_schedule(start=None), "start and duration"),
            (_schedule(duration="twenty minutes"), "start and duration"),
            (_schedule(values=[None, 0.0044, 0.0, -0.0059]), "slot 0: value"),
            (_first_rule(reference=None), "slot 0: reference is not a number"),
            (_first_rule(reference=True), "slot 0: reference is not a number"),
            (_first_rule(react="1"), "slot 0: react is not a number"),
            (_first_rule(band=[]), "slot 0: band is not a non-empty list"),
            (_first_rule(band="wide"), "slot 0: band is not a non-empty list"),
            (_first_rule(band=[[0.0014, 0.0074, 0.0]]), "range 0 is not a [lo, hi]"),
            (_first_rule(band=[[0.0074, 0.0014]]), "range 0 has its lo above its hi"),
            (_first_rule(band=[[0.0, 0.002], [0.001, 0.0074]]), "not ascending"),
            (_first_rule(band=[[0.0014, 0.0074], [0.0, 0.0]]), "not ascending"),
            (
                _first_rule(band=[[None, 0.0], [None, 0.0074]]),
                "range 0 lo is not a number",
            ),
            (
                _first_rule(band=[[0.0, None], [0.0014, 0.0074]]),
                "range 0 hi is not a number",
            ),
        ],
    )
    def test_is_refused_with_the_reason(self, schedule, reason):
        with pytest.raises(InvalidReactionError, match=re.escape(reason)):
            parse_reaction(schedule, sensor_id=CHARGER)

    def test_a_rule_that_is_not_an_object_is_refused(self):
        schedule = _schedule()
        schedule["reaction"][2] = 0.0
        with pytest.raises(
            InvalidReactionError, match="slot 2: the rule is not an object"
        ):
            parse_reaction(schedule, sensor_id=CHARGER)

    def test_a_rule_without_a_key_is_refused(self):
        schedule = _schedule()
        del schedule["reaction"][1]["react"]
        del schedule["reaction"][1]["band"]
        with pytest.raises(
            InvalidReactionError, match="slot 1: the rule lacks react, band"
        ):
            parse_reaction(schedule, sensor_id=CHARGER)


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

    def test_inside_the_band_the_power_is_returned_as_is(self):
        assert self.DEAD_ZONE.nearest(3000.0, plan_w=4400.0) == 3000.0

    def test_inside_another_range_than_the_plans_it_is_returned_as_is(self):
        """A request for a mode change; whether to follow it is the engine's
        policy, not the rule's."""
        assert self.DEAD_ZONE.nearest(-3000.0, plan_w=4400.0) == -3000.0
        assert self.DEAD_ZONE.nearest(0.0, plan_w=4400.0) == 0.0

    @pytest.mark.parametrize(
        ("power_w", "expected"),
        [(9000.0, 7400.0), (-9000.0, -7400.0), (7400.0, 7400.0)],
    )
    def test_beyond_the_band_the_outer_bound_is_returned(self, power_w, expected):
        assert self.DEAD_ZONE.nearest(power_w, plan_w=4400.0) == expected

    @pytest.mark.parametrize(
        ("power_w", "plan_w", "expected"),
        [
            (1000.0, 4400.0, 1400.0),  # near the charging edge, plan charging
            (300.0, 4400.0, 1400.0),  # near idle, but the plan is charging
            (700.0, 4400.0, 1400.0),
            (-700.0, 4400.0, 0.0),  # the other hole: towards the plan's side
            (700.0, -4400.0, 0.0),
            (-700.0, -4400.0, -1400.0),
            (-100.0, -4400.0, -1400.0),  # near idle, but the plan is discharging
            (700.0, 0.0, 0.0),  # an idle plan stays idle
            (-700.0, 0.0, 0.0),
        ],
    )
    def test_in_a_hole_the_edge_on_the_plans_side_is_returned(
        self, power_w, plan_w, expected
    ):
        """Not the nearest edge: the side of the mode the device is in, which
        at the start of a slot is the range holding the plan."""
        assert self.DEAD_ZONE.nearest(power_w, plan_w=plan_w) == expected

    @pytest.mark.parametrize(
        ("power_w", "beyond"),
        [
            (700.0, False),
            (-700.0, False),
            (3000.0, False),
            (7401.0, True),
            (-7401.0, True),
        ],
    )
    def test_a_gap_is_not_beyond_the_band_but_the_outer_edges_are(
        self, power_w, beyond
    ):
        """Q2: rounding out of the dead zone is never a band exit."""
        assert self.DEAD_ZONE.exceeds(power_w) is beyond


class TestReact:
    def test_follow_the_plan_ignores_the_deviation(self):
        """A3, C4: a cheap night, react 0 and band [[p, p]]. A heat-pump cycle
        changes the load, not the price per kWh."""
        night = rule(p_w=7400.0, react=0.0, ranges=((7400.0, 7400.0),))
        assert react(night, deviation_w=2000.0) == 7400.0
        assert react(night, deviation_w=-2000.0) == 7400.0

    def test_a_shortfall_of_sun_is_absorbed_down_to_the_band(self):
        """B1: 4.4 kW planned on a 4.4 kW surplus. Clouds leave 1.4 kW surplus
        (deviation +3 kW): charge 1.4 kW. More clouds ask 0.4 kW, in the dead
        zone: the charging mode holds, 1.4 kW. No surplus left at all: idle,
        the outer bound. Extra sun: charge harder, to 7.4 kW."""
        sunny = rule(ranges=((0.0, 0.0), (1400.0, 7400.0)))
        assert react(sunny, deviation_w=3000.0) == 1400.0
        assert react(sunny, deviation_w=4000.0) == 1400.0
        assert react(sunny, deviation_w=5000.0) == 0.0
        assert react(sunny, deviation_w=-2000.0) == 6400.0
        assert react(sunny, deviation_w=-5000.0) == 7400.0

    def test_a_balance_point_reference_keeps_the_planned_mode(self):
        """Seita's example of 26 November: modes idle and 1.4 to 7.4 kW, a
        0.5 kW surplus, a plan that charges 1.4 kW because the car needs the
        energy, so the reference is the balance point -1.4 kW. The house as
        forecast asks 0.5 kW, in the hole: the nearest edge would stop a car
        the planner wanted charging; the plan's side gives 1.4 kW. With 1 kW
        of extra sun the 1.5 kW asked is inside the range and served as is."""
        balanced = rule(
            p_w=1400.0, reference_w=-1400.0, ranges=((0.0, 0.0), (1400.0, 7400.0))
        )
        assert react(balanced, deviation_w=-500.0 - -1400.0) == 1400.0
        assert react(balanced, deviation_w=-1500.0 - -1400.0) == 1500.0

    def test_a_rising_lower_bound_makes_the_charger_import_for_the_target(self):
        """B2: with a target the band's lower bound rises; a shortfall of sun
        is absorbed only down to that bound."""
        late = rule(ranges=((3000.0, 7400.0),))
        assert react(late, deviation_w=3000.0) == 3000.0

    def test_a_collapsed_band_behaves_as_follow_the_plan(self):
        """D2: low state of charge and a target soon. The planner serves
        react 0 here; a share of 1 with a band [[p, p]] would do the same."""
        forced = rule(p_w=7400.0, ranges=((7400.0, 7400.0),))
        assert react(forced, deviation_w=5000.0) == 7400.0
        assert react(forced, deviation_w=-5000.0) == 7400.0

    def test_discharging_harder_stays_within_the_production_range(self):
        """The expensive evening: when the house imports more than planned
        the car discharges harder, up to its production range; when it
        exports more, the car eases off but never charges."""
        evening = rule(p_w=-5900.0, reference_w=2100.0, ranges=((-7400.0, -1400.0),))
        assert react(evening, deviation_w=1000.0) == -6900.0
        assert react(evening, deviation_w=20_000.0) == -7400.0
        assert react(evening, deviation_w=-3000.0) == -2900.0
        assert react(evening, deviation_w=-6000.0) == -1400.0

    def test_the_middle_of_the_dead_zone_goes_to_the_plan_side(self):
        """E1: a setpoint of 0.7 kW lies in the dead zone; the plan was
        charging, so the charging mode's edge, the minimum, is the answer."""
        sunny = rule(ranges=((-7400.0, -1400.0), (0.0, 0.0), (1400.0, 7400.0)))
        assert react(sunny, deviation_w=3700.0) == 1400.0


class TestRuleAt:
    def test_the_slot_that_contains_the_moment(self):
        rules = rules_of("schedule_with_reaction")
        assert rules.rule_at(NOON) is rules.rules[0]
        assert rules.rule_at(NOON + timedelta(minutes=7)) is rules.rules[1]
        assert rules.rule_at(NOON + timedelta(minutes=19, seconds=59)) is rules.rules[3]

    def test_outside_the_schedule_there_is_no_rule(self):
        rules = rules_of("schedule_with_reaction")
        assert rules.rule_at(NOON - timedelta(seconds=1)) is None
        assert rules.rule_at(NOON + timedelta(minutes=20)) is None


VALID_FIXTURES = [
    "schedule_with_reaction",
    "schedule_night_k0",
    "schedule_band_target",
    "schedule_reference_zero",
    "schedule_band_collapsed",
]


class TestContractInvariants:
    @pytest.mark.parametrize("name", VALID_FIXTURES)
    def test_with_no_deviation_every_slot_gives_the_plan(self, name):
        """The plan lies inside its own band, so react(rule, 0) is p."""
        for slot in rules_of(name).rules:
            assert react(slot, deviation_w=0.0) == pytest.approx(slot.p_w)

    def test_a_plan_outside_its_band_is_refused(self):
        schedule = _schedule(values=[0.001, 0.0044, 0.0, -0.0059])
        with pytest.raises(
            InvalidReactionError, match=re.escape("slot 0: the plan (1000 W)")
        ):
            parse_reaction(schedule, sensor_id=CHARGER)

    def test_a_plan_just_below_an_edge_passes_within_rounding(self):
        schedule = _schedule(values=[0.00139999999, 0.0044, 0.0, -0.0059])
        assert parse_reaction(schedule, sensor_id=CHARGER) is not None

    @pytest.mark.parametrize("share", [-1.0, -0.01, 1.01, 2.0])
    def test_a_share_outside_zero_to_one_is_refused(self, share):
        reason = f"slot 0: react {share} is outside [0, 1]"
        with pytest.raises(InvalidReactionError, match=re.escape(reason)):
            parse_reaction(_first_rule(react=share), sensor_id=CHARGER)

    def test_a_share_in_between_is_applied_as_such(self):
        rules = parse_reaction(_first_rule(react=0.5), sensor_id=CHARGER)
        assert react(rules.rules[0], deviation_w=2000.0) == pytest.approx(3400.0)

    @pytest.mark.parametrize(
        ("schedule", "reason"),
        [
            (_first_rule(reference=float("nan")), "reference is not a finite number"),
            (
                _schedule(values=[float("inf"), 0.0044, 0.0, -0.0059]),
                "value is not a finite",
            ),
            (_first_rule(band=[[float("nan"), 0.0074]]), "range 0 lo is not a finite"),
        ],
    )
    def test_nan_and_infinity_are_refused(self, schedule, reason):
        with pytest.raises(InvalidReactionError, match=re.escape(reason)):
            parse_reaction(schedule, sensor_id=CHARGER)

    def test_a_duration_in_months_is_refused_not_crashed(self):
        with pytest.raises(
            InvalidReactionError, match="duration is not a fixed length"
        ):
            parse_reaction(_schedule(duration="P1M"), sensor_id=CHARGER)

    def test_ranges_may_touch(self):
        rules = parse_reaction(
            _first_rule(band=[[0.0, 0.0], [0.0, 0.0074]]), sensor_id=CHARGER
        )
        assert_band(rules.rules[0].band, (0.0, 0.0), (0.0, 7400.0))
