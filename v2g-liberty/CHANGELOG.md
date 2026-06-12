# What's changed?

## 0.8.1 2026-06-12

### Fixed

- 🪲 BUG: Fix perpetual database is lock + Accept FM HTTP 202 when posting sensor data (#453)
- 🪲 BUG: Fix broken UI (ha-xyz elements) on HA 2026.5+ (#452)
- 🪲 BUG: Reduce excessive FM schedule requests on SoC changes (#446)
- 🪲 BUG: Fix orphaned schedule timers overriding charge mode changes (#444)
- 🪲 BUG: Fix ping card toast not showing after mwc-snackbar to ha-toast migration (#443)

### Added

- 🚀 FEAT: Pass SoC sensor ID in flex-model (#440)
- 🚀 FEAT: grid pv monitoring (#450)

### Changed

- 🛠️ Refactor: Exclude dev-only files (dev_tools, tests, dev config) from the production add-on image (#455, #457)
- 🛠️ Refactor: Quieter add-on startup — copy without per-file logging and report each step (#455)
- 🛠️ Refactor: Migrate to python logging (#448)
- 🛠️ Refactor: make AppDaemon timer-API usage consistently async in main_app (#445)
- 🛠️ Refactor: Increase FM data send frequency from daily to hourly (#441)


#### Removing

- ?

## Complete changelog of all releases

To keep things readable here a separate document is maintained
with [the complete list of all changes for all past releases](changelog_of_all_releases.md).

&nbsp;
