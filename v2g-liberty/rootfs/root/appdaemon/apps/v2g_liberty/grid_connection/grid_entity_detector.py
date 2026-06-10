"""Auto-detection of grid connection settings from HA entity names.

Scans available HA sensor entities to detect:
- Phase count (1 or 3) by finding L1/L2/L3 triplets in power sensor names
- Consumption and production entity suggestions by matching triplet pairs
- Fuse threshold (capacity per phase) from DSMR fuse entities
"""

import re


def detect_grid_entities(states: dict) -> dict:
    """Scan HA entity states and suggest grid connection settings.

    Args:
        states: dict of entity_id → state object (with .attributes dict).
               Each state object must support dict-like access to
               .attributes["device_class"], .attributes["unit_of_measurement"],
               and .attributes["friendly_name"].

    Returns:
        dict with keys:
            phases: int | None (1 or 3, or None if unclear)
            capacity_per_phase: int | None (from fuse threshold)
            consumption_entities: list[str] (suggested entity IDs, may be empty)
            production_entities: list[str] (suggested entity IDs, may be empty)
    """
    power_sensors = _find_power_sensors(states)
    triplets = _find_triplets(power_sensors)
    consumption, production = _match_consumption_production(triplets)
    capacity = _find_fuse_threshold(states)

    if consumption and len(consumption) == 3:
        phases = 3
    elif consumption and len(consumption) == 1:
        phases = 1
    elif triplets:
        # Triplets found but couldn't match consumption/production
        phases = 3
    else:
        phases = None

    return {
        "phases": phases,
        "capacity_per_phase": capacity,
        "consumption_entities": consumption,
        "production_entities": production,
    }


def _find_power_sensors(states: dict) -> list[str]:
    """Return entity IDs of all sensor.* entities with device_class power."""
    result = []
    for entity_id, state_obj in states.items():
        if not entity_id.startswith("sensor."):
            continue
        attrs = (
            state_obj.get("attributes", {})
            if isinstance(state_obj, dict)
            else getattr(state_obj, "attributes", {})
        )
        device_class = attrs.get("device_class", "")
        unit = attrs.get("unit_of_measurement", "")
        if device_class == "power" or unit in ("W", "kW", "MW"):
            result.append(entity_id)
    return result


def _find_triplets(entity_ids: list[str]) -> list[tuple[str, str, str]]:
    """Find groups of 3 entities that share a naming pattern with 1/2/3.

    Looks for entities where replacing a digit 1, 2, or 3 yields the same
    pattern, and all three variants exist. The digit must be preceded or
    followed by at least one character (not both empty).

    Returns list of (entity_1, entity_2, entity_3) tuples, sorted by entity name.
    """
    # Build a map: pattern → {1: entity, 2: entity, 3: entity}
    pattern_map: dict[str, dict[int, str]] = {}

    for entity_id in entity_ids:
        name = entity_id.lower()
        # Find all positions where 1, 2, or 3 appears
        for match in re.finditer(r"[123]", name):
            digit = int(match.group())
            pos = match.start()
            prefix = name[:pos]
            suffix = name[pos + 1 :]
            # At least one of prefix/suffix must be non-empty after "sensor."
            if prefix == "sensor." and suffix == "":
                continue
            pattern_key = f"{prefix}{{n}}{suffix}"
            if pattern_key not in pattern_map:
                pattern_map[pattern_key] = {}
            # Only keep the first entity found for each digit in this pattern
            if digit not in pattern_map[pattern_key]:
                pattern_map[pattern_key][digit] = entity_id

    # Filter to patterns that have all three digits
    triplets = []
    for pattern_key, digits in pattern_map.items():
        if len(digits) == 3 and all(d in digits for d in (1, 2, 3)):
            triplets.append((digits[1], digits[2], digits[3]))

    return sorted(triplets)


def _match_consumption_production(
    triplets: list[tuple[str, str, str]],
) -> tuple[list[str], list[str]]:
    """Try to identify which triplets are consumption and which are production.

    Heuristic: look for keywords in the entity names.
    Returns (consumption_entities, production_entities) — each a list of 3
    entity IDs, or empty lists if no match found.
    """
    if not triplets:
        return [], []

    consumption_keywords = ["consumption", "consumed", "import", "afname", "verbruik"]
    production_keywords = [
        "production",
        "produced",
        "export",
        "return",
        "teruglevering",
        "opwek",
    ]

    consumption_triplet = None
    production_triplet = None

    for triplet in triplets:
        name = triplet[0].lower()
        if any(kw in name for kw in consumption_keywords):
            consumption_triplet = triplet
        elif any(kw in name for kw in production_keywords):
            production_triplet = triplet

    if consumption_triplet:
        return list(consumption_triplet), list(production_triplet or [])
    if production_triplet:
        return [], list(production_triplet)

    # No keywords matched — if exactly 2 triplets, return them in order
    # (user can swap them in the UI)
    if len(triplets) == 2:
        return list(triplets[0]), list(triplets[1])

    # If exactly 1 triplet, return it as consumption (most common case)
    if len(triplets) == 1:
        return list(triplets[0]), []

    return [], []


def _find_fuse_threshold(states: dict) -> int | None:
    """Look for a DSMR fuse threshold entity and return its value in ampere.

    Searches for entities matching typical DSMR fuse threshold naming:
    - Contains "fuse" and/or "threshold"
    - Has unit_of_measurement "A"
    """
    for entity_id, state_obj in states.items():
        if not entity_id.startswith("sensor."):
            continue
        name = entity_id.lower()
        attrs = (
            state_obj.get("attributes", {})
            if isinstance(state_obj, dict)
            else getattr(state_obj, "attributes", {})
        )
        friendly_name = (attrs.get("friendly_name") or "").lower()
        unit = attrs.get("unit_of_measurement", "")

        if unit != "A":
            continue
        if "fuse" in name or "fuse" in friendly_name:
            state_val = (
                state_obj.get("state")
                if isinstance(state_obj, dict)
                else getattr(state_obj, "state", None)
            )
            try:
                val = int(float(state_val))
                if 6 <= val <= 80:
                    return val
            except (TypeError, ValueError):
                continue

    return None
