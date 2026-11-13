"""Three drones, one cube, and the maneuvers they are flying.

+x is forward, +y is left, +z is up. Yaw 0 faces +x. Positive yaw turns
toward +y. The cube sits on the floor at the origin, which is the center
of both views.

Each drone flies its own track. A sentence replaces the tracks of the drones
it names and leaves the others flying, so a drone circling the object keeps
circling while you move another one.
"""

from __future__ import annotations

import math
import time
from itertools import permutations

import numpy as np

from commands import AGAIN, HINT, Action, parse_commands, split_clauses

HOVER_Z = 0.70
OVER_Z = 1.05
GROUND_Z = 0.05
MIN_Z = 0.30   # SWARM: real drones never get sent below 0.30 m
MAX_Z = 1.10
STANDOFF = 0.95
CUBE_HALF = 0.30   # SWARM: the object is a chair that fits in 0.6 x 0.6 x 0.8 m
CUBE_TOP = 0.80
CUBE_CENTER = (0.0, 0.0, CUBE_HALF)
ARENA_M = 1.80   # SWARM: inside the flight program's 2.0 m box
MIN_SEP_M = 0.60  # SWARM: real drones keep 0.6 m (the sim used 0.30)
BODY_PAD_M = 0.08
MIN_RADIAL_M = 0.80  # SWARM: chair half-width 0.30 + margin
ORBIT_SPEED = 0.35
ORBIT_MIN_Z = 0.60
ORBIT_CLEAR_M = 0.30
ORBIT_RADII = (1.30, 1.60, 1.90)
SWAP_OFFSET_M = 0.40
FACING_TOL_DEG = 5.0

NO_FLY_OVER = True  # SWARM

STATIONS = {
    "left": (0.0, STANDOFF, HOVER_Z),
    "right": (0.0, -STANDOFF, HOVER_Z),
    "front": (STANDOFF, 0.0, HOVER_Z),
    "back": (-STANDOFF, 0.0, HOVER_Z),
    "over": (0.0, 0.0, OVER_Z),
}

_PRIORITY = {"left": 0, "right": 1, "front": 2, "back": 3, "over": 4}

_DIRECTION = {
    "forward": np.array([1.0, 0.0, 0.0]),
    "back": np.array([-1.0, 0.0, 0.0]),
    "left": np.array([0.0, 1.0, 0.0]),
    "right": np.array([0.0, -1.0, 0.0]),
    "up": np.array([0.0, 0.0, 1.0]),
    "down": np.array([0.0, 0.0, -1.0]),
}

_SIDE_PHRASE = {
    "left": "the left of the object, facing it",
    "right": "the right of the object, facing it",
    "front": "the front of the object, facing it",
    "back": "the back of the object, facing it",
    "over": "overhead, above the object",
}

_SIDE_PHASE = {
    "left": "Left of object",
    "right": "Right of object",
    "front": "Front of object",
    "back": "Back of object",
    "over": "Over object",
}


def staging():
    # x is far enough behind the back slot that a drone can park there
    # without crowding the drone still waiting on the line.
    return np.array(
        [
            [-1.00, 0.75, HOVER_Z],
            [-1.00, 0.00, HOVER_Z],
            [-1.00, -0.75, HOVER_Z],
        ],
        dtype=float,
    )


def wrap(angle):
    return (np.asarray(angle, dtype=float) + np.pi) % (2.0 * np.pi) - np.pi


def smoother(u):
    u = np.clip(np.asarray(u, dtype=float), 0.0, 1.0)
    return u * u * u * (u * (u * 6.0 - 15.0) + 10.0)


def trapezoid(u, ramp=0.12):
    """Fraction of the way along a path flown at constant speed with soft ends."""
    u = min(1.0, max(0.0, float(u)))
    peak = 1.0 / (1.0 - ramp)
    if u < ramp:
        return 0.5 * peak * u * u / ramp
    if u > 1.0 - ramp:
        return 1.0 - 0.5 * peak * (1.0 - u) ** 2 / ramp
    return peak * (u - ramp / 2.0)


