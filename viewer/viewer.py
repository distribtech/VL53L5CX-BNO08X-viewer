#!/usr/bin/env python3
"""VL53L5CX Point Cloud Viewer - Main application."""

import argparse
import logging
from dataclasses import dataclass, field
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


@dataclass
class MappingState:
    """State for mapping mode point accumulation."""

    accumulated_points: list[np.ndarray] = field(default_factory=list)
    accumulated_colors: list[np.ndarray] = field(default_factory=list)

    def clear(self):
        """Clear all accumulated points."""
        self.accumulated_points.clear()
        self.accumulated_colors.clear()

    def add(self, points: np.ndarray, colors: np.ndarray):
        """Add new points to the accumulator."""
        self.accumulated_points.append(points)
        self.accumulated_colors.append(colors)

    def get_display_data(self) -> tuple[np.ndarray, np.ndarray]:
        """Get current accumulated points for display."""
        if len(self.accumulated_points) == 1:
            return self.accumulated_points[0], self.accumulated_colors[0]
        return np.vstack(self.accumulated_points), np.vstack(self.accumulated_colors)

    def total_points(self) -> int:
        """Get total number of accumulated points."""
        return sum(len(p) for p in self.accumulated_points)

    def downsample(self, voxel_size: float, max_points: int):
        """Apply voxel downsampling and enforce max points limit."""
        if not self.accumulated_points:
            return

        all_points = np.vstack(self.accumulated_points)
        all_colors = np.vstack(self.accumulated_colors)

        all_points, all_colors = voxel_downsample(all_points, all_colors, voxel_size)

        if len(all_points) > max_points:
            all_points = all_points[-max_points:]
            all_colors = all_colors[-max_points:]

        self.accumulated_points.clear()
        self.accumulated_points.append(all_points)
        self.accumulated_colors.clear()
        self.accumulated_colors.append(all_colors)


def voxel_downsample(points: np.ndarray, colors: np.ndarray, voxel_size: float) -> tuple[np.ndarray, np.ndarray]:
    """Downsample points using voxel grid (vectorized for performance)."""
    if len(points) == 0:
        return points, colors
    voxel_indices = np.floor(points / voxel_size).astype(np.int64)
    keys = voxel_indices.view(dtype=[('x', np.int64), ('y', np.int64), ('z', np.int64)]).ravel()
    _, unique_idx = np.unique(keys, return_index=True)
    return points[unique_idx], colors[unique_idx]


