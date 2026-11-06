import datetime
import importlib
import json
import logging
import os
import signal
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

from .api import RemoteApiServer
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
    "hdmi_monitor": "src.devices.hdmi_monitor.HdmiMonitor",
    "gpio_panel": "src.devices.gpio_panel.GpioPanel",
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
        self.state_file = self.yaml_path.parent / ".state.json"

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
        self.persist_manual_shutdown = bool(self.general_config.get("persist_manual_shutdown", False))

        self.is_manually_shut_down = self._load_persisted_state()

        self.active_devices: List[BaseDevice] = []
        self._init_devices()

        self.api_server: Optional[RemoteApiServer] = None
        self._init_api_server()
        self._setup_signals()

    def _load_persisted_state(self) -> bool:
        if not self.persist_manual_shutdown or not self.state_file.exists():
            return False
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return bool(data.get("manually_shut_down", False))
        except Exception:
            return False

    def _save_persisted_state(self) -> None:
        if not self.persist_manual_shutdown:
            return
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({"manually_shut_down": self.is_manually_shut_down}, f)
        except Exception as e:
            logger.error(f"Failed saving state file: {e}")

    def _init_devices(self):
        default_enabled = {
            "dell_r730xd": True,
            "mcafee_rmm3": True,
            "hdmi_monitor": True,
            "gpio_panel": False,
        }
        for device_key, device_target in REGISTERED_DEVICES.items():
            device_cfg = self.config.get(device_key, {})
            is_enabled = device_cfg.get("enabled", default_enabled.get(device_key, True))

            if not is_enabled:
                logger.info(f"Device [{device_key}] is disabled in config. Skipping.")
                continue

            try:
                device_class = load_device_class(device_target)
                device_instance = device_class(
                    name=device_key,
                    config=device_cfg,
                    general_config=self.general_config,
                    controller=self
                )
                self.active_devices.append(device_instance)
                logger.info(f"Initialized device [{device_key}].")
            except Exception as e:
                logger.error(f"Failed to initialize device [{device_key}] from {device_target}: {e}")

    def _init_api_server(self) -> None:
        api_cfg = self.config.get("remote_api", {})
        if not api_cfg.get("enabled", True):
            return
        host = str(api_cfg.get("host", "0.0.0.0"))
        port = int(api_cfg.get("port", 8080))
        token = os.getenv("API_TOKEN") or api_cfg.get("token")
        self.api_server = RemoteApiServer(self, host=host, port=port, api_token=token)
        self.api_server.start()

    def get_device(self, name: str) -> Optional[BaseDevice]:
        for dev in self.active_devices:
            if dev.name == name:
                return dev
        return None

    def power_on_all(self) -> None:
        logger.info("Executing general homelab POWER ON...")
        self.is_manually_shut_down = False
        self._save_persisted_state()

        mon = self.get_device("hdmi_monitor")
        if mon is not None and hasattr(mon, "evaluate_and_apply"):
            mon.evaluate_and_apply()

        targets = [d for d in self.active_devices if d.name not in ("hdmi_monitor", "gpio_panel")]
        if targets:
            with ThreadPoolExecutor(max_workers=len(targets)) as executor:
                futures = [executor.submit(d.power_on_device) for d in targets]
                for fut in futures:
                    try:
                        fut.result()
                    except Exception as e:
                        logger.error(f"Error during power_on_all: {e}")

        logger.info("General homelab POWER ON sequence completed.")

    def power_off_all(self) -> None:
        logger.info("Executing general homelab POWER OFF...")
        self.is_manually_shut_down = True
        self._save_persisted_state()

        mon = self.get_device("hdmi_monitor")
        if mon is not None:
            mon.power_off_device()

        targets = [d for d in self.active_devices if d.name not in ("hdmi_monitor", "gpio_panel")]
        if targets:
            with ThreadPoolExecutor(max_workers=len(targets)) as executor:
                futures = [executor.submit(d.power_off_device) for d in targets]
                for fut in futures:
                    try:
                        fut.result()
                    except Exception as e:
                        logger.error(f"Error during power_off_all: {e}")

        logger.info("General homelab POWER OFF sequence completed.")

    def set_monitor_power(self, turn_on: bool) -> bool:
        mon = self.get_device("hdmi_monitor")
        if mon is None:
            return False
        return mon.power_on_device() if turn_on else mon.power_off_device()

    def toggle_monitor(self) -> bool:
        mon = self.get_device("hdmi_monitor")
        if mon is None or not hasattr(mon, "toggle_monitor"):
            return False
        return mon.toggle_monitor()

    def get_system_status(self) -> Dict[str, Any]:
        devices_status = {}
        for dev in self.active_devices:
            try:
                devices_status[dev.name] = dev.get_status()
            except Exception as e:
                devices_status[dev.name] = {"name": dev.name, "error": str(e)}

        return {
            "timestamp": datetime.datetime.now().isoformat(),
            "manually_shut_down": self.is_manually_shut_down,
            "devices": devices_status,
        }

    def _setup_signals(self):
        signal.signal(signal.SIGINT, self._handle_exit)
        signal.signal(signal.SIGTERM, self._handle_exit)

    def _handle_exit(self, signum, frame):
        logger.warning("Shutdown signal received. Running safe device cleanups...")
        self.running = False
        if self.api_server is not None:
            self.api_server.stop()
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
                try:
                    fut.result()
                except Exception as e:
                    logger.error(f"Error during device boot phase: {e}")
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


def run_cli_command(cmd: str) -> int:
    env_path, yaml_path = find_config_paths()
    if env_path.exists():
        parse_env_file(env_path)

    cfg = {}
    if yaml_path.exists() and yaml is not None:
        with open(yaml_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    api_cfg = cfg.get("remote_api", {})
    port = int(api_cfg.get("port", 8080))
    token = os.getenv("API_TOKEN") or api_cfg.get("token")

    routes = {
        "status": ("GET", "/api/status"),
        "power-on": ("POST", "/api/power/on"),
        "power-off": ("POST", "/api/power/off"),
        "monitor-on": ("POST", "/api/monitor/on"),
        "monitor-off": ("POST", "/api/monitor/off"),
        "monitor-toggle": ("POST", "/api/monitor/toggle"),
    }

    if cmd not in routes:
        print(f"Unknown command: {cmd}. Available: {', '.join(routes.keys())}")
        return 1

    method, endpoint = routes[cmd]
    url = f"http://127.0.0.1:{port}{endpoint}"
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(json.dumps(data, indent=2))
            return 0
    except Exception as e:
        print(f"Failed communicating with daemon at {url}: {e}", file=sys.stderr)
        return 1


def main():
    if len(sys.argv) > 1:
        sys.exit(run_cli_command(sys.argv[1].strip().lower()))

    daemon = ControllerDaemon()
    daemon.run_boot_phase()
    daemon.run_monitoring_loop()


if __name__ == "__main__":
    main()