def yaw_toward_origin(xy) -> float:
    return math.atan2(-float(xy[1]), -float(xy[0]))


def faces_object(point, yaw) -> bool:
    if float(np.hypot(point[0], point[1])) < 0.15:
        return False
    miss = abs(math.degrees(float(wrap(yaw - yaw_toward_origin(point[:2])))))
    return miss < FACING_TOL_DEG


def cube_faces(center=CUBE_CENTER, half=CUBE_HALF):
    cx, cy, cz = center
    x0, x1 = cx - half, cx + half
    y0, y1 = cy - half, cy + half
    z0, z1 = 0.0, CUBE_TOP
    return [
        [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)],
        [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
        [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)],
        [(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)],
        [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
    ]


def _penetrates(point) -> bool:
    pad = CUBE_HALF + BODY_PAD_M
    # SWARM: with real drones nothing flies over the chair, so the keep-out is a full-height column.
    ceiling = 99.0 if NO_FLY_OVER else CUBE_TOP + BODY_PAD_M
    return abs(float(point[0])) < pad and abs(float(point[1])) < pad and float(point[2]) < ceiling


def _clip_arena(point):
    point = np.array(point, dtype=float, copy=True)
    point[0] = min(ARENA_M, max(-ARENA_M, point[0]))
    point[1] = min(ARENA_M, max(-ARENA_M, point[1]))
    point[2] = min(MAX_Z, max(MIN_Z, point[2]))
    return point


def settle(start, end):
    """Keep a drone outside the cube and inside the room."""
    end = _clip_arena(end)
    if not _penetrates(end):
        return end
    start = np.asarray(start, dtype=float)
    if _penetrates(start):
        lifted = end.copy()
        lifted[2] = max(float(lifted[2]), CUBE_TOP + 0.12)
        return _clip_arena(lifted)
    lo, hi = 0.0, 1.0
    for _ in range(16):
        mid = 0.5 * (lo + hi)
        if _penetrates(start + (end - start) * mid):
            hi = mid
        else:
            lo = mid
    return start + (end - start) * lo


def separate(pose, moved):
    pose = np.array(pose, dtype=float, copy=True)
    moved = set(moved)
    for _ in range(3):
        for i in range(len(pose)):
            for j in range(i + 1, len(pose)):
                if i not in moved and j not in moved:
                    continue
                delta = pose[i] - pose[j]
                dist = float(np.linalg.norm(delta))
                if dist >= MIN_SEP_M:
                    continue
                if dist < 1e-9:
                    delta = np.array([1.0, 0.0, 0.0])
                    dist = 1e-9
                push = (MIN_SEP_M - dist) * (delta / dist)
                if i in moved and j in moved:
                    pose[i] = pose[i] + push * 0.5
                    pose[j] = pose[j] - push * 0.5
                elif i in moved:
                    pose[i] = pose[i] + push
                else:
                    pose[j] = pose[j] - push
    return pose


def nearest_side(pose, index: int) -> str:
    taken = set()
    for other, point in enumerate(pose):
        if other == index:
            continue
        for name, slot in STATIONS.items():
            slot = np.asarray(slot, dtype=float)
            # Tight on purpose: the staging line sits 0.25 m behind the back slot.
            close_xy = float(np.linalg.norm(point[:2] - slot[:2])) < 0.22
            close_z = abs(float(point[2]) - float(slot[2])) < 0.30
            if close_xy and close_z:
                taken.add(name)
    here = pose[index, :2]
    best_name = "over"
    best_key = None
    for name, slot in STATIONS.items():
        if name in taken:
            continue
        slot = np.asarray(slot, dtype=float)
        dist = float(np.hypot(here[0] - slot[0], here[1] - slot[1]))
        key = (dist, _PRIORITY[name])
        if best_key is None or key < best_key:
            best_key = key
            best_name = name
    return best_name


