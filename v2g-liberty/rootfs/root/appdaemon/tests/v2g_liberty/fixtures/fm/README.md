# FlexMeasures contract fixtures

The JSON files here are the contract between V2G Liberty and FlexMeasures
for the recourse rules, as agreed with Seita (design revision 3,
23 November 2026). `recourse/rules.py` is tested against them. A change to
the contract is a change to a fixture; the parser follows the fixtures, not
the other way round. After the first joint test on staging the hand-written
responses are to be replaced by real ones.

Sensor ids and timestamps are illustrative: 65 is the charger's power
sensor, 67 its state of charge, 58 and 70 the prices, 4711 the reference
(net power at the connection), 4712 and 4713 the aggregates. Powers are in
the schedule's `unit` (MW); the parser converts to W.

| File | What it shows |
|---|---|
| `trigger_request_opt_in.json` | The trigger as the client sends it once we opt in: `reference-sensor`, `response-order`, `retrigger-on-band-exit` and `reference-at-start` in the flex-context, `operation-modes` and the `response` capability in the flex-model. **Not sent in 0.9.1**: FlexMeasures rejects unknown keys (422 at the trigger, a failed job for flex-context keys), so the request side waits for a server that has the feature. |
| `schedule_response_without_response.json` | Today's schedule, also what a server without the feature returns: no rules. |
| `schedule_response_with_response.json` | Four slots that cover the variants: a single range; a list of ranges around the charger's dead zone; follow-the-plan (`gain` 0, band `[[0, 0]]`); discharging with the device's own production range as band. |
| `schedule_response_invalid_length.json` | One rule for four values. The rules are dropped; the schedule is kept. |
| `schedule_response_null_band.json` | A `null` side: legal, "no bound from the planner". It does not occur for a device with a known capacity, but the parser and `respond()` handle it. |
| `schedule_response_night_k0.json` | Cheap night, charging to the target: `gain` 0 and `[[p, p]]` in every slot. A heat-pump cycle changes the load, not the price: no reaction. |
| `schedule_response_band_target.json` | Clouds where sun was planned, with a target this evening: `gain` -1, and the lower bound of the band rises towards the deadline, so the charger imports deliberately rather than miss the target. |
| `schedule_response_reference_zero.json` | No forecast: `reference` 0.0 in every slot; `gain` -1 only where the price is at or below zero or the connection binds. |
| `schedule_response_band_collapsed.json` | Low state of charge and a target soon: `gain` -1 with a collapsed band `[[p, p]]`, which behaves as follow-the-plan. |
