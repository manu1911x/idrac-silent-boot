import unittest
from unittest.mock import MagicMock
from pathlib import Path
import tempfile

from src.controller import parse_env_file
from src.devices.dell_r730xd import DellR730xd
from src.thermal import ThermalManager


class TestSilentBootComponents(unittest.TestCase):

    def test_parse_env_file(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write("# Comentario\n")
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
        dell = DellR730xd("127.0.0.1", "user", "pass")
        # Simular salida típica de 'ipmitool sdr type Temperature'
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

    def test_thermal_manager_curve(self):
        mock_dell = MagicMock(spec=DellR730xd)
        mock_dell.manual_mode_active = True
        mock_dell.current_speed_percent = None

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
        tm = ThermalManager(mock_dell, config)

        # Temp 40°C -> debe seleccionar 5%
        tm.process_temperature(40, {})
        mock_dell.set_fan_speed_percent.assert_called_with(5)

        # Temp 60°C -> debe seleccionar 20%
        mock_dell.current_speed_percent = 5
        tm.process_temperature(60, {})
        mock_dell.set_fan_speed_percent.assert_called_with(20)

        # Temp 72°C -> debe seleccionar 50%
        mock_dell.current_speed_percent = 20
        tm.process_temperature(72, {})
        mock_dell.set_fan_speed_percent.assert_called_with(50)

    def test_watchdog_failure_trigger(self):
        mock_dell = MagicMock(spec=DellR730xd)
        mock_dell.manual_mode_active = True

        config = {
            "watchdog": {
                "max_consecutive_failures": 3,
                "emergency_temp_celsius": 80,
                "emergency_recovery_celsius": 65
            },
            "fan_curve": []
        }
        tm = ThermalManager(mock_dell, config)

        # 1er y 2do fallo no disparan watchdog todavía
        tm.handle_read_failure()
        tm.handle_read_failure()
        mock_dell.restore_auto_control.assert_not_called()

        # 3er fallo consecutivo -> activa watchdog y restaura control automático
        tm.handle_read_failure()
        mock_dell.restore_auto_control.assert_called_once()
        self.assertTrue(tm.emergency_mode_active)

    def test_emergency_high_temp(self):
        mock_dell = MagicMock(spec=DellR730xd)
        mock_dell.manual_mode_active = True

        config = {
            "watchdog": {
                "max_consecutive_failures": 3,
                "emergency_temp_celsius": 80,
                "emergency_recovery_celsius": 65
            },
            "fan_curve": []
        }
        tm = ThermalManager(mock_dell, config)

        # Temp 85°C supera 80°C de emergencia -> entrega el control a Dell
        tm.process_temperature(85, {})
        mock_dell.restore_auto_control.assert_called_once()
        self.assertTrue(tm.emergency_mode_active)


if __name__ == "__main__":
    unittest.main()
