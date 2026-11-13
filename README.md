# Crazyflie swarm: AI-deck video network, mapping and change detection

Five Crazyflie 2.x drones with AI decks fly in an OptiTrack volume. Their camera frames go over WiFi to a
GPU laptop; combined with the mocap poses they become a 3D map, and two runs are compared to highlight what
changed. Full spec: [docs/spec.md](docs/spec.md).

**Status (2026-11-10):** the video network works. A Raspberry Pi 5 runs the WiFi network `SWARM-NET`, the AI
decks of drones 5, 7 and 8 join it with fixed addresses, and the laptop shows all three live (about 8 fps each).
Mapping, calibration and swarm flight are not started yet.

| Start here | |
|---|---|
| [HANDOVER.md](HANDOVER.md) | What was built, how to use it, adding a drone, what to expect, undoing it |
| [TODO.md](TODO.md) | Open issues and the roadmap to the demo |
| [docs/speedrun.md](docs/speedrun.md) | Rebuild everything from bare hardware, every command |
| [docs/pi-access.md](docs/pi-access.md) | Logging in to the Raspberry Pi |
| [flight/README.md](flight/README.md) | Hover 3 drones at once with live video (Linux), safety, first-flight order |

## Quick start (laptop on SWARM-NET, drones on for 20 s)

```powershell
python -m pip install --user cfclient opencv-python numpy pyyaml   # once
python tools\live_view.py                                          # live video, one tile per deck
python tools\stream_test.py --seconds 60                           # fps / drop numbers
python -m unittest tests.test_swarm                                # 7 tests, simulation only
```

## Layout
- `flight/` multi-drone hover with OptiTrack (built on the course code), Linux setup script
- `tools/` live viewer, stream test
- `swarm/` config loader, AI-deck stream reader, mocap pose hub, simulator
- `config/` drones (radio address, deck IP, camera rotation), Pi fixed addresses
- `firmware/` AI-deck camera program changes, official ESP32 firmware, lab-mode image
- `docs/` speedrun, journal, Pi setup, station mode, spec, phased task plan, test evidence
- `tests/` unit tests

Passwords for the Pi and SWARM-NET: [CREDENTIALS.md](CREDENTIALS.md). Keep this repository private.
