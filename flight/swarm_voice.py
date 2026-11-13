#!/usr/bin/env python3
"""Fly the drones from voice/text commands. Run voice/voice_app.py in a second window.

Takeoff, hover and landing are swarm_hover.py's (same checks, same GO prompt). Once every drone
hovers, each one follows the target the voice window sends for it, slowly, inside a safety box.
The voice window can only ask for positions and for a landing; it can never arm or take off.

    python flight/swarm_voice.py --client 10.10.10.83 --drones 5 7 8
    python flight/swarm_voice.py --sim                      # rehearsal, no hardware

Drone 1, 2, 3 in the voice window = --slots (default 5 7 8). The chair is --chair-ahead metres in
front of the middle of the start line. Scene frame: origin on the floor under the chair, +x the
way the noses point at takeoff, +y left, +z up.

Keys: Enter = land all, k + Enter = motors off now, Ctrl+C = land all.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mocap as ext  # noqa: E402
import swarm_hover as sh  # noqa: E402

TARGET_PORT = 47800   # voice -> flight
STATUS_PORT = 47801   # flight -> voice
LINK_STALE_S = 0.6            # no target packet for this long -> hold position
MAX_SPEED_CAP = 0.5
MIN_Z = 0.30                  # lowest allowed target above the floor, m (ceiling: --ceiling)
CHAIR_HALF, CHAIR_PAD = 0.30, 0.20
YIELD_M = 0.45                # a setpoint never approaches another drone closer than this
NOT_FOLLOWING_M = 0.60        # drone this far from its setpoint for 1 s -> land all
YAW_RATE_DEG_S = 40.0


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--client", help="This computer's IP on the OptiTrack network")
    p.add_argument("--slots", type=int, nargs=3, default=[5, 7, 8], help="Body ids that are Drone 1, 2, 3")
    p.add_argument("--drones", type=int, nargs="+", default=None, help="Which of them fly (default: all slots)")
    p.add_argument("--radio", type=int, default=0)
    p.add_argument("--channel", type=int, default=80)
    p.add_argument("--datarate", default="2M")
    p.add_argument("--height", type=float, default=0.7, help="Hover height after takeoff, m (0.4-0.9)")
    p.add_argument("--chair-ahead", type=float, default=1.0, help="Chair centre this far in front of the start line, m")
    p.add_argument("--box", type=float, default=2.0, help="Half-size of the allowed square around the chair, m")
    p.add_argument("--ceiling", type=float, default=1.10, help="Highest allowed target above the floor, m (0.8-2.0)")
    p.add_argument("--speed", type=float, default=0.30, help="Max speed while following, m/s")
    p.add_argument("--max-time", type=float, default=180.0, help="Land automatically after this many seconds")
    p.add_argument("--stagger", type=float, default=1.5)
    p.add_argument("--min-separation", type=float, default=0.60)
    p.add_argument("--min-battery", type=float, default=3.80)
    p.add_argument("--follow-yaw", action="store_true", help="Also turn the drones as the scene asks (not flown yet)")
    p.add_argument("--video", action="store_true")
    p.add_argument("--sim", action="store_true")
    p.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--sim-fault", default="", help=argparse.SUPPRESS)
    a = p.parse_args()
    a.drones = a.drones or list(a.slots)
    if not set(a.drones) <= set(a.slots) or len(set(a.slots)) != 3:
        raise SystemExit("--drones must be a subset of the three distinct --slots.")
    if not 0.4 <= a.height <= 0.9:
        raise SystemExit("--height must be between 0.4 and 0.9 m.")
    if not 0.05 <= a.speed <= MAX_SPEED_CAP:
        raise SystemExit(f"--speed must be between 0.05 and {MAX_SPEED_CAP} m/s.")
    if not 0.8 <= a.ceiling <= 2.0:
        raise SystemExit("--ceiling must be between 0.8 and 2.0 m.")
    if a.height > a.ceiling:
        raise SystemExit("--height must not be above --ceiling.")
    if not 1.2 <= a.box <= 3.0:
        raise SystemExit("--box must be between 1.2 and 3.0 m.")
    if not 0.9 <= a.chair_ahead <= 2.0:
        raise SystemExit("--chair-ahead must be between 0.9 and 2.0 m.")
    if a.yes and not a.sim:
        raise SystemExit("--yes is only allowed with --sim.")
    if not a.sim and not a.client:
        raise SystemExit("--client <this computer's IP on the OptiTrack network> is required.")
    a.fence = a.box          # names swarm_hover's helpers expect
    a.hover_time = a.max_time
    a.sim_land_at = 0.0
    return a


class Frame:
    """Scene <-> Crazyflie world. Same axes, shifted so the chair is the origin."""

    def __init__(self, drones, chair_ahead):
        n = len(drones)
        self.ox = sum(d.home[0] for d in drones) / n + chair_ahead
        self.oy = sum(d.home[1] for d in drones) / n
        self.oz = sum(d.home[2] for d in drones) / n

    def to_scene(self, p):
        return [p[0] - self.ox, p[1] - self.oy, p[2] - self.oz]

    def to_real(self, p):
        return (p[0] + self.ox, p[1] + self.oy, p[2] + self.oz)


def in_chair(p, pad=CHAIR_PAD):
    """No-fly column over the chair: nothing flies above it either (downwash, little clearance)."""
    return abs(p[0]) < CHAIR_HALF + pad and abs(p[1]) < CHAIR_HALF + pad


def push_out_of_chair(p):
    """Move a target sideways to the edge of the chair zone (keeps its height)."""
    edge = CHAIR_HALF + CHAIR_PAD + 0.02
    x, y = p[0], p[1]
    if abs(x) >= abs(y):
        x = edge if x >= 0 else -edge
    else:
        y = edge if y >= 0 else -edge
    return [x, y, p[2]]


def clamp_target(args, p):
    lim = args.box - 0.15
    return [min(lim, max(-lim, p[0])), min(lim, max(-lim, p[1])), min(args.ceiling, max(MIN_Z, p[2]))]


class Link:
    def __init__(self):
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.bind(("127.0.0.1", TARGET_PORT))
        self.rx.setblocking(False)
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.epoch = int(time.time() * 1000) % 1000000000
        self.packet = None
        self.packet_t = 0.0
        self.land_base = None
        self.heard = False

    def poll(self, now):
        while True:
            try:
                data, _addr = self.rx.recvfrom(65535)
            except (BlockingIOError, OSError):
                break
            try:
                packet = json.loads(data.decode())
            except ValueError:
                continue
            if packet.get("epoch") != self.epoch:
                continue                      # voice window has not placed our drones yet
            if self.land_base is None:
                self.land_base = int(packet.get("land_serial", 0))
            self.packet, self.packet_t = packet, now

    def fresh(self, now):
        return self.packet is not None and now - self.packet_t <= LINK_STALE_S

    def land_request(self):
        """Return a new landing request (list) once, else None."""
        if self.packet is None:
            return None
        serial = int(self.packet.get("land_serial", 0))
        if serial > self.land_base:
            self.land_base = serial
            return list(self.packet.get("land") or ["all"])
        return None

    def send_status(self, status):
        try:
            self.tx.sendto(json.dumps(status).encode(), ("127.0.0.1", STATUS_PORT))
        except OSError:
            pass


def land_one(d, now, reason):
    print(f"\n*** {d.name}: landing ({reason})")
    x, y, z = d.setpoint
    cf_pos, age, _yaw = d.pose()
    if cf_pos is not None and age is not None and age <= ext.STALE_S \
            and abs(cf_pos[2] - z) < 0.4 and math.hypot(cf_pos[0] - x, cf_pos[1] - y) < 0.5:
        x, y, z = cf_pos
    d.land_from = (x, y, max(z, d.home[2]))
    d.phase, d.phase_t = "LAND", now


def safety(args, drones, frame, now):
    flying = [d for d in drones if d.phase in ("ARMED", "CLIMB", "HOVER")]
    for d in flying:
        f = d.flags
        if d.link_lost.is_set():
            return f"{d.name}: radio link lost"
        cf_pos, age, _yaw = d.pose()
        if cf_pos is None or age is None or age > sh.POSE_STALE_S:
            f.setdefault("stale", now)
            if now - f["stale"] >= sh.POSE_LOST_HOLD_S:
                return f"{d.name}: OptiTrack lost"
            continue
        f.pop("stale", None)
        s = frame.to_scene(cf_pos)
        if abs(s[0]) > args.box + 0.15 or abs(s[1]) > args.box + 0.15:
            f.setdefault("box", now)
            if now - f["box"] >= sh.FENCE_HOLD_S:
                return f"{d.name}: outside the box (x={s[0]:+.2f}, y={s[1]:+.2f})"
        else:
            f.pop("box", None)
        if s[2] > args.ceiling + 0.35:
            return f"{d.name}: too high ({s[2]:.2f} m)"
        if in_chair(s, pad=0.05):
            return f"{d.name}: inside the chair zone"
        if d.phase == "HOVER":
            off = math.dist(cf_pos, d.setpoint)
            if off > NOT_FOLLOWING_M:
                f.setdefault("follow", now)
                if now - f["follow"] >= 1.0:
                    return f"{d.name}: {off:.2f} m away from where it should be"
            else:
                f.pop("follow", None)
            if s[2] < 0.12 and d.setpoint[2] - frame.oz > 0.25:
                f.setdefault("low", now)
                if now - f["low"] >= 0.4:
                    return f"{d.name}: altitude collapsed"
            else:
                f.pop("low", None)
            err = ext.tracking_error(d.poses, d.estimates)
            if err is not None and err > sh.TRACK_ERR_M:
                f.setdefault("track", now)
                if now - f["track"] >= 0.5:
                    return f"{d.name}: onboard estimate is {err:.2f} m off OptiTrack"
            else:
                f.pop("track", None)
        if d.vbat is not None and d.vbat < sh.LOW_BATTERY_AIR_V:
            f.setdefault("bat", now)
            if now - f["bat"] >= 1.0:
                return f"{d.name}: battery {d.vbat:.2f} V"
        else:
            f.pop("bat", None)
    for i, a in enumerate(flying):
        for b in flying[i + 1:]:
            pa, pb = a.pose()[0], b.pose()[0]
            if pa is not None and pb is not None and math.dist(pa, pb) < sh.MIN_AIR_SEPARATION_M:
                return f"{a.name} and {b.name} only {math.dist(pa, pb):.2f} m apart"
    return None


def follow(args, d, others, frame, link, now, dt):
    """Move this drone's setpoint toward its voice target: capped speed, box, chair, other drones."""
    sp = list(d.setpoint)
    if link.fresh(now):
        raw = link.packet["targets"].get(str(d.slot))
        if raw is not None:
            goal_s = clamp_target(args, raw[:3])
            if in_chair(goal_s):
                goal_s = push_out_of_chair(goal_s)
                if not getattr(d, "chair_warned", False):
                    print(f"{d.name}: target was inside the chair zone; stopping at its edge instead.")
                    d.chair_warned = True
            else:
                d.chair_warned = False
            d.goal = frame.to_real(goal_s)
            d.goal_yaw = float(raw[3])
    goal = d.goal
    step = [goal[i] - sp[i] for i in range(3)]
    dist = math.sqrt(sum(v * v for v in step))
    reach = args.speed * dt
    if dist > reach and dist > 0:
        step = [v * reach / dist for v in step]
    new = [sp[i] + step[i] for i in range(3)]
    blocked = in_chair(frame.to_scene(new), pad=0.10)
    waiting_for = "the chair zone" if blocked else ""
    for o in others:
        if o.phase not in ("ARMED", "CLIMB", "HOVER"):
            continue
        for point in (o.setpoint, o.pose()[0]):
            if point is None:
                continue
            before, after = math.dist(sp, point), math.dist(new, point)
            if after < YIELD_M and after < before:
                blocked = True
                waiting_for = o.name
    if not blocked:
        sp = new
    was = getattr(d, "waiting_for", "")
    d.waiting_for = waiting_for if blocked and dist > 0.05 else ""
    if d.waiting_for and d.waiting_for != was:
        print(f"{d.name}: holding, {d.waiting_for} is in the way. Move that drone or give a new command.")
    d.setpoint = tuple(sp)
    yaw = d.yaw_deg
    if args.follow_yaw:
        want = d.yaw_home + math.degrees(d.goal_yaw)
        diff = ext.wrap_deg(want - d.yaw_cmd)
        d.yaw_cmd = ext.wrap_deg(d.yaw_cmd + max(-YAW_RATE_DEG_S * dt, min(YAW_RATE_DEG_S * dt, diff)))
        yaw = d.yaw_cmd
    d.cf.commander.send_position_setpoint(sp[0], sp[1], sp[2], yaw)


