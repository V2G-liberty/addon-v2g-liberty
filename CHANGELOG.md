# Changelog

## [Unreleased]

The next release might include:

### Adding

- Hopefully add "self_provided" option again. Needs refactoring.

### Changing

- Use flexmeasures_client python library for all calls to FM

### Removing

- ?




## 0.1.12 - 2024-07-15

### Added

- A function to restart V2G Liberty on the settings view and referenced this in the "how to fix the communication
  fault instructions".


### Fixed

- Improved detection of communication failure with charger (#43)
- Fixed "mistake" whereby the Admin mobile user setting was not used correctly.

### Changed

- Replaced `try - except` for code by `is None` test for code that calls methods in other modules. The caught exception 
  was too wide and caught all (underlying) exceptions also from the other modules. This prevented propper debugging.


### Removed

- None.



## 0.1.11 - 2024-07-03

### Added

- Take multiple calendar items into account for the schedule (#36)
- Show all calendar items in graph
- Notify users of "car still connected during calendar item duration, want to: Keep or Dismiss?"
  - if Dismiss: strikethrough in list of items, remove from graph and ask for new schedule.
  - if Keep: show normal and keep same schedule
- Remove location/timezone from secrets (#16)


### Fixed

- Minor fix: await fm_client close()
- Handle installation of V2G Liberty in combination with other (HACS) resources.
  E.g. lovelace mode set to 'storage' so V2G Liberty can work with other HACS resources
- Minor fixes in fm_get_data module

### Changed

- None


### Removed

- Date-time from feedback on FM TEST button in settings page.



## 0.1.10 - 2024-06-21

### Added

- None


### Fixed

- Make charger-has-crashend-notification not critical when no car is connected (#31)
  This is hard to test as the real hardware reacts differently than the charger mock, it has been running on the test 
  environment for a couple of days. Not so much on real hardware.


### Changed

- None


### Removed

- None



## 0.1.9 - 2024-06-13

### Added

- None


### Fixed

- None.


### Changed

- Getting sensor_id's from FM instead of through UI (#34)
- Introduced flexmeasures_client python library for stability and reduction of code complexity.
- Minor improvements in documentation.
- Made setting options in a select more robust


### Removed

- Temporary removed the option "self_provided" for energy prices as users for this functionality use another repository.



## 0.1.8 - 2024-06-11

### Added

- Added `try - except` across all code for calling other modules to increase stability.


### Fixed

- Bug: Local calendar only works if one calendar is present (#32).


### Changed

- None


### Removed

- None



## 0.1.7 - 2024-06-07

### Added

- None


### Fixed

- Bug: Integration calendar not populated in settings screen (#27).
  Temporary fix, removed input select. Only one local calendar is supported now.

### Changed

- None

### Removed

- None



## 0.1.6 - 2024-06-06

### Added

- None


### Fixed

- Bug: Reset to factory defaults (#28).  

### Changed

- None

### Removed

- (Temporarily) removed ping to flexmeasures as it seems to overload the client is some cases. Needs research



## 0.1.5 - 2024-06-06

### Added

- None


### Fixed

- Bug: Settings get lost (#25)
- Bug: flexmeasures ping increased timer was not canceled correctly (no await).  

### Changed

- Set production mode to true in appdaemon.yaml

### Removed

- None



## 0.1.4 - 2024-06-03

### Added

- Added nl_greenchoice to the list of possible energy providers.


### Fixed

- Bug fix: SoC above 100% (#21) due to wrong roundtrip efficiency (85 instead of 0.85)
- Bug: Energy provider and optimisation mode not changing in UI when setting have changed (a restart was needed)
- Bug: An empty settings file lead to errors and crashed V2G Liberty. Made more robust.
- Bug: If no devices are registered for notifications, V2G Liberty stopped initialising.

### Changed

- Documentation on URL changed flexmeasures.seita.nl to seita.energy

### Removed

- None



## 0.1.3 - 2024-06-03

### Added

- None


### Fixed

- Bug fix: SoC does not get updated in UI (#19)

### Changed

- None

### Removed

- None



## 0.1.2 - 2024-05-31

### Added

- Instructions for editing `secrets.yaml` to the docs.md file.
- Corrected versionnumber in UI.


### Fixed

- None 

### Changed

- None

### Removed

- None



## 0.1.1 - 2024-05-28

### Added

- None


### Fixed

- Fixed issue 14: Notification config not loading correctly.
- Fixed a minor bug where writing empty initial settings file lead to errors.
- Fixed a minor bug in __handle_charger_state_change for case where old state is None.
- 

### Changed

- None

### Removed

- 



## 0.1.0 - 2024-05-28

### Added

- None


### Fixed

- Setting charge mode to 'Off' not stopping charging
- Key-error on failed auth request in get_fm_data
- Improved handling of charger crashes. Restored high-priority message.
- Handle rare case where system would not restart after re-connect


### Changed

- Move configuration from secrets.yaml to add-on configuration (#5)
- Bumped docker ghcr.io/hassio-addons/base to15.0.8
- Bumped docker python3-dev and python3 to 3.11.9-r0
- Legal notice of Wallbox moved from wallbox_registers.yaml to modbus_evse_client.py

### Removed

- 



## 0.0.2 - 2024-04-05

### Added

- None


### Fixed

- #6-bug-notification-for-no-schedule-does-not-get-send


### Changed

- None


### Removed

- Nothing



## 0.0.1 - 2024-04-04

This is the initial version


### Added

- The add-on is based on / wraps https://github.com/V2G-liberty/HA-manual-install


### Fixed

- None


### Changed

- Everything ;-)


### Removed

- Nothing


## Format

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


