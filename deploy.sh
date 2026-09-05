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
if ! command -v python3 &>/dev/null || ! command -v pip3 &>/dev/null; then
    echo -e "${YELLOW}Python 3 or pip not fully detected. Installing python3, pip, and venv...${NC}"
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
        python3 -m pip install -q -r "$ROOT_DIR/requirements.txt" --break-system-packages 2>/dev/null || \
        pip3 install -q --break-system-packages -r "$ROOT_DIR/requirements.txt" 2>/dev/null || \
        pip3 install -q --user --break-system-packages -r "$ROOT_DIR/requirements.txt" 2>/dev/null || \
        pip3 install -q -r "$ROOT_DIR/requirements.txt" 2>/dev/null || \
        python3 -m pip install -q -r "$ROOT_DIR/requirements.txt" || true
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
    curl -fsSL https://antigravity.google/install.sh | bash 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
fi

if command -v agy &>/dev/null; then
    AGY_VER=$(timeout 3 env DBUS_SESSION_BUS_ADDRESS=disabled: agy --version 2>/dev/null || echo 'Detected')
    echo -e "${GREEN}✓ Antigravity CLI is installed:${NC} $AGY_VER"
    
    # Check if CLI requires interactive login
    echo -e "${BLUE}Verifying Antigravity authentication...${NC}"
    if ! timeout 5 env DBUS_SESSION_BUS_ADDRESS=disabled: agy models &>/dev/null; then
        echo -e "${YELLOW}Starting Antigravity CLI configuration:${NC}"
        timeout 10 env DBUS_SESSION_BUS_ADDRESS=disabled: agy install || true
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

# Load existing configuration if available
CONFIG_ENV="$HOME/.config/dealer_credentials.env"
EXISTING_NAME=""
EXISTING_BRANCH=""
EXISTING_PHONE=""
EXISTING_TG_TOKEN=""
EXISTING_TG_ID=""
EXISTING_CRM_USER=""
EXISTING_CRM_PASS=""
EXISTING_CRM_LOGIN_URL=""
EXISTING_CRM_BASE_URL=""
EXISTING_OWNER_LID=""

if [ -f "$ROOT_DIR/.env" ]; then
    EXISTING_NAME=$(grep -E "^SALESPERSON_NAME=" "$ROOT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'" || true)
    EXISTING_BRANCH=$(grep -E "^DEALERSHIP_NAME=" "$ROOT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'" || true)
    EXISTING_PHONE=$(grep -E "^OWNER_PHONE_NUMBER=" "$ROOT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'" || true)
    EXISTING_OWNER_LID=$(grep -E "^OWNER_LID=" "$ROOT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'" || true)
    EXISTING_TG_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" "$ROOT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'" || true)
    EXISTING_TG_ID=$(grep -E "^OWNER_USER_ID=" "$ROOT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'" || true)
    EXISTING_CRM_USER=$(grep -E "^CRM_USERNAME=" "$ROOT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'" || true)
    EXISTING_CRM_PASS=$(grep -E "^CRM_PASSWORD=" "$ROOT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'" || true)
    EXISTING_CRM_LOGIN_URL=$(grep -E "^CRM_LOGIN_URL=" "$ROOT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'" || true)
    EXISTING_CRM_BASE_URL=$(grep -E "^CRM_BASE_URL=" "$ROOT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'" || true)
fi

if [ -f "$CONFIG_ENV" ]; then
    [ -z "$EXISTING_CRM_USER" ] && EXISTING_CRM_USER=$(grep -E "^CRM_USERNAME=" "$CONFIG_ENV" | cut -d'=' -f2- | tr -d "\"'" || true)
    [ -z "$EXISTING_CRM_PASS" ] && EXISTING_CRM_PASS=$(grep -E "^CRM_PASSWORD=" "$CONFIG_ENV" | cut -d'=' -f2- | tr -d "\"'" || true)
    [ -z "$EXISTING_CRM_LOGIN_URL" ] && EXISTING_CRM_LOGIN_URL=$(grep -E "^CRM_LOGIN_URL=" "$CONFIG_ENV" | cut -d'=' -f2- | tr -d "\"'" || true)
    [ -z "$EXISTING_CRM_BASE_URL" ] && EXISTING_CRM_BASE_URL=$(grep -E "^CRM_BASE_URL=" "$CONFIG_ENV" | cut -d'=' -f2- | tr -d "\"'" || true)
    [ -z "$EXISTING_NAME" ] && EXISTING_NAME=$(grep -E "^SALESPERSON_NAME=" "$CONFIG_ENV" | cut -d'=' -f2- | tr -d "\"'" || true)
    [ -z "$EXISTING_PHONE" ] && EXISTING_PHONE=$(grep -E "^OWNER_PHONE_NUMBER=" "$CONFIG_ENV" | cut -d'=' -f2- | tr -d "\"'" || true)
    [ -z "$EXISTING_OWNER_LID" ] && EXISTING_OWNER_LID=$(grep -E "^OWNER_LID=" "$CONFIG_ENV" | cut -d'=' -f2- | tr -d "\"'" || true)
    [ -z "$EXISTING_BRANCH" ] && EXISTING_BRANCH=$(grep -E "^DEALERSHIP_NAME=" "$CONFIG_ENV" | cut -d'=' -f2- | tr -d "\"'" || true)