class VL53L5CXViewer:
    """Real-time point cloud viewer for VL53L5CX ToF sensor."""

    def __init__(self, port: str, baud: int = 115200):
        self.serial_reader = SerialReader(port, baud)
        self.zone_angles = compute_zone_angles()
        self.temporal_filter = TemporalFilter()
        self.imu_to_tof_offset = np.array(config.IMU_TO_TOF_OFFSET)

    def _setup_scene(self, server: viser.ViserServer):
        """Initialize the 3D scene with meshes and visualization elements."""
        server.scene.add_frame("/origin", axes_length=0.002, axes_radius=0.0001)
        create_grid(server)

        assets_dir = Path(__file__).parent.parent / "assets"
        self.tof_board_mesh = create_board_mesh(server, assets_dir)
        self.imu_board_mesh = create_imu_board_mesh(server, assets_dir)
        self.zone_rays, self.rays_frame = create_zone_rays(server, self.zone_angles)

    def _setup_gui(self, server: viser.ViserServer, mapping_state: MappingState):
        """Initialize GUI controls and return handles."""
        # Sensor info folder
        with server.gui.add_folder("Sensor Info"):
            self.distance_text = server.gui.add_text("Status", initial_value="Waiting...")
            self.freq_text = server.gui.add_text("Frequency (Hz)", initial_value="0")
            self.imu_status_text = server.gui.add_text("IMU", initial_value="Not detected")

        # Settings folder
        with server.gui.add_folder("Settings"):
            self.point_size_slider = server.gui.add_slider(
                "Point Size", min=0.001, max=0.020, step=0.001, initial_value=0.001
            )
            self.show_rays_checkbox = server.gui.add_checkbox("Show Zone Rays", initial_value=True)

            server.gui.add_markdown("---")
            self.imu_rotation_checkbox = server.gui.add_checkbox("Apply IMU Rotation", initial_value=True)

            server.gui.add_markdown("---")
            self.filter_checkbox = server.gui.add_checkbox("Enable Filtering", initial_value=False)
            self.filter_strength_slider = server.gui.add_slider(
                "Filter Strength", min=0.0, max=1.0, step=0.05, initial_value=0.5, disabled=True
            )

            @self.filter_checkbox.on_update
            def _on_filter_toggle(event: viser.GuiEvent) -> None:
                self.filter_strength_slider.disabled = not self.filter_checkbox.value
                if not self.filter_checkbox.value:
                    self.temporal_filter.reset()

            server.gui.add_markdown("---")
            self.fit_plane_checkbox = server.gui.add_checkbox("Fit Plane", initial_value=False)
            self.plane_method_dropdown = server.gui.add_dropdown(
                "Method", options=["Least Squares", "RANSAC"], initial_value="Least Squares", disabled=True
            )
            self.ransac_threshold_slider = server.gui.add_slider(
                "RANSAC Threshold (mm)", min=1, max=50, step=1, initial_value=10, visible=False
            )

            @self.fit_plane_checkbox.on_update
            def _on_fit_plane_toggle(event: viser.GuiEvent) -> None:
                self.plane_method_dropdown.disabled = not self.fit_plane_checkbox.value
                self.ransac_threshold_slider.visible = (
                    self.fit_plane_checkbox.value and self.plane_method_dropdown.value == "RANSAC"
                )

            @self.plane_method_dropdown.on_update
            def _on_plane_method_change(event: viser.GuiEvent) -> None:
                self.ransac_threshold_slider.visible = self.plane_method_dropdown.value == "RANSAC"

        # Mapping folder
        with server.gui.add_folder("Mapping"):
            self.mapping_checkbox = server.gui.add_checkbox("Mapping Mode", initial_value=False)
            self.voxel_size_slider = server.gui.add_slider(
                "Voxel Size (mm)", min=5, max=50, step=5, initial_value=10
            )
            self.max_points_slider = server.gui.add_slider(
                "Max Points (k)", min=10, max=500, step=10, initial_value=100
            )
            self.point_count_text = server.gui.add_text("Points", initial_value="0")
            clear_button = server.gui.add_button("Clear Map")

            @clear_button.on_click
            def _on_clear_click(event: viser.GuiEvent) -> None:
                mapping_state.clear()
                self.point_count_text.value = "0"

            @self.mapping_checkbox.on_update
            def _on_mapping_toggle(event: viser.GuiEvent) -> None:
                if not self.mapping_checkbox.value:
                    mapping_state.clear()
                    self.point_count_text.value = "0"

    def _update_scene_transforms(self, corrected_quat: np.ndarray, imu_active: bool, apply_rotation: bool):
        """Update board and ray positions based on IMU orientation."""
        if apply_rotation and imu_active:
            tof_position = rotate_points_by_quaternion(
                self.imu_to_tof_offset.reshape(1, 3), corrected_quat
            )[0]
            self.imu_board_mesh.wxyz = corrected_quat
            self.imu_board_mesh.position = (0.0, 0.0, 0.0)
            self.tof_board_mesh.wxyz = corrected_quat
            self.tof_board_mesh.position = tuple(tof_position)
            self.rays_frame.wxyz = corrected_quat
            self.rays_frame.position = tuple(tof_position)
            return tof_position
        else:
            self.imu_board_mesh.wxyz = (1.0, 0.0, 0.0, 0.0)
            self.imu_board_mesh.position = (0.0, 0.0, 0.0)
            self.tof_board_mesh.wxyz = (1.0, 0.0, 0.0, 0.0)
            self.tof_board_mesh.position = tuple(self.imu_to_tof_offset)
            self.rays_frame.wxyz = (1.0, 0.0, 0.0, 0.0)
            self.rays_frame.position = tuple(self.imu_to_tof_offset)
            return None

    def _process_frame(self, server: viser.ViserServer, mapping_state: MappingState, plane_handle):
        """Process a single frame of sensor data."""
        distances, status, quaternion = self.serial_reader.get_data()

        # Apply temporal filtering if enabled
        if self.filter_checkbox.value:
            distances = self.temporal_filter.apply(distances, self.filter_strength_slider.value)

        # Check if IMU is providing data
        imu_active = not np.allclose(quaternion, [1.0, 0.0, 0.0, 0.0], atol=0.01)
        self.imu_status_text.value = "Active" if imu_active else "Idle"

        corrected_quat = correct_imu_to_tof_frame(quaternion) if imu_active else quaternion

        if np.any(distances > 0):
            points = distances_to_points(distances, self.zone_angles)

            # Transform points based on IMU orientation
            tof_position = self._update_scene_transforms(
                corrected_quat, imu_active, self.imu_rotation_checkbox.value
            )
            if tof_position is not None:
                points = rotate_points_by_quaternion(points, corrected_quat) + tof_position

            colors = get_colors(distances, status)
            valid_mask = (status == 5) & (distances >= config.MIN_RANGE_MM)

            if np.any(valid_mask):
                valid_points = points[valid_mask].astype(np.float32)
                valid_colors = colors[valid_mask]

                # Handle mapping mode or normal display
                if self.mapping_checkbox.value:
                    mapping_state.add(valid_points, valid_colors)

                    if (mapping_state.total_points() > config.DOWNSAMPLE_POINT_THRESHOLD or
                            len(mapping_state.accumulated_points) > config.DOWNSAMPLE_BUFFER_THRESHOLD):
                        voxel_size_m = self.voxel_size_slider.value / 1000.0
                        max_pts = self.max_points_slider.value * 1000
                        mapping_state.downsample(voxel_size_m, max_pts)

                    display_points, display_colors = mapping_state.get_display_data()
                    self.point_count_text.value = f"{len(display_points):,}"

                    server.scene.add_point_cloud(
                        "/sensor/points", points=display_points, colors=display_colors,
                        point_size=self.point_size_slider.value, point_shape="circle"
                    )
                else:
                    server.scene.add_point_cloud(
                        "/sensor/points", points=valid_points, colors=valid_colors,
                        point_size=self.point_size_slider.value, point_shape="circle"
                    )

                # Plane fitting
                if self.fit_plane_checkbox.value and np.sum(valid_mask) >= 3:
                    if self.plane_method_dropdown.value == "RANSAC":
                        threshold_m = self.ransac_threshold_slider.value / 1000.0
                        plane_fit = fit_plane_ransac(points[valid_mask], threshold=threshold_m)
                    else:
                        plane_fit = fit_plane(points[valid_mask])

                    if plane_fit is not None:
                        pos, wxyz, size = plane_fit
                        plane_handle = server.scene.add_box(
                            "/sensor/fitted_plane", dimensions=(size, size, 0.0001),
                            position=pos, wxyz=wxyz, color=(255, 255, 0), opacity=0.5
                        )

                valid_distances = distances[valid_mask]
                self.distance_text.value = f"Range: {valid_distances.min():.0f}-{valid_distances.max():.0f}mm"
            else:
                self.distance_text.value = "No valid data"

        # Update plane visibility
        if plane_handle is not None:
            plane_handle.visible = self.fit_plane_checkbox.value

        # Update frequency and ray visibility
        self.freq_text.value = f"{self.serial_reader.data_fps:.1f}"
        for ray in self.zone_rays:
            ray.visible = self.show_rays_checkbox.value

        return plane_handle

    def run(self, host: str = "0.0.0.0", port: int = 8080):
        """Start the viewer."""
        self.serial_reader.connect()
        self.serial_reader.start()

        server = viser.ViserServer(host=host, port=port)
        logger.info("Viser server started at http://localhost:%d", port)

        @server.on_client_connect
        def on_client_connect(client: viser.ClientHandle) -> None:
            client.camera.position = (0.0, -0.50, 0.50)
            client.camera.look_at = (0.0, 0.0, 0.0)
            client.camera.up = (0.0, 0.0, 1.0)
            client.camera.near = 0.001
            client.camera.fov = 0.35

        mapping_state = MappingState()
        self._setup_scene(server)
        self._setup_gui(server, mapping_state)

        plane_handle = None

        try:
            while True:
                frame_start = time.time()
                plane_handle = self._process_frame(server, mapping_state, plane_handle)

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
        "--port", "-p", default="/dev/cu.usbserial-0001",
        help="Serial port (default: /dev/cu.usbserial-0001)"
    )
    parser.add_argument("--baud", "-b", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--host", default="0.0.0.0", help="Viser server host (default: 0.0.0.0)")
    parser.add_argument("--viser-port", type=int, default=8080, help="Viser server port (default: 8080)")
    args = parser.parse_args()

    viewer = VL53L5CXViewer(port=args.port, baud=args.baud)
    viewer.run(host=args.host, port=args.viser_port)


if __name__ == "__main__":
    main()
