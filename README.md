# Home Assistant Community Add-on: V2G Liberty

[V2G Liberty][v2g-liberty] Automated and optimised vehicle-to-grid charging

This add-on lets you add full automatic and optimised control over bidirectional charging of your electric
vehicle (EV), also known as Vehicle to Grid (V2G). It is a practical app based on home assistant frontend that works in
the browser and on your phone.

The schedules are optimised to maximise revenues ór minimising emissions. The optimised schedules are produced by
[FlexMeasures](https://flexmeasures.io/).

You can read more about V2G Liberty on [v2g-liberty.eu](https://v2g-liberty.eu/).

## Installation

The installation of this add-on is pretty straightforward.

1. Copy this URL: `https://github.com/V2G-liberty/addon-v2g-liberty.git`
2. In home assistant got to `setting > add-ons`.
3. Open the menu in the top right with the ⋮-icon and select the option `repostitories`
4. Paste the URL of step 1 and click `ADD`.
5. Click the `Install` button to install the add-on.

## Configuration

**Note**: _Remember to restart the add-on when the configuration is changed._

1. Go to the tab `configuration` and set your personal preferences and configuration options 
   according to your needs.
2. Start the V2G Liberty add-on.
3. Check the logs of the to see if everything went well.


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

The original setup of this repository is by Ard Jonker, [Seita B.V.](https://github.com/seitabv) and 
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
