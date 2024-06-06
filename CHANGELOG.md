# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased]

This section shows what the next release might include.

### Adding

- ?

### Changing

- ?

### Removing

- ?



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
