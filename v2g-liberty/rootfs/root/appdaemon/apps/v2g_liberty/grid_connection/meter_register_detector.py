"""Auto-detection of cumulative energy meter registers (import / export).

Scans HA entities for the smart meter's cumulative energy registers — the
DSMR tariff registers (OBIS 1.8.x import / 2.8.x export) or a single total
register (1.8.0 / 2.8.0) — and suggests which are import (consumption) and
which are export (production). These feed the aggregate consumption /
production sensors on the Mains Connection.

Language-robust by design:
- Filters on ``device_class`` / ``state_class`` / ``unit_of_measurement``.
  These are internal HA values and are never translated.
- Classifies import vs. export on the ``entity_id`` pattern. HA derives the
  entity_id from the integration's English name, so it is stable across UI
  languages (e.g. native DSMR always yields ``*_energy_consumption_tarif_1``,
  even on a non-English install). ``friendly_name`` keywords are only a
  last-resort hint, since those are translated.

The suggestion is a proposal only; the user confirms and can reassign or
pick entities manually in the grid-connection dialog (covers HomeWizard P1,
custom integrations and renamed entities).
"""

_ENERGY_UNITS = ("Wh", "kWh", "MWh")

# entity_id substrings, English-derived and therefore language-independent.
# Native HA DSMR spells "tarif" (one f). The ``electricity_used`` /
# ``electricity_delivered`` forms match the translation_key spelling in case
# an install kept entity_ids close to the key.
_CONSUMPTION_PATTERNS = (
    "energy_consumption_tarif",  # DSMR 1.8.1-4
    "energy_consumption_total",  # DSMR 1.8.0
    "electricity_used_tariff",
    "electricity_imported_total",
)
_PRODUCTION_PATTERNS = (
    "energy_production_tarif",  # DSMR 2.8.1-4
    "energy_production_total",  # DSMR 2.8.0
    "electricity_delivered_tariff",
    "electricity_exported_total",
)

# Fallback keywords (entity_id or friendly_name). Less reliable — translated
# friendly_names vary — so only used when the patterns above do not match.
_CONSUMPTION_KEYWORDS = (
    "consumption",
    "consumed",
    "import",
    "used",
    "afname",
    "verbruik",
)
_PRODUCTION_KEYWORDS = (
    "production",
    "produced",
    "export",
    "delivered",
    "returned",
    "teruglevering",
    "opwek",
)


def detect_meter_registers(states: dict) -> dict:
    """Scan HA entity states and suggest cumulative energy meter registers.

    Args:
        states: dict of entity_id -> state object, supporting dict-like or
            attribute access to ``.attributes`` (device_class, state_class,
            unit_of_measurement, friendly_name).

    Returns:
        dict with keys:
            consumption_registers: list[str] (import register entity IDs)
            production_registers: list[str] (export register entity IDs)
        Each list holds either a single total register (preferred when
        present) or all matching tariff registers (to be summed downstream).
        Empty lists mean nothing could be classified automatically.
    """
    registers = _find_energy_registers(states)
    consumption, production = _classify(registers, states)
    # These are cumulative ENERGY meter registers (kWh, total_increasing;
    # OBIS 1.8.x import / 2.8.x export) — distinct from the per-phase POWER
    # sensors detected by grid_entity_detector (consumption/production_entities).
    return {
        "consumption_registers": _prefer_total(consumption),
        "production_registers": _prefer_total(production),
    }


def _attrs(state_obj) -> dict:
    return (
        state_obj.get("attributes", {})
        if isinstance(state_obj, dict)
        else getattr(state_obj, "attributes", {})
    ) or {}


def _find_energy_registers(states: dict) -> list[str]:
    """Return entity IDs of cumulative energy sensors (the meter registers).

    A register is a ``sensor.*`` with ``device_class == "energy"``,
    ``state_class == "total_increasing"`` and an energy unit. The
    total_increasing class is what separates a cumulative meter reading from
    a per-interval energy sensor.
    """
    result = []
    for entity_id, state_obj in states.items():
        if not entity_id.startswith("sensor."):
            continue
        attrs = _attrs(state_obj)
        if attrs.get("device_class") != "energy":
            continue
        if attrs.get("state_class") != "total_increasing":
            continue
        if attrs.get("unit_of_measurement") not in _ENERGY_UNITS:
            continue
        result.append(entity_id)
    return result


def _classify(entity_ids: list[str], states: dict) -> tuple[list[str], list[str]]:
    """Split registers into (consumption/import, production/export).

    Two tiers, strict first:
    1. Language-independent DSMR entity_id patterns.
    2. Keyword fallback (entity_id or translated friendly_name), used **per
       side only when tier 1 found nothing for that side**. This keeps a
       clean DSMR result free of PV/EV energy meters that merely contain
       "production"/"consumption" — those only surface on non-DSMR setups,
       where the user confirms anyway.

    Registers that match neither are left out (user assigns manually).
    """
    cons_pat = [
        e for e in entity_ids if any(p in e.lower() for p in _CONSUMPTION_PATTERNS)
    ]
    prod_pat = [
        e for e in entity_ids if any(p in e.lower() for p in _PRODUCTION_PATTERNS)
    ]

    matched = set(cons_pat) | set(prod_pat)
    cons_kw: list[str] = []
    prod_kw: list[str] = []
    for entity_id in entity_ids:
        if entity_id in matched:
            continue
        text = (
            entity_id.lower()
            + " "
            + str(_attrs(states.get(entity_id)).get("friendly_name") or "").lower()
        )
        # Production keywords first: "delivered"/"returned"/"export" are
        # unambiguous, whereas a consumption keyword rarely appears on export.
        if any(k in text for k in _PRODUCTION_KEYWORDS):
            prod_kw.append(entity_id)
        elif any(k in text for k in _CONSUMPTION_KEYWORDS):
            cons_kw.append(entity_id)

    consumption = cons_pat if cons_pat else cons_kw
    production = prod_pat if prod_pat else prod_kw
    return consumption, production


def _prefer_total(entity_ids: list[str]) -> list[str]:
    """Prefer a single total register (1.8.0 / 2.8.0) when present.

    A total register already sums the tariffs, so using it avoids summing and
    cannot miss a tariff. When no total is present, return all (tariff)
    registers so they can be summed downstream.
    """
    totals = [e for e in entity_ids if "total" in e.lower()]
    return sorted(totals) if totals else sorted(entity_ids)
