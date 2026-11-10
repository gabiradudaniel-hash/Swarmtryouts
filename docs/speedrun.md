# Speedrun: from bare hardware to live AI-deck video on SWARM-NET

Verified 2026-11-10 with deck 7 (body 7, radio address E7E7E7E7E7). Follow the parts in order.
Time once you have done it before: Pi ~20 min, laptop ~15 min, build ~5 min, ~10 min per deck.

What you end up with: a Raspberry Pi 5 broadcasting SWARM-NET (192.168.50.1/24, channel 6, WPA2);
each AI deck joins it at a fixed address 192.168.50.1<body id>; the laptop shows all decks live with
`python tools\live_view.py`. Flight control stays on the Crazyradio; only video goes over WiFi.

Never write the SWARM-NET passphrase into chat, git or this doc. It goes only into the Pi's AP setup
command (typed on the Pi) and into `wifi_credentials.h` (edited by hand).

---

## Part A. Raspberry Pi access point (once)

1. Raspberry Pi Imager -> Raspberry Pi OS Lite (64-bit). Settings: hostname `swarm-pi`, user `swarm-pi`
   (lowercase!), a password, SSH on, WiFi country RO. Power the Pi from a real USB-C supply, not the laptop.
2. SSH in (first boot on your hotspot or a cable): `ssh swarm-pi@swarm-pi.local`
3. Create the AP (type your passphrase in place of the placeholder):
   ```bash
   sudo nmcli con add type wifi ifname wlan0 con-name swarm-ap autoconnect yes ssid SWARM-NET \
     802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
     ipv4.method shared ipv4.addresses 192.168.50.1/24 \
     wifi-sec.key-mgmt wpa-psk wifi-sec.psk 'YOUR-PASSPHRASE' \
     wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp
   sudo nmcli con modify swarm-ap connection.autoconnect-priority 100 802-11-wireless.powersave disable
   sudo nmcli con up swarm-ap
   ```
   Your SSH session drops. Join the laptop to SWARM-NET, then log in with (all options: docs/pi-access.md):
   `ssh -o PreferredAuthentications=password swarm-pi@192.168.50.1`
4. Check: `vcgencmd get_throttled` prints `throttled=0x0` (power OK).

Details and the Ethernet/route plan: docs/pi-ap-setup.md.

## Part B. Laptop tools (once)

All in PowerShell. Python 3.14 user installs work (a 3.12 venv is planned, see tasks.md Phase 1).
1. `python -m pip install --user cfclient opencv-python numpy pyyaml`
   - NEVER `pip install cfloader`: that is an unrelated PyPI package that breaks cfclient's cfloader.
     If it happened: `pip uninstall cfloader` then `pip install --user --force-reinstall --no-deps cfclient`.
   - cfloader is not on PATH. Use: `& "$env:APPDATA\Python\Python314\Scripts\cfloader.exe"`
     (below written as `cfloader`). `cfloader --help` printing "Action --help unknown!" is normal.
2. Crazyradio driver: run Zadig (download from zadig.akeo.ie) -> Options -> List All Devices ->
   "Crazyradio 2.0" (USB ID 1915 7777) -> **libusb-win32** (NOT WinUSB) -> Replace Driver -> replug.
   Check: `Get-PnpDevice -PresentOnly | ? InstanceId -match 'VID_1915'` shows Status OK.
   With WinUSB, cflib says "Cannot find a Crazyradio Dongle".
3. Docker Desktop running, image `bitcraze/aideck` pulled.

## Part C. Build the deck's camera (GAP8) station image (once for all decks)

1. Get the examples repo, inside this repository's folder (it is git-ignored; skip if already there):
   ```powershell
   git clone https://github.com/bitcraze/aideck-gap8-examples.git
   git -C aideck-gap8-examples checkout 5fd95ca
   ```
