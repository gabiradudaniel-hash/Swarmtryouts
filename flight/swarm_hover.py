#!/usr/bin/env python3
"""Hover several Crazyflies at once with OptiTrack, then land. Live video optional.

Built on the course code (crazyflie_progressive_square_v2): mocap.py and motive.py in this folder are
unchanged copies, so OptiTrack handling, the Kalman checks, the takeoff ramp and the landing are the
lab's. This file adds: several drones on one Crazyradio, one heading capture for all, a shared safety
monitor that lands EVERY drone when anything looks wrong, and an optional live-video window.

    python3 swarm_hover.py --client 10.10.10.2 --drones 5 7 8 --video
    python3 swarm_hover.py --sim --drones 5 7 8          # rehearsal, no hardware

During the flight: Enter = land all, k then Enter = motors off now (drones drop), Ctrl+C = land all
(a second Ctrl+C = motors off).
"""

from __future__ import annotations

import argparse
import math
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import mocap as ext  # noqa: E402  (course code, unchanged)

# Hover and safety limits. Conservative on purpose; change one at a time.
TICK_HZ = 20.0                # setpoint rate per drone (course code uses 20 Hz)
ARM_HOLD_S = 1.0              # position setpoint on the floor after arming, before the climb
CLIMB_S = ext.CLIMB_S         # 3 s ramp up (course value)
LAND_S = ext.CLIMB_S          # 3 s ramp down
FLOOR_HOLD_S = 0.5            # setpoint on the floor before disarming
POSE_STALE_S = 0.25           # OptiTrack older than this counts as lost
POSE_LOST_HOLD_S = 0.30       # ...for this long -> land all
FENCE_HOLD_S = 0.25
MIN_AIR_SEPARATION_M = 0.30   # any two flying drones closer than this -> land all
TRACK_ERR_M = 0.30            # onboard estimate vs OptiTrack in the hover
LOW_BATTERY_AIR_V = 3.20      # under load, held for 1 s -> land all
SETTLE_TOL_M = 0.15


class QuietEstimates(ext.EstimateBuffer):
    """The course EstimateBuffer without its console printing (three drones would flood it)."""

    def on_log(self, _timestamp, data, _logconf):
        with self._lock:
            if "locSrv.x" in data:
                self.loc = (data["locSrv.x"], data["locSrv.y"], data["locSrv.z"])
            if "stateEstimate.x" in data:
                self.estimate = (data["stateEstimate.x"], data["stateEstimate.y"], data["stateEstimate.z"])
                self.yaw = data["stateEstimate.yaw"]
                if "stateEstimate.roll" in data:
                    self.roll = data["stateEstimate.roll"]
                    self.pitch = data["stateEstimate.pitch"]


class Drone:
    def __init__(self, body_id, uri, matrix):
        self.body_id = body_id
        self.name = f"cf{body_id}"
        self.uri = uri
        self.matrix = matrix
        self.poses = ext.PoseBuffer(body_id, 0.0, matrix)
        self.estimates = QuietEstimates()
        self.vbat = None
        self.cf = None
        self.scf = None
        self.stop = threading.Event()
        self.yaw_locked = threading.Event()
        self.link_lost = threading.Event()
        self.sender = None
        # flight state
        self.phase = "GROUND"     # GROUND -> WAIT -> ARMED -> CLIMB -> HOVER -> LAND -> DONE
        self.phase_t = 0.0
        self.home = None          # (x, y, z) Crazyflie frame
        self.yaw_deg = 0.0
        self.setpoint = None      # last commanded (x, y, z)
        self.land_from = None
        self.armed = False
        self.flags = {}           # safety timers

    # ---- OptiTrack helpers
    def pose(self):
        _m, cf_pos, age, _trail, yaw = self.poses.snapshot()
        return cf_pos, age, yaw

    def fresh(self, max_age=ext.STALE_S):
        cf_pos, age, _yaw = self.pose()
        return cf_pos is not None and age is not None and age <= max_age

    def on_vbat(self, _ts, data, _conf):
        self.vbat = float(data["pm.vbat"])


