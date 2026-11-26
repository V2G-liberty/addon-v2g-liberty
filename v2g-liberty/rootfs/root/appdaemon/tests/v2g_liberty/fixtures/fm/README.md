# FlexMeasures contract fixtures

The JSON files here are the contract between V2G Liberty and FlexMeasures
for the reaction rules (the design calls them recourse rules), as agreed
with Seita in the kick-off document of 24 and 26 November 2026. `recourse/rules.py`
is tested against them. A change to the contract is a change to a fixture;
the parser follows the fixtures, not the other way round. After the first
joint test on staging the hand-written responses are to be replaced by real
ones.

The contract in short: the opt-in is `"react": true` on the device's
flex-model item (nothing changes in the flex-context); the schedule then
carries a `reaction` array with one entry per value, `{reference, react,
band}`. `reference` is the uncontrolled power the plan is balanced against:
the forecast, or the balance point where the device's modes keep the plan
from balancing the connection exactly. The controller computes
`deviation = (meter - device) - reference` and
`setpoint = clip(p - react * deviation, band)`; a setpoint in a dead zone
takes the edge on the side of the plan's mode.

Sensor ids and timestamps are illustrative: 65 is the charger's power
sensor, 67 its state of charge, 58 and 70 the prices, 4712 and 4713 the
aggregates. Powers are in the schedule's `unit` (MW); the parser converts
to W.

| File | What it shows |
|---|---|
| `trigger_request_opt_in.json` | The trigger as the client sends it once we opt in: today's flex-context, and on the device `operation-modes` plus `react: true`. **Not sent in 0.9.1**: a FlexMeasures without the feature rejects `react` with HTTP 422, so the request side waits for a server version that has it. |
| `schedule_without_reaction.json` | Today's schedule, also what a server without the feature returns: no rules. |
| `schedule_with_reaction.json` | Four slots that cover the variants: a single range; a list of ranges around the charger's dead zone; follow-the-plan (`react` 0, band `[[0, 0]]`); discharging with the device's own production range as band. |
| `schedule_invalid_length.json` | One rule for four values. The rules are dropped; the schedule is kept. |
| `schedule_null_side.json` | A `null` side. Never served: the planner requires a power capacity for every device, so every side is a finite number. Refused, as the client refuses it; kept as a parser test. |
| `schedule_night_k0.json` | Cheap night, charging to the target: `react` 0 and `[[p, p]]` in every slot. A heat-pump cycle changes the load, not the price: no reaction. |
| `schedule_band_target.json` | Clouds where sun was planned, with a target this evening: `react` 1, and the lower bound of the band rises towards the deadline, so the charger imports deliberately rather than miss the target. The last slot's band is a single point, and a point band always comes with `react` 0. |
| `schedule_reference_zero.json` | No forecast: `reference` 0.0 in every slot; `react` 1 only where the price is at or below zero or the connection binds. |
| `schedule_band_collapsed.json` | Low state of charge and a target soon: the band leaves no room, and the planner then serves `react` 0 with `[[p, p]]`, as it does whenever a band collapses. |

Two slots export (`schedule_with_reaction.json` slot 4, `schedule_reference_zero.json`
slot 3). They are parser tests; at asymmetric prices the planner does not
produce them.
