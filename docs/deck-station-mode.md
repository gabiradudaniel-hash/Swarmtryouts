# Moving the AI decks onto SWARM-NET (station mode)

Status (2026-11-10): station image built and checked (see journal); flashing deck 7 first. Based on reading aideck-gap8-examples and aideck_build_flash_commands.rtf.

## What the professor did (verified from the files)
- Source: aideck-gap8-examples/examples/other/wifi-img-streamer/wifi-img-streamer.c (the stock old streamer, NOT NanoCockpit).
- Two edits: `streamerMode = JPEG_ENCODING` (stock is RAW) and a per-deck SSID in `setupWiFi()` (`"aideck_2"` in this copy; your deck 7 shows as `aideck7`).
- Build (Docker image bitcraze/aideck): tools/build/build, which runs
  `make-example examples/other/wifi-img-streamer build image SETUP_WIFI_AP=1`.
- Flash over the radio, GAP8 only, no JTAG:
  `cfloader flash examples/other/wifi-img-streamer/BUILD/GAP8_V2/GCC_RISCV_FREERTOS/target.board.devices.flash.img deck-bcAI:gap8-fw -w radio://0/80/2M/E7E7E7E7E2`
  (the second block in the rtf has a typo, "cd deck-bcAI..."; the first block is the correct one.)
- Nothing in these commands is custom to the Crazyflie. Only the paths are the professor's (/Users/tlala/...).

## How the WiFi mode is chosen here
The GAP8 app sends CPX WIFI_CTRL packets to the ESP32 at startup:
1. WIFI_CTRL_SET_SSID (0x10) with the network name
2. WIFI_CTRL_WIFI_CONNECT (0x20) with data[0] = 0x01 -> create an access point (this is the AP mode your decks use now)

wifi.h also defines WIFI_CTRL_SET_KEY (0x11). The AP is open (no key sent).

## Station-mode idea (verified 2026-11-10, works on drones 5, 7, 8)
Replace the body of setupWiFi() so the deck joins SWARM-NET:
1. SET_SSID  "SWARM-NET"
2. SET_KEY   "<your passphrase>"
3. WIFI_CONNECT with data[0] = 0x00 (0 = join a network, 1 = access point). VERIFIED 2026-11-10: crazyflie-firmware src/deck/drivers/src/aideck.c defines WIFI_CONNECT_AS_STA 0x00 and WIFI_CONNECT_AS_AP 0x01 for the same CPX WIFI_CTRL command.

Same image for all five decks (no per-deck SSID needed): build once, flash 5 times.
If connected, the deck prints "Wifi connected (a.b.c.d)" to the cfclient console (CPX console), which gives its IP.
If the guess about data[0] is wrong the deck may do nothing or still come up as an AP; then reflash the AP build to recover.

Alternative: the official route in Bitcraze's docs is the Crazyflie firmware menuconfig (Support AI deck -> WiFi setup at startup), which also lists "connect to an existing network". That means rebuilding and flashing the Crazyflie firmware itself, which is heavier, so only if the GAP8 route fails.

## Safety / recovery
- GAP8 over-the-air flashing keeps the bootloader; a bad image leaves the bootloader running and you can flash again with cfloader.
- Keep a copy of the current AP image (target.board.devices.flash.img, dated Nov 5) per deck before flashing.
- Props off during all flashing.
- The passphrase would sit in the C source: do not commit it to git.

## Steps (Windows laptop)
1. Docker Desktop running. From the aideck-gap8-examples folder:
   `docker run --rm -v ${PWD}:/module bitcraze/aideck tools/build/make-example examples/other/wifi-img-streamer image`
   (build with the station setupWiFi, the SETUP_WIFI_AP flag must still be passed if the code is inside #ifdef SETUP_WIFI_AP: add `SETUP_WIFI_AP=1` at the end).
2. cfloader needs the Bitcraze Python tools (pip install cfclient or cflib; I have not confirmed which package ships cfloader in your version).
3. Flash ONE deck: `cfloader flash <img> deck-bcAI:gap8-fw -w radio://0/80/2M/E7E7E7E7E<id>`
4. Power the drone near the Pi, watch the cfclient console for the IP, and look for the deck in the Pi's station list:
   `iw dev wlan0 station dump | grep Station` on the Pi.
5. `python tools/stream_test.py --deck cf2=<ip>` (or capture.py --ip <ip>).

## Per-deck procedure (verified on deck 7, 2026-11-10)
Props off, cfclient closed, drone on. Crazyradio driver must be libusb-win32 (Zadig). From the SWARM folder, <id> = body id:
1. GAP8 station image (run inside aideck-gap8-examples):
   cfloader flash examples\other\wifi-img-streamer\BUILD\GAP8_V2\GCC_RISCV_FREERTOS\target.board.devices.flash.img deck-bcAI:gap8-fw -w radio://0/80/2M/E7E7E7E7E<id>
2. ESP32 firmware, Bitcraze official 2025.02 (the decks' old ESP firmware dropped off SWARM-NET every few seconds):
   cfloader flash firmware\esp32\aideck_esp_2025.02_official.bin deck-bcAI:esp-fw -w radio://0/80/2M/E7E7E7E7E<id>
3. Power-cycle the drone (the GAP8 hands SSID/key to the ESP32 only at power-on), wait ~20 s.
4. On the Pi: sudo cat /var/lib/NetworkManager/dnsmasq-wlan0.leases -> the 'espressif' line gives MAC and IP.
5. Add 'dhcp-host=<MAC>,192.168.50.1<id>,deck<id>' to config/pi/decks.conf, deck_ip to config/drones.yaml.
(cfloader = "$env:APPDATA\Python\Python314\Scripts\cfloader.exe")