# ------------------------------------------------------------------------------------------------
# Simulation: stands in for the Crazyflies and Motive so the whole program can be rehearsed.
# ------------------------------------------------------------------------------------------------
class _Obj:
    pass


class SimCrazyflie:
    """Just enough of cflib's Crazyflie for this program and mocap.py."""

    def __init__(self, drone, start_xyz, fault=None):
        self.drone = drone
        self.pos = list(start_xyz)
        self.target = list(start_xyz)
        self.armed = False
        self.last_sp = 0.0
        self.vbat = 4.05
        self.fault = fault
        self.bias = 0.0
        self.t0 = time.monotonic()
        self.commander = _Obj()
        self.commander.send_position_setpoint = self._sp
        self.commander.send_notify_setpoint_stop = lambda *a: None
        self.commander.send_stop_setpoint = lambda *a: self._kill()
        self.supervisor = _Obj()
        self.supervisor.send_arming_request = self._arm
        self.supervisor.send_emergency_stop = self._kill
        self.extpos = _Obj()
        self.extpos.send_extpos = lambda *a: None
        self.extpos.send_extpose = lambda *a: None
        self.param = _Obj()
        self.param.get_value = lambda name: "2"
        self.param.set_value = lambda name, value: None

    def _sp(self, x, y, z, yaw):
        self.target = [x, y, z]
        self.last_sp = time.monotonic()

    def _arm(self, on):
        self.armed = bool(on)

    def _kill(self):
        self.armed = False

    def physics(self, dt):
        flying = self.armed and time.monotonic() - self.last_sp < 0.5
        if flying:
            if self.fault == "drift" and time.monotonic() - self.t0 > 14.0:
                self.bias += 0.15 * dt            # growing estimate error: the drone slides away
            goal = [self.target[0] + self.bias, self.target[1], self.target[2]]
            for i in range(3):
                self.pos[i] += (goal[i] - self.pos[i]) * min(1.0, dt / 0.35)
            self.vbat -= dt * 0.004
        else:
            self.pos[2] = max(self.drone.home_floor, self.pos[2] - 1.5 * dt)


def run_simulation_feed(drones, sims, stop, faults):
    """100 Hz OptiTrack-like feed + 10 Hz onboard estimate/battery logs."""
    k = 0
    last = time.monotonic()
    while not stop.is_set():
        now = time.monotonic()
        dt, last = now - last, now
        for d in drones:
            sim = sims[d.name]
            sim.physics(dt)
            if faults.get(d.name) == "mocap" and now - sim.t0 > 14.0:
                continue                        # rigid body lost in Motive
            m = d.matrix                        # Motive = matrix^T @ crazyflie (signed permutation)
            x, y, z = sim.pos
            motive = tuple(m[0][i] * x + m[1][i] * y + m[2][i] * z for i in range(3))
            d.poses.on_rigid_body(d.body_id, motive, (0.0, 0.0, 0.0, 1.0))
            if k % 10 == 0:
                n = lambda: random.gauss(0, 0.004)  # noqa: E731
                d.estimates.on_log(0, {"stateEstimate.x": x + n(), "stateEstimate.y": y + n(),
                                       "stateEstimate.z": z + n(), "stateEstimate.yaw": 0.0,
                                       "stateEstimate.roll": 0.0, "stateEstimate.pitch": 0.0}, None)
                d.vbat = sim.vbat
        k += 1
        stop.wait(0.01)


