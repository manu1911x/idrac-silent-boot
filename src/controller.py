import os
import sys
import time
import signal
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Cargar soporte de YAML (con mensaje claro si falta el paquete del sistema)
try:
    import yaml
except ImportError:
    yaml = None

from .devices.dell_r730xd import DellR730xd
from .devices.intel_rmm3 import IntelRMM3
from .thermal import ThermalManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("silent_boot.main")


def parse_env_file(filepath: Path) -> dict:
    """
    Parser liviano para archivos .env usando únicamente la librería estándar de Python.
    No requiere dependencias externas adicionales.
    """
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
    """Localiza los archivos .env y config.yaml en rutas estándar."""
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


class SilentBootDaemon:
    def __init__(self):
        self.running = True
        self.env_path, self.yaml_path = find_config_paths()

        if self.env_path.exists():
            parse_env_file(self.env_path)
            logger.info(f"Cargado entorno desde {self.env_path}")
        else:
            logger.warning(f"No se encontró archivo .env en {self.env_path} (usando variables de entorno existentes).")

        self.config = {}
        if self.yaml_path.exists():
            if yaml is not None:
                with open(self.yaml_path, "r", encoding="utf-8") as f:
                    self.config = yaml.safe_load(f) or {}
                logger.info(f"Cargada configuración YAML desde {self.yaml_path}")
            else:
                logger.error("El módulo 'pyyaml' no está disponible. Ejecuta: sudo apt install python3-yaml")
        else:
            logger.warning(f"No se encontró config.yaml en {self.yaml_path}, usando parámetros predeterminados.")

        # Configuración general
        gen_cfg = self.config.get("general", {})
        self.poll_interval = gen_cfg.get("poll_interval_seconds", 5)
        self.bmc_boot_timeout = gen_cfg.get("bmc_boot_wait_timeout_seconds", 300)
        self.bmc_probe_interval = gen_cfg.get("bmc_probe_interval_seconds", 3)

        # Dispositivos
        dell_cfg = self.config.get("dell_r730xd", {})
        self.dell_enabled = dell_cfg.get("enabled", True)
        self.dell = None
        self.thermal_manager = None

        if self.dell_enabled:
            host = os.getenv("DELL_IDRAC_HOST", "192.168.1.120")
            user = os.getenv("DELL_IDRAC_USER", "root")
            password = os.getenv("DELL_IDRAC_PASSWORD", "calvin")
            self.dell = DellR730xd(host=host, user=user, password=password)
            self.thermal_manager = ThermalManager(self.dell, dell_cfg)
            self.dell_initial_speed = dell_cfg.get("initial_fan_speed_percent", 5)
            self.dell_power_on_delay = dell_cfg.get("power_on_delay_seconds", 2)

        mcafee_cfg = self.config.get("mcafee_rmm3", {})
        self.mcafee_enabled = mcafee_cfg.get("enabled", True)
        self.mcafee = None

        if self.mcafee_enabled:
            m_host = os.getenv("MCAFEE_RMM3_HOST", "192.168.1.121")
            m_user = os.getenv("MCAFEE_RMM3_USER", "admin")
            m_password = os.getenv("MCAFEE_RMM3_PASSWORD", "password")
            self.mcafee = IntelRMM3(host=m_host, user=m_user, password=m_password)

        self._setup_signals()

    def _setup_signals(self):
        """Captura señales de terminación para garantizar que Dell vuelva a control automático."""
        signal.signal(signal.SIGINT, self._handle_exit)
        signal.signal(signal.SIGTERM, self._handle_exit)

    def _handle_exit(self, signum, frame):
        logger.warning("Señal de parada recibida. Ejecutando parada limpia y segura...")
        self.running = False
        if self.dell and self.dell.manual_mode_active:
            logger.info("Restaurando control automático de ventiladores de Dell antes de salir...")
            self.dell.restore_auto_control()
        sys.exit(0)

    def wait_and_init_dell(self):
        """Espera a que el iDRAC responda por red y ejecuta el arranque silencioso."""
        if not self.dell:
            return

        logger.info(f"[Dell R730xd] Esperando a que el iDRAC ({self.dell.host}) esté en línea tras el encendido...")
        start_time = time.time()
        while self.running and (time.time() - start_time < self.bmc_boot_timeout):
            if self.dell.is_online():
                logger.info(f"[Dell R730xd] ¡iDRAC detectado en línea!")
                break
            time.sleep(self.bmc_probe_interval)
        else:
            if not self.running:
                return
            logger.error(f"[Dell R730xd] Timeout ({self.bmc_boot_timeout}s) esperando al iDRAC.")
            return

        # Comprobar estado de energía
        status = self.dell.get_power_status()
        logger.info(f"[Dell R730xd] Estado actual del chasis: {status}")

        if status == "off":
            logger.info("[Dell R730xd] El servidor está apagado. Iniciando secuencia SILENT BOOT:")
            logger.info(f"[Dell R730xd] 1. Activando control manual al {self.dell_initial_speed}% RPM previo al arranque...")
            self.dell.enable_manual_control()
            self.dell.set_fan_speed_percent(self.dell_initial_speed)
            
            logger.info(f"[Dell R730xd] 2. Esperando {self.dell_power_on_delay}s para aplicar perfil térmico...")
            time.sleep(self.dell_power_on_delay)

            logger.info("[Dell R730xd] 3. Enviando comando de encendido 'chassis power on'...")
            self.dell.power_on()
        elif status == "on":
            logger.info("[Dell R730xd] El servidor ya estaba encendido. Activando control manual y curva térmica...")
            self.dell.enable_manual_control()
            self.dell.set_fan_speed_percent(self.dell_initial_speed)

    def wait_and_init_mcafee(self):
        """Espera a que el BMC Intel RMM3 del firewall responda y lo enciende si está apagado."""
        if not self.mcafee:
            return

        logger.info(f"[McAfee RMM3] Esperando a que el BMC ({self.mcafee.host}) esté en línea...")
        start_time = time.time()
        while self.running and (time.time() - start_time < self.bmc_boot_timeout):
            if self.mcafee.is_online():
                logger.info(f"[McAfee RMM3] ¡BMC detectado en línea!")
                break
            time.sleep(self.bmc_probe_interval)
        else:
            if not self.running:
                return
            logger.error(f"[McAfee RMM3] Timeout ({self.bmc_boot_timeout}s) esperando al RMM3.")
            return

        status = self.mcafee.get_power_status()
        logger.info(f"[McAfee RMM3] Estado actual del chasis: {status}")
        if status == "off":
            self.mcafee.power_on()

    def run_boot_phase(self):
        """Ejecuta en paralelo la detección y encendido de Dell y McAfee."""
        logger.info("Iniciando fase de arranque y comprobación de BMCs en paralelo...")
        with ThreadPoolExecutor(max_workers=2) as executor:
            fut_dell = executor.submit(self.wait_and_init_dell) if self.dell_enabled else None
            fut_mcafee = executor.submit(self.wait_and_init_mcafee) if self.mcafee_enabled else None

            if fut_dell:
                fut_dell.result()
            if fut_mcafee:
                fut_mcafee.result()
        logger.info("Fase de encendido inicial completada.")

    def run_monitoring_loop(self):
        """Bucle continuo de control de temperatura y ajuste de ventiladores para Dell R730xd."""
        if not self.dell_enabled or not self.dell or not self.thermal_manager:
            logger.info("Dell R730xd no configurado para monitoreo continuo. Finalizando.")
            return

        logger.info(f"Iniciando bucle de monitoreo térmico (intervalo: {self.poll_interval}s)...")
        while self.running:
            try:
                p_status = self.dell.get_power_status()
                if p_status != "on":
                    logger.debug(f"[Dell R730xd] Chasis no está 'on' ({p_status}). Esperando...")
                    time.sleep(self.poll_interval)
                    continue

                temp, readings = self.dell.read_temperatures()
                if temp is not None:
                    self.thermal_manager.process_temperature(temp, readings)
                else:
                    self.thermal_manager.handle_read_failure()

            except Exception as e:
                logger.error(f"Error imprevisto en el bucle de monitoreo: {e}")
                if self.thermal_manager:
                    self.thermal_manager.handle_read_failure()

            time.sleep(self.poll_interval)


def main():
    daemon = SilentBootDaemon()
    daemon.run_boot_phase()
    daemon.run_monitoring_loop()


if __name__ == "__main__":
    main()
