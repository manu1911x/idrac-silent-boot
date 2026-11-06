import logging
from typing import Optional
from ..ipmi import IpmiClient

logger = logging.getLogger("silent_boot.mcafee")

class IntelRMM3:
    """
    Controlador para el Firewall McAfee basado en BMC Intel RMM3 vía IPMI LAN.
    Los ventiladores ya están fijos en hardware/BIOS; solo se gestiona el encendido.
    """
    def __init__(self, host: str, user: str, password: str, timeout_seconds: int = 8):
        self.ipmi = IpmiClient(host, user, password, timeout_seconds=timeout_seconds)
        self.host = host

    def is_online(self) -> bool:
        """Verifica si el BMC Intel RMM3 está respondiendo en la red."""
        return self.ipmi.is_reachable()

    def get_power_status(self) -> Optional[str]:
        return self.ipmi.get_power_status()

    def power_on(self) -> bool:
        """Envía el comando de encendido si el dispositivo no está encendido."""
        status = self.get_power_status()
        if status == "on":
            logger.info(f"[{self.host}] El firewall McAfee ya se encuentra ENCENDIDO.")
            return True
        logger.info(f"[{self.host}] Encendiendo firewall McAfee vía Intel RMM3...")
        return self.ipmi.power_on()