# ------------------------------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("server", nargs="?", help="Motive PC IP (informational; the stream is multicast)")
    p.add_argument("--client", help="This computer's IP on the OptiTrack network (required for real flights)")
    p.add_argument("--drones", type=int, nargs="+", default=[5, 7, 8], help="Body ids = last digit of the radio address")
    p.add_argument("--radio", type=int, default=0, help="Crazyradio index (default 0: all drones on one radio)")
    p.add_argument("--channel", type=int, default=80)
    p.add_argument("--datarate", default="2M")
    p.add_argument("--height", type=float, default=0.5, help="Climb above each takeoff point, m (0.3-0.8)")
    p.add_argument("--hover-time", type=float, default=20.0, help="Seconds of hover before landing (5-120)")
    p.add_argument("--stagger", type=float, default=1.5, help="Seconds between takeoffs")
    p.add_argument("--fence", type=float, default=0.40, help="Max horizontal drift from the hover point, m")
    p.add_argument("--min-separation", type=float, default=0.60, help="Min distance between drones on the floor, m")
    p.add_argument("--min-battery", type=float, default=3.80, help="Min battery on the floor, V")
    p.add_argument("--video", action="store_true", help="Open tools/live_view.py for the drones' decks first")
    p.add_argument("--sim", action="store_true", help="Rehearse without drones, radio or Motive")
    p.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)        # sim only: no prompts
    p.add_argument("--sim-fault", default="", help=argparse.SUPPRESS)          # e.g. cf7:drift, cf8:mocap
    p.add_argument("--sim-land-at", type=float, default=0.0, help=argparse.SUPPRESS)
    a = p.parse_args()
    if not 0.3 <= a.height <= 0.8:
        raise SystemExit("--height must be between 0.3 and 0.8 m.")
    if not 5 <= a.hover_time <= 120:
        raise SystemExit("--hover-time must be between 5 and 120 s.")
    if not 0.2 <= a.fence <= 1.0:
        raise SystemExit("--fence must be between 0.2 and 1.0 m.")
    if len(set(a.drones)) != len(a.drones) or not all(1 <= d <= 9 for d in a.drones):
        raise SystemExit("--drones takes distinct body ids 1-9.")
    if len(a.drones) > 3 and not a.sim:
        print("Note: the lab limit is 3 drones per Crazyradio.")
    if a.yes and not a.sim:
        raise SystemExit("--yes is only allowed with --sim.")
    if not a.sim and not a.client:
        raise SystemExit("--client <this computer's IP on the OptiTrack network> is required.")
    return a


def ask(args, prompt):
    if args.sim and args.yes:
        print(prompt + " [auto]")
        return ""
    return input(prompt)


def deck_ips(ids):
    """Deck addresses from config/drones.yaml, else the 192.168.50.1<id> scheme."""
    ips = {i: f"192.168.50.1{i}" for i in ids}
    try:
        sys.path.insert(0, str(REPO))
        from swarm.config import load_config
        for d in load_config(REPO / "config" / "drones.yaml").drones:
            if d.body_id in ips and d.deck_ip:
                ips[d.body_id] = d.deck_ip
    except Exception:
        pass
    return ips


def start_video(ids):
    viewer = REPO / "tools" / "live_view.py"
    if not viewer.exists():
        print("tools/live_view.py not found; no video.")
        return None
    cmd = [sys.executable, str(viewer)]
    for i, ip in deck_ips(ids).items():
        cmd += ["--deck", f"cf{i}={ip}"]
    print("Opening the live video window: " + " ".join(cmd[2:]))
    print("  (needs this computer on SWARM-NET; the window stays open after landing, press q in it)")
    return subprocess.Popen(cmd, cwd=str(REPO))


