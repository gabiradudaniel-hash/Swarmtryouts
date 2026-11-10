#!/usr/bin/env python3
"""Live video window for one or more AI decks (no files saved unless you ask).

    python tools/live_view.py                          # every deck with a deck_ip in config/drones.yaml
    python tools/live_view.py --deck cf7=192.168.50.17 # one deck by IP
    python tools/live_view.py --save recordings/run1   # also save every frame (DIR/<deck>/<ms>.jpg)
    python tools/live_view.py --rotate cf8=180         # turn one deck's picture (overrides camera_rotation)

Each deck's picture is turned by camera_rotation from drones.yaml (0/90/180/270); saved frames
stay exactly as the deck sent them.

Mouse: left-click a tile = flip it upside down (180), right-click = turn it 90 degrees.
Keys in the window: q or Esc = quit, s = save a snapshot of the window to snapshots/,
+ / - = bigger / smaller. On exit it prints the rotations to put in drones.yaml.

Every run also writes logs/live_view_<date>_<time>.txt: per deck, when it connected, got its
first frame, stalled (> 1.5 s without frames) and recovered, dropped (with the error), plus the summary.

Each deck gets a tile with its name, frame rate and reconnect count. A tile turns red
with "NO VIDEO" when that deck has sent nothing for more than 1.5 s; the reader keeps
reconnecting on its own in the background.

Needs: pip install opencv-python numpy pyyaml
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import cv2
    import numpy as np
except ImportError:
    raise SystemExit("OpenCV is missing. Install it with:\n    python -m pip install opencv-python numpy")

from swarm.stream import DeckReader  # noqa: E402

TILE_W, TILE_H = 324, 244  # AI-deck camera resolution
STALE_S = 1.5
ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
WINDOW = "AI-deck live view"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=Path("config/drones.yaml"))
    p.add_argument("--deck", action="append", default=[], metavar="NAME=IP[:PORT]",
                   help="Show this deck instead of the config list; repeat for more decks.")
    p.add_argument("--save", type=Path, default=None, metavar="DIR",
                   help="Also save every received frame under DIR/<deck name>/.")
    p.add_argument("--rotate", action="append", default=[], metavar="NAME=DEG",
                   help="Turn a deck's picture clockwise by 0/90/180/270 degrees; repeat per deck.")
    p.add_argument("--scale", type=float, default=2.0, help="Tile zoom factor (default 2).")
    p.add_argument("--seconds", type=float, default=0.0, help="Close after this many seconds (0 = run until q).")
    return p.parse_args()


def decks_from_args(args):
    if args.deck:
        out = []
        for item in args.deck:
            name, _, addr = item.partition("=")
            if not addr:
                raise SystemExit(f"--deck needs NAME=IP, got {item!r}")
            ip, _, port = addr.partition(":")
            out.append((name, ip, int(port or 5000), 0))
        return apply_rotations(out, args.rotate)
    from swarm.config import load_config  # needs pyyaml, only when reading the config

    cfg = load_config(args.config)
    return apply_rotations([(d.name, d.deck_ip, d.deck_port, d.camera_rotation) for d in cfg.drones if d.deck_ip],
                           args.rotate)


def apply_rotations(decks, items):
    rot = {}
    for item in items:
        name, _, deg = item.partition("=")
        if not deg or int(deg) % 360 not in ROTATIONS:
            raise SystemExit(f"--rotate needs NAME=0|90|180|270, got {item!r}")
        rot[name] = int(deg) % 360
    return [(n, ip, port, rot.get(n, r)) for n, ip, port, r in decks]


class LatestFrames:
    """Keeps only the newest frame per deck; the reader threads write, the window reads."""

    def __init__(self, save_dir: Path | None):
        self._lock = threading.Lock()
        self._frames: dict[str, tuple[int, int, int, bytes, int]] = {}
        self._save_dir = save_dir
        self._last_ms: dict[str, int] = {}
        self._times: dict[str, deque] = {}

    def on_frame(self, name, _t, width, height, fmt, payload):
        with self._lock:
            seq = self._frames.get(name, (0, 0, 0, b"", 0))[4] + 1
            self._frames[name] = (width, height, fmt, payload, seq)
            self._times.setdefault(name, deque(maxlen=200)).append(time.monotonic())
        if self._save_dir is not None:
            ms = max(int(time.time() * 1000), self._last_ms.get(name, 0) + 1)
            self._last_ms[name] = ms
            folder = self._save_dir / name
            folder.mkdir(parents=True, exist_ok=True)
            ext = "raw" if fmt == 0 else "jpg"
            (folder / f"{ms}.{ext}").write_bytes(payload)

    def recent_fps(self, name, window_s=2.0):
        """Frames per second over the last window_s seconds (drops to 0 when the deck stalls)."""
        now = time.monotonic()
        with self._lock:
            times = self._times.get(name, ())
            return sum(1 for x in times if now - x <= window_s) / window_s

    def get(self, name):
        with self._lock:
            return self._frames.get(name)


class EventLog:
    """Watches each reader's state from the window loop and writes changes to a text file."""

    def __init__(self, decks, readers):
        Path("logs").mkdir(exist_ok=True)
        self.path = Path("logs") / time.strftime("live_view_%Y%m%d_%H%M%S.txt")
        self._f = self.path.open("w", encoding="utf-8")
        self._decks, self._readers = decks, readers
        self._state = {n: {"connected": False, "frames": 0, "reconnects": 0, "stalled": False, "stall_t": 0.0}
                       for n, *_ in decks}
        self.write("start " + ", ".join(f"{n}={ip}" for n, ip, *_ in decks))

    def write(self, text):
        stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
        self._f.write(f"{stamp} {text}\n")
        self._f.flush()

    def poll(self):
        now = time.monotonic()
        for (name, *_), reader in zip(self._decks, self._readers):
            st, s = self._state[name], reader.snapshot()
            conn = getattr(reader, "connected", False)
            if s.reconnects > st["reconnects"]:
                self.write(f"{name} DROP #{s.reconnects}: {s.last_error}")
                st["reconnects"] = s.reconnects
            if conn and not st["connected"]:
                self.write(f"{name} connected")
            st["connected"] = conn
            if s.frames and not st["frames"]:
                self.write(f"{name} first frame {s.size[0]}x{s.size[1]}")
            stalled = bool(s.frames) and now - s.last_frame_t > STALE_S
            if stalled and not st["stalled"]:
                st["stall_t"] = s.last_frame_t
                self.write(f"{name} STALL start (last frame {now - s.last_frame_t:.1f}s ago, frames so far {s.frames})")
            if not stalled and st["stalled"]:
                self.write(f"{name} stall end after {s.last_frame_t - st['stall_t']:.1f}s")
            st["stalled"] = stalled
            st["frames"] = s.frames

    def close(self, lines):
        for line in lines:
            self.write(line)
        self._f.close()