2. Put our two files in place (professor's JPEG mode + our station mode + camera start-up fix):
   ```powershell
   $S = "aideck-gap8-examples\examples\other\wifi-img-streamer"
   Copy-Item firmware\gap8\wifi-img-streamer.c, firmware\gap8\Makefile $S -Force
   Copy-Item firmware\gap8\wifi_credentials.h.example "$S\wifi_credentials.h"
   ```
   (The same change as a diff: firmware/gap8/station-mode-vs-bitcraze-5fd95ca.patch.)
3. Open `$S\wifi_credentials.h` in an editor and replace the placeholder with the real passphrase. Save.
4. Build (from inside `aideck-gap8-examples`):
   ```powershell
   cd aideck-gap8-examples
   docker run --rm -v "${PWD}:/module" bitcraze/aideck tools/build/make-example examples/other/wifi-img-streamer image SETUP_WIFI_AP=1 WIFI_STATION=1
   cd ..
   ```
   Result: `$S\BUILD\GAP8_V2\GCC_RISCV_FREERTOS\target.board.devices.flash.img`.
   This image contains the passphrase: do not share it or commit it.

## Part D. Each deck (~10 min). Example for body id 7

Props off, cfclient closed, Crazyradio plugged in, drone on. Laptop can stay on the hotspot (flashing uses the radio).
1. Camera chip (GAP8), the image from Part C:
   ```powershell
   cfloader flash aideck-gap8-examples\examples\other\wifi-img-streamer\BUILD\GAP8_V2\GCC_RISCV_FREERTOS\target.board.devices.flash.img deck-bcAI:gap8-fw -w radio://0/80/2M/E7E7E7E7E7
   ```
   CHECK the output has `100% writing to bcAI:gap8 deck memory`. If cfloader ends without any
   "writing"/"Skipping bcAI" line, the drone did not report a flashable deck and NOTHING was flashed
   (drone 4 on 2026-11-10). See Troubleshooting.
2. WiFi chip (ESP32), Bitcraze official 2025.02. Required: the decks' old ESP firmware kept dropping off SWARM-NET.
   ```powershell
   cfloader flash firmware\esp32\aideck_esp_2025.02_official.bin deck-bcAI:esp-fw -w radio://0/80/2M/E7E7E7E7E7
   ```
   "Could not save cache, no writable directory" is harmless.
3. Switch the drone off and on (the camera chip hands the network name/key to the WiFi chip only at power-on). Wait 20 s.
4. Laptop to SWARM-NET. On the Pi: `sudo cat /var/lib/NetworkManager/dnsmasq-wlan0.leases`.
   The `espressif` line has the deck's MAC.
5. Fixed address: add `dhcp-host=<MAC>,192.168.50.1<id>,deck<id>` to `config\pi\decks.conf`, set
   `deck_ip` for that drone in `config\drones.yaml`, then install it on the Pi:
   ```powershell
   scp -o PreferredAuthentications=password config\pi\decks.conf swarm-pi@192.168.50.1:/tmp/decks.conf
   ssh -t -o PreferredAuthentications=password swarm-pi@192.168.50.1 "sudo mkdir -p /etc/NetworkManager/dnsmasq-shared.d && sudo cp /tmp/decks.conf /etc/NetworkManager/dnsmasq-shared.d/ && sudo systemd-run --on-active=2 nmcli connection up swarm-ap"
   ```
   SWARM-NET restarts; rejoin it, power-cycle the drone, wait 20 s.

Known decks:

| Body | Radio address | Deck MAC | Deck IP |
|---|---|---|---|
| 7 | E7E7E7E7E7 | 38:18:2b:c2:0b:48 | 192.168.50.17 |
| 8 | E7E7E7E7E8 | 38:18:2b:c2:0b:9c | 192.168.50.18 |
| 5 | E7E7E7E7E5 | 38:18:2b:c2:11:d0 | 192.168.50.15 |
| 4 | E7E7E7E7E4 | - | deck not flashable over radio (unresolved) |

## Part E. Watch

Laptop on SWARM-NET, drones on:
```powershell
python tools\live_view.py                           # all decks with deck_ip in drones.yaml
python tools\live_view.py --deck cf7=192.168.50.17  # one deck
python tools\live_view.py --save recordings\run1    # also keep the frames
python tools\stream_test.py --seconds 60            # pass/fail numbers (>= 1 fps, 0 reconnects)
```
Keys: q quit, s snapshot, +/- zoom. Left-click a tile = flip it 180, right-click = turn 90; on exit it prints
the rotations to keep (set `camera_rotation: 180` for that drone in drones.yaml).
Tile messages: "CONNECTING..." = deck not reachable (not on SWARM-NET yet / wrong IP);
"CONNECTED, NO VIDEO YET" = deck on the network but its camera sends nothing; "NO VIDEO 3.2s" = was streaming, now silent.

## Part F. Back to the professor's lab mode (deck makes its own network, e.g. `aideck7`)

The WiFi chip keeps the official 2025.02 firmware (it serves both modes). Only the camera chip changes:
1. In `$S\wifi-img-streamer.c`, in the `#else` branch of `setupWiFi()`, change `"aideck_2"` to the deck's
   name (e.g. `"aideck7"`).
2. Build WITHOUT `WIFI_STATION=1`:
   `docker run --rm -v "${PWD}:/module" bitcraze/aideck tools/build/make-example examples/other/wifi-img-streamer image SETUP_WIFI_AP=1`
3. Flash it with the Part D step 1 command, then power-cycle. The laptop joins `aideck7`; deck IP is 192.168.4.1.
   Quick option without building: `firmware\gap8\ap-backup_aideck_2.img` (network name `aideck_2`).
Not yet tested on a deck (planned on deck 7). Rebuild the station image (Part C step 4) before going back.

## Troubleshooting (all seen on 2026-11-10)

| Symptom | Cause / fix |
|---|---|
| `Cannot find a Crazyradio Dongle` | Zadig driver must be libusb-win32, not WinUSB. Close cfclient. |
| SSH `Permission denied (publickey,password)` | User name is lowercase `swarm-pi`. |
| `cat ...leases: Permission denied` | Use `sudo cat`. |
| `iw: command not found` in a one-off ssh command | Use `/usr/sbin/iw`. |
| capture/live view times out, never "Connected." | Laptop is not on SWARM-NET, or the deck has not joined yet (power-cycle, wait 20 s). |
| Video stalls ~10 s, Pi log shows the deck DISCONNECTED+CONNECTED | Old ESP firmware: flash the official 2025.02 (Part D step 2). A rare single drop still happens. |
| Pi log for the deck | `journalctl --since '-15min' --no-pager \| grep -i <deck MAC>` on the Pi |
| cfloader finishes but prints no "writing" and no "Skipping bcAI" line | The drone reported no flashable deck; nothing was flashed. Check in cfclient: Parameters -> deck -> bcAI (1 = deck seen) and the firmware version (Help -> About). Drone 4: unresolved. |
| Live view "CONNECTING..." for a deck that should be there | Wrong IP: fixed lease not installed (Part D step 5 must print "Running timer as unit"), or deck not joined. Check: ping 192.168.50.1<id>. |
| "CONNECTED, NO VIDEO YET" / deck pings but no frames | Old camera program gave up when the camera rotation register did not stick. Rebuild (Part C, files from firmware/gap8 include the retry fix) and reflash gap8-fw. Fixed drone 8. |
| Picture upside down | Deck mounting / rotation register. Click the tile; make it permanent with camera_rotation in drones.yaml. |
