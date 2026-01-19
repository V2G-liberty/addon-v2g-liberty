#!/usr/bin/env bash
#
# Quick setup script for V2G-Liberty development environment.
#
# USAGE:
#   ./setup-devcontainer.sh [command]
#
# COMMANDS:
#   dev      Start complete development session (default)
#   stop     Stop all services
#   reset    Clean all state for a fresh start
#   status   Show what's running
#   help     Show usage information
#
# EXAMPLES:
#   ./setup-devcontainer.sh           # Start dev session (same as 'dev')
#   ./setup-devcontainer.sh dev       # Start dev session explicitly
#   ./setup-devcontainer.sh status    # Check what's running
#   ./setup-devcontainer.sh stop      # Stop everything
#   ./setup-devcontainer.sh reset     # Clean slate (requires confirmation)

set -e

# Configuration
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEVCONTAINER_DIR="$REPO_ROOT/.devcontainer"
CHARGER_MOCKS_DIR="$REPO_ROOT/charger-mocks"
QUASAR_MOCK_PORT=5020
HA_URL="http://localhost:8123"
HA_USER="gebruiker"
HA_PASSWORD="wachtwoord"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

function print_header() {
    echo -e "\n${CYAN}========================================${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}========================================${NC}\n"
}

function test_docker_running() {
    if ! docker info >/dev/null 2>&1; then
        echo -e "${RED}ERROR: Docker is not running. Please start Docker Desktop.${NC}"
        return 1
    fi
    return 0
}

function test_quasar_mock_running() {
    cd "$DEVCONTAINER_DIR"
    local state
    state=$(docker-compose --project-name addon-v2g-liberty_devcontainer ps quasar-mock --format json 2>/dev/null | grep -o '"State":"[^"]*"' | cut -d'"' -f4 || echo "")
    cd - >/dev/null
    [ "$state" = "running" ]
}

function start_quasar_mock() {
    print_header "Starting Wallbox Quasar Mock Server"

    if ! test_docker_running; then
        exit 1
    fi

    # Check if already running
    if test_quasar_mock_running; then
        echo -e "${GREEN}Quasar mock is already running on port $QUASAR_MOCK_PORT${NC}"
        return 0
    fi

    echo -e "${GREEN}Starting Wallbox Quasar mock server on port $QUASAR_MOCK_PORT...${NC}"
    echo -e "${GRAY}Config: $CHARGER_MOCKS_DIR/configs/quasar_charging_33pct.json${NC}"

    cd "$DEVCONTAINER_DIR"
    docker-compose --project-name addon-v2g-liberty_devcontainer up -d quasar-mock
    sleep 2

    if test_quasar_mock_running; then
        echo -e "${GREEN}Quasar mock server started successfully!${NC}"
        echo -e "${CYAN}  Port: $QUASAR_MOCK_PORT${NC}"
        echo -e "${CYAN}  Initial state: Charging at 5750W, SoC 33%${NC}"
    else
        echo -e "${YELLOW}WARNING: Failed to verify Quasar mock is running${NC}"
    fi

    cd - >/dev/null
}

function stop_devcontainers() {
    print_header "Stopping All Services"

    echo -e "${YELLOW}Stopping Quasar mock...${NC}"
    cd "$DEVCONTAINER_DIR"
    docker-compose --project-name addon-v2g-liberty_devcontainer stop quasar-mock 2>/dev/null || true

    echo -e "${YELLOW}Stopping DevContainers...${NC}"
    docker-compose --project-name addon-v2g-liberty_devcontainer down
    echo -e "\n${GREEN}All services stopped successfully.${NC}"

    cd - >/dev/null
}