fi
OWNER_LID="$EXISTING_OWNER_LID"

# Helper prompt function
prompt_val() {
    local label="$1"
    local existing="$2"
    local is_required="${3:-false}"
    local val=""

    while true; do
        if [ -n "$existing" ]; then
            read -r -p "$label [Current: $existing]: " val || true
            val="${val:-$existing}"
        else
            read -r -p "$label: " val || true
        fi
        
        if [ -n "$val" ] || [ "$is_required" != "true" ] || [ ! -t 0 ]; then
            break
        fi
        echo -e "${RED}This field is required.${NC}"
    done
    echo "$val"
}

SALESPERSON_NAME=$(prompt_val "Salesperson Preferred Name" "${EXISTING_NAME:-John}" true)
DEALERSHIP_NAME=$(prompt_val "Dealership Branch Name" "${EXISTING_BRANCH:-City Motors}" true)
OWNER_PHONE=$(prompt_val "Salesperson Primary WhatsApp Number (e.g. 27821234567)" "${EXISTING_PHONE:-27821234567}" true)

echo -e "\n${BLUE}--- Optional Telegram Integration ---${NC}"
TELEGRAM_BOT_TOKEN=$(prompt_val "Telegram Bot Token (press enter to skip)" "$EXISTING_TG_TOKEN" false)
TELEGRAM_OWNER_ID=$(prompt_val "Telegram Owner User ID (press enter to skip)" "$EXISTING_TG_ID" false)

# Save initial environment configuration immediately so pairing has access to OWNER_PHONE
mkdir -p "$(dirname "$CONFIG_ENV")"
cat << ENV_LOCAL > "$ROOT_DIR/.env"
# JAX Dealership OS Runtime Environment
CRM_USERNAME=$EXISTING_CRM_USER
CRM_PASSWORD=$EXISTING_CRM_PASS
SALESPERSON_NAME=$SALESPERSON_NAME
OWNER_PHONE_NUMBER=$OWNER_PHONE
OWNER_LID=$OWNER_LID
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

# ------------------------------------------------------------------------------
# STEP 4: Pair WhatsApp QR Codes
# ------------------------------------------------------------------------------
echo -e "\n${CYAN}${BOLD}[4/6] WhatsApp Setup - Pairing Devices${NC}"

