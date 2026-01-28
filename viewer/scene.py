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


def _create_board_mesh_generic(
    server: viser.ViserServer,
    scene_path: str,
    dimensions: tuple[float, float, float],
    texture_path: Path | None,
    fallback_color: tuple[int, int, int, int],
    z_offset: float = 0.0,
):
    """Create a board mesh with optional texture.

    Args:
        server: Viser server instance
        scene_path: Path in the scene hierarchy
        dimensions: (width, length, height) in meters
        texture_path: Path to texture image, or None
        fallback_color: RGBA color if texture not found
        z_offset: Offset to apply to z vertices (e.g., -height/2 for top at z=0)

    Returns:
        Mesh handle
    """
    width, length, height = dimensions
    board_mesh = trimesh.creation.box(extents=[width, length, height])

    if z_offset != 0.0:
        board_mesh.vertices[:, 2] += z_offset

    if texture_path and texture_path.exists():
        texture_image = Image.open(texture_path)
        # UV coordinates based on vertex x,y positions (maps top face correctly)
        uv = np.zeros((len(board_mesh.vertices), 2))
        for i, v in enumerate(board_mesh.vertices):
            uv[i, 0] = (v[0] + width / 2) / width
            uv[i, 1] = (v[1] + length / 2) / length
        material = trimesh.visual.material.PBRMaterial(
            baseColorTexture=texture_image,
            metallicFactor=0.0,
            roughnessFactor=1.0,
        )
        board_mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    else:
        board_mesh.visual.face_colors = fallback_color

    return server.scene.add_mesh_trimesh(scene_path, mesh=board_mesh)


def create_board_mesh(server: viser.ViserServer, assets_dir: Path):
    """Create the Pololu VL53L5CX board mesh.

    Args:
        server: Viser server instance
        assets_dir: Path to assets directory containing textures

    Returns:
        Mesh handle
    """
    # Board dimensions: 13mm x 18mm x 1mm (measured from Pololu carrier board)
    dimensions = (0.013, 0.018, 0.001)
    return _create_board_mesh_generic(
        server,
        scene_path="/sensor/board",
        dimensions=dimensions,
        texture_path=assets_dir / "vl53l5cx-top.jpg",
        fallback_color=(0, 100, 0, 255),  # Green
        z_offset=-dimensions[2] / 2,  # Top face at z=0
    )


def create_imu_board_mesh(server: viser.ViserServer, assets_dir: Path):
    """Create the GY-BNO08X IMU board mesh.

    Args:
        server: Viser server instance
        assets_dir: Path to assets directory containing textures

    Returns:
        Mesh handle
    """
    # GY-BNO08X board dimensions: 15mm x 26mm x 1mm
    dimensions = (0.015, 0.026, 0.001)
    return _create_board_mesh_generic(
        server,
        scene_path="/sensor/imu_board",
        dimensions=dimensions,
        texture_path=assets_dir / "bno08x-top.jpg",
        fallback_color=(128, 0, 128, 255),  # Purple
    )


def create_zone_rays(
    server: viser.ViserServer,
    zone_angles: ZoneAngles,
) -> tuple[list, "viser.FrameHandle"]:
    """Create zone ray visualization (64 rays showing discrete sampling directions).

    Args:
        server: Viser server instance
        zone_angles: Pre-computed zone angle data

    Returns:
        Tuple of (list of ray handles, parent frame handle for transformation)
    """
    # Create parent frame for all rays - this allows transforming all rays together
    rays_frame = server.scene.add_frame("/sensor/rays", show_axes=False)

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

    return zone_rays, rays_frame
