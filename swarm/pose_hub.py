"""One Motive stream, many drones.

The legacy scripts open a Motive listener per drone, and the listener owns
UDP port 1511, so only one can run per machine. PoseHub is the single
callback: it keeps the latest pose of every configured body, mapped into the
Crazyflie world frame with that drone's own axis calibration.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_AXIS_MATRIX = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))


@dataclass(frozen=True)
class Pose:
    position: tuple          # Crazyflie world frame, meters
    motive_position: tuple   # raw OptiTrack frame, meters
    rotation: tuple | None   # Motive quaternion (x, y, z, w)
    t: float                 # time.monotonic() when received


def load_axis_calibration(calib_dir: Path, body_id: int):
    """Return (matrix, yaw_offset_deg, found). Same file format as the legacy code."""
    path = Path(calib_dir) / f"axis_calibration_{int(body_id)}.json"
    if not path.exists():
        return DEFAULT_AXIS_MATRIX, 0.0, False
    data = json.loads(path.read_text(encoding="utf-8"))
    matrix = tuple(tuple(float(v) for v in row) for row in data["matrix"])
    return matrix, float(data.get("yaw_offset_deg", 0.0)), True


def apply_matrix(matrix, x, y, z):
    return tuple(r[0] * x + r[1] * y + r[2] * z for r in matrix)


class PoseHub:
    def __init__(self, body_ids, calib_dir):
        self._lock = threading.Lock()
        self._poses: dict[int, Pose] = {}
        self._matrices = {}
        self.missing_calibration = []
        for body_id in body_ids:
            matrix, _yaw, found = load_axis_calibration(calib_dir, body_id)
            self._matrices[int(body_id)] = matrix
            if not found:
                self.missing_calibration.append(int(body_id))

    def on_rigid_body(self, rb_id, pos, rot, markers=None):
        """Callback with the same signature MotiveListener expects."""
        del markers
        rb_id = int(rb_id)
        matrix = self._matrices.get(rb_id)
        if matrix is None:
            return
        motive = (float(pos[0]), float(pos[1]), float(pos[2]))
        stored_rot = tuple(float(v) for v in rot) if rot is not None and len(rot) == 4 else None
        pose = Pose(apply_matrix(matrix, *motive), motive, stored_rot, time.monotonic())
        with self._lock:
            self._poses[rb_id] = pose

    def get(self, body_id):
        with self._lock:
            return self._poses.get(int(body_id))

    def age(self, body_id):
        pose = self.get(body_id)
        return None if pose is None else time.monotonic() - pose.t

    def fresh(self, body_id, max_age=0.2):
        age = self.age(body_id)
        return age is not None and age <= max_age

    def all_fresh(self, max_age=0.2):
        with self._lock:
            ids = list(self._matrices)
        return all(self.fresh(i, max_age) for i in ids)