def fly(args, drones, frame, link):
    commands = []

    def keyboard():
        while True:
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if line == "":
                return
            commands.append("kill" if line.strip().lower() == "k" else "land")

    threading.Thread(target=keyboard, daemon=True).start()
    print("\nFLYING. Enter = land all.  k + Enter = motors off now.  Ctrl+C = land all.\n")
    t0 = time.monotonic()
    for i, d in enumerate(drones):
        d.phase, d.phase_t = "WAIT", t0 + i * args.stagger
        d.setpoint = d.home
    landing = False
    msg = "taking off"
    ready_since = None
    next_print = next_status = t0
    last = t0
    period = 1.0 / sh.TICK_HZ
    ctrl_c = 0

    def status(now):
        info = {}
        for d in drones:
            cf_pos = d.pose()[0]
            top = (d.home[0], d.home[1], d.home[2] + args.height)
            info[str(d.slot)] = {"home": frame.to_scene(top), "phase": d.phase,
                                 "pos": None if cf_pos is None else frame.to_scene(cf_pos)}
        ready = (not landing) and all(d.phase in ("HOVER", "DONE") for d in drones) \
            and any(d.phase == "HOVER" for d in drones)
        link.send_status({"epoch": link.epoch, "ready": ready, "msg": msg, "drones": info, "ceiling": args.ceiling})
        return ready

    while True:
        try:
            now = time.monotonic()
            dt, last = min(0.2, now - last), now
            link.poll(now)
            if commands:
                if commands.pop(0) == "kill":
                    sh.kill_all(drones)
                    return
                if not landing:
                    sh.begin_landing(drones, now, "Enter pressed")
                    landing, msg = True, "landing (Enter)"
            for d in drones:
                if d.phase == "WAIT" and now >= d.phase_t and not landing:
                    print(f"{d.name}: arming and taking off.")
                    sh.arm(d.cf)
                    d.armed = True
                    d.phase, d.phase_t = "ARMED", now
            if not landing:
                reason = safety(args, drones, frame, now)
                if reason is None and now - t0 > args.max_time:
                    reason = "maximum flight time reached"
                if reason is None:
                    for d in drones:
                        if d.phase in ("ARMED", "CLIMB"):
                            problem = sh.step_drone(args, d, now)
                            if problem:
                                reason = f"{d.name}: {problem}"
                                break
                            if d.phase == "HOVER":
                                d.goal, d.goal_yaw = d.setpoint, 0.0
                        elif d.phase == "HOVER":
                            others = [o for o in drones if o is not d]
                            if all(o.phase in ("HOVER", "DONE", "LAND") for o in drones):
                                follow(args, d, others, frame, link, now, dt)
                            else:                      # wait for the rest of the swarm to finish climbing
                                d.cf.commander.send_position_setpoint(*d.setpoint, d.yaw_deg)
                        elif d.phase == "LAND":
                            sh.step_drone(args, d, now)
                if reason is None:
                    request = link.land_request()
                    if request is not None:
                        if "all" in request:
                            reason = "voice command: land"
                        else:
                            for d in drones:
                                if d.slot in request and d.phase == "HOVER":
                                    land_one(d, now, "voice command")
                if reason:
                    sh.begin_landing(drones, now, reason)
                    landing, msg = True, "LANDING ALL: " + reason
            else:
                for d in drones:
                    if d.phase == "LAND":
                        sh.step_drone(args, d, now)
            if all(d.phase == "DONE" for d in drones):
                status(now)
                print("All drones landed and disarmed.")
                return
            if now >= next_status:
                ready = status(now)
                next_status = now + 0.2
                if ready and ready_since is None:
                    ready_since = now
                    msg = "hovering"
                    print("All drones hovering. " + ("Voice window connected: say or type a command."
                          if link.packet is not None else "Waiting for the voice window (voice/voice_app.py)."))
            if now >= next_print:
                tag = "voice OK" if link.fresh(now) else "voice: no signal (holding)"
                held = [f"{d.name} waits for {d.waiting_for}" for d in drones if getattr(d, "waiting_for", "") and d.phase == "HOVER"]
                if held:
                    tag += " | " + ", ".join(held)
                    msg = "hovering; " + ", ".join(held)
                elif msg.startswith("hovering;"):
                    msg = "hovering"
                print(sh.status_line(drones, now - t0) + "  | " + tag)
                next_print = now + 1.0
            time.sleep(period)
        except KeyboardInterrupt:
            ctrl_c += 1
            if ctrl_c >= 2:
                sh.kill_all(drones)
                return
            if not landing:
                sh.begin_landing(drones, time.monotonic(), "Ctrl+C")
                landing, msg = True, "landing (Ctrl+C)"


