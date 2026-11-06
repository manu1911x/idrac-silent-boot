#!/usr/bin/env bash

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${BLUE}  idrac-silent-boot & Homarr Kiosk Installer (Raspberry Pi)       ${NC}"
echo -e "${BLUE}==================================================================${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] Please run this script as root: sudo ./install.sh${NC}"
    exit 1
fi

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "\n${YELLOW}[1/5] Installing required system packages...${NC}"
apt-get update -y
apt-get install -y \
    ipmitool \
    python3 \
    python3-yaml \
    python3-gpiozero \
    unclutter \
    xdotool \
    curl

if ! command -v chromium-browser &>/dev/null && ! command -v chromium &>/dev/null; then
    echo "Installing Chromium browser..."
    apt-get install -y chromium-browser || apt-get install -y chromium
fi

echo -e "\n${YELLOW}[2/5] Setting up project files in ${INSTALL_DIR}...${NC}"

if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo "Creating .env from .env.example..."
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    chown "$REAL_USER:$REAL_USER" "$INSTALL_DIR/.env"
    echo -e "${GREEN}✓ Created .env with 600 permissions.${NC}"
else
    echo -e "${GREEN}✓ Existing .env detected. Keeping.${NC}"
fi

if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    echo "Creating config.yaml from config.example.yaml..."
    cp "$INSTALL_DIR/config.example.yaml" "$INSTALL_DIR/config.yaml"
    chown "$REAL_USER:$REAL_USER" "$INSTALL_DIR/config.yaml"
    echo -e "${GREEN}✓ Created config.yaml.${NC}"
else
    echo -e "${GREEN}✓ Existing config.yaml detected. Keeping.${NC}"
fi

chmod +x "$INSTALL_DIR/main.py"
chmod +x "$INSTALL_DIR/kiosk/launch_kiosk.sh"
ln -sf "$INSTALL_DIR/main.py" /usr/local/bin/homelabctl

echo -e "\n${YELLOW}[3/5] Installing Systemd service idrac-silent-boot.service...${NC}"
SERVICE_DEST="/etc/systemd/system/idrac-silent-boot.service"

sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$INSTALL_DIR/systemd/idrac-silent-boot.service" > "$SERVICE_DEST"

systemctl daemon-reload
systemctl enable idrac-silent-boot.service
echo -e "${GREEN}✓ Systemd service installed and enabled for system boot.${NC}"

echo -e "\n${YELLOW}[4/5] Configuring Homarr Kiosk autostart for user ${REAL_USER}...${NC}"
AUTOSTART_DIR="$REAL_HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat <<EOF > "$AUTOSTART_DIR/homarr-kiosk.desktop"
[Desktop Entry]
Type=Application
Name=Homarr Kiosk
Comment=Homarr Kiosk Dashboard for Raspberry Pi
Exec=$INSTALL_DIR/kiosk/launch_kiosk.sh
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
EOF

chown -R "$REAL_USER:$REAL_USER" "$AUTOSTART_DIR"
chmod 644 "$AUTOSTART_DIR/homarr-kiosk.desktop"
echo -e "${GREEN}✓ Autostart configured at $AUTOSTART_DIR/homarr-kiosk.desktop.${NC}"

echo -e "\n${YELLOW}[5/5] Installation Summary:${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}  Installation completed successfully!                            ${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo -e "Next steps:"
echo -e "1. Edit .env with your actual IPMI hosts, credentials, Homarr URL, and API_TOKEN:"
echo -e "   ${YELLOW}nano $INSTALL_DIR/.env${NC}"
echo -e ""
echo -e "2. Configure GPIO pins and fan curves in config.yaml:"
echo -e "   ${YELLOW}nano $INSTALL_DIR/config.yaml${NC}"
echo -e ""
echo -e "3. Control your homelab via CLI or HTTP API:"
echo -e "   ${YELLOW}homelabctl status${NC}"
echo -e "   ${YELLOW}homelabctl power-off${NC}"
echo -e "   ${YELLOW}homelabctl power-on${NC}"
echo -e ""
echo -e "4. Start or restart the service:"
echo -e "   ${YELLOW}sudo systemctl restart idrac-silent-boot.service${NC}"
echo -e "=================================================================="
