import logging
import re
from typing import Dict, List, Optional, Tuple
from ..ipmi import IpmiClient

logger = logging.getLogger("silent_boot.dell")

class DellR730xd:
    """
    Controlador para Dell PowerEdge R730xd mediante iDRAC 8 sobre IPMI LAN.
    """
    def __init__(self, host: str, user: str, password: str, timeout_seconds: int = 8):
        self.ipmi = IpmiClient(host, user, password, timeout_seconds=timeout_seconds)
        self.host = host
        self.manual_mode_active = False
        self.current_speed_percent: Optional[int] = None

    def is_online(self) -> bool:
        """Verifica si el iDRAC está listo para recibir comandos IPMI."""
        return self.ipmi.is_reachable()

    def get_power_status(self) -> Optional[str]:
        return self.ipmi.get_power_status()

    def power_on(self) -> bool:
        return self.ipmi.power_on()

    def enable_manual_control(self) -> bool:
        """
        Desactiva el control automático de Dell y habilita el control manual por IPMI:
        ipmitool raw 0x30 0x30 0x01 0x00
        """
        success, out = self.ipmi.raw_command(["0x30", "0x30", "0x01", "0x00"])
        if success:
            self.manual_mode_active = True
            logger.info(f"[{self.host}] Control manual de ventiladores ACTIVADO.")
            return True
        logger.error(f"[{self.host}] Error al activar control manual de ventiladores: {out}")
        return False

    def restore_auto_control(self) -> bool:
        """
        Restaura el control térmico automático y dinámico gestionado por la BIOS/iDRAC de Dell:
        ipmitool raw 0x30 0x30 0x01 0x01
        """
        success, out = self.ipmi.raw_command(["0x30", "0x30", "0x01", "0x01"])
        if success:
            self.manual_mode_active = False
            self.current_speed_percent = None
            logger.warning(f"[{self.host}] Control automático de ventiladores RESTAURADO en Dell iDRAC.")
            return True
        logger.error(f"[{self.host}] Error al restaurar control automático de ventiladores: {out}")
        return False

    def set_fan_speed_percent(self, percent: int) -> bool:
        """
        Establece la velocidad de los ventiladores en porcentaje (0-100%):
        ipmitool raw 0x30 0x30 0x02 0xff <hex_speed>
        """
        percent = max(0, min(100, int(percent)))
        hex_speed = f"0x{percent:02x}"
        success, out = self.ipmi.raw_command(["0x30", "0x30", "0x02", "0xff", hex_speed])
        if success:
            self.current_speed_percent = percent
            logger.info(f"[{self.host}] Ventiladores ajustados a {percent}% ({hex_speed}).")
            return True
        logger.error(f"[{self.host}] Error al ajustar ventiladores a {percent}%: {out}")
        return False

    def read_temperatures(self) -> Tuple[Optional[int], Dict[str, int]]:
        """
        Lee los sensores de temperatura mediante 'sdr type Temperature'.
        Retorna (max_cpu_temp: Optional[int], detailed_readings: Dict[str, int]).
        """
        success, out = self.ipmi.run_command(["sdr", "type", "Temperature"])
        if not success or not out:
            return None, {}

        readings: Dict[str, int] = {}
        cpu_temps: List[int] = []
        general_temps: List[int] = []

        # Formato habitual de ipmitool:
        # Inlet Temp       | 04h | ok  |  7.1 | 21 degrees C
        # Temp             | 0Eh | ok  |  3.1 | 42 degrees C
        # Temp             | 0Fh | ok  |  3.2 | 44 degrees C
        # CPU1 Temp        | 22h | ok  |  3.1 | 45 degrees C
        for line in out.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 5:
                sensor_name = parts[0]
                value_str = parts[4]
                match = re.search(r"(\d+)\s*(?:degrees\s*C|C)?", value_str, re.IGNORECASE)
                if match:
                    temp_val = int(match.group(1))
                    readings[sensor_name] = temp_val
                    lower_name = sensor_name.lower()

                    if "cpu" in lower_name or lower_name == "temp":
                        cpu_temps.append(temp_val)
                    elif "inlet" not in lower_name and "ambient" not in lower_name:
                        general_temps.append(temp_val)

        if cpu_temps:
            highest = max(cpu_temps)
            return highest, readings
        elif general_temps:
            highest = max(general_temps)
            return highest, readings
        elif readings:
            highest = max(readings.values())
            return highest, readings

        return None, readings
