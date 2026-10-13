# What's changed?

## 0.9.0 2026-10-13

### Fixed

- 🪲 BUG: Every start logged a traceback from the reference price fetch (#519)
- 🪲 BUG: The charger dialog accepted powers outside the charger's limits (#518)
- 🪲 BUG: After a restart, the UI cards (car, grid, solar panel) can not reach backend (#517)
- 🪲 BUG: A charger fault could leave the charger on Stop after recovery (#516)
- 🪲 BUG: Missing admin's phone; no message to user and critical notifications lost (#514)
- 🪲 BUG: The charger's phase was not sent to FlexMeasures, unlike that of solar panels (#513)
- 🪲 BUG: When a schedule ran out without a new one the car could go on (dis)charging unasked (#512)
- 🪲 BUG: A recovered charger could still be reported as unreachable (#511)
- 🪲 BUG: A new optimisation mode (price / emissions) only took effect at the next restart (#510)
- 🪲 BUG: An empty FlexMeasures password or a doubled https:// stopped the app from starting (#508)
- 🪲 BUG: A reservation or SoC change arriving while fetching a schedule was ignored (#508)
- 🪲 BUG: For five minutes after a restart the schedule did not know the calendar reservations (#508)
- 🪲 BUG: Orphaned charging timers overrule later schedules and the Charge/Discharge buttons (#507)
- 🪲 BUG: A refused discharge below the minimum SoC also stopped the boost to that minimum (#507)
- 🪲 BUG: After an add-on update the browser keeps serving the old cards (#506)
- 🪲 BUG: Saving charger settings stopped halfway when a power had to be clamped (#504)
- 🪲 BUG: Settings could be lost when the settings file was damaged (#503)
- 🪲 BUG: The app fails to start on unknow new entity (#500)
- 🪲 BUG: Home Assistant restarts rais false alarms about settings / add-on (#501)
- 🪲 BUG: "Battery at max SoC" notification reports the wrong range after a restart (#469)
- 🪲 BUG: Fix db schema validation (#470)
- 🪲 BUG: Paused app lets the charger charge the car to full on reconnect (#480, #481)
- 🪲 BUG: Guard the max-SoC notification against a non-numeric new SoC (#483)
- 🪲 BUG: Fix ttl-based notification clearing (unpack AppDaemon's kwargs dict) - (#484)
- 🪲 BUG: Fix grid connection save fm gate - (#485)
- 🪲 BUG: FlexMeasures connection wrongly shows "Error" when a single sensor's data is rejected (#486)
- 🪲 BUG: Turning off "use other than default server" still tests against the configured URL (#494)


### Added

- 🚀 FEAT: Residential load per phase (#471)
- 🚀 FEAT: Warn negative grid power (#472)
- 🚀 FEAT: Live reregister grid listeners (#473)
- 🚀 FEAT: Aggregate import/export meter energy from the meter registers to FlexMeasures (#487)
- 🚀 FEAT: Grid connection redesign (#488)
- 🚀 FEAT: Support for the EVtec BiDiPro 10 charger (#482, #496, #497, #498)
- 🚀 FEAT: All car settings in one dialog + unregistered car pauses automatic charging (#478, #502)
- 🚀 FEAT: FM stores the scheduled aggregate consumption and production on the Mains Connection (#495)
- 🚀 FEAT: Recover automatically once an unusable charger is back (#499)

### Changed

- Explain the 'charger phase not set' warning and keep it in sync (#474)
- ⬆️ Bump flexmeasures-client to 0.9.5 (#489)
- ⬆️ Bump flexmeasures-client to 0.9.6 (#508)
- 🛠️ Harden shared Modbus client: retries=0 and 10s timeout - (#490)
- 🛠️ Refactoring: Share the identical methods of the two charger drivers in the base class (#504)

#### Removing

-

## Complete changelog of all releases

To keep things readable here a separate document is maintained
with [the complete list of all changes for all past releases](changelog_of_all_releases.md).


&nbsp;
