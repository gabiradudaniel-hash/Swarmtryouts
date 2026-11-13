#!/usr/bin/env python3
"""Voice / text command window for the three drones (Windows, Linux or Mac).

    python voice/voice_app.py                 # linked to flight/swarm_voice.py, microphone on
    python voice/voice_app.py --no-voice      # typed commands only
    python voice/voice_app.py --sim           # no flight program: just the simulation window
    python voice/voice_app.py --mic 2 --stt-model small.en

Speech -> text: Whisper (faster-whisper, on this computer). Text -> action: Qwen3 in Ollama,
or the built-in phrase parser when Ollama is not running. Based on the professor's
"responder local"; README_original.md is his description.
"""

from __future__ import annotations

import argparse
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from link_scene import LinkedScene  # noqa: E402
from planner import DEFAULT_OLLAMA_MODEL, Planner  # noqa: E402

HELP = (
    "Say or type: 'drone 2 move left' · 'move 30 cm forward' · 'higher' · 'drone 1 go to the left of the chair' · "
    "'return to start' · 'stop' · 'land'"
)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sim", action="store_true", help="Simulation only: do not talk to the flight program.")
    p.add_argument("--no-voice", action="store_true", help="Do not listen on the microphone.")
    p.add_argument("--backend", choices=("auto", "ollama", "rules"), default="auto",
                   help="auto = Qwen3 in Ollama if it is running, otherwise the phrase parser.")
    p.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    p.add_argument("--stt-model", default="base.en", help="Whisper size: tiny.en, base.en, small.en.")
    p.add_argument("--mic", type=int, default=None, help="Microphone number (python -m sounddevice lists them).")
    p.add_argument("--all-commands", action="store_true",
                   help="Also allow circling, swapping and 'over the chair' (simulation only).")
    a = p.parse_args(argv)
    if a.all_commands and not a.sim:
        raise SystemExit("--all-commands is only allowed with --sim.")
    return a


def main(argv=None):
    args = parse_args(argv)
    import matplotlib

    try:
        matplotlib.use("TkAgg", force=True)
        from matplotlib import pyplot as plt

        plt.close(plt.figure())
    except Exception as exc:
        raise SystemExit(f"Could not open a window ({exc}). Python needs Tk (the python.org installer includes it).")
    from view import ResponderView

    backend = "ollama" if args.backend == "ollama" else args.backend
    planner = Planner(backend="ollama" if backend == "auto" else backend, ollama_model=args.ollama_model)
    scene = LinkedScene(planner=planner, link=not args.sim, allow_all=args.all_commands, climb_first=args.sim)
    print(HELP, flush=True)
    print("Forward = the way the drones' noses point at takeoff, left = their left. The chair is the object.", flush=True)
    lines = queue.Queue()
    voice = None
    if not args.no_voice:
        from voice import VoiceListener

        voice = VoiceListener(on_text=lines.put, model=args.stt_model, device=args.mic)
    ResponderView(scene, voice=voice, lines=lines).run()


if __name__ == "__main__":
    main()
