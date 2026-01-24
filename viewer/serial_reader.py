"""Serial communication with VL53L5CX sensor via ESP32."""

import json
import threading
import time

import numpy as np
import serial

from . import config


class SerialReader:
    """Background thread for reading sensor data over serial."""

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self.serial: serial.Serial | None = None
        self.running = False

        # Data storage
        self.distances = np.zeros(config.NUM_ZONES, dtype=np.float32)
        self.status = np.zeros(config.NUM_ZONES, dtype=np.uint8)
        self.quaternion = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)  # wxyz identity
        self._data_lock = threading.Lock()

        # FPS tracking
        self._frame_count = 0
        self._last_fps_time = time.time()
        self._data_fps = 0.0

        self._thread: threading.Thread | None = None

    @property
    def data_fps(self) -> float:
        """Current data frame rate from sensor."""
        return self._data_fps

    def connect(self):
        """Open serial connection."""
        print(f"Connecting to {self.port} at {self.baud} baud...")
        self.serial = serial.Serial(self.port, self.baud, timeout=1)
        time.sleep(2)  # Wait for ESP32 to initialize
        self.serial.reset_input_buffer()
        print("Serial connected.")

    def start(self):
        """Start the reader thread."""
        if self._thread is not None:
            return

        self.running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the reader thread and close serial."""
        self.running = False
        if self.serial:
            self.serial.close()
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None

    def get_data(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Get a copy of the latest distance, status, and quaternion data.

        Returns:
            Tuple of (distances, status, quaternion) arrays
        """
        with self._data_lock:
            return self.distances.copy(), self.status.copy(), self.quaternion.copy()

    def _reconnect(self) -> bool:
        """Attempt to reconnect to serial port.

        Returns:
            True if reconnection successful, False otherwise
        """
        try:
            if self.serial:
                try:
                    self.serial.close()
                except Exception:
                    pass
            self.serial = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(0.5)  # Brief wait for device to initialize
            self.serial.reset_input_buffer()
            print("Serial reconnected.")
            return True
        except (serial.SerialException, OSError):
            return False

    def _read_loop(self):
        """Background thread to read serial data."""
        print("Serial reader thread started")
        while self.running:
            try:
                if self.serial and self.serial.is_open:
                    line = self.serial.readline()
                    if line:
                        line_str = line.decode("utf-8", errors="ignore").strip()
                        if line_str.startswith("{"):
                            try:
                                data = json.loads(line_str)
                                if "distances" in data and "status" in data:
                                    with self._data_lock:
                                        self.distances = np.array(
                                            data["distances"], dtype=np.float32
                                        )
                                        self.status = np.array(
                                            data["status"], dtype=np.uint8
                                        )
                                        if "quat" in data:
                                            self.quaternion = np.array(
                                                data["quat"], dtype=np.float32
                                            )
                                    # Track data FPS
                                    self._frame_count += 1
                                    now = time.time()
                                    elapsed = now - self._last_fps_time
                                    if elapsed >= 1.0:
                                        self._data_fps = self._frame_count / elapsed
                                        self._frame_count = 0
                                        self._last_fps_time = now
                            except json.JSONDecodeError:
                                pass
            except (serial.SerialException, OSError):
                if not self.running:
                    break
                print("Serial connection lost, attempting to reconnect...")
                self._data_fps = 0.0  # Reset FPS indicator
                while self.running:
                    if self._reconnect():
                        break
                    time.sleep(1)  # Wait before retry
