# Dell PowerEdge R730xd Silent Boot & Homelab Kiosk Controller

Modular controller designed for a **Raspberry Pi** (Raspberry Pi OS Bookworm / Debian 12). It eliminates server jet-engine noise during power recovery on **Dell PowerEdge R730xd** servers, coordinates power management for an **Intel RMM3-based McAfee firewall**, provides a **cursorless full-screen kiosk frontend** for **Homarr**, and supports **remote power control via HTTP API, CLI, and Raspberry Pi GPIO hardware (buttons, LEDs, I2C LCD)**.

---

## Modular Device Architecture

Every managed server or hardware peripheral is isolated inside `src/devices/[DEVICE].py` implementing `BaseDevice`:

* `src/devices/dell_r730xd.py`: Dell iDRAC 8 silent boot, manual fan speed control, SDR temperature polling, thermal curve evaluation, watchdog failsafe, and silent ACPI graceful shutdown.
* `src/devices/mcafee_rmm3.py`: Intel RMM3 BMC discovery, automatic boot power-on, and graceful ACPI shutdown.
* `src/devices/gpio_panel.py`: Raspberry Pi GPIO integration for physical power buttons, status/alert LEDs, and I2C character LCD displays (`16x2` / `20x4`).

In `src/controller.py`, devices are registered in `REGISTERED_DEVICES`:
```python
REGISTERED_DEVICES = {
    "dell_r730xd": "src.devices.dell_r730xd.DellR730xd",
    "mcafee_rmm3": "src.devices.mcafee_rmm3.McAfeeRMM3",
    "gpio_panel": "src.devices.gpio_panel.GpioPanel",
}
```

Any device can be enabled or disabled directly in `config.yaml` via `enabled: true/false` without editing Python files.

---

## Cursor Hiding in Chromium Kiosk

To guarantee the mouse cursor is completely invisible on both the local splash page and Homarr (under both Wayland/Labwc/Wayfire and X11):
1. `kiosk/hide_cursor_ext/` is loaded into Chromium via `--load-extension`, injecting `* { cursor: none !important; }` at `document_start` across all pages and frames.
2. `kiosk/launch_kiosk.sh` also invokes `unclutter -idle 0 -root` and moves the pointer off-screen.

---

## Remote Power Control

### 1. CLI (`homelabctl` or `./main.py`)
```bash
homelabctl status
homelabctl power-off
homelabctl power-on
```

### 2. HTTP REST API (Port `8080`)
Protected by `API_TOKEN` in `.env` (pass via `Authorization: Bearer <TOKEN>`, `X-API-Token: <TOKEN>`, or `?token=<TOKEN>`):

* `GET /api/status` — Full JSON status of all devices, temperatures, and fan speeds.
* `POST /api/power/on` (or `GET`) — Triggers general silent boot across all homelab devices.
* `POST /api/power/off` (or `GET`) — Triggers silent ACPI graceful shutdown across all homelab devices.
* `POST /api/device/<device_name>/on` — Powers on a specific device (`dell_r730xd`, `mcafee_rmm3`).
* `POST /api/device/<device_name>/off` — Powers off a specific device.

---

## Installation & Update

```bash
cd /home/manu/idrac-silent-boot
sudo ./install.sh
sudo systemctl restart idrac-silent-boot.service
```

---

## Unit Testing

```bash
python3 -m unittest tests/test_components.py
```