def lock_all_headings(args, drones):
    """Course lock_wall_heading(), for every drone at once: noses at the calibration wall, Enter."""
    print()
    print("Put every drone on the floor, nose toward the calibration wall (the wall used in calibrate_axes.py),")
    print("at least %.1f m apart, with clear space above. Do not touch them." % args.min_separation)
    while True:
        ask(args, "Press Enter to capture all headings. ")
        samples = {d.name: [] for d in drones}
        seen = {d.name: set() for d in drones}
        t_end = time.monotonic() + 0.8
        while time.monotonic() < t_end:
            for d in drones:
                rot, stamp = d.poses.rotation_snapshot()
                if rot is not None and stamp not in seen[d.name]:
                    seen[d.name].add(stamp)
                    samples[d.name].append(rot)
            time.sleep(0.02)
        ok = True
        for d in drones:
            rots = samples[d.name]
            if len(rots) < 5:
                print(f"  {d.name}: no orientation from Motive. Is rigid body {d.body_id} tracked?")
                ok = False
                continue
            mean = ext.average_quaternion(rots)
            axis = ext.wall_body_axis(mean, d.matrix[0])
            if mean is None or axis is None:
                print(f"  {d.name}: could not read a heading.")
                ok = False
                continue
            yaws = [ext.heading_of_body_axis(r, axis, 0.0, d.matrix) for r in rots]
            _m, tight = ext.circular_mean_deg(yaws)
            if tight < 0.99:
                print(f"  {d.name}: was moving. Leave it still.")
                ok = False
                continue
            d.poses.set_wall_heading(axis, ext.wrap_deg(-ext.heading_of_body_axis(mean, axis, 0.0, d.matrix)))
        if ok:
            break
        print("Try again.")
    time.sleep(0.3)
    print("Headings captured: " + ", ".join(
        f"{d.name} " + ("--" if d.pose()[2] is None else "%+.0f deg" % d.pose()[2]) for d in drones))


def ground_separation(drones):
    worst = None
    for i, a in enumerate(drones):
        for b in drones[i + 1:]:
            pa, pb = a.pose()[0], b.pose()[0]
            dist = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
            if worst is None or dist < worst[0]:
                worst = (dist, a.name, b.name)
    return worst


def connect(args, drones, sims):
    """Open every link, start the OptiTrack->drone sender, reset Kalman, start logs."""
    if args.sim:
        for d in drones:
            d.cf = sims[d.name]
    else:
        import cflib.crtp
        from cflib.crazyflie import Crazyflie
        from cflib.crazyflie.log import LogConfig
        from cflib.crazyflie.syncCrazyflie import SyncCrazyflie
        cflib.crtp.init_drivers()
        cache = HERE / "cache"
        cache.mkdir(exist_ok=True)
        for d in drones:
            print(f"Connecting {d.name} on {d.uri} ...")
            d.scf = SyncCrazyflie(d.uri, cf=Crazyflie(rw_cache=str(cache)))
            try:
                d.scf.open_link()
            except Exception as exc:
                raise SystemExit(f"{d.name}: could not connect ({exc}). Is it on, and is cfclient closed?")
            d.cf = d.scf.cf
            d.cf.connection_lost.add_callback(lambda *_a, dd=d: dd.link_lost.set())
            d.cf.disconnected.add_callback(lambda *_a, dd=d: dd.link_lost.set())
    for d in drones:
        ext.ensure_kalman(d.cf)
        try:
            d.cf.param.set_value("locSrv.extQuatStdDev", "0.05")
        except Exception as exc:
            print(f"{d.name}: could not set locSrv.extQuatStdDev ({exc}).")
        d.sender = threading.Thread(target=ext.send_loop, args=(d.cf, d.poses, d.estimates, d.stop, d.yaw_locked),
                                    daemon=True, name=f"extpos-{d.name}")
        d.sender.start()
    time.sleep(0.3)
    for d in drones:
        yaw = d.pose()[2]
        ext.reset_kalman(d.cf, None if yaw is None else math.radians(yaw))
        if not args.sim:
            ext.start_state_log(d.cf, d.estimates)
            try:
                bat = LogConfig(name="Battery", period_in_ms=500)
                bat.add_variable("pm.vbat", "float")
                d.cf.log.add_config(bat)
                bat.data_received_cb.add_callback(d.on_vbat)
                bat.start()
            except Exception as exc:
                print(f"{d.name}: no battery log ({exc}).")


