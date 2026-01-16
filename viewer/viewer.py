#!/usr/bin/env python3
"""
VL53L5CX Point Cloud Viewer

Reads distance data from ESP32 over serial and visualizes
as a real-time 3D point cloud using Viser.
"""

import argparse
import json
import threading
import time

import numpy as np
import serial
import viser


class VL53L5CXViewer:
    """Real-time point cloud viewer for VL53L5CX ToF sensor."""

    # Sensor specs
    RESOLUTION = 8  # 8x8 zones
    NUM_ZONES = 64
    FOV_DIAGONAL_DEG = 65.0  # Diagonal field of view
    MAX_RANGE_MM = 4000
    MIN_RANGE_MM = 20

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self.serial: serial.Serial | None = None
        self.running = False

        # Pre-compute zone angles (accounting for lens flip)
        # The sensor lens flips the image, so zone 0 corresponds to top-right
        self._compute_zone_angles()

        # Latest data
        self.distances = np.zeros(self.NUM_ZONES, dtype=np.float32)
        self.status = np.zeros(self.NUM_ZONES, dtype=np.uint8)
        self.data_lock = threading.Lock()

    def _compute_zone_angles(self):
        """Pre-compute the angle for each zone center."""
        # Convert diagonal FoV to per-axis FoV (assuming square sensor)
        # For a square, diagonal = side * sqrt(2), so side = diagonal / sqrt(2)
        fov_per_axis_deg = self.FOV_DIAGONAL_DEG / np.sqrt(2)
        fov_per_axis_rad = np.deg2rad(fov_per_axis_deg)

        # Angle step per zone
        angle_step = fov_per_axis_rad / self.RESOLUTION

        # Zone center offsets from optical axis
        # Zones are numbered row-major: 0-7 = row 0, 8-15 = row 1, etc.
        # Due to lens flip, we invert the mapping
        self.zone_angles_x = np.zeros(self.NUM_ZONES)
        self.zone_angles_y = np.zeros(self.NUM_ZONES)

        for i in range(self.NUM_ZONES):
            row = i // self.RESOLUTION
            col = i % self.RESOLUTION

            # Center of zone relative to center of grid (0-7 -> -3.5 to 3.5)
            # Flip due to lens inversion
            col_offset = (self.RESOLUTION - 1) / 2 - col  # Flip X
            row_offset = (self.RESOLUTION - 1) / 2 - row  # Flip Y

            self.zone_angles_x[i] = col_offset * angle_step
            self.zone_angles_y[i] = row_offset * angle_step

    def distances_to_points(self, distances: np.ndarray) -> np.ndarray:
        """Convert distance measurements to 3D point coordinates.

        The sensor is assumed to be pointing UP (+Z direction),
        lying flat on a horizontal surface.

        Args:
            distances: Array of 64 distance values in mm

        Returns:
            Nx3 array of (x, y, z) coordinates in meters
        """
        # Convert to meters
        d_meters = distances / 1000.0

        # Calculate 3D positions
        # Z is the distance (height above sensor)
        # X and Y are lateral offsets based on angle
        x = d_meters * np.tan(self.zone_angles_x)
        y = d_meters * np.tan(self.zone_angles_y)
        z = d_meters

        return np.column_stack([x, y, z])

    def get_colors(self, distances: np.ndarray, status: np.ndarray) -> np.ndarray:
        """Generate colors based on distance and validity.

        Valid points: Blue (close) to Red (far)
        Invalid points: Gray
        """
        colors = np.zeros((len(distances), 3), dtype=np.uint8)

        # Normalize distances for color mapping
        d_norm = np.clip(
            (distances - self.MIN_RANGE_MM) / (self.MAX_RANGE_MM - self.MIN_RANGE_MM),
            0,
            1,
        )

        # Valid status is 5, treat others as potentially invalid
        valid = status == 5

        # Color gradient: blue (0,0,255) -> cyan -> green -> yellow -> red (255,0,0)
        # Using HSV-like interpolation through RGB
        for i in range(len(distances)):
            if valid[i] and distances[i] >= self.MIN_RANGE_MM:
                t = d_norm[i]
                # Blue to Red gradient
                colors[i, 0] = int(t * 255)  # R increases with distance
                colors[i, 1] = int((1 - abs(2 * t - 1)) * 200)  # G peaks in middle
                colors[i, 2] = int((1 - t) * 255)  # B decreases with distance
            else:
                # Invalid: gray
                colors[i] = [128, 128, 128]

        return colors

    def serial_reader(self):
        """Background thread to read serial data."""
        print("Serial reader thread started")
        while self.running:
            try:
                if self.serial:
                    line = self.serial.readline()
                    if line:
                        line_str = line.decode("utf-8", errors="ignore").strip()
                        if line_str.startswith("{"):
                            try:
                                data = json.loads(line_str)
                                if "distances" in data and "status" in data:
                                    with self.data_lock:
                                        self.distances = np.array(
                                            data["distances"], dtype=np.float32
                                        )
                                        self.status = np.array(
                                            data["status"], dtype=np.uint8
                                        )
                            except json.JSONDecodeError:
                                pass
            except serial.SerialException as e:
                print(f"Serial error: {e}")
                break

    def run(self, host: str = "0.0.0.0", port: int = 8080):
        """Start the viewer."""
        # Connect to serial
        print(f"Connecting to {self.port} at {self.baud} baud...")
        self.serial = serial.Serial(self.port, self.baud, timeout=1)
        time.sleep(2)  # Wait for ESP32 to initialize
        self.serial.reset_input_buffer()
        print("Serial connected.")

        # Start serial reader thread
        self.running = True
        reader_thread = threading.Thread(target=self.serial_reader, daemon=True)
        reader_thread.start()

        # Start Viser server
        server = viser.ViserServer(host=host, port=port)
        print(f"Viser server started at http://localhost:{port}")

        # Add coordinate frame at origin
        server.scene.add_frame("/origin", axes_length=0.1, axes_radius=0.005)

        # Add a grid on the XY plane for reference
        grid_size = 2.0  # meters
        grid_points = []
        for i in range(-10, 11):
            # Lines parallel to X
            grid_points.append([[-grid_size, i * 0.2, 0], [grid_size, i * 0.2, 0]])
            # Lines parallel to Y
            grid_points.append([[i * 0.2, -grid_size, 0], [i * 0.2, grid_size, 0]])

        for idx, (start, end) in enumerate(grid_points):
            server.scene.add_spline_catmull_rom(
                f"/grid/line_{idx}",
                positions=np.array([start, end]),
                color=(80, 80, 80),
                line_width=1.0,
            )

        # Add info panel
        with server.gui.add_folder("Sensor Info"):
            distance_text = server.gui.add_text("Status", initial_value="Waiting...")
            fps_text = server.gui.add_text("FPS", initial_value="0")

        # Main visualization loop
        frame_times = []
        try:
            while True:
                frame_start = time.time()

                with self.data_lock:
                    distances = self.distances.copy()
                    status = self.status.copy()

                if np.any(distances > 0):
                    # Convert to 3D points
                    points = self.distances_to_points(distances)
                    colors = self.get_colors(distances, status)

                    # Filter out invalid points (keep only valid ones for display)
                    valid_mask = (status == 5) & (distances >= self.MIN_RANGE_MM)

                    if np.any(valid_mask):
                        server.scene.add_point_cloud(
                            "/sensor/points",
                            points=points[valid_mask].astype(np.float32),
                            colors=colors[valid_mask],
                            point_size=0.03,
                            point_shape="circle",
                        )

                        # Update info
                        valid_distances = distances[valid_mask]
                        distance_text.value = (
                            f"Range: {valid_distances.min():.0f}-"
                            f"{valid_distances.max():.0f}mm"
                        )
                    else:
                        distance_text.value = "No valid data"

                # Calculate FPS
                frame_times.append(time.time() - frame_start)
                if len(frame_times) > 30:
                    frame_times.pop(0)
                avg_frame_time = sum(frame_times) / len(frame_times)
                fps_text.value = f"{1.0 / avg_frame_time:.1f}" if avg_frame_time > 0 else "0"

                # Target ~30 FPS for smooth visualization
                elapsed = time.time() - frame_start
                if elapsed < 0.033:
                    time.sleep(0.033 - elapsed)

        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.running = False
            if self.serial:
                self.serial.close()


def main():
    parser = argparse.ArgumentParser(description="VL53L5CX Point Cloud Viewer")
    parser.add_argument(
        "--port",
        "-p",
        default="/dev/cu.usbserial-0001",
        help="Serial port (default: /dev/cu.usbserial-0001)",
    )
    parser.add_argument(
        "--baud", "-b", type=int, default=115200, help="Baud rate (default: 115200)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Viser server host (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--viser-port", type=int, default=8080, help="Viser server port (default: 8080)"
    )
    args = parser.parse_args()

    viewer = VL53L5CXViewer(port=args.port, baud=args.baud)
    viewer.run(host=args.host, port=args.viser_port)


if __name__ == "__main__":
    main()
