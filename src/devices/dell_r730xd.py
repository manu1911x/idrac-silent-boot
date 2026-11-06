import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from ..ipmi import IpmiClient
from .base import BaseDevice

logger = logging.getLogger("silent_boot.dell_r730xd")


class DellR730xd(BaseDevice):
    def __init__(self, name: str, config: Dict[str, Any], general_config: Dict[str, Any], controller: Optional[Any] = None):
        super().__init__(name, config, general_config, controller)

        host = os.getenv("DELL_IDRAC_HOST", "192.168.1.120")
        user = os.getenv("DELL_IDRAC_USER", "root")
        password = os.getenv("DELL_IDRAC_PASSWORD", "calvin")

        self.ipmi = IpmiClient(host=host, user=user, password=password)
        self.host = host

        self.initial_speed = self.config.get("initial_fan_speed_percent", 5)
        self.power_on_delay = self.config.get("power_on_delay_seconds", 2)
        self.shutdown_timeout = self.config.get("shutdown_timeout_seconds", 180)
        self.force_off_on_timeout = self.config.get("force_off_on_timeout", True)
        self.bmc_boot_timeout = self.general_config.get("bmc_boot_wait_timeout_seconds", 300)
        self.bmc_probe_interval = self.general_config.get("bmc_probe_interval_seconds", 3)

        watchdog_cfg = self.config.get("watchdog", {})
        self.max_failures = watchdog_cfg.get("max_consecutive_failures", 4)
        self.emergency_temp = watchdog_cfg.get("emergency_temp_celsius", 78)
        self.emergency_recovery_temp = watchdog_cfg.get("emergency_recovery_celsius", 65)

        raw_curve = self.config.get("fan_curve", [])
        self.fan_curve: List[Dict[str, Any]] = sorted(
            raw_curve, key=lambda x: x.get("min_temp", 0), reverse=True
        )

        self.manual_mode_active = False
        self.current_speed_percent: Optional[int] = None
        self.last_temp: Optional[int] = None
        self.consecutive_failures = 0
        self.emergency_mode_active = False

    def is_online(self) -> bool:
        return self.ipmi.is_reachable()

    def get_power_status(self) -> Optional[str]:
        return self.ipmi.get_power_status()

    def power_on(self) -> bool:
        return self.ipmi.power_on()

    def enable_manual_control(self) -> bool:
        success, out = self.ipmi.raw_command(["0x30", "0x30", "0x01", "0x00"])
        if success:
            self.manual_mode_active = True
            logger.info(f"[{self.host}] Manual fan control ENABLED.")
            return True
        logger.error(f"[{self.host}] Failed to enable manual fan control: {out}")
        return False

    def restore_auto_control(self) -> bool:
        success, out = self.ipmi.raw_command(["0x30", "0x30", "0x01", "0x01"])
        if success:
            self.manual_mode_active = False
            self.current_speed_percent = None
            logger.warning(f"[{self.host}] Automatic fan control RESTORED on Dell iDRAC.")
            return True
        logger.error(f"[{self.host}] Failed to restore automatic fan control: {out}")
        return False

    def set_fan_speed_percent(self, percent: int) -> bool:
        percent = max(0, min(100, int(percent)))
        hex_speed = f"0x{percent:02x}"
        success, out = self.ipmi.raw_command(["0x30", "0x30", "0x02", "0xff", hex_speed])
        if success:
            self.current_speed_percent = percent
            logger.info(f"[{self.host}] Fan speed set to {percent}% ({hex_speed}).")
            return True
        logger.error(f"[{self.host}] Failed setting fan speed to {percent}%: {out}")
        return False

    def read_temperatures(self) -> Tuple[Optional[int], Dict[str, int]]:
        success, out = self.ipmi.run_command(["sdr", "type", "Temperature"])
        if not success or not out:
            return None, {}

        readings: Dict[str, int] = {}
        cpu_temps: List[int] = []
        general_temps: List[int] = []

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
            return max(cpu_temps), readings
        if general_temps:
            return max(general_temps), readings
        if readings:
            return max(readings.values()), readings

        return None, readings

    def power_on_device(self) -> bool:
        status = self.get_power_status()
        logger.info(f"[{self.host}] Chassis power status: {status}")

        if status == "off":
            logger.info(f"[{self.host}] Server is off. Initiating silent boot sequence:")
            logger.info(f"[{self.host}] 1. Setting fan speed to {self.initial_speed}% RPM before boot...")
            self.enable_manual_control()
            self.set_fan_speed_percent(self.initial_speed)

            logger.info(f"[{self.host}] 2. Waiting {self.power_on_delay}s for thermal profile application...")
            time.sleep(self.power_on_delay)

            logger.info(f"[{self.host}] 3. Powering on chassis...")
            return self.power_on()

        if status == "on":
            logger.info(f"[{self.host}] Server already on. Ensuring manual fan control mode...")
            self.enable_manual_control()
            if self.current_speed_percent is None:
                self.set_fan_speed_percent(self.initial_speed)
            return True

        return False

    def power_off_device(self) -> bool:
        status = self.get_power_status()
        if status == "off":
            logger.info(f"[{self.host}] Server is already off.")
            return True

        logger.info(f"[{self.host}] Initiating silent graceful ACPI shutdown...")
        self.enable_manual_control()
        self.set_fan_speed_percent(self.initial_speed)

        if not self.ipmi.power_soft():
            if self.force_off_on_timeout:
                logger.warning(f"[{self.host}] Graceful shutdown command failed, forcing power off...")
                return self.ipmi.power_off()
            return False

        start_time = time.time()
        while time.time() - start_time < self.shutdown_timeout:
            time.sleep(5)
            current = self.get_power_status()
            if current == "off":
                logger.info(f"[{self.host}] Server gracefully powered off.")
                return True

        if self.force_off_on_timeout:
            logger.warning(f"[{self.host}] Shutdown timeout ({self.shutdown_timeout}s) exceeded. Forcing power off...")
            return self.ipmi.power_off()

        return False

    def on_boot(self) -> None:
        logger.info(f"[{self.host}] Waiting for iDRAC to become available after power recovery...")
        start_time = time.time()
        while time.time() - start_time < self.bmc_boot_timeout:
            if self.is_online():
                logger.info(f"[{self.host}] iDRAC is online.")
                break
            time.sleep(self.bmc_probe_interval)
        else:
            logger.error(f"[{self.host}] Timeout ({self.bmc_boot_timeout}s) waiting for iDRAC.")
            return

        if self.controller and getattr(self.controller, "is_manually_shut_down", False):
            logger.info(f"[{self.host}] Manual shutdown state is persisted. Keeping server off.")
            return

        self.power_on_device()

    def poll(self) -> None:
        status = self.get_power_status()
        if status != "on":
            self.last_temp = None
            logger.debug(f"[{self.host}] Chassis is not on ({status}). Skipping thermal poll.")
            return

        temp, readings = self.read_temperatures()
        if temp is None:
            self._handle_read_failure()
            return

        self.last_temp = temp
        self._process_temperature(temp, readings)

    def _handle_read_failure(self) -> None:
        self.consecutive_failures += 1
        logger.warning(
            f"[{self.host}] Temperature read failure ({self.consecutive_failures}/{self.max_failures})"
        )
        if self.consecutive_failures >= self.max_failures:
            if not self.emergency_mode_active:
                logger.error(
                    f"[{self.host}] Watchdog triggered due to consecutive read failures. "
                    "Restoring Dell dynamic automatic fan control for hardware safety."
                )
                self.restore_auto_control()
                self.emergency_mode_active = True

    def _process_temperature(self, temp: int, readings: Dict[str, int]) -> None:
        self.consecutive_failures = 0

        if temp >= self.emergency_temp:
            if not self.emergency_mode_active or self.manual_mode_active:
                logger.critical(
                    f"[{self.host}] Critical temperature detected: {temp}°C (Emergency limit: {self.emergency_temp}°C). "
                    "Handing thermal control immediately to Dell BIOS."
                )
                self.restore_auto_control()
                self.emergency_mode_active = True
            return

        if self.emergency_mode_active:
            if temp <= self.emergency_recovery_temp:
                logger.info(
                    f"[{self.host}] Temperature normalized to {temp}°C (<= {self.emergency_recovery_temp}°C). "
                    "Resuming daemon-managed manual fan control."
                )
                self.emergency_mode_active = False
            else:
                logger.debug(
                    f"[{self.host}] Emergency mode active (Current: {temp}°C, target <= {self.emergency_recovery_temp}°C)."
                )
                return

        target_rule = None
        for rule in self.fan_curve:
            if temp >= rule.get("min_temp", 0):
                target_rule = rule
                break

        if not target_rule:
            target_speed = 30
            action = "manual"
        else:
            action = target_rule.get("action", "manual")
            target_speed = target_rule.get("fan_speed_percent", 10)

        if action == "dell_auto":
            if self.manual_mode_active:
                logger.info(f"[{self.host}] Fan curve requested Dell automatic control at {temp}°C.")
                self.restore_auto_control()
        else:
            if not self.manual_mode_active:
                self.enable_manual_control()

            if self.current_speed_percent != target_speed:
                logger.info(f"[{self.host}] CPU Temp: {temp}°C | Adjusting fan speed to {target_speed}% ({readings})")
                self.set_fan_speed_percent(target_speed)
            else:
                logger.debug(f"[{self.host}] CPU Temp: {temp}°C | Maintaining fan speed at {target_speed}%")

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "power": self.get_power_status() or "unreachable",
            "temperature_c": self.last_temp,
            "fan_speed_percent": self.current_speed_percent,
            "manual_mode": self.manual_mode_active,
            "emergency_mode": self.emergency_mode_active,
        }

    def cleanup(self) -> None:
        if self.manual_mode_active:
            logger.info(f"[{self.host}] Restoring Dell automatic fan control on daemon cleanup...")
            self.restore_auto_control()
