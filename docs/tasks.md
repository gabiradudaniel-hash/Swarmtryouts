# Task list: from bare hardware to the change-detection demo

Tags: [you] = hands-on or decisions on your side, [claude] = I write/verify code and configs, [both] = we iterate.
Rule: do not start a phase before the previous gate is met.

## Phase 0: Gather (this week)
- [ ] [you] Raspberry Pi for the access point: model, microSD (16 GB+), power supply, Ethernet cable.
- [ ] [you] USB-to-Ethernet adapter for the laptop (if the laptop's Ethernet port is already taken by the OptiTrack network). The laptop needs to reach Motive and the Pi at the same time.
- [ ] [you] 2 Crazyradio PA dongles, and confirm each drone's firmware version and AI deck (1.1) are noted.
- [ ] [you] Fixed-position checkerboard print (for camera calibration, Phase 5), A4/A3 on a flat board.
- [ ] [you] Decide the 5 body IDs, and write them in config/drones.yaml (currently placeholders 2,4,5,6,7).
Gate: all hardware on the bench.

## Phase 1: Laptop environment
- [ ] [you] Install Python 3.12 (keep your 3.14 as is). Create `.venv` in the SWARM folder.
- [ ] [you] `pip install -r requirements.txt` then `python -m unittest tests.test_swarm` -> 7 tests OK.
- [ ] [you] Zadig driver for both Crazyradios: choose libusb-win32, NOT WinUSB (cflib 0.1.34 on Windows uses the libusb0 backend; with WinUSB it reports "Cannot find a Crazyradio Dongle") (see legacy README-Windows.md). Close cfclient before scripts.
- [ ] [you] Update NVIDIA driver. Install PyTorch with CUDA 12.8 or newer (pytorch.org selector) and `pip install ultralytics opencv-python numpy`.
- [ ] [you] Run `python -c "import torch; print(torch.cuda.is_available())"` -> True. Then legacy webcam_depth.py runs on GPU.
Gate: tests pass, CUDA True, both radios visible to cflib.

## Phase 2: Raspberry Pi as the deck network
- [x] [you] Flash Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager. Set hostname, user, SSH on.
- [x] [you] Follow docs/pi-ap-setup.md (draft for a Pi 5 with NetworkManager, untested): AP on channel 6 with WPA2, Ethernet to the laptop, a Windows route. Report any failing check to me.
- [ ] [you] Reboot, connect a phone to SWARM-NET to prove it works.
- [ ] [you] Laptop over Ethernet to the Pi: ping the Pi, run `iperf3` between laptop and a phone/another device through the AP to see real throughput.
- [ ] [you] Keep the Pi away from the Crazyradios (USB 3 ports and the Pi's own radio can add 2.4 GHz noise). Radios stay on the laptop.
Gate: AP stable for 30 min, laptop reaches it by Ethernet.

## Phase 3: One-drone baseline in the new project
- [ ] [you] Run axis calibration for any of your 5 drones that lacks `axis_calibration_<id>.json` (legacy calibrate_axes.py).
- [ ] [you] Re-fly the legacy square (step1_position.py) with one drone to prove radio + Motive still work after the Python/venv change.
- [ ] [you] Note per-drone: firmware version, deck present, battery state, which radio (0/1).
Gate: one drone flies the legacy square.

## Phase 4: AI deck in station mode (the risky step, do one deck first)
- [x] (corrected) Decks run the old wifi-img-streamer, not NanoCockpit; see docs/deck-station-mode.md. Original note: Its ESP32 firmware (aideck-esp-firmware-v2) defaults to an OPEN access point named `aideck-nanocockpit`, so all 5 decks would currently create the same network. Read `docs/wifi-configuration.md` in that repo (Bitcraze GitHub): it describes the WiFi modes. I could not read it from here.
- [x] [you] Test first: join one deck's own network and run the legacy `capture.py`. If frames arrive, the protocol matches what swarm/stream.py expects. If not, send me the error and what the Bitcraze viewer uses.
- [x] [you] Configure ONE deck to join the Pi AP. (deck 7, 2026-11-10) Find its IP (cfclient console) and MAC.
- [x] [you] `python legacy_scripts/.../aideck_capture/capture.py --ip <deck ip>` shows frames. (deck 7, 2026-11-10)
- [x] [claude] Adapt swarm/stream.py if the protocol differs. (not needed: same protocol)
- [ ] [you] Repeat for the other 4 decks. Write each MAC down. (3 of 5 done: 5, 7, 8; drone 4 blocked, see journal)
- [x] [claude] Add the fixed-lease entries (MAC -> 192.168.50.1x) to the Pi config. [you] apply. (5, 7, 8 installed)
- [x] [you] Put deck IPs in config/drones.yaml. (5, 7, 8)
- [ ] [you] `python tools/stream_test.py --seconds 60` with 1 deck, then 2, then 5. Save the printed summary. (live_view 3-deck run 19:44: 7 and 8 pass, 5 had one 10.5 s drop)
Gate: all 5 decks >= 1 FPS, no reconnects, for 60 s. If not: reduce JPEG quality/resolution, stagger captures, try another channel; tell me the numbers.

## Phase 5: Camera calibration
- [ ] [you] For each deck, capture ~30 checkerboard frames at varied angles.
- [ ] [claude] Write the OpenCV calibration script and a YAML per drone (intrinsics + distortion).
- [ ] [both] Camera-to-body transform: measure the mount, then validate with a target at a known mocap position.
Gate: reprojection error < 1 px, validation target lands within a few cm.

## Phase 6: Test recording (unblocks the depth work)
- [ ] [you] Hold or hover one drone at 5-10 known spots. At each, save a deck frame and the mocap position/orientation (a small logger script, I will write it).
- [ ] [you] Move one object between two recordings of the same spots (gives us change-detection test data).
Gate: dataset in `data/recording_01/`.

## Phase 7: Depth and mapping (offline, on the recording)
- [ ] [claude] Run yolo26n-depth on the deck frames. Check metric scale against mocap, fit a per-frame scale if needed.
- [ ] [claude] Back-project with real intrinsics and the camera-to-body transform into the mocap frame.
- [ ] [claude] Voxel map merge from multiple frames/drones. Viewer (Open3D or Rerun).
- [ ] [you] Look at the map in the viewer: does it resemble the room?
Gate: recognizable map of the room from the recording. If depth on grayscale is too poor, switch to the detections route.

## Phase 8: Swarm flight manager
- [ ] [claude] One process: Motive via PoseHub, 5 Crazyflie links (cflib), per-drone controllers from the legacy step functions, shared start/stop, kill switch, battery and pose-loss auto-land, separation monitor. Simulated first.
- [ ] [you] Fly 1 drone through the manager, then 2, then 5, hovering only at first.
- [ ] [you] Measure pose rate and link loss per radio (3 drones on one radio is the risk).
Gate: 5 drones hover together for 2 min, separation monitor quiet.

## Phase 9: Scripted scan, run 1 (baseline)
- [ ] [claude] Lane trajectories (about 1 m lanes, staggered altitudes, >= 0.5 m separation), hover waypoints, dwell 1-2 s.
- [ ] [you] Fly one dry run without capture, then the capture run. Short runs (3-4 min), swap batteries.
- [ ] [claude] Live merge during the run.
Gate: baseline map of the area saved as run_01.

## Phase 10: Change detection
- [ ] [you] Move, add or remove one object. Fly run 2 with the identical trajectories.
- [ ] [claude] Voxel diff with a persistence threshold, then object-detection diff.
- [ ] [you] Check the highlight matches the object you moved.
Gate: demo bar met: one changed object highlighted.

## Phase 11: Demo hardening
- [ ] [both] Rehearse the full sequence twice. Battery swap routine, abort procedure.
- [ ] [both] Backup plan: replay the saved run if hardware fails.
- [ ] [claude] Final README, architecture diagram, and run instructions.

## Open risks to track
- Deck WiFi can drop in flight: a Bitcraze user reported the AI deck network dying during flight (maintainers suspect motor current resets the deck). Mitigations: start the motors before takeoff, take off slowly, healthy batteries, a thrust upgrade kit if needed. Test in Phase 4 with a drone powered and in Phase 8 in flight.
- Station-mode setup on the decks (Phase 4) is unverified.
- Monocular depth quality on small grayscale deck images (Phase 7).
- 3 drones per Crazyradio at the needed pose rate (Phase 8).
- 2.4 GHz crowding between the AP, Crazyradios and anything else in the lab.