function reset_dev_environment() {
    print_header "Resetting Development Environment"

    echo -e "${RED}WARNING: This will delete all development state and data!${NC}"
    echo -e "${YELLOW}  - All Docker containers and volumes${NC}"
    echo -e "${YELLOW}  - v2g-liberty/data/ (settings)${NC}"
    echo -e "${YELLOW}  - v2g-liberty/logs/ (AppDaemon logs)${NC}"
    echo -e "${YELLOW}  - .devcontainer/config/ (Home Assistant database)${NC}"
    echo -e "${YELLOW}  - All Python __pycache__ folders${NC}"
    echo ""

    read -p "Are you sure you want to continue? (yes/no) " confirm
    if [ "$confirm" != "yes" ]; then
        echo -e "${GREEN}Reset cancelled.${NC}"
        return 0
    fi

    # Step 1: Stop and remove all containers
    echo -e "\n${CYAN}[1/5] Stopping and removing Docker containers...${NC}"
    cd "$DEVCONTAINER_DIR"
    if docker-compose --project-name addon-v2g-liberty_devcontainer down -v 2>/dev/null; then
        echo -e "${GREEN}  ✓ Containers and volumes removed${NC}"
    else
        echo -e "${GRAY}  ! No containers to remove${NC}"
    fi
    cd - >/dev/null

    # Step 2: Delete data folder
    echo -e "\n${CYAN}[2/5] Cleaning v2g-liberty/data/...${NC}"
    local data_path="$REPO_ROOT/v2g-liberty/data"
    if [ -d "$data_path" ]; then
        rm -rf "$data_path"
        echo -e "${GREEN}  ✓ Data folder deleted${NC}"
    fi
    mkdir -p "$data_path"
    echo -e "${GREEN}  ✓ Empty data folder created${NC}"

    # Step 3: Delete logs folder
    echo -e "\n${CYAN}[3/5] Cleaning v2g-liberty/logs/...${NC}"
    local logs_path="$REPO_ROOT/v2g-liberty/logs"
    if [ -d "$logs_path" ]; then
        rm -rf "$logs_path"
        echo -e "${GREEN}  ✓ Logs folder deleted${NC}"
    fi
    mkdir -p "$logs_path"
    echo -e "${GREEN}  ✓ Empty logs folder created${NC}"

    # Step 4: Delete devcontainer config folder
    echo -e "\n${CYAN}[4/5] Cleaning .devcontainer/config/...${NC}"
    local config_path="$DEVCONTAINER_DIR/config"
    if [ -d "$config_path" ]; then
        rm -rf "$config_path"
        echo -e "${GREEN}  ✓ Config folder deleted${NC}"
    fi
    mkdir -p "$config_path"
    echo -e "${GREEN}  ✓ Empty config folder created${NC}"

    # Step 5: Remove Python cache
    echo -e "\n${CYAN}[5/5] Removing Python __pycache__ folders...${NC}"
    local count=0
    while IFS= read -r -d '' folder; do
        rm -rf "$folder"
        ((count++))
    done < <(find "$REPO_ROOT" -type d -name "__pycache__" -print0 2>/dev/null)
    echo -e "${GREEN}  ✓ Removed $count __pycache__ folders${NC}"

    echo -e "\n${GREEN}========================================${NC}"
    echo -e "${GREEN}  Reset Complete!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "${CYAN}Your development environment is now clean.${NC}"
    echo ""
    echo -e "${YELLOW}Next steps:${NC}"
    echo -e "${WHITE}  1. Run: ./setup-devcontainer.sh dev${NC}"
    echo -e "${WHITE}  2. Wait for containers to start and Home Assistant to initialize${NC}"
    echo -e "${WHITE}  3. Open http://localhost:8123 (user: $HA_USER, password: $HA_PASSWORD)${NC}"
    echo ""
}

