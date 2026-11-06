#!/usr/bin/env bash
# ==============================================================================
# Script de instalación para idrac-silent-boot & Homarr Kiosk
# Compatible con Raspberry Pi OS Bookworm (Debian 12)
# ==============================================================================

set -e

# Colores para la salida
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}==================================================================${NC}"
echo -e "${BLUE}  Instalador de idrac-silent-boot & Homarr Kiosk (Raspberry Pi)   ${NC}"
echo -e "${BLUE}==================================================================${NC}"

# Verificar permisos de root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] Por favor ejecuta este script con privilegios de root: sudo ./install.sh${NC}"
    exit 1
fi

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "\n${YELLOW}[1/5] Instalando paquetes del sistema requeridos...${NC}"
apt-get update -y
apt-get install -y \
    ipmitool \
    python3 \
    python3-yaml \
    unclutter \
    curl

# Instalar Chromium si no está presente
if ! command -v chromium-browser &>/dev/null && ! command -v chromium &>/dev/null; then
    echo "Instalando chromium-browser..."
    apt-get install -y chromium-browser || apt-get install -y chromium
fi

echo -e "\n${YELLOW}[2/5] Configurando archivos del proyecto en ${INSTALL_DIR}...${NC}"

# Archivo de credenciales .env
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo "Creando .env a partir de .env.example..."
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    chown "$REAL_USER:$REAL_USER" "$INSTALL_DIR/.env"
    echo -e "${GREEN}✓ Archivo .env creado con permisos 600.${NC}"
else
    echo -e "${GREEN}✓ Archivo .env existente detectado. Conservando.${NC}"
fi

# Archivo de curva térmica config.yaml
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    echo "Creando config.yaml a partir de config.example.yaml..."
    cp "$INSTALL_DIR/config.example.yaml" "$INSTALL_DIR/config.yaml"
    chown "$REAL_USER:$REAL_USER" "$INSTALL_DIR/config.yaml"
    echo -e "${GREEN}✓ Archivo config.yaml creado.${NC}"
else
    echo -e "${GREEN}✓ Archivo config.yaml existente detectado. Conservando.${NC}"
fi

# Permisos ejecutables
chmod +x "$INSTALL_DIR/main.py"
chmod +x "$INSTALL_DIR/kiosk/launch_kiosk.sh"

echo -e "\n${YELLOW}[3/5] Instalando servicio Systemd idrac-silent-boot.service...${NC}"
SERVICE_DEST="/etc/systemd/system/idrac-silent-boot.service"

# Reemplazar __INSTALL_DIR__ con la ruta real del proyecto
sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$INSTALL_DIR/systemd/idrac-silent-boot.service" > "$SERVICE_DEST"

systemctl daemon-reload
systemctl enable idrac-silent-boot.service
echo -e "${GREEN}✓ Servicio Systemd instalado y habilitado para el inicio del sistema.${NC}"

echo -e "\n${YELLOW}[4/5] Configurando Autostart de Homarr Kiosk para el usuario ${REAL_USER}...${NC}"
AUTOSTART_DIR="$REAL_HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

# Generar archivo desktop con la ruta absoluta al script launch_kiosk.sh
cat <<EOF > "$AUTOSTART_DIR/homarr-kiosk.desktop"
[Desktop Entry]
Type=Application
Name=Homarr Kiosk
Comment=Pantalla Kiosk de Homarr para Raspberry Pi
Exec=$INSTALL_DIR/kiosk/launch_kiosk.sh
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
EOF

chown -R "$REAL_USER:$REAL_USER" "$AUTOSTART_DIR"
chmod 644 "$AUTOSTART_DIR/homarr-kiosk.desktop"
echo -e "${GREEN}✓ Autostart configurado en $AUTOSTART_DIR/homarr-kiosk.desktop.${NC}"

echo -e "\n${YELLOW}[5/5] Resumen de Instalación:${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}  ¡Instalación completada con éxito!                              ${NC}"
echo -e "${GREEN}==================================================================${NC}"
echo -e "Pasos siguientes:"
echo -e "1. Edita el archivo de credenciales con las IPs y contraseñas reales:"
echo -e "   ${YELLOW}nano $INSTALL_DIR/.env${NC}"
echo -e ""
echo -e "2. Ajusta la curva de ventiladores según tus preferencias si lo deseas:"
echo -e "   ${YELLOW}nano $INSTALL_DIR/config.yaml${NC}"
echo -e ""
echo -e "3. Recuerda configurar en la BIOS del Dell R730xd:"
echo -e "   ${YELLOW}System BIOS Settings -> AC Power Recovery -> Power Off (Remain Off)${NC}"
echo -e ""
echo -e "4. Puedes iniciar el servicio del backend manualmente ahora con:"
echo -e "   ${YELLOW}sudo systemctl start idrac-silent-boot.service${NC}"
echo -e "   Y ver los logs en tiempo real con:"
echo -e "   ${YELLOW}sudo journalctl -u idrac-silent-boot.service -f${NC}"
echo -e "=================================================================="
