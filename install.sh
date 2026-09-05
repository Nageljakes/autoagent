#!/usr/bin/env bash
set -e

# Reconnect stdin to controlling terminal if piped (e.g. curl ... | bash)
if [ ! -t 0 ] && [ -e /dev/tty ]; then
    exec < /dev/tty
fi

# JAX Dealership OS - One-Liner Installer
echo -e "\033[1;36m══════════════════════════════════════════════════════════════════\033[0m"
echo -e "\033[1;32m      🚘 Downloading and Installing JAX Dealership OS...      \033[0m"
echo -e "\033[1;36m══════════════════════════════════════════════════════════════════\033[0m"

# Require git
if ! command -v git &>/dev/null; then
    echo -e "\033[1;33mGit not found. Installing git...\033[0m"
    sudo apt-get update && sudo apt-get install -y git
fi

INSTALL_DIR="$HOME/autoagent"

if [ -d "$INSTALL_DIR" ]; then
    echo -e "\033[1;33mExisting installation found at $INSTALL_DIR. Updating...\033[0m"
    cd "$INSTALL_DIR"
    git pull origin main
else
    echo -e "\033[1;34mCloning repository to $INSTALL_DIR...\033[0m"
    REPO_URL="${AUTOAGENT_REPO_URL:-https://github.com/Nageljakes/autoagent.git}"
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Make deploy scripts executable
chmod +x deploy.sh setup.sh

echo -e "\033[1;32m✓ Download complete. Launching interactive onboarding harness...\033[0m"
exec ./deploy.sh "$@"
