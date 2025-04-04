# Running the V2G-Liberty application in a devcontainer

These instructions are specific to Visual Studio Code (VSC) IDE.

## Set-up the devcontainer development environment

1. Get the repository on your machine:

   - Open a terminal and open the folder in which you want the code to be stored
   - Clone the repository by typing the command:<br/>
     `git clone https://github.com/V2G-liberty/addon-v2g-liberty.git`<br>
     This will create all the files and folders on your local machine.

2. Start VSC and open the `addon-v2g-liberty` folder that has just been created on your machine.

3. VSC wil detect there is a dev container in this folder and thus should ask
   `Dev Containers: Reopen in Container?` click Yes.<br/>
   If this question does not popup, run the commands menu (Cmd-Shift-P) and run the above command.

4. This will automatically create and start three devcontainers:
   - One running the vanilla Home Assistant core
   - One for running the V2G-Liberty AppDaemon
   - And one for developing the V2G-Liberty frontend cards

During creation of the devcontainers, an initial homeassistant config folder
is created with user "gebruiker" and password "wachtwoord".
This also includes a long-lived access token shared with the AppDaemon.

Home Assistant is available on http://localhost:8123/

## Running V2G-Liberty

Open the Commands menu (Cmd-Shift-P) and select "Tasks: Run Task" and then
"Run V2G-Liberty app".

Alternatively, from the VScode main menu select "Run" / "Run Without Debugging"
(shortcut Ctrl-F5).

## Debugging V2G-Liberty

From the VScode main menu select "Run" / "Start Debugging" (shortcut F5).

## Copying V2G-Liberty package files to Home Assistant configuration folder

To update the V2G-Liberty packages files and www folders in the Home Assistant
configuration folder , select "Tasks: Run Task" and then
"Copy V2G-Liberty homeassistant files to config".

You may have to restart Home Assistant in the usual way to make it use the
newly copied files.
