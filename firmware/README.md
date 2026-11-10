# Firmware for the AI deck

The AI deck has two chips, flashed separately over the Crazyradio with `cfloader`:

| Chip | Target | What we run | File |
|---|---|---|---|
| GAP8 (camera + JPEG) | `deck-bcAI:gap8-fw` | Bitcraze `wifi-img-streamer` example with three changes (below), built with Docker | built from `gap8/` (see docs/speedrun.md Part C) |
| ESP32 (WiFi) | `deck-bcAI:esp-fw` | Bitcraze official release 2025.02, unmodified | `esp32/aideck_esp_2025.02_official.bin` |

## gap8/
- `wifi-img-streamer.c`, `Makefile`: drop-in replacements for
  `aideck-gap8-examples/examples/other/wifi-img-streamer/` (Bitcraze commit 5fd95ca).
  `station-mode-vs-bitcraze-5fd95ca.patch` is the same change as a diff. Changes versus Bitcraze:
  1. JPEG streaming instead of RAW (the professor's original change).
  2. `WIFI_STATION=1` build flag: the deck joins SWARM-NET (SSID + key from `wifi_credentials.h`,
     `WIFI_CTRL_WIFI_CONNECT` mode 0 = station) instead of creating its own access point.
     Without the flag the code is the access-point version (lab mode).
  3. Camera start-up fix: the stock code stopped the camera completely when the image-rotation register
     did not read back after one try (deck on WiFi, no video). Now it retries 5 times and streams anyway.
- `wifi_credentials.h.example`: copy to `wifi_credentials.h` next to the .c file and put the real passphrase in.
  The real file and every built station image contain the passphrase: never commit or share them.
- `ap-backup_aideck_2.img`: a prebuilt access-point image (network name `aideck_2`, no passphrase, without the
  camera fix). Quick way to put a deck back into lab mode.

## esp32/
- `aideck_esp_2025.02_official.bin`: downloaded unchanged from the GitHub release
  bitcraze/aideck-esp-firmware 2025.02 (ESP-IDF v4.3.1). Required for station mode: the older ESP32 firmware
  on the lab decks dropped off SWARM-NET every 10-60 s. It also runs the access-point (lab) mode.
  Firmware cannot be read back from the chip, so the decks' previous ESP32 version is not recoverable;
  it is not needed.

Bitcraze firmware is GPL-3.0; sources at github.com/bitcraze/aideck-gap8-examples and
github.com/bitcraze/aideck-esp-firmware.