def line_time(p0, y0, p1, y1) -> float:
    travel = float(np.linalg.norm(np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)))
    turn = abs(math.degrees(float(wrap(y1 - y0))))
    if travel < 0.02 and turn < 1.0:
        return 0.0
    return float(min(40.0, max(1.5, travel / 0.25, turn / 30.0)))  # SWARM: 0.25 m/s (sim: 0.45)


def _who(ids) -> str:
    if list(ids) == [1, 2, 3]:
        return "All drones"
    if len(ids) == 1:
        return f"Drone {ids[0]}"
    return "Drones " + " and ".join(str(drone) for drone in ids)


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Line:
    """Eased straight move. A bulge bows the path sideways, zero at both ends."""

    def __init__(self, p0, y0, p1, y1, phase, duration=None, bulge=None):
        self.p0 = np.array(p0, dtype=float, copy=True)
        self.p1 = np.array(p1, dtype=float, copy=True)
        self.y0 = float(y0)
        self.y1 = float(y1)
        self.phase = phase
        self.duration = line_time(self.p0, self.y0, self.p1, self.y1) if duration is None else float(duration)
        self.bulge = None if bulge is None else np.asarray(bulge, dtype=float)

    def at(self, t):
        u = 1.0 if self.duration <= 0 else min(1.0, max(0.0, t / self.duration))
        s = float(smoother(u))
        pos = self.p0 + (self.p1 - self.p0) * s
        if self.bulge is not None:
            pos = pos + self.bulge * math.sin(math.pi * s)
        return pos, self.y0 + float(wrap(self.y1 - self.y0)) * s

    def end(self):
        return self.p1.copy(), float(wrap(self.y1))


class Arc:
    """Constant-speed circle around the cube with the nose on the cube."""

    def __init__(self, angle, sweep, radius, z, phase="Orbit"):
        self.angle = float(angle)
        self.sweep = float(sweep)
        self.radius = float(radius)
        self.z = float(z)
        self.phase = phase
        self.duration = max(3.0, abs(self.sweep) * self.radius / ORBIT_SPEED)

    def at(self, t):
        u = 1.0 if self.duration <= 0 else t / self.duration
        angle = self.angle + self.sweep * trapezoid(u)
        pos = np.array([self.radius * math.cos(angle), self.radius * math.sin(angle), self.z])
        return pos, float(wrap(angle + math.pi))

    def end(self):
        return self.at(self.duration)


class Track:
    def __init__(self, t0, segments):
        self.t0 = float(t0)
        self.segments = list(segments)
        self.marks = np.cumsum([0.0, *[segment.duration for segment in self.segments]])

    @property
    def end_time(self) -> float:
        return self.t0 + float(self.marks[-1])

    def sample(self, now):
        """Return (pos, yaw, done, segment index)."""
        elapsed = float(now) - self.t0
        last = len(self.segments) - 1
        if elapsed >= float(self.marks[-1]) - 1e-9:
            pos, yaw = self.segments[-1].end()
            return pos, yaw, True, last
        if elapsed <= 0.0:
            pos, yaw = self.segments[0].at(0.0)
            return pos, yaw, False, 0
        index = int(np.searchsorted(self.marks, elapsed, side="right") - 1)
        index = min(max(index, 0), last)
        pos, yaw = self.segments[index].at(elapsed - float(self.marks[index]))
        return pos, yaw, False, index

    def end_state(self):
        return self.segments[-1].end()

    def path(self, now, samples=80):
        start = max(float(now), self.t0)
        times = np.linspace(start, self.end_time, samples)
        return np.array([self.sample(t)[0] for t in times])


