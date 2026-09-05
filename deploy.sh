#!/usr/bin/env bash
# ==============================================================================
# JAX Dealership OS - AutoAgent All-in-One Deployment & Onboarding Harness
# ==============================================================================
set -e

# Reconnect stdin to controlling terminal if script was piped or detached
if [ ! -t 0 ] && [ -e /dev/tty ]; then
    exec < /dev/tty
fi

# ANSI Color Codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m' # No Color

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

clear || true

echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}     🚘  JAX DEALERSHIP OS  |  AUTOAGENT DEPLOYMENT HARNESS     ${NC}"
echo -e "${CYAN}${BOLD}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Autonomous Automotive CRM Engine, WhatsApp Companion & AI Co-Pilot${NC}"
echo ""

# Ensure user directories and paths
export PATH="/usr/local/bin:$HOME/.local/bin:$HOME/.local/node/bin:$PATH"
export PYTHONPATH="$ROOT_DIR/skills/autohub-portal/scripts:$ROOT_DIR/skills/whatsapp-monitor/scripts:$ROOT_DIR/skills/bb-used-cars/scripts:$PYTHONPATH"

# ------------------------------------------------------------------------------
# STEP 1: System Dependencies & Environment Setup
# ------------------------------------------------------------------------------
echo -e "\n${CYAN}${BOLD}[1/6] Checking and Installing System Dependencies...${NC}"

# Check Node.js
if ! command -v node &>/dev/null; then
    echo -e "${YELLOW}Node.js not detected. Installing Node.js 22 LTS...${NC}"
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y nodejs
else
    NODE_VER=$(node -v)
    echo -e "${GREEN}✓ Node.js detected:${NC} $NODE_VER"
fi

# Check Python 3 and pip
if ! command -v python3 &>/dev/null; then
    echo -e "${YELLOW}Python 3 not detected. Installing python3, pip, and venv...${NC}"
    sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv
else
    PY_VER=$(python3 --version)
    echo -e "${GREEN}✓ Python detected:${NC} $PY_VER"
fi

# Ensure pip3 is available
if ! command -v pip3 &>/dev/null && ! python3 -m pip --version &>/dev/null; then
    echo -e "${YELLOW}pip not detected. Installing python3-pip and python3-venv...${NC}"
    sudo apt-get update && sudo apt-get install -y python3-pip python3-venv
fi

# Pre-install common distro packages where possible (fast & avoids PEP 668 issues)
sudo apt-get install -y python3-requests python3-bs4 2>/dev/null || true

# Install Python requirements
echo -e "Checking Python packages..."
if [ -f "$ROOT_DIR/requirements.txt" ]; then
    if ! python3 -c "import curl_cffi, bs4, requests" &>/dev/null; then
        echo -e "${YELLOW}Installing required Python packages (curl_cffi, beautifulsoup4, requests)...${NC}"
        pip3 install -q --break-system-packages -r "$ROOT_DIR/requirements.txt" 2>/dev/null || \
        pip3 install -q --user --break-system-packages -r "$ROOT_DIR/requirements.txt" 2>/dev/null || \
        pip3 install -q -r "$ROOT_DIR/requirements.txt" 2>/dev/null || \
        python3 -m pip install -q --break-system-packages -r "$ROOT_DIR/requirements.txt" 2>/dev/null || \
        python3 -m pip install -q -r "$ROOT_DIR/requirements.txt"
    fi
    echo -e "${GREEN}✓ Python dependencies verified (curl_cffi, beautifulsoup4, requests).${NC}"
fi

# Check PM2
if ! command -v pm2 &>/dev/null; then
    echo -e "${YELLOW}Installing PM2 Process Manager globally...${NC}"
    npm install -g pm2 --silent || sudo npm install -g pm2 --silent
fi
echo -e "${GREEN}✓ PM2 detected:${NC} $(pm2 -v)"

# Setup Directory Skeletons
mkdir -p "$ROOT_DIR/jax-shared/data/logs"
mkdir -p "$ROOT_DIR/jax-shared/data/inventory"
mkdir -p "$ROOT_DIR/jax-whatsapp-agent/auth_info_baileys"
mkdir -p "$ROOT_DIR/jax-whatsapp-monitor/auth_info_monitor"
mkdir -p "$HOME/.config"

