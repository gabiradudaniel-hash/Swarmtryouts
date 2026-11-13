# Voice control for the swarm

Say or type a command; the drones move. Two programs run side by side on the same computer:

| Window | Program | Job |
|---|---|---|
| Flight | `flight/swarm_voice.py` | Takeoff, hover, landing, ALL safety. Follows the targets it is sent, at most 0.30 m/s. |
| Voice | `voice/voice_app.py` | Microphone -> Whisper -> text -> Qwen3 (Ollama) or phrase parser -> planned positions, shown on screen and sent to the flight window. |

The voice window can only ask for positions and for a landing. It cannot arm or take off.
Based on the professor's simulation "responder local" (`README_original.md`); what was changed is listed at the end.

**Status: tested in simulation only (both programs together, with faults). Not flown yet.**

## Layout in the room
- Drones on the floor in a line, noses toward the calibration wall, 0.75 m apart:
  **Drone 1 = cf5 (left), Drone 2 = cf7 (middle), Drone 3 = cf8 (right)**.
- The chair stands **1 m in front of the middle drone** (fits in 0.6 x 0.6 x 0.8 m).
- "forward" = the way the noses point at takeoff, "left" = the drones' left.
- Allowed area: 2 m around the chair, height 0.30 m up to the ceiling (default 1.10 m, `--ceiling 1.5` on the
  flight program raises it; the voice window follows automatically). Nothing flies over the chair.

## One-time setup (with internet)
```powershell
powershell -ExecutionPolicy Bypass -File voice\get_models.ps1
```
Installs faster-whisper, sounddevice, matplotlib and downloads Whisper base.en (145 MB) into `voice\models`.
Ollama must have `qwen3:8b` (`ollama list`). Without Ollama the phrase parser is used; without Whisper use `--no-voice` and type.

## Try it without drones
```powershell
python voice\voice_app.py --sim                    # voice window alone
python flight\swarm_voice.py --sim                 # window 1: simulated flight (press Enter, type GO)
python voice\voice_app.py                          # window 2: commands move the simulated drones
```

## Fly
Window 1 (cable on OptiTrack):
```powershell
python flight\swarm_voice.py --client <your-ip> --drones 7
```
Enter for the headings, check the plan, type `GO`. Wait for "All drones hovering".
Window 2:
```powershell
python voice\voice_app.py            # add --mic N to pick a microphone, --no-voice to type only
```
Start with one drone (`--drones 7`), then `--drones 5 7 8`. `--video` opens the deck video as before.

## Commands (first version)
| Say / type | Effect |
|---|---|
| `drone 2 move left` / `move 30 cm forward` / `higher` / `lower` | step of 20 cm, or the distance you say |
| `drone 1 go to the left of the chair` (right / front / behind) | parks 0.95 m from the chair on that side, going around it |
| `closer` / `back off a little` | 20 cm toward / away from the chair |
| `drone 3` | selects that drone for the next commands |
| `return to start` | back to the takeoff spots |
| `stop` | freezes where it is (instant, skips the model) |
| `land` / `drone 2 land` | lands all / that drone where it is |

Not enabled yet: circling, swapping, "over the chair", "inspect", and turning (the drones keep their noses forward;
`--follow-yaw` on the flight program turns them, untested in flight).

## Safety (flight window)
- Keys: **Enter** = land all, **k** + Enter = motors off, **Ctrl+C** = land all.
- Lands ALL drones when any drone: loses OptiTrack or radio, leaves the box, enters the chair zone, is more than 0.6 m
  from where it should be for 1 s, comes within 0.30 m of another, has low battery, or after `--max-time` (180 s).
- A drone **holds** (message "holding, cfX is in the way") instead of approaching another drone closer than 0.45 m.
- If the voice window closes or freezes, the drones hold position ("voice: no signal"); land with Enter.

## Changes to the professor's code (all marked `SWARM` in the files)
- `voice.py`: Whisper through faster-whisper instead of MLX; prompt mentions the chair and "land".
- `planner.py`: Ollama model `qwen3:8b` with thinking off, kept loaded; falls back to the phrase parser on any model error.
- `scene.py`: object = chair 0.6 x 0.6 x 0.8 m, full-height no-fly column, routes go around it; drones 0.6 m apart
  (was 0.30); height 0.30-1.10 m; speed 0.25 m/s (was 0.45); start line 1 m behind the chair.
- `commands.py`: "chair" is understood as the object.
- New: `link_scene.py` (UDP link, land/take-off words, disabled commands), `voice_app.py` (window for Windows/Linux).
