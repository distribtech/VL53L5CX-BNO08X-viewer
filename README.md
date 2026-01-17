# VL53L5CX Point Cloud Viewer

Real-time 3D visualization of VL53L5CX ToF sensor data using Viser.

## Hardware Setup

**Components:**
- ESP32 DevKit V1
- Pololu VL53L5CX carrier board

**Wiring:**
| VL53L5CX | ESP32   |
| -------- | ------- |
| VIN      | 3V3     |
| GND      | GND     |
| SDA      | GPIO 21 |
| SCL      | GPIO 22 |
| LPn      | GPIO 19 |

## Installation

### ESP32 Firmware

```bash
# Install library
arduino-cli lib install "SparkFun VL53L5CX Arduino Library"

# Compile and upload
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/vl53l5cx_reader
arduino-cli upload --fqbn esp32:esp32:esp32 --port /dev/cu.usbserial-0001 firmware/vl53l5cx_reader
```

### Python Viewer

```bash
pip install -r viewer/requirements.txt
```

## Usage

```bash
python -m viewer --port /dev/cu.usbserial-0001
```

Open http://localhost:8080 in your browser.

**Options:**
- `--port`, `-p`: Serial port (default: `/dev/cu.usbserial-0001`)
- `--baud`, `-b`: Baud rate (default: `115200`)
- `--viser-port`: Viser server port (default: `8080`)

## Sensor Specs

- **Resolution:** 8x8 zones (64 points)
- **FoV:** 65° diagonal
- **Range:** 20mm - 4000mm
- **Update rate:** 15 Hz
- **Distance type:** Perpendicular (z-axis), not radial. The chip converts ToF measurements internally so a flat surface perpendicular to the sensor reports the same distance across all zones.

## Serial Protocol

The ESP32 streams JSON over serial at 115200 baud:

```json
{"distances":[d0,d1,...,d63],"status":[s0,s1,...,s63]}
```

- `distances`: 64 values in mm (perpendicular distance)
- `status`: 64 values where `5` = valid measurement

Zones are row-major: 0-7 = row 0, 8-15 = row 1, etc.