def decode(width, height, fmt, payload):
    if fmt == 0:  # raw 8-bit grayscale
        if len(payload) < width * height:
            return None
        gray = np.frombuffer(payload, dtype=np.uint8, count=width * height).reshape(height, width)
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)


def label(img, text, org, color, scale):
    """Text on a dark box so it stays readable on any image."""
    font, fs, th = cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, max(1, int(round(scale)))
    (tw, tht), base = cv2.getTextSize(text, font, fs, th)
    x, y = org
    cv2.rectangle(img, (x - 3, y - tht - 4), (x + tw + 3, y + base + 2), (0, 0, 0), -1)
    cv2.putText(img, text, (x, y), font, fs, color, th, cv2.LINE_AA)


def render_tile(name, ip, rotation, reader, frames, cache, scale):
    w, h = int(TILE_W * scale), int(TILE_H * scale)
    s = reader.snapshot()
    item = frames.get(name)
    if item is not None and cache.get(name, (None, -1, None))[1:] != (item[4], rotation):
        img = decode(*item[:4])
        if img is not None and ROTATIONS.get(rotation) is not None:
            img = cv2.rotate(img, ROTATIONS[rotation])
        if img is not None:
            cache[name] = (cv2.resize(img, (w, h), interpolation=cv2.INTER_NEAREST), item[4], rotation)
    tile = cache[name][0].copy() if name in cache else np.zeros((h, w, 3), np.uint8)
    if tile.shape[:2] != (h, w):
        tile = cv2.resize(tile, (w, h), interpolation=cv2.INTER_NEAREST)

    age = time.monotonic() - s.last_frame_t if s.frames else None
    text_scale = max(0.8, scale / 2)
    label(tile, f"{name}  {ip}" + (f"  rot {rotation}" if rotation else ""), (8, int(22 * text_scale)), (255, 255, 255), text_scale)
    label(tile, f"{frames.recent_fps(name):4.1f} fps  frames {s.frames}  reconnects {s.reconnects}",
          (8, h - int(10 * text_scale)), (255, 255, 255), text_scale)
    if age is None or age > STALE_S:
        if age is not None:
            msg = f"NO VIDEO {age:4.1f}s"
        elif getattr(reader, "connected", False):
            msg = "CONNECTED, NO VIDEO YET"  # deck is on the network but its camera sends nothing
        else:
            msg = "CONNECTING..."
        cv2.rectangle(tile, (0, 0), (w - 1, h - 1), (0, 0, 255), max(2, int(3 * scale)))
        label(tile, msg, (8, h // 2), (80, 80, 255), text_scale * 1.3)
    return tile


def grid(tiles):
    n = len(tiles)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    h, w = tiles[0].shape[:2]
    blank = np.zeros_like(tiles[0])
    tiles = tiles + [blank] * (rows * cols - n)
    return np.vstack([np.hstack(tiles[r * cols:(r + 1) * cols]) for r in range(rows)]), cols


def main():
    args = parse_args()
    decks = decks_from_args(args)
    if not decks:
        raise SystemExit("No decks with an IP. Set deck_ip in config/drones.yaml or pass --deck NAME=IP.")

    frames = LatestFrames(args.save)
    readers = [DeckReader(n, ip, port, on_frame=frames.on_frame) for n, ip, port, _ in decks]
    for r in readers:
        r.start()
    print("Showing " + ", ".join(f"{n} ({ip})" for n, ip, _, _ in decks) + ". Press q in the window to quit.", flush=True)
    if args.save:
        print(f"Saving frames under {args.save}", flush=True)

    events = EventLog(decks, readers)
    print(f"Event log: {events.path}", flush=True)
    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    cache: dict = {}
    scale = args.scale
    rotations = {n: rot for n, _, _, rot in decks}
    layout = {"cols": 1, "w": 1, "h": 1}

    def on_mouse(event, x, y, _flags, _param):
        step = {cv2.EVENT_LBUTTONDOWN: 180, cv2.EVENT_RBUTTONDOWN: 90}.get(event)
        if step is None:
            return
        idx = (y // layout["h"]) * layout["cols"] + (x // layout["w"])
        if 0 <= idx < len(decks):
            name = decks[idx][0]
            rotations[name] = (rotations[name] + step) % 360
            print(f"{name}: rotation {rotations[name]}", flush=True)
            events.write(f"{name} rotation set to {rotations[name]} by click")

    cv2.setMouseCallback(WINDOW, on_mouse)
    t_end = time.monotonic() + args.seconds if args.seconds > 0 else None
    try:
        while t_end is None or time.monotonic() < t_end:
            tiles = [render_tile(n, ip, rotations[n], r, frames, cache, scale) for (n, ip, _, _), r in zip(decks, readers)]
            events.poll()
            view, layout["cols"] = grid(tiles)
            layout["h"], layout["w"] = tiles[0].shape[:2]
            cv2.imshow(WINDOW, view)
            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("s"):
                Path("snapshots").mkdir(exist_ok=True)
                path = Path("snapshots") / f"{int(time.time() * 1000)}.png"
                cv2.imwrite(str(path), view)
                print(f"Snapshot saved: {path}", flush=True)
            if key in (ord("+"), ord("=")):
                scale, cache = min(scale + 0.5, 4.0), {}
            if key == ord("-"):
                scale, cache = max(scale - 0.5, 1.0), {}
            try:
                if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    break  # window closed with the X button
            except cv2.error:
                break
    except KeyboardInterrupt:
        pass
    finally:
        for r in readers:
            r.stop()
        cv2.destroyAllWindows()

    changed = {n: rotations[n] for n, _, _, rot in decks if rotations[n] != rot}
    if changed:
        print("\nRotations changed in this session. To keep them, set in config/drones.yaml:")
        for n, deg in changed.items():
            print(f"  {n}: camera_rotation: {deg}")
    lines = ["summary"]
    for r in readers:
        s = r.snapshot()
        lines.append(f"  {s.name}: {s.fps:.2f} fps, {s.frames} frames, max gap {s.max_gap_s:.2f}s, reconnects {s.reconnects}"
                     + (f" (last error: {s.last_error})" if s.last_error else ""))
    print("\n" + "\n".join(lines))
    events.close(lines + [f"rotations {rotations}"])
    print(f"Event log saved: {events.path}")

if __name__ == "__main__":
    main()