def wait_tracking(drones, timeout=25.0):
    """Course wait_until_tracking() for all drones at once, one status line per second."""
    print("Waiting until every onboard estimate matches OptiTrack (%.2f m, %.0f deg). Drones stay down."
          % (ext.TRACK_TOL_M, ext.YAW_TOL_DEG))
    since = {d.name: None for d in drones}
    t_end = time.monotonic() + timeout
    next_print = 0.0
    while time.monotonic() < t_end:
        parts = []
        for d in drones:
            err = ext.tracking_error(d.poses, d.estimates)
            mocap_yaw = d.pose()[2]
            _e, est_yaw, _r, _p = d.estimates.snapshot()
            yaw_ok = mocap_yaw is not None and abs(ext.wrap_deg(est_yaw - mocap_yaw)) <= ext.YAW_TOL_DEG
            if err is not None and err <= ext.TRACK_TOL_M and yaw_ok:
                since[d.name] = since[d.name] or time.monotonic()
                if time.monotonic() - since[d.name] >= 1.0:
                    d.yaw_locked.set()
            else:
                since[d.name] = None
            state = "OK" if d.yaw_locked.is_set() else "..."
            parts.append(f"{d.name} {state} err={'--' if err is None else '%.2f' % err} "
                         f"yaw est {est_yaw:+.0f}/mocap {'--' if mocap_yaw is None else '%+.0f' % mocap_yaw}")
        if time.monotonic() >= next_print:
            print("  " + " | ".join(parts))
            next_print = time.monotonic() + 1.0
        if all(d.yaw_locked.is_set() for d in drones):
            print("All estimates match OptiTrack.")
            return True
        time.sleep(0.1)
    return False


def preflight(args, drones):
    problems = []
    for d in drones:
        if not d.fresh():
            problems.append(f"{d.name}: OptiTrack position is stale.")
            continue
        err = ext.tracking_error(d.poses, d.estimates)
        if err is None or err > ext.TRACK_TOL_M:
            problems.append(f"{d.name}: estimate and OptiTrack disagree ({err}).")
        _e, est_yaw, _r, _p = d.estimates.snapshot()
        yaw = d.pose()[2]
        if yaw is None or abs(ext.wrap_deg(est_yaw - yaw)) > ext.YAW_TOL_DEG:
            problems.append(f"{d.name}: yaw drifted (estimate {est_yaw:+.0f}, OptiTrack {yaw}).")
        if d.vbat is None:
            print(f"  {d.name}: battery unknown (no pm.vbat log).")
        elif d.vbat < args.min_battery:
            problems.append(f"{d.name}: battery {d.vbat:.2f} V < {args.min_battery:.2f} V. Swap it.")
    worst = ground_separation(drones) if len(drones) > 1 else None
    if worst and worst[0] < args.min_separation:
        problems.append(f"{worst[1]} and {worst[2]} are only {worst[0]:.2f} m apart (need {args.min_separation:.2f}).")
    return problems


# ------------------------------------------------------------------------------------------------
def arm(cf):
    cf.commander.send_notify_setpoint_stop()
    time.sleep(0.05)
    cf.supervisor.send_arming_request(True)


def disarm(d):
    try:
        d.cf.commander.send_notify_setpoint_stop()
        time.sleep(0.05)
        d.cf.supervisor.send_arming_request(False)
    except Exception as exc:
        print(f"{d.name}: could not disarm cleanly ({exc}). Cut its power if the motors still spin.")
    d.armed = False


def kill_all(drones):
    print("MOTORS OFF for all drones.")
    for d in drones:
        if d.cf is None:
            continue
        try:
            d.cf.supervisor.send_emergency_stop()
        except Exception:
            try:
                d.cf.commander.send_stop_setpoint()
            except Exception:
                pass
        d.armed = False
        d.phase = "DONE"


def begin_landing(drones, now, reason):
    print(f"\n*** LANDING ALL: {reason}")
    for d in drones:
        if d.phase in ("GROUND", "WAIT"):
            d.phase = "DONE"
        elif d.phase in ("ARMED", "CLIMB", "HOVER"):
            x, y, z = d.setpoint
            cf_pos, age, _yaw = d.pose()
            if cf_pos is not None and age is not None and age <= ext.STALE_S \
                    and abs(cf_pos[2] - z) < 0.4 and math.hypot(cf_pos[0] - x, cf_pos[1] - y) < 0.5:
                x, y, z = cf_pos             # start the descent from where it really is (course descend())
            z = min(max(z, d.home[2]), d.home[2] + 1.2)
            d.land_from = (x, y, z)
            d.phase, d.phase_t = "LAND", now


