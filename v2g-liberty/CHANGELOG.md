# What's changed?

## 0.4.3 2024-12-??

### Fixed

- 🪲 BUG: During Max charge the power sometimes is set to 0 (#152)
- 🪲 BUG: Local Home Assistant calendar only triggers changed event on first upcoming calendar item (#167)
- 🪲 BUG: Alert for negative prices not working correctly (#169)
- 🪲 BUG: Detection of charger crash not 100% accurate (#179)<br/>
  Includes Bump of pyModbus library to 3.8.0
- 🪲 BUG: BUG: Cost data blinking at startup (#181)

### Added

- Feature Request: Notify user when at the end of a calendar item the car is not connected (#158)
- Feature Request: Add history to V2G Liberty entities (#176)
- Feature request: Add "unknown" state to real_charge_power and car_state_of_charge sensors (#170)
- Feature Request: Add 'Alive since' to UI (#182)

### Changed

- 🛠️ REFACTOR: Use sections precision mode for card sizes in UI for a.o. `Automatic`, `Charge now` and `Pause` buttons (#147)
- ⬆️ Bump caldav library to 1.4.0 (#180)

### [Unreleased]

The next release might include:

#### Adding

- Support for uni-directional charging

#### Changing

- New frontend for the settings page

#### Removing

- ?

## Complete changelog of all releases

To keep things readable here a separate document is maintained
with [the complete list of all changes for all past releases](changelog_of_all_releases.md).

&nbsp;
