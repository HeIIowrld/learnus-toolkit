#!/bin/bash
# Install LearnUs Web as a systemd service

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SERVICE_FILE="$SCRIPT_DIR/learnus-web.service"
SYSTEMD_DIR="/etc/systemd/system"

echo "Installing LearnUs Web as systemd service..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
   echo "❌ Please run as root: sudo ./install-service.sh"
   exit 1
fi

# Get current user (the one who ran sudo)
ACTUAL_USER=${SUDO_USER:-$USER}

# Update service file with correct paths and user
sed -i "s|/opt/learnus-web|$SCRIPT_DIR|g" "$SERVICE_FILE"
sed -i "s|User=%i|User=$ACTUAL_USER|g" "$SERVICE_FILE"

# Copy service file
cp "$SERVICE_FILE" "$SYSTEMD_DIR/learnus-web.service"

# Reload systemd
systemctl daemon-reload

# Enable service
systemctl enable learnus-web.service

echo "Service installed successfully!"
echo ""
echo "Service commands:"
echo "  Start:   sudo systemctl start learnus-web"
echo "  Stop:    sudo systemctl stop learnus-web"
echo "  Status:  sudo systemctl status learnus-web"
echo "  Logs:    sudo journalctl -u learnus-web -f"
echo ""

