# V2G Liberty add-on

The V2G Liberty add-on delivers full automatic and optimised control over bidirectional charging 
of your electric vehicle (EV). Bidirectional charging is also known as Vehicle to Grid (V2G).

*Liberty* in the name refers to:
- V2G&nbsp;Liberty strives for you to be independent of the make/type of charger & car.
 
  <span class="sub-text">The truth now is that only the Wallbox Quasar charger offers (affordable) bidirectional charging.
  As it is equipped with a CHAdeMo connector, so currently only cars with this connector are supported.
  We expect to add new chargers to V2G&nbsp;Liberty soon.</span>
- V2G&nbsp;Liberty strives for you to be independent of power company.
 
  <span class="sub-text">You can choose any contract but V2G is only relevant for contracts with dynamic 
  (e.g. hourly changing) electricity prices.</span>

You can read more about V2G Liberty on [v2g-liberty.eu](https://v2g-liberty.eu/) or [github.com](https://github.com/V2G-liberty/addon-v2g-liberty)

The schedules are optimised to maximise revenues. The optimised schedules
are produced by [FlexMeasures](https://flexmeasures.io/). While the price optimisation already 
decreases emissions significantly, you can also choose to take sustainability a step further and optimise for minimising emissions (and still do quite well on savings on charge costs).


## Preparation

Before installing or activation of V2G Liberty, please make sure:
 - The charging and discharging with the EV and Quasar charger works properly. Test this with the app supplied with the charger.
 - You have a FlexMeasures account
 - You have an electricity contract with dynamic prices (can be added later)
 - You have an online calendar

For details see [readme on GitHub](https://github.com/V2G-liberty/addon-v2g-liberty?tab=readme-ov-file#Preparation).


## Installation 

The installation of this add-on is pretty straightforward and only needs to be done once.
Later updates will install automatically.

As you see this text in the add-on UI you've probably already 
completed steps 1 to 7, if so, skip them.

1. Copy this URL: `https://github.com/V2G-liberty/addon-v2g-liberty.git`
2. In home assistant got to `settings > add-ons`.
3. Hit the big blue button in the bottom right `add-on shop`.
4. Open the menu in the top right with the ⋮-icon and select the option `repostitories`.
5. Paste the URL of step 1 and click `ADD`, wait a little and click `Close`.
6. Scroll down to the bottom of the page, the V2G Liberty add-on should be visible there.
   If not, refresh the page. Click the add-on.
7. The add-on page opens, click the `Install` button to install the add-on. This might take 
   quite a while as several files have to be copied.
8. Consider activating `Watch dog` and `Automatic update`.
9. Click `Start` to get the V2G Liberty add-on going.<br>
   Have a look at the logs (most right tab on top of the page) to see if all went well.
   The last line should say something like:

   `s6-rc: info: service legacy-services successfully started`.


Now the add-on needs to be configured before finishing the installation.


## Configuration

If you've upgraded from an earlier version of V2G Liberty (also with the manual installation) you can normally 
skip the configuration steps.

You'll need to edit some .yaml files, here we use the File Editor add-on, you can use another one if you prefer.

1. Start the File Editor add-on `settings > add-ons > File Editor`. 
2. Open `v2g_liberty_example_configuration.yaml` and paste this text:
   ```yaml
   homeassistant:
     packages:
       v2g_pack: !include packages/v2g_liberty/v2g_liberty_package.yaml

   # Loads default set of integrations. Do not remove.
   default_config:
   ```
    - If there is a section `homeassistant:` already, just add the lines `packages: ... .yaml` in there.
    - If there is a section `default_config:` already, leave it unchanged.

   Don't forget to save your changes.

3. Now restart Home Assistant by going to `settings > system` and in the top 
   right click the top right ⏼ menu and select `Restart Home Assistant`.

4. When the restart finished the *V2G&nbsp;Liberty* menu item in the left menu should be visible, open this by clicking it.
5. The UI shows but more configuration is needed, so go to the settings tab. 
6. Review all sections of the page and complete the requested information as necessary.

## Tips & tricks

Make your V2G life even more enjoyable!
These are "out of the box" super handy Home Assistant features. These settings are optional but highly recommended.


### Add additional users

This lets more users (persons in the household) operate the charger and receive relevant notifications.


### Install the HA companion app on your mobile

You can get it from the official app store of your phone platform.
If you’ve added your HA instance and logged in you can manage the charging via the app and, 
very conveniently, receive notifications about the charging on you mobile.


### Make V2G Liberty your default dashboard

To make the V2G Liberty dashboard your default go to `Settings > Dashboards`. 
Select the V2G Liberty dashboard row and click the link "SET AS DEFAULT IN THIS DEVICE".


#### Happy 🚘 ← ⚡ → 🏡 charging!


<style>
  body {
    max-width: 50em;
    margin: 4em;
  }
  span.sub-text {
     font-size: 90%;
     color: #797979;
  }
</style>


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

## Got questions?

You have several options to get them answered:

- The [Home Assistant Community Add-ons Discord chat server][discord] for add-on
  support and feature requests.
- The [Home Assistant Discord chat server][discord-ha] for general Home
  Assistant discussions and questions.

You could also [open an issue][issue] on GitHub.

## Authors & contributors

This repository is created by Ard Jonker, [Seita B.V.](https://github.com/seitabv) and 
[Ronald Pijnacker](https://github.com/rhpijnacker).


## License

Apache 2.0 License

Copyright (c) 2021 - 2024 Ard Jonker

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.


The V2G Liberty add-on is based upon the AppDaemon add-on by Franck Nijhof:

MIT License

Copyright (c) 2021 - 2024 Franck Nijhof

Permission is hereby granted, free of charge, to any person obtaining a copy of 
this software and associated documentation files (the "Software"), to deal in 
the Software without restriction, including without limitation the rights to use, 
copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the 
Software, and to permit persons to whom the Software is furnished to do so, 
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all 
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, 
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR 
PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE 
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, 
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE 
USE OR OTHER DEALINGS IN THE SOFTWARE.

[addon-badge]: https://my.home-assistant.io/badges/supervisor_addon.svg
[alpine-packages]: https://pkgs.alpinelinux.org/packages
[v2g-liberty]: https://v2g-liberty.eu
[discord-ha]: https://discord.gg/c5DvZ4e
[discord]: https://discord.me/hassioaddons
[issue]: https://github.com/V2G-liberty/addon-v2g-liberty/issues
[python-packages]: https://pypi.org/
[releases]: https://github.com/V2G-liberty/addon-v2g-liberty/releases
[semver]: http://semver.org/spec/v2.0.0.htm
