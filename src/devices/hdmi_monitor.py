import datetime
import glob
import logging
import os
import pwd
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from .base import BaseDevice

logger = logging.getLogger("silent_boot.hdmi_monitor")


class HdmiMonitor(BaseDevice):
    def __init__(self, name: str, config: Dict[str, Any], general_config: Dict[str, Any], controller: Optional[Any] = None):
        super().__init__(name, config, general_config, controller)

        self.turn_on_str = str(self.config.get("turn_on_time", "09:00"))
        self.turn_off_str = str(self.config.get("turn_off_time", "22:00"))
        self.turn_off_on_shutdown = bool(self.config.get("turn_off_on_system_shutdown", True))
        self.cec_osd_name = str(self.config.get("cec_osd_name", "HomelabPi"))
        self.verify_hardware_state = bool(self.config.get("verify_hardware_state", True))

        self.on_time = self._parse_time(self.turn_on_str, datetime.time(9, 0))
        self.off_time = self._parse_time(self.turn_off_str, datetime.time(22, 0))

        self.current_state: Optional[bool] = None
        self.last_scheduled_state: Optional[bool] = None
        self.manual_override: Optional[bool] = None
        self.last_wayland_socket: Optional[str] = None
        self.cached_phys_addr: Dict[str, str] = {}
        self.last_cec_detail: str = "uninitialized"

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

    def _find_cec_devices(self) -> List[str]:
        configured = self.config.get("cec_device")
        if configured and Path(str(configured)).exists():
            return [str(configured)]
        return sorted(glob.glob("/dev/cec*"))

    def _ensure_cec_configured(self, cec_dev: str) -> Optional[str]:
        if not shutil.which("cec-ctl"):
            self.last_cec_detail = "cec-ctl binary not installed (run sudo apt install v4l-utils)"
            return None
        try:
            info = subprocess.run(
                ["cec-ctl", "-d", cec_dev],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5
            )
            out = info.stdout
            phys_match = re.search(r"Physical Address\s*:\s*([0-9a-fA-F.]+)", out)
            phys_addr = phys_match.group(1) if phys_match else None

            if phys_addr and phys_addr != "f.f.f.f":
                self.cached_phys_addr[cec_dev] = phys_addr
            else:
                return None

            mask_zero = bool(re.search(r"Logical Address Mask\s*:\s*0x0000", out))
            addrs_zero = bool(re.search(r"Logical Addresses\s*:\s*0\b", out))

            if mask_zero or addrs_zero:
                cfg_res = subprocess.run(
                    [
                        "cec-ctl",
                        "-d", cec_dev,
                        "--cec-version-1.4",
                        "--playback",
                        f"--osd-name={self.cec_osd_name}"
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                if cfg_res.returncode != 0:
                    subprocess.run(
                        [
                            "cec-ctl",
                            "-d", cec_dev,
                            "--playback",
                            f"--osd-name={self.cec_osd_name}"
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=5
                    )

            self.last_cec_detail = f"{cec_dev} phys={phys_addr}"
            return phys_addr
        except Exception as e:
            self.last_cec_detail = f"cec-ctl error on {cec_dev}: {e}"
            logger.debug(self.last_cec_detail)
            return None

    def get_cec_power_status(self) -> Optional[bool]:
        if not shutil.which("cec-ctl"):
            self.last_cec_detail = "cec-ctl not found"
            return None

        cec_devs = self._find_cec_devices()
        if not cec_devs:
            self.last_cec_detail = "no /dev/cec* devices found"
            return None

        for cec_dev in cec_devs:
            phys_addr = self._ensure_cec_configured(cec_dev)
            if not phys_addr:
                continue
            try:
                res = subprocess.run(
                    ["cec-ctl", "-d", cec_dev, "--to", "0", "--give-device-power-status"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                out = (res.stdout + "\n" + res.stderr).lower()
                if "pwr-state: on" in out or "in-transition-standby-to-on" in out or "(0x00)" in out:
                    self.last_cec_detail = f"{cec_dev} TV power=on"
                    return True
                if "pwr-state: standby" in out or "in-transition-on-to-standby" in out or "(0x01)" in out:
                    self.last_cec_detail = f"{cec_dev} TV power=standby"
                    return False
                self.last_cec_detail = f"{cec_dev} query output: {out.strip()[:120]}"
            except Exception as e:
                self.last_cec_detail = f"{cec_dev} status query exception: {e}"
                logger.debug(self.last_cec_detail)

        return None

    def _apply_cec_power(self, turn_on: bool) -> bool:
        if shutil.which("cec-ctl"):
            for cec_dev in self._find_cec_devices():
                phys_addr = self._ensure_cec_configured(cec_dev)
                if not phys_addr:
                    continue
                try:
                    if turn_on:
                        res = subprocess.run(
                            ["cec-ctl", "-d", cec_dev, "--to", "0", "--image-view-on"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=6
                        )
                        subprocess.run(
                            ["cec-ctl", "-d", cec_dev, "--to", "0", "--text-view-on"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=6
                        )
                        subprocess.run(
                            ["cec-ctl", "-d", cec_dev, "--to", "0", "--active-source", f"phys-addr={phys_addr}"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=6
                        )
                        if res.returncode == 0:
                            logger.info(f"[HDMI Monitor] Sent CEC image-view-on + active-source ({phys_addr}) via {cec_dev}.")
                            return True
                        logger.warning(f"[HDMI Monitor] cec-ctl ON failed on {cec_dev}: {res.stderr.strip() or res.stdout.strip()}")
                    else:
                        res = subprocess.run(
                            ["cec-ctl", "-d", cec_dev, "--to", "0", "--standby"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=6
                        )
                        if res.returncode == 0:
                            logger.info(f"[HDMI Monitor] Sent CEC standby to TV via {cec_dev}.")
                            return True
                        logger.warning(f"[HDMI Monitor] cec-ctl standby failed on {cec_dev}: {res.stderr.strip() or res.stdout.strip()}")
                except Exception as e:
                    logger.warning(f"[HDMI Monitor] cec-ctl exception on {cec_dev}: {e}")

        if shutil.which("cec-client"):
            cmd_str = "on 0\nas\n" if turn_on else "standby 0\n"
            try:
                res = subprocess.run(
                    ["cec-client", "-s", "-d", "1"],
                    input=cmd_str,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=10
                )
                if res.returncode == 0:
                    logger.info(f"[HDMI Monitor] Executed cec-client ({'on 0' if turn_on else 'standby 0'}).")
                    return True
            except Exception as e:
                logger.debug(f"cec-client execution error: {e}")

        return False

    def _apply_ddc_power(self, turn_on: bool) -> bool:
        if not shutil.which("ddcutil"):
            return False
        val = "01" if turn_on else "04"
        try:
            res = subprocess.run(
                ["ddcutil", "setvcp", "D6", val, "--noverify"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8
            )
            return res.returncode == 0
        except Exception as e:
            logger.debug(f"ddcutil execution error: {e}")
            return False

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

    def _apply_dpms_signal(self, turn_on: bool) -> bool:
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

        return success

    def _apply_display_power(self, turn_on: bool) -> bool:
        self._apply_dpms_signal(True)
        time.sleep(0.3)

        if turn_on:
            cec_ok = self._apply_cec_power(True)
            ddc_ok = False if cec_ok else self._apply_ddc_power(True)
            success = cec_ok or ddc_ok
        else:
            cec_ok = self._apply_cec_power(False)
            ddc_ok = False if cec_ok else self._apply_ddc_power(False)
            success = cec_ok or ddc_ok

        if success:
            state_str = "ON" if turn_on else "OFF"
            if self.current_state != turn_on:
                logger.info(f"[HDMI Monitor] TV/Monitor hardware turned {state_str} (CEC={cec_ok}).")
            self.current_state = turn_on
        else:
            logger.warning(
                f"[HDMI Monitor] Hardware CEC/DDC command failed for target={'ON' if turn_on else 'OFF'} "
                f"(detail: {self.last_cec_detail})."
            )

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

        actual_hw_state = self.get_cec_power_status() if self.verify_hardware_state else None
        if actual_hw_state is not None and actual_hw_state != target:
            logger.info(
                f"[HDMI Monitor] TV hardware state ({'ON' if actual_hw_state else 'STANDBY'}) "
                f"differs from target ({'ON' if target else 'OFF'}). Enforcing target state..."
            )
            self._apply_display_power(target)
            return

        if self.current_state != target:
            self._apply_display_power(target)

    def power_on_device(self) -> bool:
        self.manual_override = True
        return self._apply_display_power(True)

    def power_off_device(self) -> bool:
        self.manual_override = False
        return self._apply_display_power(False)

    def toggle_monitor(self) -> bool:
        hw_state = self.get_cec_power_status()
        base_state = hw_state if hw_state is not None else bool(self.current_state)
        next_state = not base_state
        self.manual_override = next_state
        return self._apply_display_power(next_state)

    def on_boot(self) -> None:
        logger.info(
            f"[HDMI Monitor] Initialized schedule: ON at {self.turn_on_str}, OFF at {self.turn_off_str}."
        )
        self._apply_dpms_signal(True)
        self.evaluate_and_apply()

    def poll(self) -> None:
        self.evaluate_and_apply()

    def get_status(self) -> Dict[str, Any]:
        hw_state = self.get_cec_power_status()
        effective = hw_state if hw_state is not None else self.current_state
        return {
            "name": self.name,
            "power": "on" if effective else ("off" if effective is False else "unknown"),
            "cec_power_status": "on" if hw_state else ("standby" if hw_state is False else "unknown"),
            "cec_detail": self.last_cec_detail,
            "scheduled_on": self.is_scheduled_on(),
            "turn_on_time": self.turn_on_str,
            "turn_off_time": self.turn_off_str,
            "manual_override": self.manual_override,
        }

    def cleanup(self) -> None:
        pass
