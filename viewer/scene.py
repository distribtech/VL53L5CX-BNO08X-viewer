"""Viser scene setup for VL53L5CX viewer."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
import trimesh
import viser

from . import config
from .geometry import ZoneAngles


@dataclass
class SensorFrames:
    """Handles to the sensor frame hierarchy."""

    imu_frame: viser.FrameHandle
    imu_board: viser.MeshHandle
    tof_frame: viser.FrameHandle
    tof_board: viser.MeshHandle
    rays_frame: viser.FrameHandle
    zone_rays: list


def create_grid(server: viser.ViserServer, size: float = 2.0) -> list:
    """Create a reference grid on the XY plane.

    Args:
        server: Viser server instance
        size: Half-size of the grid in meters

    Returns:
        List of spline handles
    """
    grid_handles = []
    for i in range(-10, 11):
        # Lines parallel to X
        start_x = [-size, i * 0.2, 0]
        end_x = [size, i * 0.2, 0]
        handle_x = server.scene.add_spline_catmull_rom(
            f"/grid/line_x_{i}",
            positions=np.array([start_x, end_x]),
            color=(160, 160, 160),
            line_width=1.0,
        )
        grid_handles.append(handle_x)

        # Lines parallel to Y
        start_y = [i * 0.2, -size, 0]
        end_y = [i * 0.2, size, 0]
        handle_y = server.scene.add_spline_catmull_rom(
            f"/grid/line_y_{i}",
            positions=np.array([start_y, end_y]),
            color=(160, 160, 160),
            line_width=1.0,
        )
        grid_handles.append(handle_y)

    return grid_handles


def _create_board_mesh(
    server: viser.ViserServer,
    scene_path: str,
    dimensions: tuple[float, float, float],
    position: tuple[float, float, float],
    texture_path: Path | None,
    fallback_color: tuple[int, int, int, int],
    is_atlas: bool = False,
):
    """Create a board mesh at a specified position.

    Args:
        server: Viser server instance
        scene_path: Path in the scene hierarchy
        dimensions: (width, length, height) in meters
        position: (x, y, z) position relative to parent frame
        texture_path: Path to texture image, or None
        fallback_color: RGBA color if texture not found
        is_atlas: If True, texture is a vertical atlas with top texture in upper
            half (UV y: 0.5-1.0) and bottom texture in lower half (UV y: 0.0-0.5)

    Returns:
        Mesh handle
    """
    width, length, height = dimensions
    board_mesh = trimesh.creation.box(extents=[width, length, height])

    if texture_path and texture_path.exists():
        texture_image = Image.open(texture_path)
        uv = np.zeros((len(board_mesh.vertices), 2))
        for i, v in enumerate(board_mesh.vertices):
            # Base UV from vertex position (normalized to 0-1)
            u = (v[0] + width / 2) / width
            v_coord = (v[1] + length / 2) / length

            # For atlas textures, scale v_coord based on vertex z position
            if is_atlas:
                if v[2] > 0:  # Top face vertices
                    v_coord = 0.5 + v_coord * 0.5  # Map to upper half (0.5-1.0)
                else:  # Bottom face vertices
                    v_coord = v_coord * 0.5  # Map to lower half (0.0-0.5)

            uv[i, 0] = u
            uv[i, 1] = v_coord

        material = trimesh.visual.material.PBRMaterial(
            baseColorTexture=texture_image,
            metallicFactor=0.0,
            roughnessFactor=1.0,
        )
        board_mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    else:
        board_mesh.visual.face_colors = fallback_color

    return server.scene.add_mesh_trimesh(scene_path, mesh=board_mesh, position=position)


def create_sensor_frames(
    server: viser.ViserServer,
    assets_dir: Path,
    zone_angles: ZoneAngles,
) -> SensorFrames:
    """Create the complete sensor frame hierarchy.

    Hierarchy:
        /imu (frame at IMU sensor origin)
            /imu/board (mesh offset so chip aligns with frame)
        /tof (frame at ToF sensor origin)
            /tof/board (mesh offset so aperture aligns with frame)
            /tof/rays (frame for zone rays)
                /tof/rays/ray_N

    Args:
        server: Viser server instance
        assets_dir: Path to assets directory
        zone_angles: Pre-computed zone angle data

    Returns:
        SensorFrames dataclass with all handles
    """
    # IMU frame and board
    imu_frame = server.scene.add_frame("/imu", show_axes=False)
    imu_board = _create_board_mesh(
        server,
        scene_path="/imu/board",
        dimensions=config.IMU_BOARD_DIMENSIONS,
        position=config.IMU_BOARD_OFFSET,
        texture_path=assets_dir / "bno08x-top.jpg",
        fallback_color=(128, 0, 128, 255),  # Purple
    )

    # ToF frame and board
    tof_frame = server.scene.add_frame("/tof", show_axes=False)
    tof_board = _create_board_mesh(
        server,
        scene_path="/tof/board",
        dimensions=config.TOF_BOARD_DIMENSIONS,
        position=config.TOF_BOARD_OFFSET,
        texture_path=assets_dir / "vl53l5cx-aliexpress-atlas.png",
        fallback_color=(0, 100, 0, 255),  # Green
        is_atlas=True,
    )

    # Zone rays (children of /tof frame)
    rays_frame = server.scene.add_frame("/tof/rays", show_axes=False)
    zone_rays = _create_zone_rays(server, zone_angles)

    return SensorFrames(
        imu_frame=imu_frame,
        imu_board=imu_board,
        tof_frame=tof_frame,
        tof_board=tof_board,
        rays_frame=rays_frame,
        zone_rays=zone_rays,
    )


def _create_zone_rays(
    server: viser.ViserServer,
    zone_angles: ZoneAngles,
) -> list:
    """Create zone ray visualization (64 rays showing discrete sampling directions).

    Args:
        server: Viser server instance
        zone_angles: Pre-computed zone angle data

    Returns:
        List of ray handles
    """
    min_z = config.MIN_RANGE_MM / 1000  # 20mm in metres
    max_z = config.MAX_RANGE_MM / 1000  # 4000mm in metres

    zone_rays = []
    for i in range(config.NUM_ZONES):
        # Rays from min to max z-distance (flat planes, not curved surfaces)
        start = [
            min_z * zone_angles.tan_x[i],
            min_z * zone_angles.tan_y[i],
            min_z,
        ]
        end = [
            max_z * zone_angles.tan_x[i],
            max_z * zone_angles.tan_y[i],
            max_z,
        ]
        ray = server.scene.add_spline_catmull_rom(
            f"/tof/rays/ray_{i}",
            positions=np.array([start, end], dtype=np.float32),
            color=(100, 150, 255),
            line_width=1.0,
        )
        zone_rays.append(ray)

    return zone_rays
