# What's changed?

## 0.8.3 2026-08-??

### Fixed

- 🪲 BUG: "Battery at max SoC" notification reports the wrong range after a restart (#469)
- 🪲 BUG: Fix db schema validation (#470)
- 🪲 BUG: Paused app lets the charger charge the car to full on reconnect (#480, #481)
- 🪲 BUG: Guard the max-SoC notification against a non-numeric new SoC (#483)
- 🪲 BUG: Fix ttl-based notification clearing (unpack AppDaemon's kwargs dict) - (#484)
- 🪲 BUG: Fix grid connection save fm gate - (#485)


### Added

- 🚀 FEAT: Residential load per phase (#471)
- 🚀 FEAT: Warn negative grid power (#472)
- 🚀 FEAT: Live reregister grid listeners (#473)

### Changed

- Explain the 'charger phase not set' warning and keep it in sync (#474)

#### Removing

-

## Complete changelog of all releases

To keep things readable here a separate document is maintained
with [the complete list of all changes for all past releases](changelog_of_all_releases.md).

&nbsp;
