import logging
from typing import Any, Dict, List, Optional
from .devices.dell_r730xd import DellR730xd

logger = logging.getLogger("silent_boot.thermal")

class ThermalManager:
    """
    Gestiona la curva de ventiladores y el watchdog de seguridad para Dell R730xd.
    """
    def __init__(self, dell: DellR730xd, config: Dict[str, Any]):
        self.dell = dell
        self.config = config
        
        watchdog_cfg = config.get("watchdog", {})
        self.max_failures = watchdog_cfg.get("max_consecutive_failures", 4)
        self.emergency_temp = watchdog_cfg.get("emergency_temp_celsius", 78)
        self.emergency_recovery_temp = watchdog_cfg.get("emergency_recovery_celsius", 65)

        # Ordenar la curva de mayor a menor temperatura
        raw_curve = config.get("fan_curve", [])
        self.fan_curve: List[Dict[str, Any]] = sorted(
            raw_curve, key=lambda x: x.get("min_temp", 0), reverse=True
        )

        self.consecutive_failures = 0
        self.emergency_mode_active = False

    def handle_read_failure(self):
        """Llamado cuando falla la lectura de temperatura."""
        self.consecutive_failures += 1
        logger.warning(
            f"Fallo de lectura de temperatura ({self.consecutive_failures}/{self.max_failures})"
        )
        if self.consecutive_failures >= self.max_failures:
            if not self.emergency_mode_active:
                logger.error(
                    "¡WATCHDOG ACTIVADO! Se alcanzó el máximo de fallos consecutivos de temperatura. "
                    "Restaurando control automático dinámico de Dell para proteger el hardware."
                )
                self.dell.restore_auto_control()
                self.emergency_mode_active = True

    def process_temperature(self, temp: int, detailed_readings: Dict[str, int]):
        """
        Evalúa la temperatura actual contra los umbrales de emergencia y la curva configurada.
        """
        self.consecutive_failures = 0

        # Verificación de emergencia térmica
        if temp >= self.emergency_temp:
            if not self.emergency_mode_active or self.dell.manual_mode_active:
                logger.critical(
                    f"¡TEMPERATURA CRÍTICA DETECTADA: {temp}°C! (Umbral de emergencia: {self.emergency_temp}°C). "
                    "Entregando el control térmico inmediatamente a la BIOS de Dell."
                )
                self.dell.restore_auto_control()
                self.emergency_mode_active = True
            return

        # Recuperación de modo de emergencia
        if self.emergency_mode_active:
            if temp <= self.emergency_recovery_temp:
                logger.info(
                    f"Temperatura estabilizada en {temp}°C (<= {self.emergency_recovery_temp}°C). "
                    "Recuperando control manual gestionado por el daemon."
                )
                self.emergency_mode_active = False
            else:
                logger.debug(
                    f"Modo de emergencia aún activo (Temp actual: {temp}°C, esperando <= {self.emergency_recovery_temp}°C)."
                )
                return

        # Evaluar la curva configurada
        target_rule = None
        for rule in self.fan_curve:
            if temp >= rule.get("min_temp", 0):
                target_rule = rule
                break

        if not target_rule:
            # Fallback seguro
            target_speed = 30
            action = "manual"
        else:
            action = target_rule.get("action", "manual")
            target_speed = target_rule.get("fan_speed_percent", 10)

        if action == "dell_auto":
            if self.dell.manual_mode_active:
                logger.info(f"Curva solicita control automático de Dell para {temp}°C.")
                self.dell.restore_auto_control()
        else:
            # Asegurar modo manual activo
            if not self.dell.manual_mode_active:
                self.dell.enable_manual_control()

            # Ajustar velocidad sólo si cambia
            if self.dell.current_speed_percent != target_speed:
                logger.info(
                    f"Temp CPU: {temp}°C | Ajustando ventiladores a {target_speed}% "
                    f"(Detalles: {detailed_readings})"
                )
                self.dell.set_fan_speed_percent(target_speed)
            else:
                logger.debug(
                    f"Temp CPU: {temp}°C | Manteniendo ventiladores al {target_speed}%"
                )
