"""Geometry and coordinate transformation utilities for VL53L5CX."""

from dataclasses import dataclass

import numpy as np

from . import config


@dataclass
class ZoneAngles:
    """Pre-computed zone angle data for coordinate transforms."""

    tan_x: np.ndarray  # Tangent of X angles for each zone
    tan_y: np.ndarray  # Tangent of Y angles for each zone
    ray_dir_x: np.ndarray  # Normalized ray direction X component
    ray_dir_y: np.ndarray  # Normalized ray direction Y component
    ray_dir_z: np.ndarray  # Normalized ray direction Z component


def compute_zone_angles() -> ZoneAngles:
    """Pre-compute the angle for each zone center.

    The sensor lens flips the image, so zone 0 corresponds to top-right.
    """
    # Convert diagonal FoV to per-axis FoV (assuming square sensor)
    # For a square, diagonal = side * sqrt(2), so side = diagonal / sqrt(2)
    fov_per_axis_deg = config.FOV_DIAGONAL_DEG / np.sqrt(2)
    fov_per_axis_rad = np.deg2rad(fov_per_axis_deg)

    # Angle step per zone
    angle_step = fov_per_axis_rad / config.RESOLUTION

    # Zone center offsets from optical axis
    # Zones are numbered row-major: 0-7 = row 0, 8-15 = row 1, etc.
    # Due to lens flip, we invert the mapping
    zone_angles_x = np.zeros(config.NUM_ZONES)
    zone_angles_y = np.zeros(config.NUM_ZONES)

    for i in range(config.NUM_ZONES):
        row = i // config.RESOLUTION
        col = i % config.RESOLUTION

        # Center of zone relative to center of grid (0-7 -> -3.5 to 3.5)
        # Flip due to lens inversion
        col_offset = (config.RESOLUTION - 1) / 2 - col  # Flip X
        row_offset = (config.RESOLUTION - 1) / 2 - row  # Flip Y

        zone_angles_x[i] = col_offset * angle_step
        zone_angles_y[i] = row_offset * angle_step

    # Precompute tan of zone angles for XY calculation
    # The sensor reports perpendicular (z-axis) distance, not radial
    tan_x = np.tan(zone_angles_x)
    tan_y = np.tan(zone_angles_y)

    # Also precompute normalized ray directions for visualization
    norm = np.sqrt(tan_x**2 + tan_y**2 + 1)
    ray_dir_x = tan_x / norm
    ray_dir_y = tan_y / norm
    ray_dir_z = 1.0 / norm

    return ZoneAngles(
        tan_x=tan_x,
        tan_y=tan_y,
        ray_dir_x=ray_dir_x,
        ray_dir_y=ray_dir_y,
        ray_dir_z=ray_dir_z,
    )


def distances_to_points(distances: np.ndarray, zone_angles: ZoneAngles) -> np.ndarray:
    """Convert distance measurements to 3D point coordinates.

    The sensor is assumed to be pointing UP (+Z direction),
    lying flat on a horizontal surface. The VL53L5CX reports
    perpendicular (z-axis) distance, not radial distance - the
    chip performs this conversion internally.

    Args:
        distances: Array of 64 distance values in mm (perpendicular)
        zone_angles: Pre-computed zone angle data

    Returns:
        Nx3 array of (x, y, z) coordinates in meters
    """
    # Convert to meters - this IS the z-coordinate
    z = distances / 1000.0

    # Calculate XY positions: lateral offset at this z-distance
    x = z * zone_angles.tan_x
    y = z * zone_angles.tan_y

    return np.column_stack([x, y, z])


def get_colors(distances: np.ndarray, status: np.ndarray) -> np.ndarray:
    """Generate colors based on distance and validity.

    Valid points: Blue (close) to Red (far)
    Invalid points: Gray

    Args:
        distances: Array of distance values in mm
        status: Array of status values (5 = valid)

    Returns:
        Nx3 array of RGB colors (uint8)
    """
    colors = np.zeros((len(distances), 3), dtype=np.uint8)

    # Normalize distances for color mapping
    d_norm = np.clip(
        (distances - config.MIN_RANGE_MM) / (config.MAX_RANGE_MM - config.MIN_RANGE_MM),
        0,
        1,
    )

    # Valid status is 5, treat others as potentially invalid
    valid = status == 5

    # Color gradient: blue (0,0,255) -> cyan -> green -> yellow -> red (255,0,0)
    for i in range(len(distances)):
        if valid[i] and distances[i] >= config.MIN_RANGE_MM:
            t = d_norm[i]
            # Blue to Red gradient
            colors[i, 0] = int(t * 255)  # R increases with distance
            colors[i, 1] = int((1 - abs(2 * t - 1)) * 200)  # G peaks in middle
            colors[i, 2] = int((1 - t) * 255)  # B decreases with distance
        else:
            # Invalid: gray
            colors[i] = [128, 128, 128]

    return colors


def rotate_points_by_quaternion(points: np.ndarray, quaternion: np.ndarray) -> np.ndarray:
    """Rotate points using a quaternion.

    Args:
        points: Nx3 array of 3D points
        quaternion: [w, x, y, z] quaternion (wxyz format from BNO08X)

    Returns:
        Rotated Nx3 array of 3D points
    """
    from scipy.spatial.transform import Rotation

    # scipy uses xyzw format, convert from wxyz
    quat_xyzw = np.array([quaternion[1], quaternion[2], quaternion[3], quaternion[0]])
    rotation = Rotation.from_quat(quat_xyzw)

    return rotation.apply(points)


def rotation_matrix_from_vectors(vec_from: np.ndarray, vec_to: np.ndarray) -> np.ndarray:
    """Compute rotation matrix that rotates vec_from to vec_to.

    Uses Rodrigues' rotation formula.

    Args:
        vec_from: Source vector (will be normalized)
        vec_to: Target vector (will be normalized)

    Returns:
        3x3 rotation matrix
    """
    # Normalize inputs
    a = vec_from / np.linalg.norm(vec_from)
    b = vec_to / np.linalg.norm(vec_to)

    # Handle parallel vectors
    dot = np.dot(a, b)
    if dot > 0.9999:
        return np.eye(3)
    if dot < -0.9999:
        # 180 degree rotation - find perpendicular axis
        perp = np.array([1, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1, 0])
        axis = np.cross(a, perp)
        axis = axis / np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)

    # Rodrigues' formula
    v = np.cross(a, b)
    s = np.linalg.norm(v)  # sin(angle)
    c = dot  # cos(angle)

    # Skew-symmetric cross-product matrix
    vx = np.array([
        [0, -v[2], v[1]],
        [v[2], 0, -v[0]],
        [-v[1], v[0], 0],
    ])

    # Rotation matrix: R = I + vx + vx^2 * (1-c)/s^2
    return np.eye(3) + vx + vx @ vx * ((1 - c) / (s * s))
