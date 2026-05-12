# Developer events

V2G Liberty exposes several Home Assistant events for debugging and development.
Fire these from **Developer Tools → Events** in your HA instance.

---

## `v2g_enable_debug_logging`

Temporarily switches the V2G Liberty log level from INFO to DEBUG.
Useful for diagnosing issues without restarting the app.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `duration_hours` | int | 1 | How long to keep debug logging enabled (max 24) |

**Example event data:**
```json
{"duration_hours": 2}
```

Or fire without data for the default of 1 hour.

**Behaviour:**
- All V2G Liberty modules immediately start logging at DEBUG level
- A timer automatically restores INFO level after the specified duration
- If the app restarts while debug is active, the level resets to INFO (no persistent state)
- Firing the event again resets the timer to the new duration

---

## `v2g_run_full_repair`

Triggers a full database repair of the `interval_log` table.
Runs in a background thread (`run_in_executor`) to avoid blocking the app.

**Parameters:** None

**Example:** Fire with empty data `{}`.

**Behaviour:**
- Repairs SoC gaps, out-of-range values, and energy/power inconsistencies
- Results are logged (number of repairs, any validation issues)
- Safe to run while the app is operating — uses its own database connection

---

## `v2g_data_query`

Query aggregated charging data from the local database.
Results are returned via a `v2g_data_query.result` event.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `start` | string | ISO 8601 timestamp (inclusive) |
| `end` | string | ISO 8601 timestamp (exclusive) |
| `granularity` | string | One of: `quarter_hours`, `hours`, `days`, `weeks`, `months`, `years` |

**Example event data:**
```json
{
  "start": "2026-05-01T00:00:00+02:00",
  "end": "2026-05-12T00:00:00+02:00",
  "granularity": "days"
}
```

**Behaviour:**
- Fires `v2g_data_query.result` with the aggregated data or an error message
- Also available as REST endpoint: `GET /api/appdaemon/v2g_data?start=...&end=...&granularity=...`
