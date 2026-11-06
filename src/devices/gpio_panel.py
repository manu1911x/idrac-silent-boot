import logging
import threading
from typing import Any, Dict, List, Optional
from .base import BaseDevice

try:
    from gpiozero import Button, LED
except ImportError:
    Button = None
    LED = None

try:
    from RPLCD.i2c import CharLCD
except ImportError:
    CharLCD = None

logger = logging.getLogger("silent_boot.gpio_panel")


class GpioPanel(BaseDevice):
    def __init__(self, name: str, config: Dict[str, Any], general_config: Dict[str, Any], controller: Optional[Any] = None):
        super().__init__(name, config, general_config, controller)

        self.pull_up = bool(self.config.get("button_pull_up", True))
        self.bounce_time = float(self.config.get("button_bounce_time", 0.2))

        self.power_toggle_pin = self.config.get("power_toggle_button_pin")
        self.power_on_pin = self.config.get("power_on_button_pin")
        self.power_off_pin = self.config.get("power_off_button_pin")
        self.monitor_toggle_pin = self.config.get("monitor_toggle_button_pin")

        self.status_led_pin = self.config.get("status_led_pin")
        self.alert_led_pin = self.config.get("alert_led_pin")

        self.lcd_cfg = self.config.get("lcd", {})
        self.lcd_enabled = bool(self.lcd_cfg.get("enabled", False))

        self._buttons: List[Any] = []
        self.status_led: Optional[Any] = None
        self.alert_led: Optional[Any] = None
        self.lcd: Optional[Any] = None
        self.last_lcd_lines: List[str] = []

    def _setup_gpio(self) -> None:
        if Button is None or LED is None:
            logger.warning("[GPIO Panel] gpiozero library not available. Install via: sudo apt install python3-gpiozero")
            return

        if self.power_toggle_pin is not None:
            btn = Button(int(self.power_toggle_pin), pull_up=self.pull_up, bounce_time=self.bounce_time)
            btn.when_pressed = self._on_power_toggle_pressed
            self._buttons.append(btn)
            logger.info(f"[GPIO Panel] Bound power toggle button on BCM pin {self.power_toggle_pin}.")

        if self.power_on_pin is not None:
            btn = Button(int(self.power_on_pin), pull_up=self.pull_up, bounce_time=self.bounce_time)
            btn.when_pressed = self._on_power_on_pressed
            self._buttons.append(btn)
            logger.info(f"[GPIO Panel] Bound power ON button on BCM pin {self.power_on_pin}.")

        if self.power_off_pin is not None:
            btn = Button(int(self.power_off_pin), pull_up=self.pull_up, bounce_time=self.bounce_time)
            btn.when_pressed = self._on_power_off_pressed
            self._buttons.append(btn)
            logger.info(f"[GPIO Panel] Bound power OFF button on BCM pin {self.power_off_pin}.")

        if self.monitor_toggle_pin is not None:
            btn = Button(int(self.monitor_toggle_pin), pull_up=self.pull_up, bounce_time=self.bounce_time)
            btn.when_pressed = self._on_monitor_toggle_pressed
            self._buttons.append(btn)
            logger.info(f"[GPIO Panel] Bound monitor toggle button on BCM pin {self.monitor_toggle_pin}.")

        if self.status_led_pin is not None:
            self.status_led = LED(int(self.status_led_pin))
            logger.info(f"[GPIO Panel] Bound status LED on BCM pin {self.status_led_pin}.")

        if self.alert_led_pin is not None:
            self.alert_led = LED(int(self.alert_led_pin))
            logger.info(f"[GPIO Panel] Bound alert LED on BCM pin {self.alert_led_pin}.")

    def _setup_lcd(self) -> None:
        if not self.lcd_enabled:
            return
        if CharLCD is None:
            logger.warning("[GPIO Panel] RPLCD library not available for I2C LCD.")
            return
        try:
            expander = self.lcd_cfg.get("i2c_expander", "PCF8574")
            addr = int(str(self.lcd_cfg.get("i2c_address", "0x27")), 16)
            port = int(self.lcd_cfg.get("i2c_port", 1))
            cols = int(self.lcd_cfg.get("cols", 16))
            rows = int(self.lcd_cfg.get("rows", 2))
            self.lcd = CharLCD(
                i2c_expander=expander,
                address=addr,
                port=port,
                cols=cols,
                rows=rows,
                charmap="A00",
                auto_linebreaks=False
            )
            self.lcd.clear()
            logger.info(f"[GPIO Panel] Initialized I2C LCD ({cols}x{rows}) at 0x{addr:02x}.")
        except Exception as e:
            logger.error(f"[GPIO Panel] Failed to initialize I2C LCD: {e}")

    def _on_power_toggle_pressed(self) -> None:
        if not self.controller:
            return
        logger.info("[GPIO Panel] Physical power toggle button pressed.")
        if getattr(self.controller, "is_manually_shut_down", False):
            threading.Thread(target=self.controller.power_on_all, daemon=True).start()
        else:
            threading.Thread(target=self.controller.power_off_all, daemon=True).start()

    def _on_power_on_pressed(self) -> None:
        if not self.controller:
            return
        logger.info("[GPIO Panel] Physical power ON button pressed.")
        threading.Thread(target=self.controller.power_on_all, daemon=True).start()

    def _on_power_off_pressed(self) -> None:
        if not self.controller:
            return
        logger.info("[GPIO Panel] Physical power OFF button pressed.")
        threading.Thread(target=self.controller.power_off_all, daemon=True).start()

    def _on_monitor_toggle_pressed(self) -> None:
        if not self.controller:
            return
        logger.info("[GPIO Panel] Physical monitor toggle button pressed.")
        threading.Thread(target=self.controller.toggle_monitor, daemon=True).start()

    def _update_outputs(self) -> None:
        if not self.controller:
            return

        status = self.controller.get_system_status()
        devices = status.get("devices", {})

        dell_info = devices.get("dell_r730xd", {})
        mcafee_info = devices.get("mcafee_rmm3", {})
        mon_info = devices.get("hdmi_monitor", {})

        dell_on = dell_info.get("power") == "on"
        emergency = bool(dell_info.get("emergency_mode", False))

        if self.status_led is not None:
            if dell_on:
                self.status_led.on()
            else:
                self.status_led.off()

        if self.alert_led is not None:
            if emergency:
                self.alert_led.on()
            else:
                self.alert_led.off()

        if self.lcd is not None:
            cols = int(self.lcd_cfg.get("cols", 16))
            dell_pwr = str(dell_info.get("power", "--")).upper()
            temp = dell_info.get("temperature_c")
            temp_str = f"{temp}C" if temp is not None else "--C"
            fan = dell_info.get("fan_speed_percent")
            fan_str = f"{fan}%" if fan is not None else "--%"

            fw_pwr = str(mcafee_info.get("power", "--")).upper()
            mon_pwr = str(mon_info.get("power", "--")).upper()

            line1 = f"R730:{dell_pwr} {temp_str} {fan_str}"[:cols].ljust(cols)
            line2 = f"FW:{fw_pwr} MON:{mon_pwr}"[:cols].ljust(cols)
            lines = [line1, line2]

            if lines != self.last_lcd_lines:
                try:
                    self.lcd.home()
                    self.lcd.write_string(line1 + "\r\n" + line2)
                    self.last_lcd_lines = lines
                except Exception as e:
                    logger.debug(f"[GPIO Panel] LCD update error: {e}")

    def on_boot(self) -> None:
        self._setup_gpio()
        self._setup_lcd()
        self._update_outputs()

    def poll(self) -> None:
        self._update_outputs()

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "gpio_ready": Button is not None,
            "lcd_ready": self.lcd is not None,
            "bound_buttons": len(self._buttons),
        }

    def cleanup(self) -> None:
        for btn in self._buttons:
            try:
                btn.close()
            except Exception:
                pass
        if self.status_led is not None:
            try:
                self.status_led.off()
                self.status_led.close()
            except Exception:
                pass
        if self.alert_led is not None:
            try:
                self.alert_led.off()
                self.alert_led.close()
            except Exception:
                pass
        if self.lcd is not None:
            try:
                self.lcd.clear()
                self.lcd.close(clear=True)
            except Exception:
                pass
