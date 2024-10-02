# V2G&nbsp;Liberty

##### Full automatic and optimised control over bidirectional charging of your electric vehicle, saving the average user over *€ 1000* on charging cost in 2023.

Use your electric vehicle (EV) as you would normally do, drive where you want go.<br>
In addition, the EV's battery is used to:
- store cheap & green (own solar) electricity
- when prices are high, deliver this energy to the house or the grid, alas the name: Vehicle-to-Grid (V2G).


*Liberty* in the name refers to:
- V2G&nbsp;Liberty strives for you to be independent of the make/type of charger & car.
  <span class="sub-text">Currently only the Wallbox Quasar charger offers affordable bidirectional charging
  in combination with Nissan Leaf / Evalia cars.</span>
- V2G&nbsp;Liberty strives for you to be independent of power company.
 
  <span class="sub-text">You can choose any contract but V2G is only relevant for contracts with dynamic 
  (e.g. hourly changing) electricity prices.<br/>
  At the time of writing, October 2024 we are testing with new CCS2 chargers and compatible cars. 
  We hope to announce new compatible chargers and cars soon.</span>
 
V2G&nbsp;Liberty is a practical local app that works in the browser and on your phone.

![The V2G&nbsp;Liberty Dashboard](https://v2g-liberty.eu/wp-content/uploads/2024/08/ui_v0_1_13.png)

The schedules are optimised to maximise revenues ór minimising emissions. The optimised schedules
are produced by [FlexMeasures](https://flexmeasures.io/), our smart backend by Seita. While the price optimisation already 
decreases emissions significantly, you can also choose to take sustainability a step further and optimise for minimising emissions (and still do quite well on savings on charge costs).

Most Dutch energy suppliers are listed and all 
European energy prices (EPEX) are available for optimisation. For none European markets (e.g. UK, Australia) 
there is an option to (automatically) upload price data to FlexMeasures. Amber Electric is preconfigured and can be selected
from a settings list.

Read more about V2G&nbsp;Liberty on [v2g-liberty.eu](https://v2g-liberty.eu/) or [github.com](https://github.com/V2G-liberty/addon-v2g-liberty).

Read more about FlexMeasures on [flexMeasures.io](https://flexmeasures.io/), [seita.nl](https://seita.nl) or [github.com](https://github.com/FlexMeasures/flexmeasures).


## Prerequisites
 
As said, we've fine-tuned our software over the last 4 years for the (only) bidirectional [Wallbox Quasar 1 charger](https://wallbox.com/en_uk/quasar-dc-charger).

Compatible cars that can do V2G with this charger are the [Nissan Leaf](https://ev-database.org/car/1657/Nissan-Leaf-eplus) (also earlier models) and [Nissan Evalia](https://ev-database.org/car/1117/Nissan-e-NV200-Evalia).


## Preparation

### Quasar charger

Before installing or activation of V2G Liberty, please make sure that charging and discharging with the EV and Quasar charger works properly.
Test this with the app supplied with the charger.


### FlexMeasures

You can run your own instance of FlexMeasures, but you can also make use of an instance run by V2G Liberty.
If you prefer this option, please [contact us](https://v2g-liberty.eu).


### An electricity contract with dynamic prices

As said, the software optimizes dynamic prices, so a contract of this type is the best option.
There is no rush, though, you can try out V2G Liberty first and later on get the dynamic contract.

In the Netherlands, almost all suppliers, such as Eneco and Vattenfall, offer dynamic contracts. Many newer, smaller companies, like Greenchoice, ANWB Energy, Tibber, Energy Zero, and Next Energy, also provide this type of agile pricing. Most of these can be selected from a pre-configured list in the V2G Liberty settings. However, any EPEX-based (so anywhere in Europe) contract is supported, as you can adjust the region, VAT and markup separately. V2G Liberty also fully supports agile contracts from Octopus Energy (Great Britain) and Amber Electric (Australia).

A dynamic contract changes the way your electricity is priced and billed, so it is wise to find information and make sure you really understand what this means for your situation before making this change.


### Get an online calendar

For the situations where you would like the car to be fully charged, e.g. for a longer trip, V2G Liberty optimizes on a 
dedicated online calendar.This is mandatory, without it V2G Liberty cannot work.

It is of course most useful if the calendar is integrated with your normal calendar and if you can easily edit the calendar items on your smartphone (outside HA / V2G Liberty).
Options are, for example:
- A CalDav compatible calendar. E.g. NextCloud or OwnCloud if you’d like an open-source solution
- iCloud calendar<br>
  This can be reached through CalDav. See the instructions on [tasks.org](https://tasks.org/docs/caldav_icloud.html{:target="_blank"}).
- Google calendar<br>
  This -from early 2024- should also be reachable via CalDav but has not been successfully tested yet with V2G Liberty. If you've got experience with this, please let us know! See [developers.google.com](https://developers.google.com/calendar/caldav/v2/guide).<br>
  The Google calendar is confirmed to work with the HA Google Calendar integration in Home Assistant (not to be confused with Google Assistant).
- Office 365. Via non-official [O365-HomeAssistant integration](https://github.com/RogerSelwyn/O365-HomeAssistant).
- ICS Calendar (or iCalendar) integration, also non-official. It can be found on HACS.

We recommend a separate calendar for your car reservations. The result must be that in Home Assistant only the events meant for the car are present.
Preferably name the calendar something like `car_reservation` so you can easily find it later.


## Installation

The installation of this add-on is pretty straightforward.

1. Copy this URL: `https://github.com/V2G-liberty/addon-v2g-liberty.git`
2. In home assistant got to `settings > add-ons`.
3. Hit the big blue button in the bottom right `add-on shop`.
4. Open the menu in the top right with the ⋮-icon and select the option `repositories`.
5. Paste the URL of step 1 and click `ADD`, wait a little and click `Close`.
6. Scroll down to the bottom of the page, the V2G&nbsp;Liberty add-on should be visible there.
   If not, refresh the page. Click the add-on.
7. The add-on page opens, click the `Install` button to install the add-on. This might take 
   quite a while as several some files.
8. Consider activating `Watch dog` and `Automatic update`.

Further steps in the proces of installation and configuration are explained in the documentation tab of the add-on.


## Configuration

Charging is fully automatic an optimised, so you can just connect your EV and forget about it.
You can tweak the charging process by setting min- and max-limits, min- and max-charge power etc.


## Updates

After an update it is usually necessary to restart 'every thing':

Restart *V2G&nbsp;Liberty* `settings -> Add-ons -> V2G Liberty -> Restart`.


#### Happy 🚘 ← ⚡ → 🏡 charging!


## Need help / got questions?

You have several options to get help or your questions answered:

- Get [remote (live) support from the V2G Liberty team](docs/remote_support.md).
- The [Home Assistant Community Add-ons Discord chat server][discord] for add-on
  support and feature requests.
- The [Home Assistant Discord chat server][discord-ha] for general Home
  Assistant discussions and questions.

You could also [open an issue][issue] on GitHub.


## Changelog & Releases

This repository keeps a change log using [GitHub's releases][releases]
functionality. In Home Assistant a notification is shown when a new version is available.
We advise you to keep the software up to date.

Releases are based on [Semantic Versioning][semver], and use the format
of `MAJOR.MINOR.PATCH`. In a nutshell, the version will be incremented
based on the following:

- `MAJOR`: Incompatible or major changes.
- `MINOR`: Backwards-compatible new features and enhancements.
- `PATCH`: Backwards-compatible bugfixes and package updates.


## Authors & contributors

The original setup of this repository is by Ard Jonker, [Seita B.V.](https://github.com/seitabv) and 
[Ronald Pijnacker](https://github.com/rhpijnacker).


## License

Apache 2.0

Copyright (c) 2021 - 2024 Ard Jonker

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.


[addon-badge]: https://my.home-assistant.io/badges/supervisor_addon.svg
[alpine-packages]: https://pkgs.alpinelinux.org/packages
[v2g-liberty]: https://v2g-liberty.eu
[discord-ha]: https://discord.gg/c5DvZ4e
[discord]: https://discord.me/hassioaddons
[issue]: https://github.com/V2G-liberty/addon-v2g-liberty/issues
[python-packages]: https://pypi.org/
[releases]: https://github.com/V2G-liberty/addon-v2g-liberty/releases
[semver]: http://semver.org/spec/v2.0.0.htm


<!--
<style>
  body {
    max-width: 50em;
    margin: 4em;
  }
  .sub-text {
     font-size: 90%;
     color: #797979;
  }
</style>
-->