# Install Node dependencies across repo
echo -e "Installing Node modules..."
npm install --allow-git=all --silent
echo -e "${GREEN}✓ Node modules installed successfully.${NC}"

# ------------------------------------------------------------------------------
# STEP 2: Antigravity CLI Installation & Login
# ------------------------------------------------------------------------------
echo -e "\n${CYAN}${BOLD}[2/6] Configuring Antigravity CLI (Agent Brain)...${NC}"

if ! command -v agy &>/dev/null; then
    echo -e "${YELLOW}Antigravity CLI (agy) not found in PATH.${NC}"
    echo -e "Initiating official Antigravity CLI installer..."
    curl -fsSL https://antigravity.google/install.sh | bash || true
    export PATH="$HOME/.local/bin:$PATH"
fi

if command -v agy &>/dev/null; then
    AGY_VER=$(timeout 3 env DBUS_SESSION_BUS_ADDRESS=disabled: agy --version 2>/dev/null || echo 'Detected')
    echo -e "${GREEN}✓ Antigravity CLI is installed:${NC} $AGY_VER"
    
    # Check if CLI requires interactive login
    echo -e "${BLUE}Verifying Antigravity authentication...${NC}"
    if ! timeout 5 env DBUS_SESSION_BUS_ADDRESS=disabled: agy whoami &>/dev/null && ! timeout 5 env DBUS_SESSION_BUS_ADDRESS=disabled: agy auth status &>/dev/null; then
        echo -e "${YELLOW}Starting interactive Antigravity CLI authentication:${NC}"
        agy auth login || agy install || true
    else
        echo -e "${GREEN}✓ Antigravity CLI session is authenticated.${NC}"
    fi
else
    echo -e "${YELLOW}Note: Antigravity CLI installation was skipped or custom binary is used.${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 3: Salesperson Profile & Phone Setup
# ------------------------------------------------------------------------------
echo -e "
${CYAN}${BOLD}[3/6] Salesperson Identity & Configuration${NC}"
echo -e "${BLUE}──────────────────────────────────────────────────────────────────${NC}"
echo -e "${BOLD}SALES COMPANION FIRST POLICY:${NC}"
echo -e " • All customer chats stay strictly on YOUR personal/sales phone."
echo -e " • Zero unprompted auto-replies to customers."
echo -e " • The AI only sends WhatsApp messages to customers when YOU instruct it."
echo -e "${BLUE}──────────────────────────────────────────────────────────────────${NC}"

SALESPERSON_NAME=""
while [ -z "$SALESPERSON_NAME" ]; do
    read -r -p "Salesperson Preferred Name (required, e.g. John): " INPUT_NAME
    SALESPERSON_NAME=${INPUT_NAME:-""}
done

DEALERSHIP_NAME=""
while [ -z "$DEALERSHIP_NAME" ]; do
    read -r -p "Dealership Branch Name (required, e.g. City Motors): " INPUT_BRANCH
    DEALERSHIP_NAME=${INPUT_BRANCH:-""}
done

OWNER_PHONE=""
while [ -z "$OWNER_PHONE" ]; do
    read -r -p "Salesperson Primary WhatsApp Number (required, e.g. 27821234567): " INPUT_PHONE
    OWNER_PHONE=${INPUT_PHONE:-""}
    if [ -z "$OWNER_PHONE" ]; then
        echo -e "${RED}Error: Salesperson Primary WhatsApp Number is required.${NC}"
    fi
done

echo -e "
${BLUE}--- Optional Telegram Integration ---${NC}"
read -r -p "Telegram Bot Token (press enter to skip): " TELEGRAM_BOT_TOKEN
read -r -p "Telegram Owner User ID (press enter to skip): " TELEGRAM_OWNER_ID

# ------------------------------------------------------------------------------
# STEP 4: Pair WhatsApp QR Codes
# ------------------------------------------------------------------------------
echo -e "\n${CYAN}${BOLD}[4/6] WhatsApp Setup - Pairing Devices${NC}"

