# Changelog of all releases

A separate [changelog for only the current release](CHANGELOG.md) is available to keep things readable.
That file also contains possible changes that the next release might include.

## 0.3.3 2024-10-17

### Fixed

-  🪲 BUG: admin_mobile name select is changing several times per second #124 



## 0.3.2 2024-10-16

### Added

- FEATURE: Strip leading and trailing whitespace from user input (#96)
- Documentation for getting remote access / remote support (#104)

### Fixed

- 🪲 BUG: Timeout on __force_get_register on modbus_evse_client not handled (#98)
- 🪲 BUG: Changelog not available from add-on screen in HA (un-numbered)
- 🩹 PATCH: Delay start time of getting prices (#117)

### Changed

- 🛠️ FR: Charge mode "Off" is renamed to "Pause" and now keeps polling the charger (#100)
- 🛠️ FR: Make getting prices more robust (#115)
- TECH: Use get_plugin_config() instead of template sensor TZ in package.yaml (#103)
- TECH: Add "initialised" attribute to settings entities (=preparation for UI improvements) #113 
- REFACTOR: Restructure code in set_next_action (#108)
- Bumped flexmeasures_client library to version 0.2.4 (#110)

### Removed

- None



## 0.3.1 2024-09-25

### Added

- None


### Fixed

- 🪲 BUG: Price and emission data for tomorrow does not get loaded. #92

### Changed

- None

### Removed

- None



## 0.3.0 2024-09-20

### Added

- An expected SoC line (dashed) in the chart for "Max charge now".

### Fixed

🪲 Bugfixes:
- 'Reduce max(dis-) charge power' setting not preserved (#71)
- Too frequent fluctuating (scheduled) charge power (#74)
- Sometimes the SoC does not get renewed after car gets connected (#77)
- Notification "retry getting EPEX prices" not sent when call completly fails (#79)


### Changed

- Use flexmeasures_client python library for all calls to FM
  This makes getting schedules and other data (much) faster.
- Made python dependencies in Dockerfile versionless.
- Renamed set_fm_data to data_monitor
  This reflects its function better now that all data is sent through the fm_client.

### Removed

- None



## 0.2.0 2024-08-28

### Added

- Support for Octopus Energy Agile contracts (GB).
- Make UI "on hold" when car is disconnected (#60)

### Fixed

- BUG: in get_fm_data the get_app call to v2g_liberty module is not awaited (#65)
- BUG: sending data to FM is broken (#63)

### Changed

- ⬆️ Update ghcr.io/hassio-addons/base Docker tag to v15.0.9

### Removed

- None


## 0.1.14 2024-08-21

### Added

- None

### Fixed

- Bug #57: Calendar can now handle all-day events.
- Bug (Unnumbered): Better handle empty calendar
- Bug (unnumbered): More robust cancel_listener


### Changed

- None

### Removed

- None


## 0.1.13 2024-08-19

### Added

- Re-introduced the option for self-provided price data for Amber Energy. This is based on the (deprecated) 'HA Manual install' repository (Issue #51).


### Fixed

- Bug (not numbered) that prevented the stats from showing properly in th UI if the charged energy is 0 (first day at least). Division by zero.


### Changed

- Yet another try to improve the stability of the modbus connection.
  It seems to have good results, but we'll have to wait and see how it preforms. "Testing" this is really difficult / time-consuming. It has run for several days in 'production' 🤞 (Issue #48).<br/>
  This action also strongly simplified the modbus related code:
  - Removed mechanism to prevent 'parallel calls' to charger
  - Removed closing the connection after every call
- Upgraded (the L&F of) the chart:
  - The chart is now larger (taller).
  - The zoom/pan function has been replaced by a unit of measure, '¢ent/kWh.'
  - Prices now align with the grid lines, which are less "dotty."
  - A separate line for the 'Sell price' (also known as production price, feed-in price, or 'teruglever prijs' in Dutch) has been added. This is in preparation for the abolition of the Tax Arrangement (Salderingsregeling) in the Dutch market, where currently, the Buy and Sell prices are always the same.
  - The price lines are now purple/pink for a fresher look.
  - The line-color is dynamic: the Sell line turns red when negative, and the Buy line turns green.
  - A legend has been added.
  - One can now toggle the visibility of individual lines in the chart by clicking on the legend. For example, the Emissions line is hidden. The Sell price and Emissions are hidden by default.

- Bumped ApexCharts-Card to version 2.1.2.
- Improve charger-state storage and handling (Issue #50)


### Removed

- None.


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

