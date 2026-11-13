"""The professor's Scene, linked to the real flight program (flight/swarm_voice.py).

The scene plans where each drone should be; this class sends those targets to the
flight program over local UDP about 30 times per second and reads its status back.
It can only ask for positions: arming, takeoff and every safety decision stay in
the flight program.

Scene frame: origin on the floor under the chair, +x forward (the way the noses
point), +y left, +z up. Drone 1, 2, 3 are whatever the flight program maps them to.
"""

from __future__ import annotations

import json
import re
import socket
import time

import numpy as np

from commands import parse_commands
import scene as scene_module
from scene import GROUND_Z, Scene

TARGET_PORT = 47800   # voice -> flight
STATUS_PORT = 47801   # flight -> voice
STATUS_STALE_S = 2.0
PARKED = ((-3.0, 0.9, GROUND_Z), (-3.0, 0.0, GROUND_Z), (-3.0, -0.9, GROUND_Z))

_LAND = re.compile(r"\b(land|landing|touch\s*down|come\s+down\s+and\s+land)\b", re.I)
_TAKEOFF = re.compile(r"\btake\s*-?\s*off\b|\blift\s*off\b", re.I)
_DRONE = re.compile(r"\bdrone\s+(1|2|3|one|two|three)\b", re.I)
_NUMBER = {"1": 1, "2": 2, "3": 3, "one": 1, "two": 2, "three": 3}
BLOCKED = "Not enabled for real flight yet: circling, swapping, 'over the chair' and 'inspect'."


def _blocked(action) -> bool:
    if action.kind in {"orbit", "swap", "inspect"}:
        return True
    return action.kind == "station" and action.side == "over"


class LinkedScene(Scene):
    def __init__(self, planner=None, link=True, allow_all=False, climb_first=False, clock=None):
        super().__init__(climb_first=climb_first, clock=clock, planner=planner)
        self.link = link
        self.allow_all = allow_all
        self.ready = not link
        self.synced = False
        self.flight = None
        self.flight_t = 0.0
        self.flight_msg = ""
        self.land_serial = 0
        self.land_who: list = []
        self.seq = 0
        self._tx = self._rx = None
        if link:
            self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._rx.bind(("127.0.0.1", STATUS_PORT))
            self._rx.setblocking(False)

    # ---- commands
    def submit(self, text, now=None):
        raw = " ".join(text.split())
        if not raw:
            return
        if _LAND.search(raw):
            named = [_NUMBER[m.lower()] for m in _DRONE.findall(raw)]
            self.land_who = named or ["all"]
            self.land_serial += 1
            who = "all drones" if not named else "drone " + " and ".join(str(n) for n in named)
            self.notice = f"LAND sent for {who}."
            self.said = True
            print(f"\n> {raw}\n  {self.notice}", flush=True)
            return
        if _TAKEOFF.search(raw):
            self._say(raw, "Takeoff is started in the flight window (type GO there), not by voice.")
            return
        if self.link and not self.ready:
            actions, error = parse_commands(raw)
            only_select = not error and actions and all(a.kind in {"stop", "select"} for a in actions)
            if not only_select:
                self._say(raw, "The drones are not hovering yet. Start flight/swarm_voice.py and wait for the hover.")
                return
        super().submit(text, now)

    def apply(self, actions, raw, now=None, error="", source="", llm=""):
        if not self.allow_all and not error and any(_blocked(action) for action in actions):
            self._say(raw, BLOCKED)
            return
        super().apply(actions, raw, now, error=error, source=source, llm=llm)

    def _say(self, raw, notice):
        self.notice = notice
        self.said = True
        print(f"\n> {raw}\n  {notice}", flush=True)

    def caption(self) -> str:
        text = super().caption()
        if not self.link:
            return "[simulation only]  " + text
        if self.flight is None:
            return "[flight program: not running]  " + text
        state = "hovering, listening to you" if self.ready else (self.flight_msg or "getting ready")
        return f"[flight: {state}]  " + text

    # ---- link
    def tick(self, now=None):
        if self.link:
            self._receive()
        pos = super().tick(now)
        if self.link:
            self._send()
        return pos

    def _receive(self):
        latest = None
        while True:
            try:
                data, _addr = self._rx.recvfrom(65535)
            except (BlockingIOError, OSError):
                break
            try:
                latest = json.loads(data.decode())
            except ValueError:
                continue
        wall = time.monotonic()
        if latest is None:
            if self.flight is not None and wall - self.flight_t > STATUS_STALE_S:
                self.flight, self.ready, self.synced = None, False, False
                print("\n  flight program: no longer heard", flush=True)
            return
        self.flight, self.flight_t = latest, wall
        self.flight_msg = str(latest.get("msg", ""))
        drones = latest.get("drones", {})
        if not self.synced or latest.get("epoch") != getattr(self, "_epoch", None):
            # Start every drone in the scene exactly where it really is.
            self._epoch = latest.get("epoch")
            for index in range(3):
                info = drones.get(str(index + 1))
                home = info["home"] if info else PARKED[index]
                self.home[index] = home
                self.pos[index] = home
                self.yaw[index] = 0.0
                self.tracks[index] = None
            self.active = None
            self.last_actions = None
            self.synced = True
            print("\n  flight program: connected, drones " + ", ".join(sorted(drones)) + " placed in the scene", flush=True)
        for index in range(3):  # a drone that has landed leaves the scene
            info = drones.get(str(index + 1))
            if info and info.get("phase") == "DONE" and self.pos[index][0] > -2.9:
                self.tracks[index] = None
                self.pos[index] = PARKED[index]
        ceiling = latest.get("ceiling")
        if ceiling and abs(float(ceiling) - scene_module.MAX_Z) > 1e-6:
            scene_module.MAX_Z = float(ceiling)   # plan with the flight program's ceiling
            print(f"\n  ceiling set by the flight program: {scene_module.MAX_Z:.2f} m", flush=True)
        self.ready = bool(latest.get("ready"))

    def _send(self):
        self.seq += 1
        targets = {
            str(index + 1): [float(self.pos[index][0]), float(self.pos[index][1]), float(self.pos[index][2]),
                             float(self.yaw[index])]
            for index in range(3)
        }
        packet = {"seq": self.seq, "targets": targets, "land_serial": self.land_serial, "land": self.land_who,
                  "epoch": getattr(self, "_epoch", None)}
        try:
            self._tx.sendto(json.dumps(packet).encode(), ("127.0.0.1", TARGET_PORT))
        except OSError:
            pass


def as_array(points):
    return np.asarray(points, dtype=float)
