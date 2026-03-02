"""Tests for wifi_reader module."""

import numpy as np

from viewer.wifi_reader import WifiReader


def test_process_line_updates_state():
    reader = WifiReader()
    line = '{"distances":[' + ','.join(['100'] * 64) + '],"status":[' + ','.join(['5'] * 64) + '],"quat":[1,0,0,0],"v":"0.1.0"}'

    reader._process_line(line)

    distances, status, quat = reader.get_data()
    assert np.all(distances == 100)
    assert np.all(status == 5)
    assert np.allclose(quat, np.array([1, 0, 0, 0], dtype=np.float32))
    assert reader.imu_connected is True
