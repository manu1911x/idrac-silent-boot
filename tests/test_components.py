import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.controller import ControllerDaemon, parse_env_file
from src.devices.dell_r730xd import DellR730xd
from src.devices.mcafee_rmm3 import McAfeeRMM3


class TestSilentBootComponents(unittest.TestCase):

    def test_parse_env_file(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write("DELL_IDRAC_HOST=192.168.1.50\n")
            f.write("DELL_IDRAC_USER=\"admin\"\n")
            f.write("DELL_IDRAC_PASSWORD='secret_password'\n")
            f.write("HOMARR_URL=http://192.168.1.50:7575\n")
            f.flush()
            temp_path = Path(f.name)

        parsed = parse_env_file(temp_path)
        self.assertEqual(parsed.get("DELL_IDRAC_HOST"), "192.168.1.50")
        self.assertEqual(parsed.get("DELL_IDRAC_USER"), "admin")
        self.assertEqual(parsed.get("DELL_IDRAC_PASSWORD"), "secret_password")
        self.assertEqual(parsed.get("HOMARR_URL"), "http://192.168.1.50:7575")
        temp_path.unlink()

    def test_dell_temperature_parser(self):
        dell = DellR730xd("dell_r730xd", {}, {})
        mock_output = (
            "Inlet Temp       | 04h | ok  |  7.1 | 21 degrees C\n"
            "Temp             | 0Eh | ok  |  3.1 | 42 degrees C\n"
            "Temp             | 0Fh | ok  |  3.2 | 48 degrees C\n"
            "Exhaust Temp     | 01h | ok  |  7.1 | 35 degrees C\n"
        )
        dell.ipmi.run_command = MagicMock(return_value=(True, mock_output))
        max_temp, readings = dell.read_temperatures()

        self.assertEqual(max_temp, 48)
        self.assertIn("Inlet Temp", readings)
        self.assertEqual(readings["Inlet Temp"], 21)

    def test_dell_fan_curve_and_watchdog(self):
        config = {
            "watchdog": {
                "max_consecutive_failures": 3,
                "emergency_temp_celsius": 80,
                "emergency_recovery_celsius": 65
            },
            "fan_curve": [
                {"min_temp": 70, "action": "manual", "fan_speed_percent": 50},
                {"min_temp": 55, "action": "manual", "fan_speed_percent": 20},
                {"min_temp": 0, "action": "manual", "fan_speed_percent": 5}
            ]
        }
        dell = DellR730xd("dell_r730xd", config, {})
        dell.enable_manual_control = MagicMock()
        dell.restore_auto_control = MagicMock()
        dell.set_fan_speed_percent = MagicMock()

        dell.manual_mode_active = True
        dell._process_temperature(40, {})
        dell.set_fan_speed_percent.assert_called_with(5)

        dell.current_speed_percent = 5
        dell._process_temperature(60, {})
        dell.set_fan_speed_percent.assert_called_with(20)

        dell._process_temperature(85, {})
        dell.restore_auto_control.assert_called_once()
        self.assertTrue(dell.emergency_mode_active)

        dell.restore_auto_control.reset_mock()
        dell.emergency_mode_active = False
        dell._handle_read_failure()
        dell._handle_read_failure()
        dell.restore_auto_control.assert_not_called()

        dell._handle_read_failure()
        dell.restore_auto_control.assert_called_once()

    def test_controller_device_filtering(self):
        daemon = ControllerDaemon.__new__(ControllerDaemon)
        daemon.config = {
            "general": {"poll_interval_seconds": 10},
            "dell_r730xd": {"enabled": True},
            "mcafee_rmm3": {"enabled": False}
        }
        daemon.general_config = daemon.config["general"]
        daemon.active_devices = []
        daemon._init_devices()

        device_names = [dev.name for dev in daemon.active_devices]
        self.assertIn("dell_r730xd", device_names)
        self.assertNotIn("mcafee_rmm3", device_names)


if __name__ == "__main__":
    unittest.main()