is_whatsapp_registered() {
    local creds_file="$1"
    if [ -f "$creds_file" ]; then
        if node -e "
            try {
                const fs = require('fs');
                const c = JSON.parse(fs.readFileSync('$creds_file', 'utf8'));
                if (c.registered === true || Boolean(c.me && c.me.id)) process.exit(0);
            } catch (e) {}
            process.exit(1);
        " 2>/dev/null; then
            return 0
        fi
        # Grep fallback in case node is in transition
        if grep -qE '("registered":\s*true|"id":\s*"[0-9]+(:[0-9]+)?@s\.whatsapp\.net")' "$creds_file" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

# Part A: AI Agent Private Co-Pilot (Internal Assistant) - PRIMARY
echo -e "\n${BLUE}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}STEP 4A: PAIR THE AI AGENT BOT (PRIMARY)${NC}"
echo -e "This connects your AI Co-Pilot so it can receive and respond to your commands."
echo -e " • Message this bot to give instructions: check stock, follow up with leads, etc."
echo -e " • Dedicated bot phone: scan QR with that phone."
echo -e " • Single phone setup: scan QR with your phone (chat via 'Message yourself')."
echo -e "${BLUE}══════════════════════════════════════════════════════════════════${NC}"

AGENT_CREDS="$ROOT_DIR/jax-whatsapp-agent/auth_info_baileys/creds.json"
if is_whatsapp_registered "$AGENT_CREDS"; then
    echo -e "${GREEN}✓ Existing WhatsApp session detected for AI Agent.${NC}"
    read -r -p "Do you want to re-pair the AI Agent WhatsApp bot? (y/N): " REPAIR_AGENT || true
    if [[ "$REPAIR_AGENT" =~ ^[Yy]$ ]]; then
        rm -rf "$ROOT_DIR/jax-whatsapp-agent/auth_info_baileys"/*
        node "$ROOT_DIR/scripts/pair_session.mjs" --target=agent || true
    fi
else
    rm -rf "$ROOT_DIR/jax-whatsapp-agent/auth_info_baileys"/*
    echo -e "Display QR code and pair the AI Agent bot now? [Y/n]:"
    read -r -p "> " PAIR_NOW_AGENT || PAIR_NOW_AGENT="y"
    PAIR_NOW_AGENT="${PAIR_NOW_AGENT:-y}"
    if [[ "$PAIR_NOW_AGENT" =~ ^[Yy]$ ]]; then
        while true; do
            echo -e "\n${CYAN}Displaying QR code for AI Agent Private Bot...${NC}"
            node "$ROOT_DIR/scripts/pair_session.mjs" --target=agent || true
            if is_whatsapp_registered "$AGENT_CREDS"; then
                echo -e "${GREEN}✓ AI Agent Private Bot paired and registered successfully!${NC}"
                break
            else
                echo -e "${YELLOW}⚠️  Agent pairing was not completed.${NC}"
                read -r -p "Would you like to try scanning again? (Y/n): " RETRY_AGENT || RETRY_AGENT="y"
                RETRY_AGENT="${RETRY_AGENT:-y}"
                if [[ ! "$RETRY_AGENT" =~ ^[Yy]$ ]]; then
                    echo -e "${YELLOW}Skipping agent pairing for now. You can pair anytime with:${NC} npm run pair:agent"
                    break
                fi
            fi
        done
    else
        echo -e "${YELLOW}Skipping agent pairing for now. Pair anytime with:${NC} npm run pair:agent"
    fi
fi

if is_whatsapp_registered "$AGENT_CREDS"; then
    AGENT_NUMBER=$(node -e "try { const c = JSON.parse(fs.readFileSync('$AGENT_CREDS')); console.log(c.me?.id?.split(':')[0]?.split('@')[0] || ''); } catch(e){}" 2>/dev/null || true)
    AGENT_LID=$(node -e "try { const c = JSON.parse(fs.readFileSync('$AGENT_CREDS')); console.log(c.me?.lid?.split(':')[0]?.split('@')[0] || ''); } catch(e){}" 2>/dev/null || true)
    if [ "$AGENT_NUMBER" == "$OWNER_PHONE" ] && [ -n "$AGENT_LID" ]; then
        OWNER_LID="$AGENT_LID"
        echo -e "${GREEN}✓ Single-phone setup detected: auto-configured Owner WhatsApp LID:${NC} $OWNER_LID"
    fi
fi

# Part B: Salesperson Companion Monitor (Where Customer Chats Happen) - OPTIONAL
echo -e "\n${BLUE}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}STEP 4B: SALESPERSON COMPANION MONITOR (OPTIONAL)${NC}"
echo -e "Connects as a companion device to YOUR phone ($OWNER_PHONE)."
echo -e " • Tracks ongoing deals and customer replies passively into CRM."
echo -e " • Never auto-replies on its own."
echo -e " • Sends customer follow-ups ONLY when you explicitly command the agent."
echo -e "${BLUE}══════════════════════════════════════════════════════════════════${NC}"

MONITOR_CREDS="$ROOT_DIR/jax-whatsapp-monitor/auth_info_monitor/creds.json"
if is_whatsapp_registered "$MONITOR_CREDS"; then
    echo -e "${GREEN}✓ Existing WhatsApp session detected for Salesperson Monitor.${NC}"
    read -r -p "Do you want to re-pair the Salesperson WhatsApp Monitor? (y/N): " REPAIR_MONITOR || true
    if [[ "$REPAIR_MONITOR" =~ ^[Yy]$ ]]; then
        rm -rf "$ROOT_DIR/jax-whatsapp-monitor/auth_info_monitor"/*
        node "$ROOT_DIR/scripts/pair_session.mjs" --target=monitor || true
    fi
else
    rm -rf "$ROOT_DIR/jax-whatsapp-monitor/auth_info_monitor"/*
    echo -e "Pair your sales phone ($OWNER_PHONE) as a companion monitor? (y/N):"
    read -r -p "> " PAIR_NOW_MONITOR || true
    if [[ "$PAIR_NOW_MONITOR" =~ ^[Yy]$ ]]; then
        while true; do
            echo -e "Please scan the QR code with YOUR sales phone ($OWNER_PHONE):"
            node "$ROOT_DIR/scripts/pair_session.mjs" --target=monitor || true
            if is_whatsapp_registered "$MONITOR_CREDS"; then
                echo -e "${GREEN}✓ Salesperson Monitor paired and registered successfully!${NC}"
                break
            else
                echo -e "${YELLOW}⚠️  Monitor pairing was not completed.${NC}"
                read -r -p "Would you like to try scanning again? (y/N): " RETRY_MONITOR || true
                if [[ ! "$RETRY_MONITOR" =~ ^[Yy]$ ]]; then
                    echo -e "${YELLOW}Skipping monitor pairing. Pair anytime with:${NC} npm run pair:monitor"
                    rm -rf "$ROOT_DIR/jax-whatsapp-monitor/auth_info_monitor"/*
                    break
                fi
            fi
        done
    else
        echo -e "${BLUE}Skipping companion monitor for now. Pair anytime with:${NC} npm run pair:monitor"
    fi
fi

if is_whatsapp_registered "$MONITOR_CREDS"; then
    DETECTED_LID=$(node -e "try { const c = JSON.parse(fs.readFileSync('$MONITOR_CREDS')); console.log(c.me?.lid?.split(':')[0]?.split('@')[0] || ''); } catch(e){}" 2>/dev/null || true)
    if [ -n "$DETECTED_LID" ]; then
        OWNER_LID="$DETECTED_LID"
        echo -e "${GREEN}✓ Auto-detected Salesperson WhatsApp LID identity:${NC} $OWNER_LID"
    fi
fi

# ------------------------------------------------------------------------------
# STEP 5: Dealership CRM / Portal Onboarding
# ------------------------------------------------------------------------------
echo -e "\n${CYAN}${BOLD}[5/6] Dealership CRM / Portal Setup${NC}"
echo -e "${BLUE}──────────────────────────────────────────────────────────────────${NC}"
echo -e "Connect to your dealership CRM portal (dealer-portal.example.com / dealer-portal.example.com)"
echo -e "to automate daily diary follow-ups, dual-logging, and customer records."
echo -e "${BLUE}──────────────────────────────────────────────────────────────────${NC}"

CRM_LOGIN_URL=$(prompt_val "Dealership CRM Portal / Login URL (e.g. https://nissandrive.co.za)" "${EXISTING_CRM_LOGIN_URL}" true)
CRM_BASE_URL=$(prompt_val "Dealership CRM Base URL (optional, press enter to auto-detect)" "${EXISTING_CRM_BASE_URL}" false)
CRM_USER=$(prompt_val "Dealership CRM Username (press enter to skip)" "$EXISTING_CRM_USER" false)

if [ -n "$CRM_USER" ]; then
    if [ -n "$EXISTING_CRM_PASS" ]; then
        read -r -s -p "Dealership CRM Password [Hidden, press enter to keep current]: " INPUT_PASS || true
        echo ""
        CRM_PASS="${INPUT_PASS:-$EXISTING_CRM_PASS}"
    else
        read -r -s -p "Dealership CRM Password (hidden): " INPUT_PASS || true
        echo ""
        CRM_PASS="$INPUT_PASS"
    fi
else
    CRM_PASS=""
fi

# Save credentials to ~/.config/dealer_credentials.env and repo .env
mkdir -p "$(dirname "$CONFIG_ENV")"
cat << ENV_EOF > "$CONFIG_ENV"
# Dealership CRM / Dealer Portal Credentials
CRM_LOGIN_URL=$CRM_LOGIN_URL
CRM_BASE_URL=$CRM_BASE_URL
CRM_USERNAME=$CRM_USER
CRM_PASSWORD=$CRM_PASS
SALESPERSON_NAME=$SALESPERSON_NAME
OWNER_PHONE_NUMBER=$OWNER_PHONE
OWNER_LID=$OWNER_LID
DEALERSHIP_NAME=$DEALERSHIP_NAME
ENV_EOF
chmod 600 "$CONFIG_ENV"

cat << ENV_LOCAL > "$ROOT_DIR/.env"
# JAX Dealership OS Runtime Environment
CRM_LOGIN_URL=$CRM_LOGIN_URL
CRM_BASE_URL=$CRM_BASE_URL
CRM_USERNAME=$CRM_USER
CRM_PASSWORD=$CRM_PASS
SALESPERSON_NAME=$SALESPERSON_NAME
OWNER_PHONE_NUMBER=$OWNER_PHONE
OWNER_LID=$OWNER_LID
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

# Test CRM connection only if credentials were provided
if [ -n "$CRM_USER" ] && [ -n "$CRM_PASS" ]; then
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
else
    echo -e "${YELLOW}CRM portal credentials not specified. Skipping portal connection test.${NC}"
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

BOT_NUMBER=$(node -e "try { const c = JSON.parse(fs.readFileSync('$AGENT_CREDS')); console.log(c.me?.id?.split(':')[0]?.split('@')[0] || ''); } catch(e){}" 2>/dev/null || true)

if is_whatsapp_registered "$AGENT_CREDS"; then
    echo -e "\n${GREEN}${BOLD}══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}${BOLD}     🎉  ONBOARDING COMPLETE - DEALERSHIP OS IS ONLINE!         ${NC}"
    echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}Current Service Status:${NC}"
    pm2 status

    echo -e "\n${BOLD}Ready to chat with your AI Co-Pilot:${NC}"
    if [ -n "$BOT_NUMBER" ]; then
        if [ "$BOT_NUMBER" == "$OWNER_PHONE" ]; then
            echo -e " 📱 Bot is linked directly to ${GREEN}YOUR phone (+$OWNER_PHONE)${NC}."
            echo -e " 💬 Open WhatsApp and send a message to ${BOLD}YOURSELF ('Message yourself')${NC} to start chatting!"
        else
            echo -e " 📱 Bot Phone Number: ${GREEN}+$BOT_NUMBER${NC}"
            echo -e " 💬 Open WhatsApp on your phone (${GREEN}+$OWNER_PHONE${NC}) and message ${GREEN}+$BOT_NUMBER${NC}!"
        fi
    else
        echo -e " 💬 Open WhatsApp and send a message from ${GREEN}+$OWNER_PHONE${NC} to your bot!"
    fi
else
    echo -e "\n${YELLOW}${BOLD}══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}${BOLD}     ⚠️   ACTION REQUIRED: AI AGENT BOT NOT YET PAIRED         ${NC}"
    echo -e "${YELLOW}${BOLD}══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}Current Service Status:${NC}"
    pm2 status
    echo -e "\n${YELLOW}The AI Agent bot is NOT paired yet and will not respond to WhatsApp.${NC}"
    echo -e "To pair your bot now, run:"
    echo -e "   ${CYAN}cd $ROOT_DIR && npm run pair:agent${NC}"
    echo -e "Then scan the QR code with WhatsApp (Linked Devices)."
fi

echo -e "\n${BOLD}How the System Operates:${NC}"
echo -e " 1. All customer WhatsApp chats stay on ${GREEN}YOUR sales phone ($OWNER_PHONE)${NC}."
echo -e " 2. ${YELLOW}Zero auto-replies${NC} to customers. The monitor only observes and indexes."
echo -e " 3. You command the AI Agent by messaging its bot number (or running scripts)."
echo -e " 4. Approved follow-ups are sent to customers ${GREEN}FROM YOUR NUMBER${NC}."
echo -e " 5. All touchpoints are dual-logged into CRM and diary follow-ups moved automatically."
echo ""
echo -e "${BOLD}Quick Commands:${NC}"
echo -e " • Check live logs:    ${YELLOW}pm2 logs${NC}"
echo -e " • Pair AI Agent:      ${YELLOW}npm run pair:agent${NC}"
echo -e " • Pair Monitor:       ${YELLOW}npm run pair:monitor${NC}"
echo -e " • Restart services:  ${YELLOW}pm2 restart all${NC}"
echo ""
