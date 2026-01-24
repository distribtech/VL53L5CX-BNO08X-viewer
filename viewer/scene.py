"""Viser scene setup for VL53L5CX viewer."""

from pathlib import Path

import numpy as np
from PIL import Image
import trimesh
import viser

from . import config
from .geometry import ZoneAngles


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


def create_board_mesh(server: viser.ViserServer, assets_dir: Path):
    """Create the Pololu VL53L5CX board mesh.

    Args:
        server: Viser server instance
        assets_dir: Path to assets directory containing textures

    Returns:
        Mesh handle
    """
    # Board dimensions: 13mm x 18mm x 1mm (width x length x height)
    # Measured from Pololu VL53L5CX carrier board with calipers
    board_width = 0.013  # 13mm in metres
    board_length = 0.018  # 18mm in metres
    board_height = 0.001  # 1mm in metres

    # Create box with top face at z=0
    board_mesh = trimesh.creation.box(extents=[board_width, board_length, board_height])
    board_mesh.vertices[:, 2] -= board_height / 2

    # Load texture and apply to box
    texture_path = assets_dir / "vl53l5cx-top.jpg"
    if texture_path.exists():
        texture_image = Image.open(texture_path)
        # UV coordinates based on vertex x,y positions (maps top face correctly)
        uv = np.zeros((len(board_mesh.vertices), 2))
        for i, v in enumerate(board_mesh.vertices):
            uv[i, 0] = (v[0] + board_width / 2) / board_width
            uv[i, 1] = (v[1] + board_length / 2) / board_length
        material = trimesh.visual.material.PBRMaterial(
            baseColorTexture=texture_image,
            metallicFactor=0.0,
            roughnessFactor=1.0,
        )
        board_mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    else:
        board_mesh.visual.face_colors = [0, 100, 0, 255]  # Green fallback

    return server.scene.add_mesh_trimesh("/sensor/board", mesh=board_mesh)


def create_imu_board_mesh(server: viser.ViserServer, assets_dir: Path):
    """Create the GY-BNO08X IMU board mesh.

    Args:
        server: Viser server instance
        assets_dir: Path to assets directory containing textures

    Returns:
        Mesh handle
    """
    # GY-BNO08X board dimensions: 15mm x 26mm x 1mm (width x length x height)
    # Board is portrait orientation (taller than wide)
    board_width = 0.015  # 15mm
    board_length = 0.026  # 26mm
    board_height = 0.001  # 1mm

    # Create box centered at origin
    board_mesh = trimesh.creation.box(extents=[board_width, board_length, board_height])

    # Load texture and apply to box
    texture_path = assets_dir / "bno08x-top.jpg"
    if texture_path.exists():
        texture_image = Image.open(texture_path)
        # UV coordinates based on vertex x,y positions (maps top face correctly)
        uv = np.zeros((len(board_mesh.vertices), 2))
        for i, v in enumerate(board_mesh.vertices):
            uv[i, 0] = (v[0] + board_width / 2) / board_width
            uv[i, 1] = (v[1] + board_length / 2) / board_length
        material = trimesh.visual.material.PBRMaterial(
            baseColorTexture=texture_image,
            metallicFactor=0.0,
            roughnessFactor=1.0,
        )
        board_mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    else:
        # Purple fallback color
        board_mesh.visual.face_colors = [128, 0, 128, 255]

    return server.scene.add_mesh_trimesh("/sensor/imu_board", mesh=board_mesh)


def create_zone_rays(
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
            f"/sensor/rays/ray_{i}",
            positions=np.array([start, end], dtype=np.float32),
            color=(100, 150, 255),
            line_width=1.0,
        )
        zone_rays.append(ray)

    return zone_rays
