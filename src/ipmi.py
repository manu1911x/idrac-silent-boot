import subprocess
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("silent_boot.ipmi")

class IpmiClient:
    """
    Cliente para ejecutar comandos IPMI vía ipmitool utilizando la interfaz lanplus.
    """
    def __init__(self, host: str, user: str, password: str, timeout_seconds: int = 8, interface: str = "lanplus"):
        self.host = host
        self.user = user
        self.password = password
        self.timeout = timeout_seconds
        self.interface = interface

    def _base_cmd(self) -> List[str]:
        return [
            "ipmitool",
            "-I", self.interface,
            "-H", self.host,
            "-U", self.user,
            "-P", self.password,
        ]

    def run_command(self, args: List[str], mask_args: bool = False) -> Tuple[bool, str]:
        """
        Ejecuta un comando ipmitool con timeout.
        Retorna (éxito: bool, salida_o_error: str).
        """
        cmd = self._base_cmd() + args
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout
            )
            if result.returncode == 0:
                return True, result.stdout.strip()
            else:
                err_msg = result.stderr.strip() or result.stdout.strip()
                logger.debug(f"IPMI error en {self.host} ({' '.join(args)}): {err_msg}")
                return False, err_msg
        except subprocess.TimeoutExpired:
            logger.debug(f"IPMI timeout ({self.timeout}s) en {self.host} ({' '.join(args)})")
            return False, f"Timeout tras {self.timeout}s"
        except Exception as e:
            logger.error(f"Excepción al ejecutar IPMI en {self.host}: {e}")
            return False, str(e)

    def is_reachable(self) -> bool:
        """Comprueba si el BMC responde a una consulta básica de estado de energía."""
        success, out = self.run_command(["chassis", "power", "status"])
        return success and ("Chassis Power is" in out or "Chassis Power is on" in out or "Chassis Power is off" in out)

    def get_power_status(self) -> Optional[str]:
        """
        Retorna el estado del chasis: 'on', 'off' o None si no se puede consultar.
        """
        success, out = self.run_command(["chassis", "power", "status"])
        if not success:
            return None
        out_lower = out.lower()
        if "power is on" in out_lower:
            return "on"
        elif "power is off" in out_lower:
            return "off"
        return "unknown"

    def power_on(self) -> bool:
        """Envía el comando de encendido del chasis."""
        success, out = self.run_command(["chassis", "power", "on"])
        if success:
            logger.info(f"[{self.host}] Comando 'chassis power on' ejecutado exitosamente.")
            return True
        logger.warning(f"[{self.host}] Falló 'chassis power on': {out}")
        return False

    def raw_command(self, hex_bytes: List[str]) -> Tuple[bool, str]:
        """Ejecuta un comando raw de IPMI (ej. ['0x30', '0x30', '0x01', '0x00'])."""
        return self.run_command(["raw"] + hex_bytes)