# Part A: Salesperson Companion Monitor (Where Customer Chats Happen)
echo -e "\n${BLUE}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}QR CODE 1: SALESPERSON COMPANION MONITOR (YOUR PHONE)${NC}"
echo -e "Connects as a companion device to YOUR phone ($OWNER_PHONE)."
echo -e " • Tracks ongoing deals and customer replies passively."
echo -e " • Never auto-replies on its own."
echo -e " • Sends customer follow-ups ONLY when you explicitly command the agent."
echo -e "${BLUE}══════════════════════════════════════════════════════════════════${NC}"

MONITOR_CREDS="$ROOT_DIR/jax-whatsapp-monitor/auth_info_monitor/creds.json"
if [ -f "$MONITOR_CREDS" ]; then
    echo -e "${GREEN}✓ Existing WhatsApp session detected for Salesperson Monitor.${NC}"
    read -r -p "Do you want to re-pair the Salesperson WhatsApp Monitor? (y/N): " REPAIR_MONITOR
    if [[ "$REPAIR_MONITOR" =~ ^[Yy]$ ]]; then
        rm -rf "$ROOT_DIR/jax-whatsapp-monitor/auth_info_monitor"/*
        node "$ROOT_DIR/scripts/pair_session.mjs" --target=monitor
    fi
else
    echo -e "Please scan the QR code with YOUR sales phone ($OWNER_PHONE):"
    node "$ROOT_DIR/scripts/pair_session.mjs" --target=monitor
fi

# Part B: AI Agent Private Co-Pilot (Internal Assistant)
echo -e "\n${BLUE}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}QR CODE 2: AI AGENT PRIVATE BOT NUMBER${NC}"
echo -e "This is a private AI assistant number exclusively for YOU to message."
echo -e " • Message this bot to give instructions: e.g. 'follow up with Joseph'"
echo -e " • It does NOT converse with customers directly."
echo -e " • Auto-replies to unknown numbers are strictly blocked."
echo -e "${BLUE}══════════════════════════════════════════════════════════════════${NC}"

AGENT_CREDS="$ROOT_DIR/jax-whatsapp-agent/auth_info_baileys/creds.json"
if [ -f "$AGENT_CREDS" ]; then
    echo -e "${GREEN}✓ Existing WhatsApp session detected for AI Agent.${NC}"
    read -r -p "Do you want to re-pair the AI Agent WhatsApp bot? (y/N): " REPAIR_AGENT
    if [[ "$REPAIR_AGENT" =~ ^[Yy]$ ]]; then
        rm -rf "$ROOT_DIR/jax-whatsapp-agent/auth_info_baileys"/*
        node "$ROOT_DIR/scripts/pair_session.mjs" --target=agent
    fi
else
    echo -e "Please scan the QR code to pair the AI Agent Private Bot:"
    node "$ROOT_DIR/scripts/pair_session.mjs" --target=agent
fi

# ------------------------------------------------------------------------------
# STEP 5: Dealership CRM / Portal Onboarding
# ------------------------------------------------------------------------------
echo -e "\n${CYAN}${BOLD}[5/6] Dealership CRM / Portal Setup${NC}"
echo -e "${BLUE}──────────────────────────────────────────────────────────────────${NC}"
echo -e "Connect to your dealership CRM portal (dealer-portal.example.com / dealer-portal.example.com)"
echo -e "to automate daily diary follow-ups, dual-logging, and customer records."
echo -e "${BLUE}──────────────────────────────────────────────────────────────────${NC}"

CONFIG_ENV="$HOME/.config/dealer_credentials.env"
CURR_USER=""
CURR_PASS=""

if [ -f "$CONFIG_ENV" ]; then
    CURR_USER=$(grep -E "^CRM_USERNAME=" "$CONFIG_ENV" | cut -d'=' -f2- | tr -d '"'"'")
fi

PROMPT_USER="Dealership CRM Username"
[ -n "$CURR_USER" ] && PROMPT_USER="Dealership CRM Username [Current: $CURR_USER]"
read -r -p "$PROMPT_USER: " INPUT_USER
CRM_USER=${INPUT_USER:-$CURR_USER}

read -r -s -p "Dealership CRM Password (hidden): " INPUT_PASS
echo ""
CRM_PASS=${INPUT_PASS:-$CURR_PASS}

# Save credentials to ~/.config/dealer_credentials.env and repo .env
cat << ENV_EOF > "$CONFIG_ENV"
# Dealership CRM / Dealer Portal Credentials
CRM_USERNAME=$CRM_USER
CRM_PASSWORD=$CRM_PASS
SALESPERSON_NAME=$SALESPERSON_NAME
OWNER_PHONE_NUMBER=$OWNER_PHONE
DEALERSHIP_NAME=$DEALERSHIP_NAME
ENV_EOF
chmod 600 "$CONFIG_ENV"

cat << ENV_LOCAL > "$ROOT_DIR/.env"
# JAX Dealership OS Runtime Environment
CRM_USERNAME=$CRM_USER
CRM_PASSWORD=$CRM_PASS
SALESPERSON_NAME=$SALESPERSON_NAME
OWNER_PHONE_NUMBER=$OWNER_PHONE
RESTRICT_TO_OWNER=true
DEALERSHIP_NAME=$DEALERSHIP_NAME
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
OWNER_USER_ID=$TELEGRAM_OWNER_ID
API_PORT=9095
HEALTH_PORT=9090
SQLITE_DB_PATH=$ROOT_DIR/jax-shared/data/prospects.db
AUTH_DIR=$ROOT_DIR/jax-whatsapp-monitor/auth_info_monitor
AGENT_AUTH_DIR=$ROOT_DIR/jax-whatsapp-agent/auth_info_baileys
ENV_LOCAL
chmod 600 "$ROOT_DIR/.env"

echo -e "${GREEN}✓ Credentials stored securely in ~/.config/dealer_credentials.env${NC}"

# Test CRM connection
echo -e "${BLUE}Testing CRM credentials against portal...${NC}"
LOGIN_OUT=$(python3 "$ROOT_DIR/skills/autohub-portal/scripts/portal_login.py" 2>&1 || true)
if echo "$LOGIN_OUT" | grep -iq "Session Cookies"; then
    echo -e "${GREEN}✓ SUCCESS: Authenticated successfully with CRM portal!${NC}"
    echo -e "${BLUE}Synchronizing initial diary entries so harness is primed...${NC}"
    python3 "$ROOT_DIR/skills/autohub-portal/scripts/populate_all_34_diaries.py" >/dev/null 2>&1 || true
    echo -e "${GREEN}✓ Diary synchronization initialized.${NC}"
else
    echo -e "${YELLOW}Warning: Portal test did not confirm session. Please double-check credentials if diaries do not sync.${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 6: Launch & Daemonize via PM2
# ------------------------------------------------------------------------------
echo -e "\n${CYAN}${BOLD}[6/6] Launching Services with PM2...${NC}"

# Stop any previously running ecosystem
pm2 delete ecosystem.config.cjs 2>/dev/null || true

# Start clean ecosystem
pm2 start "$ROOT_DIR/ecosystem.config.cjs"
pm2 save

echo -e "\n${GREEN}${BOLD}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}     🎉  ONBOARDING COMPLETE - DEALERSHIP OS IS ONLINE!         ${NC}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}Current Service Status:${NC}"
pm2 status

echo -e "\n${BOLD}How the System Operates:${NC}"
echo -e " 1. All customer WhatsApp chats stay on ${GREEN}YOUR sales phone ($OWNER_PHONE)${NC}."
echo -e " 2. ${YELLOW}Zero auto-replies${NC} to customers. The monitor only observes and indexes."
echo -e " 3. You command the AI Agent by messaging its bot number (or running scripts)."
echo -e " 4. Approved follow-ups are sent to customers ${GREEN}FROM YOUR NUMBER${NC}."
echo -e " 5. All touchpoints are dual-logged into CRM and diary follow-ups moved automatically."
echo ""
echo -e "${BOLD}Quick Commands:${NC}"
echo -e " • Check live logs:    ${YELLOW}pm2 logs${NC}"
echo -e " • Check monitor API:  ${YELLOW}curl -s http://127.0.0.1:9095/prospects | jq .${NC}"
echo -e " • Restart services:  ${YELLOW}pm2 restart all${NC}"
echo ""
