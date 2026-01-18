#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Quick setup script for V2G-Liberty development environment.

.DESCRIPTION
    Simplified script to get new developers up and running fast:
    - 'dev' command starts everything you need (default)
    - 'stop' stops all services
    - 'reset' cleans all state for a fresh start
    - 'status' shows what's running
    - 'help' shows usage information

.PARAMETER Action
    The action to perform: dev (default), stop, reset, status, or help

.EXAMPLE
    .\setup-devcontainer.ps1
    Starts complete dev session (same as 'dev')

.EXAMPLE
    .\setup-devcontainer.ps1 dev
    Starts Quasar mock, containers, and V2G-Liberty app

.EXAMPLE
    .\setup-devcontainer.ps1 reset
    Cleans all state and returns to fresh start (requires confirmation)
#>

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet('dev', 'stop', 'reset', 'status', 'help')]
    [string]$Action = 'dev'
)

$ErrorActionPreference = "Stop"

# Configuration
$REPO_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$DEVCONTAINER_DIR = Join-Path $REPO_ROOT ".devcontainer"
$CHARGER_MOCKS_DIR = Join-Path $REPO_ROOT "charger-mocks"
$QUASAR_MOCK_PORT = 5020
$HA_URL = "http://localhost:8123"
$HA_USER = "gebruiker"
$HA_PASSWORD = "wachtwoord"

function Write-Header {
    param([string]$Message)
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "========================================`n" -ForegroundColor Cyan
}

function Test-DockerRunning {
    try {
        $null = docker info 2>&1
        return $true
    } catch {
        Write-Host "ERROR: Docker is not running. Please start Docker Desktop." -ForegroundColor Red
        return $false
    }
}

function Test-DevContainerCLI {
    try {
        $null = devcontainer --version 2>&1
        return $true
    } catch {
        Write-Host "WARNING: devcontainer CLI not found. Using docker-compose instead." -ForegroundColor Yellow
        return $false
    }
}

function Test-QuasarMockRunning {
    try {
        # Check if quasar-mock service is running in docker-compose
        Push-Location $DEVCONTAINER_DIR
        $service = docker-compose --project-name addon-v2g-liberty_devcontainer ps quasar-mock --format json 2>$null | ConvertFrom-Json
        Pop-Location
        return ($null -ne $service -and $service.State -eq "running")
    } catch {
        Pop-Location
        return $false
    }
}

