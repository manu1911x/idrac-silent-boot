import datetime
import glob
import logging
import os
import pwd
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from .base import BaseDevice

logger = logging.getLogger("silent_boot.hdmi_monitor")


class HdmiMonitor(BaseDevice):
    def __init__(self, name: str, config: Dict[str, Any], general_config: Dict[str, Any], controller: Optional[Any] = None):
        super().__init__(name, config, general_config, controller)

        self.turn_on_str = str(self.config.get("turn_on_time", "09:00"))
        self.turn_off_str = str(self.config.get("turn_off_time", "22:00"))
        self.turn_off_on_shutdown = bool(self.config.get("turn_off_on_system_shutdown", True))

        self.on_time = self._parse_time(self.turn_on_str, datetime.time(9, 0))
        self.off_time = self._parse_time(self.turn_off_str, datetime.time(22, 0))

        self.current_state: Optional[bool] = None
        self.last_scheduled_state: Optional[bool] = None
        self.manual_override: Optional[bool] = None
        self.last_wayland_socket: Optional[str] = None

    @staticmethod
    def _parse_time(time_str: str, default: datetime.time) -> datetime.time:
        try:
            parts = time_str.strip().split(":")
            return datetime.time(int(parts[0]), int(parts[1]))
        except Exception:
            return default

    def is_scheduled_on(self, now_time: Optional[datetime.time] = None) -> bool:
        if now_time is None:
            now_time = datetime.datetime.now().time()

        if self.on_time < self.off_time:
            return self.on_time <= now_time < self.off_time
        if self.on_time > self.off_time:
            return now_time >= self.on_time or now_time < self.off_time
        return True

    def _find_wayland_env(self) -> Optional[Tuple[Dict[str, str], str]]:
        for sock_path in sorted(glob.glob("/run/user/*/wayland-*")):
            if sock_path.endswith(".lock"):
                continue
            path_obj = Path(sock_path)
            runtime_dir = str(path_obj.parent)
            wayland_display = path_obj.name
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = runtime_dir
            env["WAYLAND_DISPLAY"] = wayland_display
            return env, sock_path
        return None

    def _find_x11_env(self) -> Optional[Dict[str, str]]:
        x_sockets = sorted(glob.glob("/tmp/.X11-unix/X*"))
        if not x_sockets:
            return None
        display_num = Path(x_sockets[0]).name.replace("X", "")
        env = os.environ.copy()
        env["DISPLAY"] = f":{display_num}"
        for entry in pwd.getpwall():
            if 1000 <= entry.pw_uid < 60000:
                xauth = Path(entry.pw_dir) / ".Xauthority"
                if xauth.exists():
                    env["XAUTHORITY"] = str(xauth)
                    break
        return env

    def _apply_display_power(self, turn_on: bool) -> bool:
        success = False

        if shutil.which("wlopm"):
            wl_info = self._find_wayland_env()
            if wl_info is not None:
                env, sock_path = wl_info
                flag = "--on" if turn_on else "--off"
                try:
                    res = subprocess.run(
                        ["wlopm", flag, "*"],
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=5
                    )
                    if res.returncode == 0:
                        self.last_wayland_socket = sock_path
                        success = True
                except Exception as e:
                    logger.debug(f"wlopm execution error: {e}")

        if not success and shutil.which("xset"):
            x_env = self._find_x11_env()
            if x_env is not None:
                mode = "on" if turn_on else "off"
                try:
                    res = subprocess.run(
                        ["xset", "dpms", "force", mode],
                        env=x_env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=5
                    )
                    if res.returncode == 0:
                        success = True
                except Exception as e:
                    logger.debug(f"xset execution error: {e}")

        if not success and shutil.which("vcgencmd"):
            val = "1" if turn_on else "0"
            try:
                res = subprocess.run(
                    ["vcgencmd", "display_power", val],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                if res.returncode == 0:
                    success = True
            except Exception as e:
                logger.debug(f"vcgencmd execution error: {e}")

        for bl_power in glob.glob("/sys/class/backlight/*/bl_power"):
            try:
                with open(bl_power, "w", encoding="utf-8") as f:
                    f.write("0" if turn_on else "1")
                success = True
            except Exception:
                pass

        if success:
            state_str = "ON" if turn_on else "OFF"
            if self.current_state != turn_on:
                logger.info(f"[HDMI Monitor] Display turned {state_str}.")
            self.current_state = turn_on

        return success

    def evaluate_and_apply(self) -> None:
        if self.controller and getattr(self.controller, "is_manually_shut_down", False) and self.turn_off_on_shutdown:
            target = False
        else:
            scheduled = self.is_scheduled_on()
            if self.last_scheduled_state is not None and scheduled != self.last_scheduled_state:
                self.manual_override = None
            self.last_scheduled_state = scheduled

            if self.manual_override is not None:
                target = self.manual_override
            else:
                target = scheduled

        wl_info = self._find_wayland_env()
        current_sock = wl_info[1] if wl_info else None
        socket_changed = current_sock is not None and current_sock != self.last_wayland_socket

        if self.current_state != target or socket_changed:
            self._apply_display_power(target)

    def power_on_device(self) -> bool:
        self.manual_override = True
        return self._apply_display_power(True)

    def power_off_device(self) -> bool:
        self.manual_override = False
        return self._apply_display_power(False)

    def toggle_monitor(self) -> bool:
        next_state = not bool(self.current_state)
        self.manual_override = next_state
        return self._apply_display_power(next_state)

    def on_boot(self) -> None:
        logger.info(
            f"[HDMI Monitor] Initialized schedule: ON at {self.turn_on_str}, OFF at {self.turn_off_str}."
        )
        self.evaluate_and_apply()

    def poll(self) -> None:
        self.evaluate_and_apply()

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "power": "on" if self.current_state else ("off" if self.current_state is False else "unknown"),
            "scheduled_on": self.is_scheduled_on(),
            "turn_on_time": self.turn_on_str,
            "turn_off_time": self.turn_off_str,
            "manual_override": self.manual_override,
        }

    def cleanup(self) -> None:
        pass