function get_container_status() {
    print_header "Service Status"

    cd "$DEVCONTAINER_DIR"

    echo -e "${CYAN}Quasar Mock Server:${NC}"
    local quasar_state
    quasar_state=$(docker-compose --project-name addon-v2g-liberty_devcontainer ps quasar-mock --format json 2>/dev/null | grep -o '"State":"[^"]*"' | cut -d'"' -f4 || echo "")
    if [ "$quasar_state" = "running" ]; then
        echo -e "${GREEN}  ✓ RUNNING on port $QUASAR_MOCK_PORT${NC}"
        echo -e "${GREEN}  Hostname: quasar-mock${NC}"
        echo -e "${GREEN}  Host IP: localhost${NC}"
    else
        echo -e "${YELLOW}  ✗ NOT RUNNING${NC}"
    fi

    echo -e "\n${CYAN}DevContainers:${NC}"
    docker-compose --project-name addon-v2g-liberty_devcontainer ps

    cd - >/dev/null
}

function start_dev_with_config() {
    print_header "Starting V2G-Liberty Development Session"

    # Step 1: Start Quasar mock server
    if ! test_quasar_mock_running; then
        echo -e "${CYAN}Step 1/4: Starting Quasar mock server...${NC}"
        start_quasar_mock
        echo ""
    else
        echo -e "${GREEN}Step 1/4: Quasar mock already running on port $QUASAR_MOCK_PORT${NC}"
        echo ""
    fi

    # Step 2: Start devcontainers
    cd "$DEVCONTAINER_DIR"

    # Check if addon-v2g-liberty container is running (key dependency)
    local addon_state
    addon_state=$(docker-compose --project-name addon-v2g-liberty_devcontainer ps addon-v2g-liberty --format json 2>/dev/null | grep -o '"State":"[^"]*"' | cut -d'"' -f4 || echo "")

    if [ "$addon_state" != "running" ]; then
        echo -e "${CYAN}Step 2/4: Starting devcontainers...${NC}"
        docker-compose --project-name addon-v2g-liberty_devcontainer up -d --build

        echo -e "${YELLOW}Waiting for containers to be healthy...${NC}"
        local timeout=120
        local elapsed=0
        while [ $elapsed -lt $timeout ]; do
            local health
            health=$(docker-compose --project-name addon-v2g-liberty_devcontainer ps addon-v2g-liberty --format json 2>/dev/null | grep -o '"Health":"[^"]*"' | cut -d'"' -f4 || echo "")
            if [ "$health" = "healthy" ]; then
                echo -e "\n${GREEN}All containers are running!${NC}"
                break
            fi
            echo -n "."
            sleep 2
            elapsed=$((elapsed + 2))
        done
        echo ""
    else
        echo -e "${GREEN}Step 2/4: Devcontainers already running${NC}"
        echo ""
    fi

    # Step 3: Copy config files
    echo -e "${CYAN}Step 3/4: Copying V2G-Liberty config files to Home Assistant...${NC}"
    docker-compose --project-name addon-v2g-liberty_devcontainer exec -T addon-v2g-liberty bash -c "cd /workspaces && bash v2g-liberty/script/copy-config"

    echo -e "\n${GREEN}Config files copied successfully!${NC}"
    echo ""

    # Step 4: Start V2G-Liberty app
    echo -e "${CYAN}Step 4/4: Starting V2G-Liberty AppDaemon...${NC}"
    echo -e "${YELLOW}This will run in the foreground. Press Ctrl+C to stop.${NC}"
    echo ""
    echo -e "${GREEN}=================================${NC}"
    echo -e "${GREEN}Next: Configure V2G Liberty!${NC}"
    echo -e "${GREEN}=================================${NC}"
    echo -e "${CYAN}1. Open Home Assistant: $HA_URL${NC}"
    echo -e "${GRAY}   Username: $HA_USER${NC}"
    echo -e "${GRAY}   Password: $HA_PASSWORD${NC}"
    echo ""
    echo -e "${CYAN}2. Click 'V2G Liberty' tab → 'Go to settings' → 'Configure' charger${NC}"
    echo -e "${YELLOW}   Host: quasar-mock${NC}"
    echo -e "${YELLOW}   Port: 5020${NC}"
    echo ""
    echo -e "${GRAY}TIP: Control the mock charger in another terminal:${NC}"
    echo -e "${GRAY}  cd $CHARGER_MOCKS_DIR/quasar${NC}"
    echo -e "${GRAY}  python cli.py${NC}"
    echo -e "${GRAY}  > soc 75         # Set battery to 75%${NC}"
    echo -e "${GRAY}  > charge 3000    # Charge at 3000W${NC}"
    echo ""

    # Use docker exec directly for better terminal compatibility
    local container_id
    container_id=$(docker-compose --project-name addon-v2g-liberty_devcontainer ps -q addon-v2g-liberty)
    docker exec -it "$container_id" bash -c "cd /workspaces/v2g-liberty && python3 -m appdaemon -c rootfs/root/appdaemon -C rootfs/root/appdaemon/appdaemon.devcontainer.yaml -D INFO"

    cd - >/dev/null
}