def route(p0, y0, p1, y1, phase):
    """Straight move, lifted over the cube if the straight line would go through it."""
    p0 = np.asarray(p0, dtype=float)
    p1 = np.asarray(p1, dtype=float)
    if not any(_penetrates(p0 + (p1 - p0) * s) for s in np.linspace(0.0, 1.0, 25)):
        return [Line(p0, y0, p1, y1, phase)]
    if NO_FLY_OVER:
        # SWARM: go around the chair through the nearer side point instead of over it.
        best = None
        for side in (1.0, -1.0):
            via = np.array([0.0, side * STANDOFF, 0.5 * (p0[2] + p1[2])])
            legs = [(p0, via), (via, p1)]
            if any(_penetrates(a + (b - a) * s) for a, b in legs for s in np.linspace(0.0, 1.0, 25)):
                continue
            length = float(np.linalg.norm(via - p0) + np.linalg.norm(p1 - via))
            if best is None or length < best[0]:
                best = (length, via)
        if best is not None:
            via = best[1]
            y_mid = y0 + float(wrap(y1 - y0)) * 0.5
            return [Line(p0, y0, via, y_mid, phase), Line(via, y_mid, p1, y1, phase)]
    lift = max(float(p0[2]), float(p1[2]), CUBE_TOP + 0.20)
    above_start = p0.copy()
    above_start[2] = lift
    above_end = p1.copy()
    above_end[2] = lift
    return [
        Line(p0, y0, above_start, y0, phase),
        Line(above_start, y0, above_end, y1, phase),
        Line(above_end, y1, p1, y1, phase),
    ]


def _duration(segments) -> float:
    return float(sum(segment.duration for segment in segments))


def _orbit_blocked(radius, z, points) -> bool:
    for point in points:
        rho = float(np.hypot(point[0], point[1]))
        if abs(rho - radius) < ORBIT_CLEAR_M and abs(float(point[2]) - z) < ORBIT_CLEAR_M:
            return True
    return False


def clear_orbit(radius, z, points):
    """Pick a circle no other drone sits on: wider first, then higher."""
    for candidate in (radius, *[r for r in ORBIT_RADII if r > radius + 0.1]):
        if not _orbit_blocked(candidate, z, points):
            return candidate, z
    higher = min(MAX_Z, z + 0.35)
    if not _orbit_blocked(radius, higher, points):
        return radius, higher
    return radius, z


