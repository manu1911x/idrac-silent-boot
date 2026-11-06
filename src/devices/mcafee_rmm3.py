import logging
import os
import time
from typing import Any, Dict, Optional
from ..ipmi import IpmiClient
from .base import BaseDevice

logger = logging.getLogger("silent_boot.mcafee_rmm3")


class McAfeeRMM3(BaseDevice):
    def __init__(self, name: str, config: Dict[str, Any], general_config: Dict[str, Any], controller: Optional[Any] = None):
        super().__init__(name, config, general_config, controller)

        host = os.getenv("MCAFEE_RMM3_HOST", "192.168.1.121")
        user = os.getenv("MCAFEE_RMM3_USER", "admin")
        password = os.getenv("MCAFEE_RMM3_PASSWORD", "password")

        self.ipmi = IpmiClient(host=host, user=user, password=password)
        self.host = host

        self.bmc_boot_timeout = self.general_config.get("bmc_boot_wait_timeout_seconds", 300)
        self.bmc_probe_interval = self.general_config.get("bmc_probe_interval_seconds", 3)
        self.auto_power_on = self.config.get("auto_power_on", True)
        self.shutdown_timeout = self.config.get("shutdown_timeout_seconds", 120)
        self.force_off_on_timeout = self.config.get("force_off_on_timeout", True)

    def is_online(self) -> bool:
        return self.ipmi.is_reachable()

    def get_power_status(self) -> Optional[str]:
        return self.ipmi.get_power_status()

    def power_on(self) -> bool:
        status = self.get_power_status()
        if status == "on":
            logger.info(f"[{self.host}] McAfee firewall is already powered ON.")
            return True
        logger.info(f"[{self.host}] Powering on McAfee firewall via Intel RMM3...")
        return self.ipmi.power_on()

    def power_on_device(self) -> bool:
        return self.power_on()

    def power_off_device(self) -> bool:
        status = self.get_power_status()
        if status == "off":
            logger.info(f"[{self.host}] McAfee firewall is already off.")
            return True

        logger.info(f"[{self.host}] Initiating graceful ACPI shutdown on McAfee firewall...")
        if not self.ipmi.power_soft():
            if self.force_off_on_timeout:
                logger.warning(f"[{self.host}] Graceful shutdown failed, forcing power off...")
                return self.ipmi.power_off()
            return False

        start_time = time.time()
        while time.time() - start_time < self.shutdown_timeout:
            time.sleep(5)
            if self.get_power_status() == "off":
                logger.info(f"[{self.host}] McAfee firewall gracefully powered off.")
                return True

        if self.force_off_on_timeout:
            logger.warning(f"[{self.host}] Shutdown timeout ({self.shutdown_timeout}s) exceeded. Forcing power off...")
            return self.ipmi.power_off()

        return False

    def on_boot(self) -> None:
        logger.info(f"[{self.host}] Waiting for Intel RMM3 BMC to come online...")
        start_time = time.time()
        while time.time() - start_time < self.bmc_boot_timeout:
            if self.is_online():
                logger.info(f"[{self.host}] Intel RMM3 BMC is online.")
                break
            time.sleep(self.bmc_probe_interval)
        else:
            logger.error(f"[{self.host}] Timeout ({self.bmc_boot_timeout}s) waiting for Intel RMM3 BMC.")
            return

        if self.controller and getattr(self.controller, "is_manually_shut_down", False):
            logger.info(f"[{self.host}] Manual shutdown state is persisted. Keeping firewall off.")
            return

        if self.auto_power_on:
            status = self.get_power_status()
            logger.info(f"[{self.host}] Chassis status: {status}")
            if status == "off":
                self.power_on()

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "power": self.get_power_status() or "unreachable",
        }

    def poll(self) -> None:
        pass

    def cleanup(self) -> None:
        pass