def safety_check(args, drones, now):
    """Return a reason string if everything must land, else None."""
    flying = [d for d in drones if d.phase in ("ARMED", "CLIMB", "HOVER")]
    for d in flying:
        f = d.flags
        if d.link_lost.is_set():
            return f"{d.name}: radio link lost"
        cf_pos, age, _yaw = d.pose()
        if cf_pos is None or age is None or age > POSE_STALE_S:
            f.setdefault("stale", now)
            if now - f["stale"] >= POSE_LOST_HOLD_S:
                return f"{d.name}: OptiTrack lost"
            continue
        f.pop("stale", None)
        drift = math.hypot(cf_pos[0] - d.home[0], cf_pos[1] - d.home[1])
        if drift > args.fence:
            f.setdefault("fence", now)
            if now - f["fence"] >= FENCE_HOLD_S:
                return f"{d.name}: drifted {drift:.2f} m from its spot"
        else:
            f.pop("fence", None)
        if cf_pos[2] > d.home[2] + args.height + 0.35:
            return f"{d.name}: too high ({cf_pos[2] - d.home[2]:.2f} m)"
        if d.phase == "HOVER":
            if cf_pos[2] < d.home[2] + 0.10:
                f.setdefault("low", now)
                if now - f["low"] >= 0.4:
                    return f"{d.name}: altitude collapsed"
            else:
                f.pop("low", None)
            err = ext.tracking_error(d.poses, d.estimates)
            if err is not None and err > TRACK_ERR_M:
                f.setdefault("track", now)
                if now - f["track"] >= 0.5:
                    return f"{d.name}: onboard estimate is {err:.2f} m off OptiTrack"
            else:
                f.pop("track", None)
        if d.vbat is not None and d.vbat < LOW_BATTERY_AIR_V:
            f.setdefault("bat", now)
            if now - f["bat"] >= 1.0:
                return f"{d.name}: battery {d.vbat:.2f} V"
        else:
            f.pop("bat", None)
    for i, a in enumerate(flying):
        for b in flying[i + 1:]:
            pa, pb = a.pose()[0], b.pose()[0]
            if pa is None or pb is None:
                continue
            dist = math.dist(pa, pb)
            if dist < MIN_AIR_SEPARATION_M:
                return f"{a.name} and {b.name} only {dist:.2f} m apart"
    return None


def step_drone(args, d, now):
    """Advance one drone's phase and send its setpoint for this tick."""
    hx, hy, hz = d.home
    top = hz + args.height
    t = now - d.phase_t
    if d.phase == "ARMED":
        sp = (hx, hy, hz)
        if t >= ARM_HOLD_S:
            d.phase, d.phase_t = "CLIMB", now
    elif d.phase == "CLIMB":
        frac = min(1.0, t / CLIMB_S)
        sp = (hx, hy, hz + (top - hz) * frac)
        if frac >= 1.0:
            cf_pos = d.pose()[0]
            if cf_pos is not None and abs(cf_pos[2] - top) < SETTLE_TOL_M:
                d.phase, d.phase_t = "HOVER", now
            elif t > CLIMB_S + 8.0:
                return "climb did not settle"
    elif d.phase == "HOVER":
        sp = (hx, hy, top)
    elif d.phase == "LAND":
        x, y, z = d.land_from
        frac = min(1.0, t / LAND_S)
        sp = (x, y, z + (hz - z) * frac)
        if t >= LAND_S + FLOOR_HOLD_S:
            d.cf.commander.send_position_setpoint(x, y, hz, d.yaw_deg)
            disarm(d)
            d.phase = "DONE"
            print(f"{d.name}: landed and disarmed.")
            return None
    else:
        return None
    d.setpoint = sp
    d.cf.commander.send_position_setpoint(sp[0], sp[1], sp[2], d.yaw_deg)
    return None


