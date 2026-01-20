# Charger Class Structure

This diagram shows the inheritance hierarchy and relationships between charger classes in V2G Liberty.

```mermaid
classDiagram
    direction TB

    %% External dependencies
    class AsyncIOEventEmitter {
        <<pyee.asyncio>>
    }

    class ABC {
        <<abc>>
    }

    %% Base classes
    class UnidirectionalEVSE {
        <<abstract>>
        +max_charge_power_w: int
        +max_discharge_power_w: int
        +initialise_evse(communication_config)*
        +set_active()*
        +set_inactive()*
        +start_charging(power_in_watt)*
        +stop_charging()*
        +get_hardware_power_limit()*
        +get_connected_car()*
        +is_charging()*
        +set_max_charge_power(power_in_watt)
        #_emit_is_car_connected_changed()
    }

    class BidirectionalEVSE {
        <<abstract>>
        +start_charging(power_in_watt)*
        +is_discharging()*
        +set_max_discharge_power(power_in_watt)*
    }

    %% SunSpec abstract base
    class BaseSunSpecEVSE {
        <<abstract>>
        #_get_soc_mce()*
        #_get_default_port()*
        #_get_charger_name()*
        -_handle_scale_factors()
        -_map_sunspec_state()
    }

    %% Concrete implementations
    class WallboxQuasar1Client {
        +DEFAULT_PORT: 502
        +POLL_INTERVAL: 5s
        +kick_off_evse()
        +get_max_power_pre_init()
        -_set_charger_control()
        -_set_charge_power()
        -_get_car_soc()
        -_handle_charger_state_change()
    }

    class EVtecBiDiProClient {
        +DEFAULT_PORT: 5020
        +POLL_INTERVAL: 5s
        +kick_off_evse()
        +get_max_power_pre_init()
        +get_new_ev_details()
        -_set_charge_power()
        -_get_car_soc()
        -_handle_evse_state_change()
    }

    class FermateFE20Client {
        +DEFAULT_PORT: 8502
        #_get_soc_mce()
        #_get_default_port()
        #_get_charger_name()
    }

    %% Support classes
    class V2GmodbusClient {
        +initialise(host, port)
        +read_registers(modbus_registers)
        +write_modbus_register(register, value)
        +adhoc_read_register(address, host, port)
        +force_get_register()
    }

    class MBR {
        <<dataclass>>
        +address: int
        +data_type: str
        +length: int
        +device_id: int
        +decode(registers): Any
        +encode(value): list
    }

    class ModbusConfigEntity {
        <<dataclass>>
        +modbus_register: MBR
        +minimum_value: float
        +maximum_value: float
        +current_value: Any
        +change_handler: str
        +pre_processor: str
        +set_value(new_value, owner): bool
        +is_value_fresh(max_age): bool
    }

    %% Inheritance relationships
    AsyncIOEventEmitter <|-- UnidirectionalEVSE
    ABC <|-- UnidirectionalEVSE
    UnidirectionalEVSE <|-- BidirectionalEVSE
    BidirectionalEVSE <|-- WallboxQuasar1Client
    BidirectionalEVSE <|-- EVtecBiDiProClient
    BidirectionalEVSE <|-- BaseSunSpecEVSE
    BaseSunSpecEVSE <|-- FermateFE20Client

    %% Composition relationships
    WallboxQuasar1Client *-- V2GmodbusClient : uses
    EVtecBiDiProClient *-- V2GmodbusClient : uses
    BaseSunSpecEVSE *-- V2GmodbusClient : uses
    WallboxQuasar1Client *-- MBR : defines
    WallboxQuasar1Client *-- ModbusConfigEntity : defines
    EVtecBiDiProClient *-- MBR : defines
    EVtecBiDiProClient *-- ModbusConfigEntity : defines
    BaseSunSpecEVSE *-- MBR : defines
    BaseSunSpecEVSE *-- ModbusConfigEntity : defines
    ModbusConfigEntity *-- MBR : contains
```

## Summary

| Class | Type | Lines | Purpose |
|-------|------|-------|---------|
| `UnidirectionalEVSE` | Abstract | 156 | Base for charge-only chargers |
| `BidirectionalEVSE` | Abstract | 33 | Base for V2G chargers |
| `BaseSunSpecEVSE` | Abstract | 914 | Base for SunSpec-compliant chargers |
| `WallboxQuasar1Client` | Concrete | 1401 | Wallbox Quasar 1 DC charger |
| `EVtecBiDiProClient` | Concrete | 1129 | EVtec BiDiPro 10 charger |
| `FermateFE20Client` | Concrete | 74 | Fermate FE20 (SunSpec-based) |
| `V2GmodbusClient` | Support | 607 | Modbus TCP communication layer |
| `MBR` | Dataclass | - | Modbus register definition |
| `ModbusConfigEntity` | Dataclass | - | Charger entity with validation |

## Adding a New Charger

1. **SunSpec-compliant charger**: Inherit from `BaseSunSpecEVSE` (simplest - see `FermateFE20Client`)
2. **Custom protocol**: Inherit from `BidirectionalEVSE` or `UnidirectionalEVSE`

---
*Generated: 2026-01-20*