def main():
    args = parse_args()
    drones = []
    for body_id in args.drones:
        matrix, _yaw, loaded = ext.load_axis_calibration(body_id)
        if not loaded or ext.resolve_calibration_path(body_id).name != f"axis_calibration_{body_id}.json":
            raise SystemExit(f"No axis calibration for drone {body_id} (flight/axis_calibration_{body_id}.json).")
        d = sh.Drone(body_id, f"radio://{args.radio}/{args.channel}/{args.datarate}/E7E7E7E7E{body_id}", matrix)
        d.slot = args.slots.index(body_id) + 1
        drones.append(d)
    if any(d.matrix != drones[0].matrix for d in drones):
        raise SystemExit("These drones have different axis calibrations, so 'forward' is not the same for all. "
                         "Recalibrate them toward the same wall.")
    print("Drones: " + ", ".join(f"Drone {d.slot} = {d.name}" for d in drones))
    if args.sim:
        print("SIMULATION: no radio, no Motive, nothing flies.")
    link = Link()

    feed_stop = threading.Event()
    sims, motive = {}, None
    if args.sim:
        faults = dict(item.split(":") for item in filter(None, args.sim_fault.split(",")))
        for d in drones:
            d.home_floor = 0.02
            sims[d.name] = sh.SimCrazyflie(d, [0.0, (2 - d.slot) * 0.75, 0.02], faults.get(d.name))
        threading.Thread(target=sh.run_simulation_feed, args=(drones, sims, feed_stop, faults), daemon=True).start()
    else:
        from motive import DATA_PORT, MULTICAST_GROUP, MotiveListener

        def on_rigid_body(rb_id, pos, rot, markers=None):
            for d in drones:
                d.poses.on_rigid_body(rb_id, pos, rot)

        print(f"Listening for rigid bodies {args.drones} on {MULTICAST_GROUP}:{DATA_PORT} via {args.client}.")
        motive = MotiveListener(args.client, on_rigid_body)
        motive.start()

    try:
        t_end = time.monotonic() + 5.0
        while time.monotonic() < t_end and not all(d.fresh(0.5) for d in drones):
            time.sleep(0.05)
        missing = [d.body_id for d in drones if not d.fresh(0.5)]
        if missing:
            raise SystemExit(f"Motive is not streaming rigid bodies {missing}.")
        if args.video:
            sh.start_video(args.drones)
        sh.lock_all_headings(args, drones)
        worst = sh.ground_separation(drones) if len(drones) > 1 else None
        if worst and worst[0] < args.min_separation:
            raise SystemExit(f"{worst[1]} and {worst[2]} are {worst[0]:.2f} m apart; need {args.min_separation:.2f} m.")
        sh.connect(args, drones, sims)
        if not sh.wait_tracking(drones):
            raise SystemExit("Not every estimate matched OptiTrack. Nothing took off.")
        time.sleep(1.0)
        problems = sh.preflight(args, drones)
        if problems:
            raise SystemExit("Pre-flight check failed, nothing took off:\n  " + "\n  ".join(problems))
        for d in drones:
            cf_pos, _age, yaw = d.pose()
            d.home = tuple(float(v) for v in cf_pos)
            d.yaw_deg = d.yaw_home = d.yaw_cmd = float(yaw)
            d.goal, d.goal_yaw = d.home, 0.0
        frame = Frame(drones, args.chair_ahead)
        print()
        print("Plan: take off one by one, hover at %.2f m, then follow the voice window at max %.2f m/s."
              % (args.height, args.speed))
        print("  Chair: %.2f m in front of the middle of the start line. Allowed area: %.1f m around the chair, "
              "height %.2f-%.2f m." % (args.chair_ahead, args.box, MIN_Z, args.ceiling))
        for d in drones:
            s = frame.to_scene(d.home)
            print(f"  Drone {d.slot} = {d.name}: {-s[0]:.2f} m behind the chair, {s[1]:+.2f} m to its left, "
                  f"battery {'--' if d.vbat is None else '%.2f V' % d.vbat}")
            if in_chair([s[0], s[1], args.height], pad=0.35) or abs(s[1]) > args.box - 0.3:
                raise SystemExit(f"{d.name} would start too close to the chair or outside the box.")
        answer = sh.ask(args, "Chair in place, props on, area clear? Type GO and Enter to fly: ")
        if answer.strip().upper() != "GO" and not (args.sim and args.yes):
            print("Cancelled. Nothing took off.")
            return
        if any(not d.fresh() for d in drones):
            raise SystemExit("OptiTrack went stale before takeoff. Nothing took off.")
        fly(args, drones, frame, link)
    finally:
        for d in drones:
            if d.armed:
                sh.disarm(d)
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


if __name__ == "__main__":
    main()