function show_help() {
    cat <<EOF

========================================
  V2G-Liberty Development Environment
========================================

Quick setup script for new developers to get V2G Liberty running fast!

USAGE:
    ./setup-devcontainer.sh [command]

COMMANDS:
    dev      Start complete development session (default)
             - Starts Quasar mock charger (port $QUASAR_MOCK_PORT)
             - Starts all containers (Home Assistant, AppDaemon, Frontend)
             - Copies configuration files
             - Runs V2G-Liberty app (interactive)

    status   Show status of all services
             - Quasar mock server status
             - Docker container status

    stop     Stop all services
             - Stops Quasar mock
             - Stops all containers

    reset    Clean all state and return to fresh start
             - Removes containers and volumes
             - Deletes data, logs, and config folders
             - Clears Python cache
             ⚠️  WARNING: This deletes all development data!

    help     Show this help message

EXAMPLES:
    ./setup-devcontainer.sh              # Start dev session (same as 'dev')
    ./setup-devcontainer.sh dev          # Start dev session explicitly
    ./setup-devcontainer.sh status       # Check what's running
    ./setup-devcontainer.sh stop         # Stop everything
    ./setup-devcontainer.sh reset        # Clean slate (requires confirmation)

GETTING STARTED (New Developers):
    1. Run: ./setup-devcontainer.sh dev
    2. Wait for "Starting V2G-Liberty AppDaemon..." message
    3. Open browser: $HA_URL
       - Username: $HA_USER
       - Password: $HA_PASSWORD
    4. Configure charger in V2G Liberty tab:
       - Click "Go to settings" → "Configure" charger
       - Host: quasar-mock
       - Port: $QUASAR_MOCK_PORT
    5. In another terminal, control the mock charger:
       cd $CHARGER_MOCKS_DIR/quasar
       python cli.py
       > soc 75              # Set battery to 75%
       > charge 3000         # Charge at 3000W
       > charge -2000        # Discharge at 2000W (V2G)

TROUBLESHOOTING:
    - If things aren't working: ./setup-devcontainer.sh reset
    - Check service status: ./setup-devcontainer.sh status
    - See logs in v2g-liberty/logs/

VSCODE INTEGRATION:
    Both Bash and VS Code methods share the same Docker containers.

    To use VS Code debugging:
    1. Press Ctrl+Shift+P → "Dev Containers: Reopen in Container"
    2. Use F5 for debugging, Ctrl+F5 to run without debugging

    The containers started by this script will be reused by VS Code.

EOF
}

# Main script execution
ACTION="${1:-dev}"

case "$ACTION" in
    dev)
        start_dev_with_config
        ;;
    stop)
        stop_devcontainers
        ;;
    reset)
        reset_dev_environment
        ;;
    status)
        get_container_status
        ;;
    help)
        show_help
        ;;
    *)
        echo -e "${RED}ERROR: Unknown command '$ACTION'${NC}"
        echo "Run './setup-devcontainer.sh help' for usage information."
        exit 1
        ;;
esac
