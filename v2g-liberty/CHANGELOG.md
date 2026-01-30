# What's changed?

## 0.7.3 2026-01-30

### Fixed

- 🪲 BUG: Chart shows SoC prediction line even if there is no schedule (#389)
- 🪲 BUG: Domain exception during Notifier initialisation causing startup failures (#385)
- 🪲 BUG: Monitoring for error state failed to notify (#369)
- 🪲 BUG: Calculation of the expected SoC incorrect (#374)

### Added

- 🚀 FEAT: Be able to use hostname instead of only IP-address for charger host in UI (#380)
- 🚀 PR: Show price forecasts in chart as dotted line (#392)

### Changed

- Bumped pymodbus library to 3.11.4 (#373)
- 🚀 FEAT: loadbalancer instructions for entities inline HA 2026 requierments (#387)

&nbsp;

---

### [Unreleased]

The next release might include:

#### Adding

- Support for uni-directional charging
- Support for multiple chargers/cars

#### Changing

- ?

#### Removing

- ?

## Complete changelog of all releases

To keep things readable here a separate document is maintained
with [the complete list of all changes for all past releases](changelog_of_all_releases.md).

&nbsp;
