#!/usr/bin/env bash
# ==============================================================================
# JAX Dealership OS - AutoAgent All-in-One Uninstaller
# ==============================================================================
set -e

# Reconnect stdin to controlling terminal if script was piped or detached
if [ ! -t 0 ] && [ -e /dev/tty ]; then
    exec < /dev/tty
fi

# ANSI Colors
CYAN='\033[1;36m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}      🗑️   Uninstalling JAX Dealership OS (AutoAgent)...          ${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════════════════════${NC}"

# Check for force / unattended flags
FORCE=false
for arg in "$@"; do
    if [ "$arg" == "-y" ] || [ "$arg" == "--yes" ] || [ "$arg" == "-f" ] || [ "$arg" == "--force" ]; then
        FORCE=true
    fi
done

# Confirm with user if interactive and not forced
if [ "$FORCE" = false ] && [ -t 0 ]; then
    read -r -p "Are you sure you want to completely remove AutoAgent and delete all local credentials/data? (y/N): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Uninstallation canceled.${NC}"
        exit 0
    fi
fi

# 1. Stop and remove PM2 background services
if command -v pm2 &>/dev/null; then
    echo -e "${YELLOW}Stopping PM2 background services...${NC}"
    pm2 delete jax-whatsapp-monitor jax-whatsapp jax-telegram jax-watchdog 2>/dev/null || true
    pm2 delete ecosystem.config.cjs 2>/dev/null || true
    pm2 save --force 2>/dev/null || true
    echo -e "${GREEN}✓ PM2 services stopped and removed.${NC}"
fi

# 2. Remove stored dealership credentials
CONFIG_FILE="$HOME/.config/dealer_credentials.env"
if [ -f "$CONFIG_FILE" ]; then
    echo -e "${YELLOW}Removing stored dealership credentials ($CONFIG_FILE)...${NC}"
    rm -f "$CONFIG_FILE"
    echo -e "${GREEN}✓ Dealership credentials removed.${NC}"
fi

# 3. Remove AutoAgent repository directory
INSTALL_DIR="${AUTOAGENT_INSTALL_DIR:-$HOME/autoagent}"
if [ -d "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}Removing AutoAgent files from $INSTALL_DIR...${NC}"
    cd "$HOME"
    rm -rf "$INSTALL_DIR"
    echo -e "${GREEN}✓ Repository files and databases removed.${NC}"
fi

echo -e "\n${GREEN}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}       ✓ AutoAgent has been completely uninstalled.              ${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════════════════${NC}\n"
