import importlib
import logging
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
except ImportError:
    yaml = None

from .devices.base import BaseDevice

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("silent_boot.controller")

REGISTERED_DEVICES = {
    "dell_r730xd": "src.devices.dell_r730xd.DellR730xd",
    "mcafee_rmm3": "src.devices.mcafee_rmm3.McAfeeRMM3",
}


def parse_env_file(filepath: Path) -> Dict[str, str]:
    env_vars = {}
    if not filepath.exists():
        return env_vars

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                env_vars[key] = val
                os.environ[key] = val
    return env_vars


def find_config_paths():
    base_dir = Path(__file__).resolve().parent.parent
    system_dir = Path("/etc/idrac-silent-boot")

    env_candidates = [
        Path.cwd() / ".env",
        base_dir / ".env",
        system_dir / ".env",
    ]
    yaml_candidates = [
        Path.cwd() / "config.yaml",
        base_dir / "config.yaml",
        system_dir / "config.yaml",
    ]

    env_path = next((p for p in env_candidates if p.exists()), env_candidates[0])
    yaml_path = next((p for p in yaml_candidates if p.exists()), yaml_candidates[0])
    return env_path, yaml_path


def load_device_class(target: Any):
    if isinstance(target, str):
        module_path, class_name = target.rsplit(".", 1)
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    return target


class ControllerDaemon:
    def __init__(self):
        self.running = True
        self.env_path, self.yaml_path = find_config_paths()

        if self.env_path.exists():
            parse_env_file(self.env_path)
            logger.info(f"Loaded environment variables from {self.env_path}")
        else:
            logger.warning(f"No .env file found at {self.env_path}")

        self.config = {}
        if self.yaml_path.exists():
            if yaml is not None:
                with open(self.yaml_path, "r", encoding="utf-8") as f:
                    self.config = yaml.safe_load(f) or {}
                logger.info(f"Loaded configuration from {self.yaml_path}")
            else:
                logger.error("PyYAML is not installed. Install via: sudo apt install python3-yaml")
        else:
            logger.warning(f"No config.yaml found at {self.yaml_path}")

        self.general_config = self.config.get("general", {})
        self.poll_interval = self.general_config.get("poll_interval_seconds", 5)

        self.active_devices: List[BaseDevice] = []
        self._init_devices()
        self._setup_signals()

    def _init_devices(self):
        for device_key, device_target in REGISTERED_DEVICES.items():
            device_cfg = self.config.get(device_key, {})
            is_enabled = device_cfg.get("enabled", True)

            if not is_enabled:
                logger.info(f"Device [{device_key}] is disabled in config. Skipping.")
                continue

            try:
                device_class = load_device_class(device_target)
                device_instance = device_class(
                    name=device_key,
                    config=device_cfg,
                    general_config=self.general_config
                )
                self.active_devices.append(device_instance)
                logger.info(f"Initialized device [{device_key}].")
            except Exception as e:
                logger.error(f"Failed to initialize device [{device_key}] from {device_target}: {e}")

    def _setup_signals(self):
        signal.signal(signal.SIGINT, self._handle_exit)
        signal.signal(signal.SIGTERM, self._handle_exit)

    def _handle_exit(self, signum, frame):
        logger.warning("Shutdown signal received. Running safe device cleanups...")
        self.running = False
        for dev in self.active_devices:
            try:
                dev.cleanup()
            except Exception as e:
                logger.error(f"Error during cleanup of [{dev.name}]: {e}")
        sys.exit(0)

    def run_boot_phase(self):
        if not self.active_devices:
            logger.warning("No active devices to manage in boot phase.")
            return

        logger.info(f"Starting boot phase for {len(self.active_devices)} devices in parallel...")
        with ThreadPoolExecutor(max_workers=max(1, len(self.active_devices))) as executor:
            futures = [executor.submit(dev.on_boot) for dev in self.active_devices]
            for fut in futures:
                fut.result()
        logger.info("Boot phase completed.")

    def run_monitoring_loop(self):
        if not self.active_devices:
            logger.warning("No active devices to monitor.")
            return

        logger.info(f"Starting periodic polling loop (interval: {self.poll_interval}s)...")
        while self.running:
            for dev in self.active_devices:
                try:
                    dev.poll()
                except Exception as e:
                    logger.error(f"Error polling device [{dev.name}]: {e}")
            time.sleep(self.poll_interval)


def main():
    daemon = ControllerDaemon()
    daemon.run_boot_phase()
    daemon.run_monitoring_loop()


if __name__ == "__main__":
    main()
