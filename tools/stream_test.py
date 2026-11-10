#!/usr/bin/env python3
"""Measure FPS, gaps and drops from several AI decks at once.

    python tools/stream_test.py --config config/drones.yaml --seconds 30
    python tools/stream_test.py --deck cf2=192.168.50.12 --deck cf4=192.168.50.14

Run it with 1 deck, then 2, then 5. Pass if every deck holds at least
--min-fps with no reconnects.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swarm.config import load_config  # noqa: E402
from swarm.stream import DeckReader  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=Path("config/drones.yaml"))
    p.add_argument("--deck", action="append", default=[], metavar="NAME=IP[:PORT]",
                   help="Override config; repeat for each deck.")
    p.add_argument("--seconds", type=float, default=30.0)
    p.add_argument("--min-fps", type=float, default=1.0)
    return p.parse_args()


def decks_from_args(args):
    if args.deck:
        out = []
        for item in args.deck:
            name, _, addr = item.partition("=")
            ip, _, port = addr.partition(":")
            out.append((name, ip, int(port or 5000)))
        return out
    cfg = load_config(args.config)
    return [(d.name, d.deck_ip, d.deck_port) for d in cfg.drones if d.deck_ip]


def main():
    args = parse_args()
    decks = decks_from_args(args)
    if not decks:
        raise SystemExit("No decks with an IP. Set deck_ip in the config or pass --deck.")
    readers = [DeckReader(n, ip, port) for n, ip, port in decks]
    for r in readers:
        r.start()
    t_end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < t_end:
            time.sleep(2.0)
            print(" | ".join(f"{s.name} {s.fps:4.1f}fps n={s.frames} rc={s.reconnects}"
                             for s in (r.snapshot() for r in readers)), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        for r in readers:
            r.stop()
    print("\nsummary")
    ok = True
    for r in readers:
        s = r.snapshot()
        good = s.fps >= args.min_fps and s.reconnects == 0
        ok &= good
        print(f"  {s.name}: {s.fps:.2f} fps, {s.frames} frames, max gap {s.max_gap_s:.2f}s, "
              f"{s.bytes / max(1, s.frames) / 1024:.1f} KiB/frame, size {s.size}, "
              f"reconnects {s.reconnects} {'OK' if good else 'FAIL ' + s.last_error}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