def status_line(drones, t):
    parts = []
    for d in drones:
        cf_pos, _age, _y = d.pose()
        h = "--" if cf_pos is None or d.home is None else "%.2f" % (cf_pos[2] - d.home[2])
        bat = "--" if d.vbat is None else "%.2fV" % d.vbat
        parts.append(f"{d.name} {d.phase:<5} h={h} {bat}")
    return f"t={t:5.1f}  " + " | ".join(parts)


def fly(args, drones):
    commands = []

    def keyboard():
        while True:
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if line == "":
                return                       # stdin closed: no keyboard control, Ctrl+C still lands
            commands.append("kill" if line.strip().lower() == "k" else "land")

    threading.Thread(target=keyboard, daemon=True).start()
    print("\nFLYING. Enter = land all.  k + Enter = motors off now.  Ctrl+C = land all.\n")
    t0 = time.monotonic()
    for i, d in enumerate(drones):
        d.phase, d.phase_t = "WAIT", t0 + i * args.stagger
        d.setpoint = d.home
    hover_since = None
    landing = False
    next_print = t0
    period = 1.0 / TICK_HZ
    ctrl_c = 0
    while True:
        try:
            now = time.monotonic()
            if commands:
                cmd = commands.pop(0)
                if cmd == "kill":
                    kill_all(drones)
                    return
                if not landing:
                    begin_landing(drones, now, "Enter pressed")
                    landing = True
            if args.sim_land_at and not landing and now - t0 > args.sim_land_at:
                begin_landing(drones, now, "simulated Enter")
                landing = True
            for d in drones:
                if d.phase == "WAIT" and now >= d.phase_t and not landing:
                    print(f"{d.name}: arming and taking off.")
                    arm(d.cf)
                    d.armed = True
                    d.phase, d.phase_t = "ARMED", now
            if not landing:
                reason = safety_check(args, drones, now)
                if reason is None:
                    for d in drones:
                        if d.phase in ("ARMED", "CLIMB", "HOVER"):
                            reason = step_drone(args, d, now)
                            if reason:
                                reason = f"{d.name}: {reason}"
                                break
                if reason:
                    begin_landing(drones, now, reason)
                    landing = True
                elif all(d.phase == "HOVER" for d in drones):
                    if hover_since is None:
                        hover_since = now
                        print(f"All {len(drones)} drones hovering. Landing in {args.hover_time:.0f} s.")
                    elif now - hover_since >= args.hover_time:
                        begin_landing(drones, now, "hover time done")
                        landing = True
            if landing:
                for d in drones:
                    if d.phase == "LAND":
                        step_drone(args, d, now)
                if all(d.phase == "DONE" for d in drones):
                    print("All drones landed and disarmed.")
                    return
            if now >= next_print:
                print(status_line(drones, now - t0))
                next_print = now + 1.0
            time.sleep(period)
        except KeyboardInterrupt:
            ctrl_c += 1
            if ctrl_c >= 2:
                kill_all(drones)
                return
            if not landing:
                begin_landing(drones, time.monotonic(), "Ctrl+C")
                landing = True
            else:
                print("Already landing. Ctrl+C again = motors off.")


