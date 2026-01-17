"""Data filtering and plane fitting for VL53L5CX."""

import numpy as np
from scipy.spatial.transform import Rotation

from . import config
from .geometry import rotation_matrix_from_vectors


class TemporalFilter:
    """Exponential moving average filter for distance data."""

    def __init__(self):
        self.filtered_distances = np.zeros(config.NUM_ZONES, dtype=np.float32)
        self.initialized = False

    def reset(self):
        """Reset the filter state."""
        self.initialized = False

    def apply(self, distances: np.ndarray, strength: float) -> np.ndarray:
        """Apply exponential moving average filtering to distance data.

        Args:
            distances: Raw distance measurements (64 values in mm)
            strength: 0.0 = no smoothing, 1.0 = maximum smoothing

        Returns:
            Filtered distance array
        """
        # Alpha controls how much of the new value to use
        # Higher strength = more smoothing = lower alpha
        alpha = 1.0 - strength

        if not self.initialized:
            # Initialize filter buffer with first valid frame
            self.filtered_distances = distances.copy()
            self.initialized = True
            return distances

        # EMA formula: filtered = alpha * new + (1 - alpha) * old
        self.filtered_distances = alpha * distances + (1.0 - alpha) * self.filtered_distances

        return self.filtered_distances.copy()


def fit_plane(
    points: np.ndarray,
    padding: float = 1.2,
) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Fit a plane to 3D points and return position, orientation, and size.

    Fits plane using least squares: z = ax + by + c

    Args:
        points: Nx3 array of valid 3D points
        padding: Multiplier for plane size (1.0 = exact fit)

    Returns:
        Tuple of (position, wxyz_quaternion, size) or None if fitting fails
    """
    if len(points) < 3:
        return None

    # Extract coordinates
    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]

    # Build design matrix for least squares: [x, y, 1] @ [a, b, c].T = z
    A = np.column_stack([x, y, np.ones_like(x)])

    try:
        # Solve least squares: find a, b, c that minimize ||Ax - z||^2
        coeffs, _, _, _ = np.linalg.lstsq(A, z, rcond=None)
        a, b, c = coeffs
    except np.linalg.LinAlgError:
        return None

    # Plane equation: z = ax + by + c
    # Normal vector: n = (-a, -b, 1) (unnormalized)
    normal = np.array([-a, -b, 1.0])
    normal = normal / np.linalg.norm(normal)

    # Compute centroid of points
    centroid = points.mean(axis=0)

    # Compute plane size based on XY span of points
    x_span = x.max() - x.min()
    y_span = y.max() - y.min()
    plane_size = max(x_span, y_span) * padding

    # Ensure minimum size for visibility
    plane_size = max(plane_size, 0.05)  # At least 5cm

    # Position: centroid adjusted to lie on fitted plane
    plane_z = a * centroid[0] + b * centroid[1] + c
    position = np.array([centroid[0], centroid[1], plane_z])

    # Build rotation matrix to align Z-axis with plane normal
    rotation = rotation_matrix_from_vectors(
        np.array([0, 0, 1]),
        normal,
    )

    # Convert rotation matrix to quaternion (wxyz format)
    r = Rotation.from_matrix(rotation)
    quat_xyzw = r.as_quat()  # scipy returns xyzw
    wxyz = np.array([quat_xyzw[3], quat_xyzw[0], quat_xyzw[1], quat_xyzw[2]])

    return position, wxyz, plane_size
