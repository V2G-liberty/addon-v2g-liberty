# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

This section shows what the next release might include.

### Added

- ?

### Changed

- ?

### Removed

- ?
 

## 0.1.0 - 2024-05-07

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
