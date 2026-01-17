#!/usr/bin/env python3
"""VL53L5CX Point Cloud Viewer - Main application."""

import argparse
from pathlib import Path
import time

import numpy as np
import viser

from . import config
from .filters import TemporalFilter, fit_plane
from .geometry import compute_zone_angles, distances_to_points, get_colors
from .scene import create_board_mesh, create_grid, create_zone_rays
from .serial_reader import SerialReader


class VL53L5CXViewer:
    """Real-time point cloud viewer for VL53L5CX ToF sensor."""

    def __init__(self, port: str, baud: int = 115200):
        self.serial_reader = SerialReader(port, baud)
        self.zone_angles = compute_zone_angles()
        self.temporal_filter = TemporalFilter()

    def run(self, host: str = "0.0.0.0", port: int = 8080):
        """Start the viewer."""
        # Connect and start serial reader
        self.serial_reader.connect()
        self.serial_reader.start()

        # Start Viser server
        server = viser.ViserServer(host=host, port=port)
        print(f"Viser server started at http://localhost:{port}")

        # Set initial camera pose for new clients
        @server.on_client_connect
        def on_client_connect(client: viser.ClientHandle) -> None:
            client.camera.position = (0.0, -0.50, 0.50)
            client.camera.look_at = (0.0, 0.0, 0.0)
            client.camera.up = (0.0, 0.0, 1.0)
            client.camera.near = 0.001  # 1mm near clipping for close-up viewing
            client.camera.fov = 0.35  # ~20° FOV for less perspective distortion

        # Setup scene
        server.scene.add_frame("/origin", axes_length=0.002, axes_radius=0.0001)
        create_grid(server)

        assets_dir = Path(__file__).parent.parent / "assets"
        create_board_mesh(server, assets_dir)

        zone_rays = create_zone_rays(server, self.zone_angles)

        # Setup GUI
        with server.gui.add_folder("Sensor Info"):
            distance_text = server.gui.add_text("Status", initial_value="Waiting...")
            freq_text = server.gui.add_text("Frequency (Hz)", initial_value="0")

        with server.gui.add_folder("Settings"):
            point_size_slider = server.gui.add_slider(
                "Point Size",
                min=0.001,
                max=0.020,
                step=0.001,
                initial_value=0.001,
            )
            show_rays_checkbox = server.gui.add_checkbox(
                "Show Zone Rays",
                initial_value=True,
            )

            # Filtering controls
            server.gui.add_markdown("---")
            filter_checkbox = server.gui.add_checkbox(
                "Enable Filtering",
                initial_value=False,
            )
            filter_strength_slider = server.gui.add_slider(
                "Filter Strength",
                min=0.0,
                max=1.0,
                step=0.05,
                initial_value=0.5,
                disabled=True,
            )

            @filter_checkbox.on_update
            def _on_filter_toggle(event: viser.GuiEvent) -> None:
                filter_strength_slider.disabled = not filter_checkbox.value
                if not filter_checkbox.value:
                    self.temporal_filter.reset()

            # Plane fitting controls
            server.gui.add_markdown("---")
            fit_plane_checkbox = server.gui.add_checkbox(
                "Fit Plane",
                initial_value=False,
            )

        # Main visualization loop
        try:
            while True:
                frame_start = time.time()

                distances, status = self.serial_reader.get_data()

                # Apply temporal filtering if enabled
                if filter_checkbox.value:
                    distances = self.temporal_filter.apply(
                        distances, filter_strength_slider.value
                    )

                if np.any(distances > 0):
                    # Convert to 3D points
                    points = distances_to_points(distances, self.zone_angles)
                    colors = get_colors(distances, status)

                    # Filter out invalid points (keep only valid ones for display)
                    valid_mask = (status == 5) & (distances >= config.MIN_RANGE_MM)

                    if np.any(valid_mask):
                        server.scene.add_point_cloud(
                            "/sensor/points",
                            points=points[valid_mask].astype(np.float32),
                            colors=colors[valid_mask],
                            point_size=point_size_slider.value,
                            point_shape="circle",
                        )

                        # Plane fitting visualization
                        if fit_plane_checkbox.value and np.sum(valid_mask) >= 3:
                            plane_fit = fit_plane(points[valid_mask])
                            if plane_fit is not None:
                                pos, wxyz, size = plane_fit
                                server.scene.add_box(
                                    "/sensor/fitted_plane",
                                    dimensions=(size, size, 0.0001),
                                    position=pos,
                                    wxyz=wxyz,
                                    color=(255, 255, 0),  # Yellow
                                    opacity=0.5,
                                )

                        # Update info
                        valid_distances = distances[valid_mask]
                        distance_text.value = (
                            f"Range: {valid_distances.min():.0f}-"
                            f"{valid_distances.max():.0f}mm"
                        )
                    else:
                        distance_text.value = "No valid data"

                # Remove plane if checkbox is disabled or no valid points
                if not fit_plane_checkbox.value:
                    try:
                        server.scene.remove("/sensor/fitted_plane")
                    except Exception:
                        pass

                # Update frequency display (actual sensor data rate)
                freq_text.value = f"{self.serial_reader.data_fps:.1f}"

                # Update zone rays visibility
                for ray in zone_rays:
                    ray.visible = show_rays_checkbox.value

                # Target ~30 FPS for smooth visualization
                elapsed = time.time() - frame_start
                if elapsed < 0.033:
                    time.sleep(0.033 - elapsed)

        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.serial_reader.stop()


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
