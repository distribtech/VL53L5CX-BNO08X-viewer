"""VL53L5CX sensor configuration constants."""

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

# Physical setup: IMU to ToF sensor offset (meters)
# ToF is ~1 inch (25.4mm) in -Y direction from IMU on breadboard
IMU_TO_TOF_OFFSET = (0.0, -0.0254, 0.0)

# Board dimensions and sensor positions (meters)
# ToF board: 10mm x 16mm x 1mm, sensor aperture ~4mm from top edge
TOF_BOARD_DIMENSIONS = (0.010, 0.016, 0.001)
TOF_BOARD_OFFSET = (0.0, -0.004, -0.0005)  # Board center relative to sensor aperture

# IMU board: 15mm x 26mm x 1mm, BNO08x chip ~4mm above board center
IMU_BOARD_DIMENSIONS = (0.015, 0.026, 0.001)
IMU_BOARD_OFFSET = (0.0, -0.004, -0.0005)  # Board center relative to IMU chip

# Visualization settings
TARGET_FPS = 30  # Target visualization frame rate
FRAME_TIME = 1.0 / TARGET_FPS  # Time per frame in seconds

# Mapping mode thresholds
DOWNSAMPLE_POINT_THRESHOLD = 500  # Trigger downsampling after this many new points
DOWNSAMPLE_BUFFER_THRESHOLD = 15  # Or after this many frame buffers
