"""Wi-Fi communication with VL53L5CX sensor via ESP32 TCP stream."""

import json
import logging
import math
import socket
import threading
import time

import numpy as np

from . import config

logger = logging.getLogger("vl53l5cx_viewer.wifi")


class WifiReader:
    """Background thread for reading sensor data over Wi-Fi TCP."""

    def __init__(self, host: str = "192.168.4.1", port: int = 8765, timeout: float = 2.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._buffer = ""
        self.running = False

        self.distances = np.zeros(config.NUM_ZONES, dtype=np.float32)
        self.status = np.zeros(config.NUM_ZONES, dtype=np.uint8)
        self.quaternion = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        self._imu_connected = False
        self._data_lock = threading.Lock()

        self._frame_count = 0
        self._last_fps_time = time.time()
        self._data_fps = 0.0

        self._thread: threading.Thread | None = None
        self._version_checked = False

    @property
    def data_fps(self) -> float:
        with self._data_lock:
            return self._data_fps

    @property
    def imu_connected(self) -> bool:
        with self._data_lock:
            return self._imu_connected

    def connect(self):
        logger.info("Connecting to ESP32 Wi-Fi stream at %s:%d...", self.host, self.port)
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._buffer = ""
        logger.info("Wi-Fi stream connected")

    def start(self):
        if self._thread is not None:
            return

        self.running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None

    def get_data(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        with self._data_lock:
            return self.distances.copy(), self.status.copy(), self.quaternion.copy()

    def _validate_distances(self, distances: list) -> bool:
        for d in distances:
            if not isinstance(d, (int, float)):
                return False
            if math.isnan(d) or math.isinf(d):
                return False
        return True

    def _validate_quaternion(self, quat: list) -> bool:
        if len(quat) != 4:
            return False
        for q in quat:
            if not isinstance(q, (int, float)):
                return False
            if math.isnan(q) or math.isinf(q):
                return False
        return True

    def _reconnect(self) -> bool:
        try:
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
            self._buffer = ""
            logger.info("Wi-Fi stream reconnected")
            return True
        except OSError as e:
            logger.debug("Wi-Fi reconnect failed: %s", e)
            return False

    def _process_line(self, line_str: str):
        if not line_str.startswith("{"):
            return
        try:
            data = json.loads(line_str)
        except json.JSONDecodeError as e:
            logger.debug("JSON decode error: %s", e)
            return

        if "distances" not in data or "status" not in data:
            return

        distances = data["distances"]
        status = data["status"]
        if len(distances) != config.NUM_ZONES or len(status) != config.NUM_ZONES:
            logger.warning(
                "Invalid array lengths: distances=%d, status=%d (expected %d)",
                len(distances), len(status), config.NUM_ZONES,
            )
            return
        if not self._validate_distances(distances):
            logger.warning("Invalid distance values detected (NaN/Inf)")
            return
        if "quat" in data and not self._validate_quaternion(data["quat"]):
            logger.warning("Invalid quaternion values detected (NaN/Inf)")
            data.pop("quat")

        if not self._version_checked:
            self._version_checked = True
            firmware_version = data.get("v")
            if firmware_version is None:
                logger.warning("No version in data. Firmware may be outdated - consider reflashing.")
            elif firmware_version != config.VERSION:
                logger.warning(
                    "Version mismatch: firmware=%s, viewer=%s. Consider reflashing the ESP32.",
                    firmware_version, config.VERSION,
                )

        with self._data_lock:
            self.distances = np.array(distances, dtype=np.float32)
            self.status = np.array(status, dtype=np.uint8)
            if "quat" in data:
                self.quaternion = np.array(data["quat"], dtype=np.float32)
                self._imu_connected = True

        self._frame_count += 1
        now = time.time()
        elapsed = now - self._last_fps_time
        if elapsed >= 1.0:
            with self._data_lock:
                self._data_fps = self._frame_count / elapsed
            self._frame_count = 0
            self._last_fps_time = now

    def _read_loop(self):
        logger.info("Wi-Fi reader thread started")
        while self.running:
            try:
                if self.sock:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        raise OSError("Connection closed by peer")
                    self._buffer += chunk.decode("utf-8", errors="ignore")
                    while "\n" in self._buffer:
                        line, self._buffer = self._buffer.split("\n", 1)
                        self._process_line(line.strip())
            except (OSError, socket.timeout) as e:
                if not self.running:
                    break
                logger.warning("Wi-Fi connection lost: %s", e)
                with self._data_lock:
                    self._data_fps = 0.0
                while self.running:
                    if self._reconnect():
                        break
                    time.sleep(1)