function Start-QuasarMock {
    Write-Header "Starting Wallbox Quasar Mock Server"

    if (-not (Test-DockerRunning)) {
        exit 1
    }

    # Check if already running
    if (Test-QuasarMockRunning) {
        Write-Host "Quasar mock is already running on port $QUASAR_MOCK_PORT" -ForegroundColor Green
        return
    }

    Write-Host "Starting Wallbox Quasar mock server on port $QUASAR_MOCK_PORT..." -ForegroundColor Green
    Write-Host "Config: $CHARGER_MOCKS_DIR\configs\quasar_charging_33pct.json" -ForegroundColor Gray

    Push-Location $DEVCONTAINER_DIR
    try {
        docker-compose --project-name addon-v2g-liberty_devcontainer up -d quasar-mock
        Start-Sleep -Seconds 2

        if (Test-QuasarMockRunning) {
            Write-Host "Quasar mock server started successfully!" -ForegroundColor Green
            Write-Host "  Port: $QUASAR_MOCK_PORT" -ForegroundColor Cyan
            Write-Host "  Initial state: Charging at 5750W, SoC 33%" -ForegroundColor Cyan
        } else {
            Write-Host "WARNING: Failed to verify Quasar mock is running" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "ERROR: Failed to start Quasar mock: $_" -ForegroundColor Red
        exit 1
    } finally {
        Pop-Location
    }
}


function Stop-DevContainers {
    Write-Header "Stopping All Services"

    Write-Host "Stopping Quasar mock..." -ForegroundColor Yellow
    Push-Location $DEVCONTAINER_DIR
    try {
        docker-compose --project-name addon-v2g-liberty_devcontainer stop quasar-mock 2>$null | Out-Null
    } catch {
        # Container might not exist, that's okay
    }

    Write-Host "Stopping DevContainers..." -ForegroundColor Yellow
    try {
        docker-compose --project-name addon-v2g-liberty_devcontainer down
        Write-Host "`nAll services stopped successfully." -ForegroundColor Green
    } finally {
        Pop-Location
    }
}

function Reset-DevEnvironment {
    Write-Header "Resetting Development Environment"

    Write-Host "WARNING: This will delete all development state and data!" -ForegroundColor Red
    Write-Host "  - All Docker containers and volumes" -ForegroundColor Yellow
    Write-Host "  - v2g-liberty/data/ (settings)" -ForegroundColor Yellow
    Write-Host "  - v2g-liberty/logs/ (AppDaemon logs)" -ForegroundColor Yellow
    Write-Host "  - .devcontainer/config/ (Home Assistant database)" -ForegroundColor Yellow
    Write-Host "  - All Python __pycache__ folders" -ForegroundColor Yellow
    Write-Host ""

    $confirm = Read-Host "Are you sure you want to continue? (yes/no)"
    if ($confirm -ne "yes") {
        Write-Host "Reset cancelled." -ForegroundColor Green
        return
    }

    # Step 1: Stop and remove all containers
    Write-Host "`n[1/5] Stopping and removing Docker containers..." -ForegroundColor Cyan
    Push-Location $DEVCONTAINER_DIR
    try {
        docker-compose --project-name addon-v2g-liberty_devcontainer down -v 2>$null | Out-Null
        Write-Host "  ✓ Containers and volumes removed" -ForegroundColor Green
    } catch {
        Write-Host "  ! No containers to remove" -ForegroundColor Gray
    } finally {
        Pop-Location
    }

    # Step 2: Delete data folder
    Write-Host "`n[2/5] Cleaning v2g-liberty/data/..." -ForegroundColor Cyan
    $dataPath = Join-Path $REPO_ROOT "v2g-liberty\data"
    if (Test-Path $dataPath) {
        Remove-Item -Path $dataPath -Recurse -Force
        Write-Host "  ✓ Data folder deleted" -ForegroundColor Green
    }
    New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
    Write-Host "  ✓ Empty data folder created" -ForegroundColor Green

    # Step 3: Delete logs folder
    Write-Host "`n[3/5] Cleaning v2g-liberty/logs/..." -ForegroundColor Cyan
    $logsPath = Join-Path $REPO_ROOT "v2g-liberty\logs"
    if (Test-Path $logsPath) {
        Remove-Item -Path $logsPath -Recurse -Force
        Write-Host "  ✓ Logs folder deleted" -ForegroundColor Green
    }
    New-Item -ItemType Directory -Path $logsPath -Force | Out-Null
    Write-Host "  ✓ Empty logs folder created" -ForegroundColor Green

    # Step 4: Delete devcontainer config folder
    Write-Host "`n[4/5] Cleaning .devcontainer/config/..." -ForegroundColor Cyan
    $configPath = Join-Path $DEVCONTAINER_DIR "config"
    if (Test-Path $configPath) {
        Remove-Item -Path $configPath -Recurse -Force
        Write-Host "  ✓ Config folder deleted" -ForegroundColor Green
    }
    New-Item -ItemType Directory -Path $configPath -Force | Out-Null
    Write-Host "  ✓ Empty config folder created" -ForegroundColor Green

    # Step 5: Remove Python cache
    Write-Host "`n[5/5] Removing Python __pycache__ folders..." -ForegroundColor Cyan
    $pycacheFolders = Get-ChildItem -Path $REPO_ROOT -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue
    $count = 0
    foreach ($folder in $pycacheFolders) {
        Remove-Item -Path $folder.FullName -Recurse -Force
        $count++
    }
    Write-Host "  ✓ Removed $count __pycache__ folders" -ForegroundColor Green

    Write-Host "`n========================================" -ForegroundColor Green
    Write-Host "  Reset Complete!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Your development environment is now clean." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Run: .\setup-devcontainer.ps1 dev" -ForegroundColor White
    Write-Host "  2. Wait for containers to start and Home Assistant to initialize" -ForegroundColor White
    Write-Host "  3. Open http://localhost:8123 (user: $HA_USER, password: $HA_PASSWORD)" -ForegroundColor White
    Write-Host ""
}

function Get-ContainerStatus {
    Write-Header "Service Status"

    Push-Location $DEVCONTAINER_DIR
    try {
        Write-Host "Quasar Mock Server:" -ForegroundColor Cyan
        $quasarStatus = docker-compose --project-name addon-v2g-liberty_devcontainer ps quasar-mock --format json 2>$null | ConvertFrom-Json
        if ($null -ne $quasarStatus -and $quasarStatus.State -eq "running") {
            Write-Host "  ✓ RUNNING on port $QUASAR_MOCK_PORT" -ForegroundColor Green
            Write-Host "  IP (from HA): 172.20.0.10" -ForegroundColor Green
            Write-Host "  IP (from host): localhost" -ForegroundColor Green
        } else {
            Write-Host "  ✗ NOT RUNNING" -ForegroundColor Yellow
        }

        Write-Host "`nDevContainers:" -ForegroundColor Cyan
        docker-compose --project-name addon-v2g-liberty_devcontainer ps
    } finally {
        Pop-Location
    }
}

function Start-DevWithConfig {
    Write-Header "Starting V2G-Liberty Development Session"

    # Step 1: Start Quasar mock server
    if (-not (Test-QuasarMockRunning)) {
        Write-Host "Step 1/4: Starting Quasar mock server..." -ForegroundColor Cyan
        Start-QuasarMock
        Write-Host ""
    } else {
        Write-Host "Step 1/4: Quasar mock already running on port $QUASAR_MOCK_PORT" -ForegroundColor Green
        Write-Host ""
    }

    # Step 2: Start devcontainers
    Push-Location $DEVCONTAINER_DIR
    try {
        # Check if addon-v2g-liberty container is running (key dependency)
        $addonStatus = docker-compose --project-name addon-v2g-liberty_devcontainer ps addon-v2g-liberty --format json 2>$null | ConvertFrom-Json
        $isRunning = ($null -ne $addonStatus -and $addonStatus.State -eq "running")

        if (-not $isRunning) {
            Write-Host "Step 2/4: Starting devcontainers..." -ForegroundColor Cyan
            docker-compose --project-name addon-v2g-liberty_devcontainer up -d --build

            Write-Host "Waiting for containers to be healthy..." -ForegroundColor Yellow
            $timeout = 120
            $elapsed = 0
            while ($elapsed -lt $timeout) {
                $addonStatus = docker-compose --project-name addon-v2g-liberty_devcontainer ps addon-v2g-liberty --format json 2>$null | ConvertFrom-Json
                if ($null -ne $addonStatus -and $addonStatus.Health -eq "healthy") {
                    Write-Host "`nAll containers are running!" -ForegroundColor Green
                    break
                }
                Start-Sleep -Seconds 2
                $elapsed += 2
                Write-Host "." -NoNewline
            }
            Write-Host ""
        } else {
            Write-Host "Step 2/4: Devcontainers already running" -ForegroundColor Green
            Write-Host ""
        }

        # Step 3: Copy config files
        Write-Host "Step 3/4: Copying V2G-Liberty config files to Home Assistant..." -ForegroundColor Cyan
        # Note: .gitattributes handles line endings at checkout, but we fix them here as a safety measure
        # in case the user cloned the repo before .gitattributes was added or with incorrect git config
        docker-compose --project-name addon-v2g-liberty_devcontainer exec addon-v2g-liberty bash -c "sed -i 's/\r$//' /workspaces/v2g-liberty/script/copy-config 2>/dev/null || true && cd /workspaces && bash v2g-liberty/script/copy-config"

        Write-Host "`nConfig files copied successfully!" -ForegroundColor Green
        Write-Host ""

        # Step 4: Start V2G-Liberty app
        Write-Host "Step 4/4: Starting V2G-Liberty AppDaemon..." -ForegroundColor Cyan
        Write-Host "This will run in the foreground. Press Ctrl+C to stop." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "=================================" -ForegroundColor Green
        Write-Host "Next: Configure V2G Liberty!" -ForegroundColor Green
        Write-Host "=================================" -ForegroundColor Green
        Write-Host "1. Open Home Assistant: $HA_URL" -ForegroundColor Cyan
        Write-Host "   Username: $HA_USER" -ForegroundColor Gray
        Write-Host "   Password: $HA_PASSWORD" -ForegroundColor Gray
        Write-Host ""
        Write-Host "2. Click 'V2G Liberty' tab → 'Go to settings' → 'Configure' charger" -ForegroundColor Cyan
        Write-Host "   Host: 172.20.0.10" -ForegroundColor Yellow
        Write-Host "   Port: 5020" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "TIP: Control the mock charger in another terminal:" -ForegroundColor Gray
        Write-Host "  cd $CHARGER_MOCKS_DIR\quasar" -ForegroundColor Gray
        Write-Host "  python cli.py" -ForegroundColor Gray
        Write-Host "  > soc 75         # Set battery to 75%" -ForegroundColor Gray
        Write-Host "  > charge 3000    # Charge at 3000W" -ForegroundColor Gray
        Write-Host ""

        # Use docker exec directly for better Windows terminal compatibility
        $containerId = docker-compose --project-name addon-v2g-liberty_devcontainer ps -q addon-v2g-liberty
        docker exec -it $containerId bash -c "cd /workspaces/v2g-liberty && python3 -m appdaemon -c rootfs/root/appdaemon -C rootfs/root/appdaemon/appdaemon.devcontainer.yaml -D INFO"
    } finally {
        Pop-Location
    }
}

function Show-Help {
    Write-Host @"

========================================
  V2G-Liberty Development Environment
========================================

Quick setup script for new developers to get V2G Liberty running fast!

USAGE:
    .\setup-devcontainer.ps1 [command]

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
    .\setup-devcontainer.ps1              # Start dev session (same as 'dev')
    .\setup-devcontainer.ps1 dev          # Start dev session explicitly
    .\setup-devcontainer.ps1 status       # Check what's running
    .\setup-devcontainer.ps1 stop         # Stop everything
    .\setup-devcontainer.ps1 reset        # Clean slate (requires confirmation)

GETTING STARTED (New Developers):
    1. Run: .\setup-devcontainer.ps1 dev
    2. Wait for "Starting V2G-Liberty AppDaemon..." message
    3. Open browser: $HA_URL
       - Username: $HA_USER
       - Password: $HA_PASSWORD
    4. Configure charger in V2G Liberty tab:
       - Click "Go to settings" → "Configure" charger
       - Host: 172.20.0.10
       - Port: $QUASAR_MOCK_PORT
    5. In another terminal, control the mock charger:
       cd $CHARGER_MOCKS_DIR\quasar
       python cli.py
       > soc 75              # Set battery to 75%
       > charge 3000         # Charge at 3000W
       > charge -2000        # Discharge at 2000W (V2G)

TROUBLESHOOTING:
    - If things aren't working: .\setup-devcontainer.ps1 reset
    - Check service status: .\setup-devcontainer.ps1 status
    - See logs in v2g-liberty/logs/

VSCODE INTEGRATION:
    Both PowerShell and VS Code methods share the same Docker containers.

    To use VS Code debugging:
    1. Press Ctrl+Shift+P → "Dev Containers: Reopen in Container"
    2. Use F5 for debugging, Ctrl+F5 to run without debugging

    The containers started by this script will be reused by VS Code.

"@ -ForegroundColor White
}

# Main script execution
switch ($Action) {
    'dev' {
        Start-DevWithConfig
    }
    'stop' {
        Stop-DevContainers
    }
    'reset' {
        Reset-DevEnvironment
    }
    'status' {
        Get-ContainerStatus
    }
    'help' {
        Show-Help
    }
}
