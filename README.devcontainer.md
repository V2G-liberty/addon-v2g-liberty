# V2G-Liberty Development Setup

Quick guide to get V2G-Liberty running in a development environment.

## 📋 Prerequisites

Before you begin, ensure you have the following installed:

1. **Visual Studio Code**
   - Download from [code.visualstudio.com](https://code.visualstudio.com/)
   - Install the **Dev Containers** extension (ms-vscode-remote.remote-containers)

2. **Docker Desktop**
   - Download from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)
   - Ensure Docker is running before proceeding

3. **Bash Shell** (Optional - only for bash script method)
   - **Windows**: Git Bash (comes with Git for Windows) or WSL
   - **macOS/Linux**: Native bash support (pre-installed)
   - **Note**: Not required if you only use VS Code (Option B below)

4. **Clone the Repository**
   ```bash
   git clone https://github.com/V2G-liberty/addon-v2g-liberty.git
   cd addon-v2g-liberty
   ```

## Getting Started - 2 Steps

### Step 1: Start the Development Environment

Choose **ONE** of these methods based on your preference:

#### ⚡ Option A: Bash Script (Recommended - Full Featured)

**Best for:** Complete automation with health checks, validation, and helpful guidance

**All platforms:**
```bash
./setup-devcontainer.sh

# Windows: use Git Bash or WSL
# macOS/Linux: native bash
```

This automatically:
1. ✅ Validates Docker is running
2. ✅ Starts Quasar mock charger (simulated EV charger on port 5020)
3. ✅ Builds and starts Home Assistant + V2G-Liberty containers
4. ✅ Waits for containers to be healthy
5. ✅ Copies config files
6. ✅ Runs V2G-Liberty app with proper output

**Bash not available?** Windows users can install Git for Windows (includes Git Bash) or use Option B/C below.

#### 🎯 Option B: VS Code (Zero Install - Just Press F5)

**Best for:** VS Code users who prefer integrated debugging

1. **Open in VS Code**:
   - Open the `addon-v2g-liberty` folder in VS Code
   - Click "Reopen in Container" when prompted
   - Or: `Ctrl+Shift+P` → "Dev Containers: Reopen in Container"
   - This automatically:
     - ✅ Starts Quasar mock charger
     - ✅ Starts Home Assistant + V2G-Liberty containers
     - ✅ Copies config files

2. **Start the V2G-Liberty App**:
   - Press `F5` to start debugging with breakpoints
   - Or `Ctrl+F5` to run without debugging

**No bash required!** VS Code handles everything via devcontainer.json.

#### 🔧 Option C: Manual Docker Compose (For the Minimalists)

**Best for:** Developers who prefer direct Docker control

```bash
# Navigate to devcontainer directory
cd .devcontainer

# Start all services (includes mock charger + Home Assistant)
docker-compose --project-name addon-v2g-liberty_devcontainer up -d --build

# Wait ~30 seconds for containers to be healthy, then copy config files
docker-compose --project-name addon-v2g-liberty_devcontainer exec addon-v2g-liberty \
  bash -c "cd /workspaces && bash v2g-liberty/script/copy-config"

# Start V2G Liberty in interactive mode
docker-compose --project-name addon-v2g-liberty_devcontainer exec -it addon-v2g-liberty \
  bash -c "cd /workspaces/v2g-liberty && python3 -m appdaemon -c rootfs/root/appdaemon -C rootfs/root/appdaemon/appdaemon.devcontainer.yaml -D INFO"
```

**Note:** The bash script (Option A) provides additional validation, health checking, and error handling that this manual approach lacks.

---

**All three options share the same Docker containers and network**, so you can switch between them freely!

### Step 2: Login and Configure V2G Liberty

1. **Login to Home Assistant**:
   - Open your browser to: http://localhost:8123
   - **Username**: `gebruiker`
   - **Password**: `wachtwoord`

2. **Navigate to V2G Liberty**:
   - Click on the **"V2G Liberty"** tab in the Home Assistant sidebar
   - You'll see an error dialog: *"App not configured correctly and probably does not work"*
   - **This is expected on first run!**

3. **Configure the Charger**:
   - Click **"Go to settings"** button
   - In the **Charger Settings** section, click **"Configure"**
   - Enter the following values:
     - **Host**: `quasar-mock` (Docker service name for the mock Quasar charger)
     - **Port**: `5020`
   - Click **"Save"** to save the configuration
   - ✅ **Success!** After pressing Save, you should see: *"Successfully connected"*

4. **Configure Other Settings** (optional):
   - **Schedule Settings**: Set your charging preferences
   - **Administrator Settings**: Configure FlexMeasures connection (optional for basic testing)
   - **Calendar Settings**: Configure CalDAV calendar (optional)
   - **Electricity Contract Settings**: Set your electricity pricing

**Why use `quasar-mock`?**
- Docker's built-in DNS resolves service names to container IPs automatically
- This is best practice (no hard-coded IP addresses)
- When using a real Wallbox Quasar charger, you'll enter its hostname or IP address on your local network instead

**Note**: If you're using the optional Load Balancer module (configured in `quasar_load_balancer.json`), use `127.0.0.1` as the host instead.

## 🎮 Controlling the Mock Charger

Use the CLI to simulate different charging scenarios and test V2G Liberty behavior:

**Windows:**
```powershell
cd charger-mocks\quasar
python cli.py
```

**macOS/Linux:**
```bash
cd charger-mocks/quasar
python cli.py
```

**Common Commands**:
```
soc 75          # Set battery to 75%
charge 3000     # Charge at 3000W
charge -2000    # Discharge at 2000W (V2G mode)
disconnect      # Simulate car disconnection
error           # Simulate charger error
connect         # Clear error and reconnect
status          # Check current state
quit            # Exit CLI
```

**See [charger-mocks/README.md](charger-mocks/README.md) for complete command reference**

## 🔄 Switching Between Methods

You can freely switch between all three methods - they use the same containers:

**Any method → VS Code**: Just open VS Code and "Reopen in Container"

**Any method → Bash script**: Run the setup script (will detect and use existing containers)
```bash
./setup-devcontainer.sh
```

**Any method → Manual**: Use the docker-compose commands from Option C above

## 🔄 Reset Environment

If things aren't working or you want a clean slate:

```bash
./setup-devcontainer.sh reset
```

This will:
- Stop and remove all containers
- Delete data folders
- Remove Docker networks
- Clean up log files

**Other commands:**

```bash
./setup-devcontainer.sh status   # Check what's running
./setup-devcontainer.sh stop     # Stop everything
./setup-devcontainer.sh help     # Show all commands
```

---

## 💡 Need Help?

- **Check logs**: `v2g-liberty/logs/appdaemon_error.log`
- **View container status**: Run setup script with `status` argument

### Windows Users: Line Ending Issues

If you encounter errors about shell scripts not executing properly on Windows, configure Git to use Unix line endings:

```bash
# Configure Git to check out files with Unix line endings (LF)
git config --global core.autocrlf input

# Refresh your local checkout to apply the line endings
git rm --cached -r .
git reset --hard
```

This ensures bash scripts have the correct line endings for Docker containers (which run Linux).

## 📚 Next Steps

- Read [CLAUDE.md](CLAUDE.md) for architecture overview
- Explore tests in `v2g-liberty/rootfs/root/appdaemon/tests/`
- Study main app in `v2g-liberty/rootfs/root/appdaemon/apps/v2g_liberty/main_app.py`
- Check [charger-mocks/README.md](charger-mocks/README.md) for mock charger details
