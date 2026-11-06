import logging
import subprocess
from typing import List, Optional, Tuple

logger = logging.getLogger("silent_boot.ipmi")


class IpmiClient:
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

    def run_command(self, args: List[str]) -> Tuple[bool, str]:
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
            err_msg = result.stderr.strip() or result.stdout.strip()
            logger.debug(f"IPMI error on {self.host} ({' '.join(args)}): {err_msg}")
            return False, err_msg
        except subprocess.TimeoutExpired:
            logger.debug(f"IPMI timeout ({self.timeout}s) on {self.host} ({' '.join(args)})")
            return False, f"Timeout after {self.timeout}s"
        except Exception as e:
            logger.error(f"IPMI exception on {self.host}: {e}")
            return False, str(e)

    def is_reachable(self) -> bool:
        success, out = self.run_command(["chassis", "power", "status"])
        return success and ("Chassis Power is" in out or "Chassis Power is on" in out or "Chassis Power is off" in out)

    def get_power_status(self) -> Optional[str]:
        success, out = self.run_command(["chassis", "power", "status"])
        if not success:
            return None
        out_lower = out.lower()
        if "power is on" in out_lower:
            return "on"
        if "power is off" in out_lower:
            return "off"
        return "unknown"

    def power_on(self) -> bool:
        success, out = self.run_command(["chassis", "power", "on"])
        if success:
            logger.info(f"[{self.host}] Command 'chassis power on' executed successfully.")
            return True
        logger.warning(f"[{self.host}] Failed 'chassis power on': {out}")
        return False

    def power_soft(self) -> bool:
        success, out = self.run_command(["chassis", "power", "soft"])
        if success:
            logger.info(f"[{self.host}] Command 'chassis power soft' (ACPI shutdown) executed successfully.")
            return True
        logger.warning(f"[{self.host}] Failed 'chassis power soft': {out}")
        return False

    def power_off(self) -> bool:
        success, out = self.run_command(["chassis", "power", "off"])
        if success:
            logger.info(f"[{self.host}] Command 'chassis power off' executed successfully.")
            return True
        logger.warning(f"[{self.host}] Failed 'chassis power off': {out}")
        return False

    def raw_command(self, hex_bytes: List[str]) -> Tuple[bool, str]:
        return self.run_command(["raw"] + hex_bytes)