def main():
    args = parse_args()
    drones = []
    for body_id in args.drones:
        matrix, _yaw, loaded = ext.load_axis_calibration(body_id)
        path = ext.resolve_calibration_path(body_id)
        if not loaded or path.name != f"axis_calibration_{body_id}.json":
            raise SystemExit(
                f"No axis calibration for drone {body_id} (flight/axis_calibration_{body_id}.json).\n"
                f"Run: python3 flight/calibrate_axes.py <motive-ip> --client <this-ip> --body-id {body_id} --address 0xE7E7E7E7E{body_id}")
        uri = f"radio://{args.radio}/{args.channel}/{args.datarate}/E7E7E7E7E{body_id}"
        drones.append(Drone(body_id, uri, matrix))
    print("Drones: " + ", ".join(f"{d.name} ({d.uri})" for d in drones))
    if args.sim:
        print("SIMULATION: no radio, no Motive, nothing flies.")

    feed_stop = threading.Event()
    sims, motive = {}, None
    if args.sim:
        faults = {}
        for item in filter(None, args.sim_fault.split(",")):
            name, _, kind = item.partition(":")
            faults[name] = kind
        for k, d in enumerate(drones):
            d.home_floor = 0.02
            sims[d.name] = SimCrazyflie(d, [k * 0.8, 0.0, 0.02], faults.get(d.name))
        threading.Thread(target=run_simulation_feed, args=(drones, sims, feed_stop, faults), daemon=True).start()
    else:
        from motive import DATA_PORT, MULTICAST_GROUP, MotiveListener

        def on_rigid_body(rb_id, pos, rot, markers=None):
            for d in drones:
                d.poses.on_rigid_body(rb_id, pos, rot)

        print(f"Listening for rigid bodies {args.drones} on {MULTICAST_GROUP}:{DATA_PORT} via {args.client}.")
        motive = MotiveListener(args.client, on_rigid_body)
        motive.start()

    video = None
    try:
        t_end = time.monotonic() + 5.0
        while time.monotonic() < t_end and not all(d.fresh(0.5) for d in drones):
            time.sleep(0.05)
        missing = [d.body_id for d in drones if not d.fresh(0.5)]
        if missing:
            seen = getattr(motive, "seen_ids", []) if motive else []
            raise SystemExit(f"Motive is not streaming rigid bodies {missing} (seen: {seen or 'none'}).\n"
                             "Check Motive: rigid bodies tracked, multicast 239.255.42.99 port 1511, --client IP.")
        if args.video:
            video = start_video(args.drones)
        lock_all_headings(args, drones)
        worst = ground_separation(drones) if len(drones) > 1 else None
        if worst and worst[0] < args.min_separation:
            raise SystemExit(f"{worst[1]} and {worst[2]} are {worst[0]:.2f} m apart. Place them at least "
                             f"{args.min_separation:.2f} m apart and start again.")
        connect(args, drones, sims)
        if not wait_tracking(drones):
            raise SystemExit("Not every estimate matched OptiTrack. Nothing took off. "
                             "Check the calibration wall, the rigid bodies, then rerun.")
        time.sleep(1.0)                                  # let battery logs arrive
        problems = preflight(args, drones)
        if problems:
            raise SystemExit("Pre-flight check failed, nothing took off:\n  " + "\n  ".join(problems))
        for d in drones:
            cf_pos, _age, yaw = d.pose()
            d.home = tuple(float(v) for v in cf_pos)
            d.yaw_deg = float(yaw)
        print()
        print("Plan: take off one by one (%.1f s apart), climb %.2f m, hover %.0f s, land on the same spots."
              % (args.stagger, args.height, args.hover_time))
        for d in drones:
            print(f"  {d.name}: spot x={d.home[0]:+.2f} y={d.home[1]:+.2f} z={d.home[2]:+.2f}, "
                  f"battery {'--' if d.vbat is None else '%.2f V' % d.vbat}")
        answer = ask(args, "Props on, area clear, everyone back? Type GO and Enter to fly (anything else cancels): ")
        if answer.strip().upper() != "GO" and not (args.sim and args.yes):
            print("Cancelled. Nothing took off.")
            return
        if any(not d.fresh() for d in drones):
            raise SystemExit("OptiTrack went stale before takeoff. Nothing took off.")
        fly(args, drones)
    finally:
        for d in drones:
            if d.armed:
                disarm(d)
            d.stop.set()
        for d in drones:
            if d.sender is not None:
                d.sender.join(timeout=1.0)
            if d.scf is not None:
                try:
                    d.scf.close_link()
                except Exception:
                    pass
        feed_stop.set()
        if motive is not None:
            motive.shutdown()
        if video is not None:
            print("The video window stays open; press q in it to close it.")


if __name__ == "__main__":
    main()
