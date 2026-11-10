# To-do list

State on 2026-11-10. Priorities: **P1** = blocks the next step, **P2** = needed soon, **P3** = later.
Details for each phase and its pass/fail gate: [docs/tasks.md](docs/tasks.md). How-to: [HANDOVER.md](HANDOVER.md).

## A. Finish the video network (Phase 4)

- [ ] **P1** Drone 5: flash the ESP32 again with `firmware/esp32/aideck_esp_2025.02_official.bin` and confirm
      `100% writing to bcAI:esp deck memory`; then a 2-minute `live_view` run. It had one 10 s drop in the last test.
- [ ] **P1** Drone 4: cfloader cannot see its deck (nothing was flashed). In cfclient check
      Parameters -> `deck` -> `bcAI` (1 = deck detected) and the Crazyflie firmware version (Help -> About),
      and compare with drone 7. If the firmware is older, updating it from cfclient's firmware upgrade is the likely
      fix; agree this with the drone's owner first.
- [ ] **P1** Confirm which five drones form the swarm and their body ids. `config/drones.yaml` still has
      placeholders 2 and 6; 5, 7, 8 are real; 4 is blocked.
- [ ] **P1** Convert the remaining drone(s) with HANDOVER.md section 8 and add their fixed addresses.
- [ ] **P1** Gate test: `python tools\stream_test.py --seconds 60` with 1, then 2, then all 5 decks.
      Pass = every deck at least 1 fps and 0 reconnects for 60 s. Keep the printed summaries.
- [ ] **P2** Deck 7: flash `gap8-fw` with the current build (it still has the camera program from before the
      camera start-up fix).
- [ ] **P2** Set `camera_rotation` in `config/drones.yaml` for each deck after checking it upright in `live_view`.
- [ ] **P2** Test the lab-mode round trip on one deck (HANDOVER.md section 9): back to its own network, old
      `capture.py` works, then back to SWARM-NET.
- [ ] **P2** Agree with the professor which lab decks may stay in station mode.
- [ ] **P2** Streaming with motors running and in flight: Bitcraze users report the deck WiFi dropping in flight
      (motor current). Start motors before takeoff, take off slowly, use healthy batteries.
- [ ] **P3** If drops grow with 5 decks: survey 2.4 GHz channels from the laptop
      (`netsh wlan show networks mode=bssid`), try channel 1 or 11 on the Pi, lower JPEG quality or frame rate.

## B. Infrastructure

- [ ] **P1** Change the Pi password (`passwd` on the Pi), then update CREDENTIALS.md.
- [ ] **P1** Change the SWARM-NET passphrase (the current one was typed into AI chats, confirmed 2026-11-10):
      `sudo nmcli con modify swarm-ap wifi-sec.psk '<new>'` + `sudo nmcli con up swarm-ap` on the Pi, update
      `wifi_credentials.h`, rebuild the station image, reflash `gap8-fw` on drones 5, 7, 8 (do it together with deck 7's reflash), update CREDENTIALS.md.
- [ ] **P2** Remove the phone-hotspot WiFi profile the Pi got from Raspberry Pi Imager at first boot
      (`nmcli con show`, then `sudo nmcli con delete "<name>"`), so the Pi never tries to join it.
- [ ] **P2** Ethernet cable laptop <-> Pi (docs/pi-ap-setup.md sections 3-4: Pi eth0 192.168.51.1, Windows route
      to 192.168.50.0/24). Then the laptop keeps its own WiFi/internet and reaches Motive at the same time.
- [ ] **P2** Phase 2 gate not formally done: SWARM-NET stable for 30 min, `iperf3` throughput noted.
- [ ] **P2** Python 3.12 virtual environment in the repo (`py -3.12 -m venv .venv`, `pip install -r requirements.txt cfclient`)
      instead of the Python 3.14 user install.
- [ ] **P2** Second Crazyradio: Zadig libusb-win32 driver; check both radios are visible to cflib.
- [ ] **P3** Keep the Pi away from the Crazyradios (2.4 GHz noise), radios stay on the laptop.
- [ ] **P3** Commit every change to this repository; never commit `wifi_credentials.h` or built station images.

## C. Roadmap to the demo (from docs/tasks.md)

| Phase | Goal | Gate |
|---|---|---|
| 1. Laptop environment | Python 3.12 venv, CUDA 12.8+ PyTorch on the RTX 5070, ultralytics | Tests pass, `torch.cuda.is_available()` True, both radios visible |
| 3. One-drone baseline | Axis calibration per drone, re-fly the legacy square with the course scripts | One drone flies the square |
| 4. Video network | (section A) | All 5 decks >= 1 fps, 0 reconnects, 60 s |
| 5. Camera calibration | ~30 checkerboard frames per deck, OpenCV intrinsics, camera-to-body transform | Reprojection error < 1 px, validation target within a few cm |
| 6. Test recording | Deck frames + mocap pose at 5-10 known spots; move one object, record again | Dataset in `data/recording_01/` |
| 7. Depth and mapping | yolo26n-depth on deck frames, scale from mocap, back-projection, voxel map, viewer | Recognizable map of the room |
| 8. Swarm flight manager | One process: Motive, 5 cflib links, controllers, kill switch, auto-land, separation monitor; simulated first | 5 drones hover together 2 min |
| 9. Scripted scan, run 1 | Lane trajectories (~1 m lanes, staggered altitudes, >= 0.5 m separation), hover captures, live merge | Baseline map saved |
| 10. Change detection | Run 2 with one object moved; voxel diff with persistence threshold, then detection diff | Changed object highlighted |
| 11. Demo hardening | Rehearse twice, battery routine, abort procedure, replay backup | Demo ready |

## D. Open questions

- The five body ids, which Crazyradios (PA or 2.0), Flow deck present or not, Motive version and frame rate,
  whether a JTAG programmer is available (not needed so far).
- Does yolo26n-depth give usable depth on 324x244 grayscale deck images? (decides the dense-map vs detections route)
- Radio-to-drone assignment and the pose rate actually reached with 3 drones on one Crazyradio.
- Voxel size and persistence thresholds (after measuring depth noise).
- Which object classes matter for the change-detection demo.
