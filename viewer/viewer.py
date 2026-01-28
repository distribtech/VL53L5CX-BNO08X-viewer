#!/usr/bin/env python3
"""VL53L5CX Point Cloud Viewer - Main application."""

import argparse
import logging
from pathlib import Path
import time

import numpy as np
import viser

from . import config
from .filters import TemporalFilter, fit_plane, fit_plane_ransac
from .geometry import compute_zone_angles, correct_imu_to_tof_frame, distances_to_points, get_colors, rotate_points_by_quaternion
from .logging_config import setup_logging
from .scene import create_board_mesh, create_grid, create_imu_board_mesh, create_zone_rays
from .serial_reader import SerialReader

logger = logging.getLogger("vl53l5cx_viewer.main")


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
        logger.info("Viser server started at http://localhost:%d", port)

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
        tof_board_mesh = create_board_mesh(server, assets_dir)
        imu_board_mesh = create_imu_board_mesh(server, assets_dir)

        # Physical offset between IMU and ToF sensor
        imu_to_tof_offset = np.array(config.imu_to_tof_offset)

        zone_rays, rays_frame = create_zone_rays(server, self.zone_angles)

        # Setup GUI
        with server.gui.add_folder("Sensor Info"):
            distance_text = server.gui.add_text("Status", initial_value="Waiting...")
            freq_text = server.gui.add_text("Frequency (Hz)", initial_value="0")
            imu_status_text = server.gui.add_text("IMU", initial_value="Not detected")

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

            # IMU rotation controls
            server.gui.add_markdown("---")
            imu_rotation_checkbox = server.gui.add_checkbox(
                "Apply IMU Rotation",
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
            plane_method_dropdown = server.gui.add_dropdown(
                "Method",
                options=["Least Squares", "RANSAC"],
                initial_value="Least Squares",
                disabled=True,
            )
            ransac_threshold_slider = server.gui.add_slider(
                "RANSAC Threshold (mm)",
                min=1,
                max=50,
                step=1,
                initial_value=10,
                visible=False,
            )

            @fit_plane_checkbox.on_update
            def _on_fit_plane_toggle(event: viser.GuiEvent) -> None:
                plane_method_dropdown.disabled = not fit_plane_checkbox.value
                if fit_plane_checkbox.value and plane_method_dropdown.value == "RANSAC":
                    ransac_threshold_slider.visible = True
                else:
                    ransac_threshold_slider.visible = False

            @plane_method_dropdown.on_update
            def _on_plane_method_change(event: viser.GuiEvent) -> None:
                ransac_threshold_slider.visible = plane_method_dropdown.value == "RANSAC"

        # Mapping mode controls
        with server.gui.add_folder("Mapping"):
            mapping_checkbox = server.gui.add_checkbox(
                "Mapping Mode",
                initial_value=False,
            )
            voxel_size_slider = server.gui.add_slider(
                "Voxel Size (mm)",
                min=5,
                max=50,
                step=5,
                initial_value=10,
            )
            max_points_slider = server.gui.add_slider(
                "Max Points (k)",
                min=10,
                max=500,
                step=10,
                initial_value=100,
            )
            point_count_text = server.gui.add_text("Points", initial_value="0")
            clear_button = server.gui.add_button("Clear Map")

        # Accumulated points storage for mapping mode
        accumulated_points: list[np.ndarray] = []
        accumulated_colors: list[np.ndarray] = []

        def clear_accumulated():
            nonlocal accumulated_points, accumulated_colors
            accumulated_points = []
            accumulated_colors = []
            point_count_text.value = "0"

        @clear_button.on_click
        def _on_clear_click(event: viser.GuiEvent) -> None:
            clear_accumulated()

        @mapping_checkbox.on_update
        def _on_mapping_toggle(event: viser.GuiEvent) -> None:
            if not mapping_checkbox.value:
                clear_accumulated()

        def voxel_downsample(points: np.ndarray, colors: np.ndarray, voxel_size: float) -> tuple[np.ndarray, np.ndarray]:
            """Downsample points using voxel grid (vectorized for performance)."""
            if len(points) == 0:
                return points, colors
            # Quantize points to voxel grid indices
            voxel_indices = np.floor(points / voxel_size).astype(np.int64)
            # Create unique key for each voxel using structured array view
            # This is much faster than Python dict iteration
            keys = voxel_indices.view(dtype=[('x', np.int64), ('y', np.int64), ('z', np.int64)]).ravel()
            _, unique_idx = np.unique(keys, return_index=True)
            return points[unique_idx], colors[unique_idx]

        # Plane handle for visibility control
        plane_handle = None

        # Main visualization loop
        try:
            while True:
                frame_start = time.time()

                distances, status, quaternion = self.serial_reader.get_data()

                # Apply temporal filtering if enabled
                if filter_checkbox.value:
                    distances = self.temporal_filter.apply(
                        distances, filter_strength_slider.value
                    )

                # Check if IMU is providing data (non-identity quaternion)
                imu_active = not np.allclose(quaternion, [1.0, 0.0, 0.0, 0.0], atol=0.01)
                imu_status_text.value = "Active" if imu_active else "Idle"

                # Apply frame correction for IMU-to-ToF alignment
                corrected_quat = correct_imu_to_tof_frame(quaternion) if imu_active else quaternion

                if np.any(distances > 0):
                    # Convert to 3D points
                    points = distances_to_points(distances, self.zone_angles)

                    # Apply IMU rotation if enabled and IMU is active
                    if imu_rotation_checkbox.value and imu_active:
                        # Rotate the offset vector to get ToF position in world frame
                        tof_position = rotate_points_by_quaternion(
                            imu_to_tof_offset.reshape(1, 3), corrected_quat
                        )[0]

                        # Points are in ToF's local frame, transform to world:
                        # 1. Rotate points by IMU orientation
                        # 2. Add ToF position offset
                        points = rotate_points_by_quaternion(points, corrected_quat)
                        points = points + tof_position

                        # Update board and rays positions
                        imu_board_mesh.wxyz = corrected_quat
                        imu_board_mesh.position = (0.0, 0.0, 0.0)  # IMU at origin
                        tof_board_mesh.wxyz = corrected_quat
                        tof_board_mesh.position = tuple(tof_position)
                        rays_frame.wxyz = corrected_quat
                        rays_frame.position = tuple(tof_position)
                    else:
                        # Reset boards and rays to identity orientation and default positions
                        imu_board_mesh.wxyz = (1.0, 0.0, 0.0, 0.0)
                        imu_board_mesh.position = (0.0, 0.0, 0.0)
                        tof_board_mesh.wxyz = (1.0, 0.0, 0.0, 0.0)
                        tof_board_mesh.position = tuple(imu_to_tof_offset)
                        rays_frame.wxyz = (1.0, 0.0, 0.0, 0.0)
                        rays_frame.position = tuple(imu_to_tof_offset)
                    colors = get_colors(distances, status)

                    # Filter out invalid points (status 5 = valid measurement)
                    valid_mask = (status == 5) & (distances >= config.MIN_RANGE_MM)

                    if np.any(valid_mask):
                        valid_points = points[valid_mask].astype(np.float32)
                        valid_colors = colors[valid_mask]

                        # Mapping mode: accumulate points
                        if mapping_checkbox.value:
                            accumulated_points.append(valid_points)
                            accumulated_colors.append(valid_colors)

                            # Only run expensive downsampling when buffer gets large
                            total_new_points = sum(len(p) for p in accumulated_points)
                            if total_new_points > config.DOWNSAMPLE_POINT_THRESHOLD or len(accumulated_points) > config.DOWNSAMPLE_BUFFER_THRESHOLD:
                                # Combine all accumulated points
                                all_points = np.vstack(accumulated_points)
                                all_colors = np.vstack(accumulated_colors)

                                # Apply voxel downsampling
                                voxel_size_m = voxel_size_slider.value / 1000.0
                                all_points, all_colors = voxel_downsample(
                                    all_points, all_colors, voxel_size_m
                                )

                                # Enforce max points limit
                                max_points = max_points_slider.value * 1000
                                if len(all_points) > max_points:
                                    # Keep most recent points
                                    all_points = all_points[-max_points:]
                                    all_colors = all_colors[-max_points:]

                                # Store downsampled result back
                                accumulated_points.clear()
                                accumulated_points.append(all_points)
                                accumulated_colors.clear()
                                accumulated_colors.append(all_colors)

                            # Get current state for display
                            if len(accumulated_points) == 1:
                                display_points = accumulated_points[0]
                                display_colors = accumulated_colors[0]
                            else:
                                display_points = np.vstack(accumulated_points)
                                display_colors = np.vstack(accumulated_colors)

                            # Update point count display
                            point_count_text.value = f"{len(display_points):,}"

                            # Display accumulated map
                            server.scene.add_point_cloud(
                                "/sensor/points",
                                points=display_points,
                                colors=display_colors,
                                point_size=point_size_slider.value,
                                point_shape="circle",
                            )
                        else:
                            # Normal mode: just show current frame
                            server.scene.add_point_cloud(
                                "/sensor/points",
                                points=valid_points,
                                colors=valid_colors,
                                point_size=point_size_slider.value,
                                point_shape="circle",
                            )

                        # Plane fitting visualization
                        if fit_plane_checkbox.value and np.sum(valid_mask) >= 3:
                            if plane_method_dropdown.value == "RANSAC":
                                # Convert mm threshold to meters
                                threshold_m = ransac_threshold_slider.value / 1000.0
                                plane_fit = fit_plane_ransac(
                                    points[valid_mask], threshold=threshold_m
                                )
                            else:
                                plane_fit = fit_plane(points[valid_mask])
                            if plane_fit is not None:
                                pos, wxyz, size = plane_fit
                                plane_handle = server.scene.add_box(
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

                # Hide plane if checkbox is disabled
                if plane_handle is not None:
                    plane_handle.visible = fit_plane_checkbox.value

                # Update frequency display (actual sensor data rate)
                freq_text.value = f"{self.serial_reader.data_fps:.1f}"

                # Update zone rays visibility
                for ray in zone_rays:
                    ray.visible = show_rays_checkbox.value

                # Target frame rate for smooth visualization
                elapsed = time.time() - frame_start
                if elapsed < config.FRAME_TIME:
                    time.sleep(config.FRAME_TIME - elapsed)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self.serial_reader.stop()


def main():
    setup_logging()
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
