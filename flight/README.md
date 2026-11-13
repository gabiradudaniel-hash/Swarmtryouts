# Flying the swarm: hover 3 drones with live video (Linux)

`swarm_hover.py` takes off several Crazyflies one after another, hovers them in place over their takeoff
spots with OptiTrack, then lands them all. It can open the live-video window for their AI decks first.

It is built on the course code `crazyflie_progressive_square_v2`: `mocap.py`, `motive.py` and
`calibrate_axes.py` here are unchanged copies, so OptiTrack handling, the Kalman checks, the takeoff ramp
and the landing behave exactly as in the lab. New: several drones on one Crazyradio, one heading capture
for all, pre-flight checks and a shared safety monitor.

**Status:** tested in simulation only (normal flight, drift, OptiTrack loss, Enter, motors-off).
Not yet flown. Follow "First flights" below.

## 1. One-time setup (Ubuntu/Debian laptop)

1. Get this repository onto the laptop: on github.com open the repo -> green **Code** button ->
   **Download ZIP**, then unzip it (or copy the folder from a USB stick).
2. Open a terminal in the repository folder (right-click the folder -> "Open in Terminal") and run:
   ```bash
   bash flight/setup_linux.sh
   ```
   It asks for your Linux password once. It installs Python packages into `.venv/`, gives your user
   permission to use the Crazyradio, and runs the tests.
3. **Log out and back in** (or reboot), then unplug and replug the Crazyradio.

Every time you open a new terminal:
```bash
cd ~/path/to/Swarmtryouts        # the repository folder
source .venv/bin/activate          # the prompt now starts with (.venv)
```

## 2. Networks: two at the same time

| What | Network | How the laptop joins |
|---|---|---|
| OptiTrack positions (needed to fly) | the Motive / OptiTrack network | usually the **Ethernet cable** |
| Deck video (optional) | **SWARM-NET** (the Raspberry Pi) | **WiFi** |

Find this laptop's address on the OptiTrack network (it is the `--client` value):
```bash
ip -4 -brief addr          # look at the Ethernet line (enp..., eth0...), e.g. 10.10.10.2/24
```
If the laptop has only WiFi and Motive streams over WiFi, you cannot be on SWARM-NET at the same time:
fly without `--video`. If the firewall is on (`sudo ufw status` says active): `sudo ufw allow 1511/udp`.

Motive settings (as for the course scripts): rigid bodies **5, 7, 8** (id = last digit of the radio
address), streaming **multicast** 239.255.42.99, data port **1511**.

## 3. Rehearse without hardware

```bash
python3 flight/swarm_hover.py --sim
```
Same prompts as a real flight (press Enter for the headings, type `GO`), nothing flies. Try pressing
Enter while "flying" to see a landing, or `k` + Enter for motors-off.

## 4. Fly

Before every flight:
- Batteries charged (the program refuses below 3.80 V on the floor). Props on and undamaged.
- Drones on the floor, **at least 0.6 m apart**, at least 1 m of free space around and above each.
- **All noses toward the calibration wall**: the wall the drones were slid toward in `calibrate_axes.py`.
  For drones 5, 7, 8 the files say Crazyflie forward = OptiTrack **-X**, so it is the same wall for all three.
- cfclient closed. One person at the keyboard, ready to press Enter.

```bash
python3 flight/swarm_hover.py --client 10.10.10.2 --drones 5 7 8 --video
```

What happens:
1. It waits for rigid bodies 5, 7, 8 from Motive (and opens the video window with `--video`).
2. **Press Enter** to capture all three headings (drones still, noses at the wall).
3. It refuses if two drones are closer than 0.6 m.
4. It connects to each drone over the radio, sends OptiTrack to it and waits until every onboard estimate
   matches OptiTrack (status line once per second).
5. Pre-flight check: OptiTrack fresh, estimate agrees, yaw agrees, battery >= 3.80 V, spacing.
6. It prints the plan. **Type `GO` + Enter.** Anything else cancels; nothing has armed yet.
7. Drones take off one by one, 1.5 s apart, climb 0.5 m, hover 20 s, then all land on their spots
   and disarm.

**During the flight**

| Key | Effect |
|---|---|
| **Enter** | land all drones now |
| **k** then Enter | motors off for all drones immediately (they drop from 0.5 m) |
| **Ctrl+C** | land all; a second Ctrl+C = motors off |

**Automatic landing of ALL drones** when any drone: loses OptiTrack (> 0.3 s), loses the radio link,
drifts more than 0.40 m from its spot, climbs 0.35 m above its target, drops low during the hover,
has its onboard estimate 0.30 m off OptiTrack, comes within 0.30 m of another drone, or its battery
stays below 3.20 V. The reason is printed (`*** LANDING ALL: ...`).

Options: `--height 0.5` (0.3-0.8 m), `--hover-time 20` (5-120 s), `--stagger 1.5`, `--fence 0.40`,
`--min-separation 0.60`, `--min-battery 3.80`, `--radio 0` (Crazyradio index), `--drones 7` (any subset).

## 5. First flights (do them in this order)

1. `--sim` rehearsal (section 3).
2. **One drone**, short: `--drones 7 --hover-time 10`. Check it climbs straight and holds still.
3. **Two drones**: `--drones 7 8`.
4. **All three**: `--drones 5 7 8`, then add `--video`.

If one drone drifts or wobbles while the others are fine, its axis calibration is probably out of date
(the files are from 2026-11-05; redo it after a rigid body is rebuilt in Motive):
```bash
python3 flight/calibrate_axes.py 10.10.10.100 --client 10.10.10.2 --body-id 7 --address 0xE7E7E7E7E7
```
(10.10.10.100 = the Motive PC, as in the course README.) Motors stay off; follow its instructions (slide the drone up, toward the wall, to its left). It writes
`flight/axis_calibration_7.json`.

## 6. Troubleshooting

| Message | Fix |
|---|---|
| `Motive is not streaming rigid bodies [...]` | Motive: rigid bodies tracked, multicast on, ids 5/7/8. Check `--client` is the Ethernet address. |
| `Could not listen on UDP 1511` | Another program uses the Motive stream; close it. |
| `could not connect` / `Cannot find a Crazyradio` | Radio plugged in? Logged out/in after setup? `lsusb | grep 1915` shows it? cfclient closed? Drone on? |
| `Not every estimate matched OptiTrack` | Noses not at the calibration wall, or the calibration is outdated (section 5). |
| `battery ... Swap it.` | Charge or swap that drone's battery. |
| Video window: CONNECTING... | Laptop not on SWARM-NET, or decks still starting (20 s after power-on). The flight does not depend on it. |
| Video window does not open on Wayland | Run `QT_QPA_PLATFORM=xcb python3 tools/live_view.py ...` once to test. |
