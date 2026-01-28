"""VL53L5CX sensor configuration constants."""

from dataclasses import dataclass


# Package version (semantic versioning) - must match firmware
VERSION = "0.1.0"

# Sensor resolution
RESOLUTION = 8  # 8x8 zones
NUM_ZONES = 64

# Field of view
FOV_DIAGONAL_DEG = 65.0  # Diagonal field of view in degrees

# Range limits
MAX_RANGE_MM = 4000
MIN_RANGE_MM = 20


@dataclass
class BoardConfig:
    """Configuration for a sensor board."""

    # Board position in world coordinates (meters)
    world_position: tuple[float, float, float]
    # Sensor position relative to board center (meters)
    sensor_offset: tuple[float, float, float]
    # Board physical dimensions: width, length, height (meters)
    dimensions: tuple[float, float, float]
    # Texture filename (looked up in assets directory)
    texture: str
    # Sensor yaw rotation around Z axis (degrees) - corrects sensor orientation
    sensor_yaw_deg: float = 0.0
    # Whether texture is a vertical atlas (top/bottom faces)
    is_atlas: bool = False
    # Fallback color if texture not found (RGBA)
    fallback_color: tuple[int, int, int, int] = (128, 128, 128, 255)


# IMU board: BNO08x on 15mm x 26mm breakout
# World position: at origin, sensor chip is the reference point
# Sensor offset: chip is ~4mm above board center in Y, flush with top surface in Z
IMU_BOARD = BoardConfig(
    world_position=(0.0, 0.0, 0.0),
    sensor_offset=(0.0, 0.004, 0.0005),  # Sensor above and forward of board center
    dimensions=(0.015, 0.026, 0.001),
    texture="bno08x-atlas.png",
    is_atlas=True,
    fallback_color=(128, 0, 128, 255),  # Purple
)

# ToF board: VL53L5CX on 10mm x 16mm breakout
# World position: ~1 inch (25.4mm) in -Y direction from IMU
# Sensor offset: aperture is ~4mm from top edge, flush with top surface
# Sensor yaw: 90° CCW to align sensor's internal coordinate system with world
TOF_BOARD = BoardConfig(
    world_position=(0.0, -0.0254, 0.0),
    sensor_offset=(0.0, 0.004, 0.0005),  # Sensor above and forward of board center
    dimensions=(0.010, 0.016, 0.001),
    texture="vl53l5cx-atlas.png",
    sensor_yaw_deg=90.0,  # 90° CCW rotation to align with world frame
    is_atlas=True,
    fallback_color=(0, 100, 0, 255),  # Green
)

# Visualization settings
TARGET_FPS = 30  # Target visualization frame rate
FRAME_TIME = 1.0 / TARGET_FPS  # Time per frame in seconds

# Mapping mode thresholds
DOWNSAMPLE_POINT_THRESHOLD = 500  # Trigger downsampling after this many new points
DOWNSAMPLE_BUFFER_THRESHOLD = 15  # Or after this many frame buffers
