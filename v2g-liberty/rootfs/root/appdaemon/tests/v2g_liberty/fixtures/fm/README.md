# FlexMeasures contract fixtures

The JSON files here are the contract between V2G Liberty and FlexMeasures
for the reaction rules (the design calls them recourse rules), as agreed
with Seita in the kick-off document of 24 November 2026. `recourse/rules.py`
is tested against them. A change to the contract is a change to a fixture;
the parser follows the fixtures, not the other way round. After the first
joint test on staging the hand-written responses are to be replaced by real
ones.

The contract in short: the opt-in is `"react": true` on the device's
flex-model item (nothing changes in the flex-context); the schedule then
carries a `reaction` array with one entry per value, `{reference, react,
band}`; the controller computes `surprise = (meter - device) - reference`
and `setpoint = clip(p - react * surprise, band)`.

Sensor ids and timestamps are illustrative: 65 is the charger's power
sensor, 67 its state of charge, 58 and 70 the prices, 4712 and 4713 the
aggregates. Powers are in the schedule's `unit` (MW); the parser converts
to W.

| File | What it shows |
|---|---|
| `trigger_request_opt_in.json` | The trigger as the client sends it once we opt in: today's flex-context, and on the device `operation-modes` plus `react: true`. **Not sent in 0.9.1**: a FlexMeasures without the feature rejects `react` with HTTP 422, so the request side waits for a server version that has it. |
| `schedule_response_without_response.json` | Today's schedule, also what a server without the feature returns: no rules. |
| `schedule_response_with_response.json` | Four slots that cover the variants: a single range; a list of ranges around the charger's dead zone; follow-the-plan (`react` 0, band `[[0, 0]]`); discharging with the device's own production range as band. |
| `schedule_response_invalid_length.json` | One rule for four values. The rules are dropped; the schedule is kept. |
| `schedule_response_null_band.json` | A `null` side: legal, "no bound from the planner". It does not occur for a device with a known capacity, but the parser and `react()` handle it. |
| `schedule_response_night_k0.json` | Cheap night, charging to the target: `react` 0 and `[[p, p]]` in every slot. A heat-pump cycle changes the load, not the price: no reaction. |
| `schedule_response_band_target.json` | Clouds where sun was planned, with a target this evening: `react` 1, and the lower bound of the band rises towards the deadline, so the charger imports deliberately rather than miss the target. |
| `schedule_response_reference_zero.json` | No forecast: `reference` 0.0 in every slot; `react` 1 only where the price is at or below zero or the connection binds. |
| `schedule_response_band_collapsed.json` | Low state of charge and a target soon: `react` 1 with a collapsed band `[[p, p]]`, which behaves as follow-the-plan. |
