"""Fake Motive source for tests and for developing without the lab."""

from __future__ import annotations

import math
import threading
import time


class FakeMotive:
    """Calls on_rigid_body(rb_id, pos, rot) at hz for each body, on a slow circle."""

    def __init__(self, on_rigid_body, body_ids, hz=100.0, radius=0.5):
        self._cb = on_rigid_body
        self._ids = list(body_ids)
        self._period = 1.0 / hz
        self._radius = radius
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def shutdown(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self):
        t0 = time.monotonic()
        while not self._stop.is_set():
            t = time.monotonic() - t0
            for k, body_id in enumerate(self._ids):
                a = t * 0.5 + k * 2 * math.pi / max(1, len(self._ids))
                self._cb(body_id, (self._radius * math.cos(a), 1.0, self._radius * math.sin(a)), (0.0, 0.0, 0.0, 1.0))
            self._stop.wait(self._period)
