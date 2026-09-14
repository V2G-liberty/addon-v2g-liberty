# What's changed?

## 0.8.3 2026-09-??

### Fixed

- 🪲 BUG: "Battery at max SoC" notification reports the wrong range after a restart (#469)
- 🪲 BUG: Fix db schema validation (#470)
- 🪲 BUG: Paused app lets the charger charge the car to full on reconnect (#480, #481)
- 🪲 BUG: Guard the max-SoC notification against a non-numeric new SoC (#483)
- 🪲 BUG: Fix ttl-based notification clearing (unpack AppDaemon's kwargs dict) - (#484)
- 🪲 BUG: Fix grid connection save fm gate - (#485)
- 🪲 BUG: FlexMeasures connection wrongly shows "Error" when a single sensor's data is rejected (#486)
- 🪲 BUG: Turning off "use other than default server" still tests against the configured FlexMeasures URL (#494)


### Added

- 🚀 FEAT: Residential load per phase (#471)
- 🚀 FEAT: Warn negative grid power (#472)
- 🚀 FEAT: Live reregister grid listeners (#473)
- 🚀 FEAT: Aggregate import/export meter energy from the meter registers to FlexMeasures (#487)
- 🚀 FEAT: Grid connection redesign (#488)
- 🚀 FEAT: Support for the EVtec BiDiPro 10 charger: charger type selection in the settings, a driver on the hardware-tested Modbus contract, and the dev emulator to test it without hardware (#359)

### Changed

- Explain the 'charger phase not set' warning and keep it in sync (#474)
- ⬆️ Bump flexmeasures-client to 0.9.5 (#489)
- 🛠️ Harden shared Modbus client: retries=0 and 10s timeout - (#490)
- 🛠️ Refactor: charger driver factory and `charger_type` setting; existing installations are migrated to `wallbox-quasar-1` automatically (#359)

#### Removing

-

## Complete changelog of all releases

To keep things readable here a separate document is maintained
with [the complete list of all changes for all past releases](changelog_of_all_releases.md).


&nbsp;
