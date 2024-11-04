# Running the V2G-Liberty application in a devcontainer

# # Set-up the devcontainer development environment

1. Open the `v2g-liberty` folder in VScode
   (note: this is not the `addon-v2g-liberty` repo root folder)

2. When asked, or from the Commands menu (Cmd-Shift-P) run
   "Dev Containers: Reopen in Container"
   This will create and start two devcontainers:
   - One running the vanilla Home Assistant core
   - The other is for running the V2G-Liberty appdaemon

During creation of the devcontainers, an initial homeassistant config folder
is created with user "gebruiker" and password "wachtwoord".
This also includes a long-lived access token shared with the appdaemon.

Home Assistant is available on http://localhost:8123/

# # Running V2G-Liberty

Open the Commands menu (Cmd-Shift-P) and select "Tasks: Run Task" and then
"Run V2G-Liberty app".

Alternatively, from the VScode main menu select "Run" / "Run Without Debugging"
(shortcut Ctrl-F5).

# # Debugging V2G-Liberty

From the VScode main menu select "Run" / "Start Debugging" (shortcut F5).

# # Copying V2G-Liberty package files to Home Assistant configuration folder

To update the V2G-Liberty packages files and www folders in the Home Assistant
configuration folder , select "Tasks: Run Task" and then
"Copy V2G-Liberty homeassistant files to config".

You may have to restart Home Assistant in the usual way to make it use the
newly copied files.
