# VL53L5CX-BNO08X-viewer Mandates

## Project Mission
Real-time 3D point cloud visualization for the VL53L5CX multi-zone Time-of-Flight (ToF) sensor, integrated with a BNO085 IMU for orientation tracking. The goal is to provide a low-latency, immersive representation of the environment.

## Tech Stack
- **Firmware**: C++/Arduino for ESP32.
  - Libraries: `SparkFun VL53L5CX Arduino Library`, `SparkFun BNO08x Cortex Based IMU`.
  - Tooling: `arduino-cli`.
- **Viewer**: Python 3.8+.
  - Libraries: `viser` (3D rendering/UI), `pyserial`, `numpy`, `scipy`, `trimesh`.
  - Environment: `.venv` in root.

## Core Components
- `/firmware/vl53l5cx_reader/`: Streams 8x8 distance data and IMU quaternions as JSON over serial.
- `/viewer/serial_reader.py`: Handles asynchronous serial communication and JSON parsing.
- `/viewer/scene.py`: Manages the `viser` 3D scene and object updates.
- `/viewer/geometry.py`: Contains math for point cloud generation and transformations.
- `/viewer/filters.py`: Signal processing for smoothing and noise reduction.

## Engineering Standards
- **Firmware Performance**: Maintain a stable 15Hz output for the 8x8 sensor. Avoid heavy blocking logic in the main loop.
- **Protocol Stability**: The serial JSON format (`{"distances": [...], "status": [...], "quat": [...]}`) is foundational. Changes must be backwards-compatible or explicitly updated in both firmware and viewer.
- **Python Style**: Adhere to PEP 8. Use type hints for all new functions.
- **Testing**:
  - Python tests reside in `viewer/tests/`.
  - Use `pytest` for running tests.
  - Every bug fix or new feature in the viewer must include a corresponding test case.
- **Validation**:
  - Firmware changes must be verified with `arduino-cli compile --fqbn esp32:esp32:esp32 firmware/vl53l5cx_reader`.
  - Viewer changes must pass all tests in `viewer/tests/`.

## Workflow
- **Research**: When modifying sensor behavior, consult the [VL53L5CX datasheet](https://www.st.com/resource/en/datasheet/vl53l5cx.pdf).
- **Strategy**: Propose changes to the JSON protocol or geometry calculations before implementation.
- **Execution**: Apply changes surgically. Update tests alongside code.
- **Verification**: Run `pytest` and, if hardware is available (or using mock data), verify the `viser` output.