class Scene:
    def __init__(self, climb_first=True, clock=None, planner=None):
        self.clock = clock or time.perf_counter
        self.planner = planner
        self.source = ""
        self.home = staging()
        self.yaw = np.zeros(3, dtype=float)
        self.names = ["Drone 1", "Drone 2", "Drone 3"]
        self.tracks: list[Track | None] = [None, None, None]
        self.active = None
        self.phase = "Hold"
        self.detail = ""
        self.notice = ""
        self.said = False
        self.goal = None
        self.last_actions: list[Action] | None = None
        self.command_serial = 0
        self.t_start = self.clock()
        if climb_first:
            ground = self.home.copy()
            ground[:, 2] = GROUND_Z
            self.pos = ground
            for index in range(3):
                segment = Line(ground[index], 0.0, self.home[index], 0.0, "Climb", duration=2.2)
                self.tracks[index] = Track(self.t_start, [segment])
            self.phase = "Climb"
            self.goal = self.home.copy()
        else:
            self.pos = self.home.copy()

    @property
    def moving(self) -> bool:
        return any(track is not None for track in self.tracks)

    def busy_until(self) -> float:
        ends = [track.end_time for track in self.tracks if track is not None]
        return max(ends) if ends else self.clock()

    def caption(self) -> str:
        if self.planner is not None and self.planner.busy:
            return f"Planning with {self.planner.short_name}..."
        if self.notice:
            return self.notice
        if not self.said:
            if self.planner is not None:
                return f"{self.planner.status}  {HINT}"
            return HINT
        selected = f"Drone {self.active} selected. " if self.active else ""
        source = f"   [{self.source}]" if self.source else ""
        return selected + self.detail + source

    def submit(self, text, now=None):
        """Plan a sentence. With a planner the motion starts when the model replies."""
        raw = " ".join(text.split())
        if not raw:
            return
        # A pasted list keeps its line breaks. The terminal echo is one line.
        source = text if "\n" in text else raw
        clauses = split_clauses(source)
        if clauses and all(clause in AGAIN for clause in clauses):
            if not self.last_actions:
                self.notice = "There is no previous command to repeat."
                self.said = True
                print(f"\n> {raw}\n  plan: none", flush=True)
                return
            self.apply(self.last_actions, raw, now, source="again")
            return
        actions, error = parse_commands(source)
        if not error and actions and all(action.kind in {"stop", "select"} for action in actions):
            # A stop must not wait a second for the model.
            self.apply(actions, raw, now, source="instant")
            return
        if self.planner is not None:
            self.said = True
            self.planner.ask(source, self.active)
            return
        self.apply(actions, raw, now, error=error, source="phrase parser")

    def wait_for_plan(self, timeout=120.0):
        """Block until the planner answers, then start that motion."""
        if self.planner is None:
            return
        reply = self.planner.wait(timeout)
        if reply is not None:
            self._take(reply, None)

    def _drain(self, now):
        if self.planner is None:
            return
        while True:
            reply = self.planner.poll()
            if reply is None:
                return
            self._take(reply, now)

    def _take(self, reply, now):
        text, actions, error, source, llm = reply
        self.apply(actions, " ".join(text.split()), now, error=error, source=source, llm=llm)

    def apply(self, actions, raw, now=None, error="", source="", llm=""):
        now = self.clock() if now is None else now
        self.said = True
        if error or not actions:
            self.notice = error or f"No command for “{raw}”."
            llm_line = f"\n  llm:  {llm}" if llm else ""
            print(f"\n> {raw}{llm_line}\n  plan: none\n  {self.notice}", flush=True)
            return
        self.source = source
        self._advance(now)
        plans, notes, active = self._compile(actions, now)
        for index, segments in plans.items():
            self.tracks[index] = Track(now, segments)
        self.active = active
        self.last_actions = list(actions)
        self.notice = ""
        self.detail = " · ".join(notes)
        self.command_serial += 1
        self._refresh(now)
        self._report(raw, llm)

    def finish(self):
        """Snap every drone to the end of its track. The next sentence starts there."""
        for index, track in enumerate(self.tracks):
            if track is None:
                continue
            pos, yaw = track.end_state()
            self.pos[index] = pos
            self.yaw[index] = yaw
            self.tracks[index] = None
        self.phase = "Hold"
        self.goal = None

    def tick(self, now=None):
        now = self.clock() if now is None else now
        self._drain(now)
        self._advance(now)
        self._refresh(now)
        return self.pos

    def _advance(self, now):
        for index, track in enumerate(self.tracks):
            if track is None:
                continue
            pos, yaw, done, _segment = track.sample(now)
            self.pos[index] = pos
            self.yaw[index] = yaw
            if done:
                self.tracks[index] = None

    def _refresh(self, now):
        phases = []
        goal = self.pos.copy()
        for index, track in enumerate(self.tracks):
            if track is None:
                continue
            _pos, _yaw, _done, segment = track.sample(now)
            phase = track.segments[segment].phase
            if phase not in phases:
                phases.append(phase)
            goal[index] = track.segments[segment].end()[0]
        self.phase = " · ".join(phases) if phases else "Hold"
        self.goal = goal if phases else None

    def path(self, index, now=None, samples=80):
        """Where a drone is still going to fly, or None when it is holding."""
        track = self.tracks[index]
        if track is None:
            return None
        return track.path(self.clock() if now is None else now, samples)

    def end_state(self):
        pos = self.pos.copy()
        yaw = self.yaw.copy()
        for index, track in enumerate(self.tracks):
            if track is not None:
                pos[index], yaw[index] = track.end_state()
        return pos, yaw

    def _ids(self, action: Action, active):
        if action.kind == "select":
            return []
        if action.kind == "swap":
            first = action.drone if action.scope == "one" else active
            if first is None or action.partner is None or first == action.partner:
                return []
            return [first, action.partner]
        if action.scope == "all":
            return [1, 2, 3]
        if action.scope == "one":
            return [action.drone]
        if active is not None:
            return [active]
        if action.kind == "station":
            return [1]
        return [1, 2, 3]

    def _next_active(self, action: Action, active, ids):
        if action.scope == "one":
            return action.drone
        if action.scope == "active" and len(ids) == 1:
            return ids[0]
        return active

    def _compile(self, actions, now):
        """Per-drone segments for one sentence.

        Drones the sentence names start from where they are now. The others
        keep flying, and their planned end poses stand in for them when
        checking which sides are taken.
        """
        active = self.active
        involved = set()
        for action in actions:
            ids = self._ids(action, active)
            involved.update(drone - 1 for drone in ids)
            active = self._next_active(action, active, ids)

        pose, yaw = self.end_state()
        for index in involved:
            pose[index] = self.pos[index]
            yaw[index] = self.yaw[index]
        flying = {
            index: track.path(now, 40)
            for index, track in enumerate(self.tracks)
            if track is not None and index not in involved
        }

        active = self.active
        plans = {index: [] for index in involved}
        clock = {index: 0.0 for index in involved}
        notes = []
        for action in actions:
            ids = self._ids(action, active)
            active = self._next_active(action, active, ids)
            indices = [drone - 1 for drone in ids]
            if len(indices) > 1:
                # Drones commanded together start together.
                start = max(clock[index] for index in indices)
                for index in indices:
                    wait = start - clock[index]
                    if wait > 1e-6:
                        plans[index].append(Line(pose[index], yaw[index], pose[index], yaw[index], "Wait", duration=wait))
                        clock[index] = start
            before_pose = pose.copy()
            before_yaw = yaw.copy()
            obstacles = self._obstacles(indices, pose, flying)
            pose, yaw, note, phase, special = self._apply(action, ids, pose, yaw, obstacles)
            notes.append(note)
            for index in indices:
                segments = special.get(index)
                if segments is None:
                    segments = route(before_pose[index], before_yaw[index], pose[index], yaw[index], phase)
                plans[index].extend(segments)
                clock[index] += _duration(segments)
        for index in involved:
            if not plans[index]:
                plans[index].append(Line(pose[index], yaw[index], pose[index], yaw[index], "Hold", duration=0.0))
        return plans, notes, active

    def _obstacles(self, indices, pose, flying):
        points = []
        for other in range(3):
            if other in indices:
                continue
            if other in flying:
                points.extend(flying[other])
            points.append(pose[other])
        return points

    def _apply(self, action: Action, ids, pose, yaw, obstacles):
        """Return (pose, yaw, note, phase, special segments by drone index)."""
        pose = pose.copy()
        yaw = yaw.copy()
        indices = [drone - 1 for drone in ids]

        if action.kind == "select":
            return pose, yaw, f"Drone {action.drone} is listening", "Select", {}

        if action.kind == "stop":
            return pose, yaw, f"{_who(ids)} stop and hover", "Stop", {}

        if action.kind == "swap":
            if len(ids) != 2:
                return pose, yaw, "Swap needs two different drones, for example: swap drone 1 and drone 3", "Hold", {}
            return self._swap(ids, pose, yaw)

        if action.kind == "orbit":
            if len(ids) > 1:
                return self._orbit_together(action, ids, pose, yaw, obstacles)
            return self._orbit_one(action, ids[0], pose, yaw, obstacles)

        if action.kind == "home":
            for index in indices:
                pose[index] = self.home[index]
                yaw[index] = 0.0
            return pose, yaw, f"{_who(ids)} → back to the start line", "Home", {}

        if action.kind == "inspect" and action.scope == "all":
            for drone, side in ((1, "left"), (2, "over"), (3, "right")):
                pose, yaw = self._place(pose, yaw, drone, side)
            pose = separate(pose, [0, 1, 2])
            note = "Drone 1 to the left, Drone 2 overhead, Drone 3 to the right, facing the object"
            return pose, yaw, note, "Inspect", {}

        if action.kind == "inspect":
            drone = ids[0]
            side = nearest_side(pose, drone - 1)
            pose, yaw = self._place(pose, yaw, drone, side)
            pose = separate(pose, [drone - 1])
            where = "overhead" if side == "over" else f"the {side}"
            return pose, yaw, f"Drone {drone} inspects from {where}", "Inspect", {}

        if action.kind == "station":
            for drone in ids:
                pose, yaw = self._place(pose, yaw, drone, action.side)
            pose = separate(pose, indices)
            return pose, yaw, f"{_who(ids)} → {_SIDE_PHRASE[action.side]}", _SIDE_PHASE[action.side], {}

        if action.kind == "radial":
            inward = action.directions[:1] == ("in",)
            for index in indices:
                pose[index], yaw[index] = self._radial(pose[index], yaw[index], action.distance, inward)
            pose = separate(pose, indices)
            word = "closer to" if inward else "farther from"
            return pose, yaw, f"{_who(ids)} → {action.distance * 100:.0f} cm {word} the object", "Closer" if inward else "Farther", {}

        if action.kind == "nudge":
            for index in indices:
                start = pose[index].copy()
                facing = faces_object(start, yaw[index])
                delta = np.zeros(3)
                for direction in action.directions:
                    delta = delta + _DIRECTION[direction] * action.distance
                pose[index] = settle(start, start + delta)
                if facing and float(np.hypot(pose[index, 0], pose[index, 1])) > 0.15:
                    yaw[index] = yaw_toward_origin(pose[index, :2])
            pose = separate(pose, indices)
            step = " and ".join(f"{direction} {action.distance:.2f} m" for direction in action.directions)
            return pose, yaw, f"{_who(ids)} → {step}", "Nudge", {}

        if action.kind == "yaw":
            delta = math.radians(action.yaw_deg)
            for index in indices:
                yaw[index] = float(wrap(yaw[index] + delta))
            return pose, yaw, f"{_who(ids)} → yaw {action.yaw_deg:+.0f}°", "Yaw", {}

        if action.kind == "face":
            for index in indices:
                if float(np.hypot(pose[index, 0], pose[index, 1])) > 0.15:
                    yaw[index] = yaw_toward_origin(pose[index, :2])
            return pose, yaw, f"{_who(ids)} → facing the object", "Face", {}

        return pose, yaw, f"{_who(ids)} hold", "Hold", {}

    def _place(self, pose, yaw, drone, side):
        pose = pose.copy()
        yaw = yaw.copy()
        index = drone - 1
        start = pose[index].copy()
        target = np.array(STATIONS[side], dtype=float)
        pose[index] = settle(start, target)
        if side != "over" and float(np.hypot(pose[index, 0], pose[index, 1])) > 0.15:
            yaw[index] = yaw_toward_origin(pose[index, :2])
        return pose, yaw

    def _radial(self, point, yaw, distance, inward):
        point = np.asarray(point, dtype=float)
        rho = float(np.hypot(point[0], point[1]))
        target = point.copy()
        if rho < 0.15:
            # Straight above the cube, closer means lower.
            if inward:
                target[2] = max(float(point[2]) - distance, CUBE_TOP + 0.20)
            else:
                target[2] = min(float(point[2]) + distance, MAX_Z)
            return settle(point, target), yaw
        new_rho = max(MIN_RADIAL_M, rho - distance) if inward else rho + distance
        target[0] = point[0] / rho * new_rho
        target[1] = point[1] / rho * new_rho
        target = settle(point, target)
        return target, yaw_toward_origin(target[:2])

    def _orbit_one(self, action, drone, pose, yaw, obstacles):
        index = drone - 1
        point = pose[index].copy()
        rho = float(np.hypot(point[0], point[1]))
        radius = action.radius or (rho if 0.6 <= rho <= 1.8 else STANDOFF)
        z = min(MAX_Z, max(ORBIT_MIN_Z, float(point[2])))
        radius, z = clear_orbit(radius, z, obstacles)
        angle = math.atan2(point[1], point[0]) if rho > 0.1 else math.pi
        turns = action.turns or 1.0
        arc = Arc(angle, action.sign * 2.0 * math.pi * turns, radius, z)
        start, start_yaw = arc.at(0.0)
        segments = route(point, yaw[index], start, start_yaw, "Orbit") + [arc]
        pose[index], yaw[index] = arc.end()
        laps = f"{turns:g} lap" + ("" if turns == 1 else "s")
        way = "clockwise" if action.sign < 0 else "counterclockwise"
        note = f"Drone {drone} circles the object {way}, {laps} at {radius:.2f} m, {z:.2f} m up, facing it"
        return pose, yaw, note, "Orbit", {index: segments}

    def _orbit_together(self, action, ids, pose, yaw, obstacles):
        indices = [drone - 1 for drone in ids]
        count = len(indices)
        radius = action.radius or STANDOFF
        radius, z = clear_orbit(radius, HOVER_Z, obstacles)
        step = 2.0 * math.pi / count
        best = None
        for reference in indices:
            base = math.atan2(pose[reference, 1], pose[reference, 0])
            for order in permutations(range(count)):
                angles = [base + step * slot for slot in order]
                travel = sum(
                    float(np.hypot(pose[i, 0] - radius * math.cos(a), pose[i, 1] - radius * math.sin(a)))
                    for i, a in zip(indices, angles)
                )
                if best is None or travel < best[0]:
                    best = (travel, angles)
        angles = best[1]
        # Each drone covers its share of the circle, so together they see all of it.
        turns = action.turns or 1.0 / count
        arcs = {}
        approaches = {}
        for index, angle in zip(indices, angles):
            arcs[index] = Arc(angle, action.sign * 2.0 * math.pi * turns, radius, z)
            start, start_yaw = arcs[index].at(0.0)
            approaches[index] = route(pose[index], yaw[index], start, start_yaw, "Orbit")
        longest = max(_duration(segments) for segments in approaches.values())
        special = {}
        for index in indices:
            segments = list(approaches[index])
            wait = longest - _duration(segments)
            start, start_yaw = arcs[index].at(0.0)
            if wait > 1e-6:
                segments.append(Line(start, start_yaw, start, start_yaw, "Orbit", duration=wait))
            segments.append(arcs[index])
            special[index] = segments
            pose[index], yaw[index] = arcs[index].end()
        way = "clockwise" if action.sign < 0 else "counterclockwise"
        sweep = 360.0 * turns
        note = (
            f"{_who(ids)} circle the object {360 // count}° apart, {way}, "
            f"{sweep:.0f}° each at {radius:.2f} m, facing it"
        )
        return pose, yaw, note, "Orbit", special

    def _swap(self, ids, pose, yaw):
        first, second = (drone - 1 for drone in ids)
        p_first, y_first = pose[first].copy(), float(yaw[first])
        p_second, y_second = pose[second].copy(), float(yaw[second])
        across = p_second - p_first
        flat = float(np.hypot(across[0], across[1]))
        if flat > 0.1:
            bulge = np.array([-across[1], across[0], 0.0]) / flat * SWAP_OFFSET_M
        else:
            bulge = np.array([SWAP_OFFSET_M, 0.0, 0.0])
        duration = max(2.0, line_time(p_first, y_first, p_second, y_second))
        special = {
            first: [Line(p_first, y_first, p_second, y_second, "Swap", duration=duration)],
            # The second drone bows out to one side so the two never meet.
            second: [Line(p_second, y_second, p_first, y_first, "Swap", duration=duration, bulge=bulge)],
        }
        pose[first], pose[second] = p_second, p_first
        yaw[first], yaw[second] = y_second, y_first
        return pose, yaw, f"Drone {ids[0]} and Drone {ids[1]} swap places", "Swap", special

    def _report(self, text, llm=""):
        print(f"\n> {text}", flush=True)
        if llm:
            print(f"  llm:  {llm}", flush=True)
        print(f"  plan: {self.detail}  [{self.source}]", flush=True)
        pos, yaw = self.end_state()
        for index, name in enumerate(self.names):
            point = pos[index]
            shown = math.degrees(float(yaw[index]))
            if abs(shown) < 0.05:
                shown = 0.0
            print(
                f"  {name}: x={point[0]:+.2f}  y={point[1]:+.2f}  z={point[2]:.2f}  "
                f"yaw={shown:+.0f}°",
                flush=True,
            )
