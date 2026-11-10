# Test evidence, 2026-11-10

Raw outputs saved during bring-up. Times are Europe/Bucharest. Deck MACs: deck 7 = 38:18:2b:c2:0b:48,
deck 8 = 38:18:2b:c2:0b:9c, deck 5 = 38:18:2b:c2:11:d0; laptop = 14:b5:cd:50:f6:ab.

| File | What it shows |
|---|---|
| pi_ps_off.txt | Pi log: deck 7 on the OLD ESP32 firmware dropping and rejoining (17:01-17:14), also after Pi power save was turned off |
| capture_ps_off.txt | capture.py on deck 7 with Pi power save off: 8 Hz between ~10 s stalls |
| capture_esp_ps_off.txt | deck 7 with my own power-save-off ESP32 build: stalls (build discarded) |
| pi_after_esp_flash.txt | Pi log around flashing the official ESP32 2025.02 on deck 7 |
| capture_deck7_2min.txt, pi_deck7_2min.txt | deck 7 on official ESP32 + fixed IP .17: 1044 frames in 137 s, one drop ~50 s after power-on, then 122 s clean |
| deck8_dhcp_first_join.txt | drone 8 joining as .240 and rejoining every 10-30 s (before its fix) |
| deck8_ping.txt, deck8_after_lease.txt | drone 8 on its fixed IP .18, answering ping, but no video (camera start-up bug, later fixed) |
| deck5_dhcp_first_join.txt | drone 5 first join (.171), rejoining every ~15 s |
| live_view_3decks_20261110_194418.txt | live_view event log, decks 5/7/8 together for 66 s: 7 and 8 clean (~8 fps, 0 drops), 5 one 10.5 s drop |
