# What's changed?

## 0.6.0 2025-04-04

### Fixed

- 🪲 BUG: Logging version_number to FM fails (#293)
- 🪲 BUG: Load balancer produces errors in log (#290)
- 🪲 MINOR BUGS (#288)
- 🪲 BUG: The max-charge power setting of V2G Liberty is overruled by FM asset setting (#279)
- 🪲 BUG: Check for max power incorrect (#276)
- 🪲 BUG: Quickly switching between charge mode automatic and other results in schedule being followed (#260)
- 🪲 BUG: When connecting during a calendar reservation period, the "target cannot be reached" process kicks in while it should not (#261)
- 🪲 BUG: fluctuating charging around min-soc (#259)

### Added

### Changed

- 🛠️ Refactoring: Make logging of DataMonitor module less verbose (#295)
- 🛠️ Refactoring: Use FlexMeasures relaxation (instead of V2G Liberty version) (#264)
- 🛠️ Refactoring: Reduce number of pylint problems. (#281)
- 🛠️ Refactoring: Use event-bus instead of HA entities (#265)
- ⬆️ BUMP base-x version to 3.0.11 (#274)
- 🛠️ Refactoring: Separate notification functionality from V2G Liberty module (#266)

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
