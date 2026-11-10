# Raspberry Pi 5 as the AI-deck WiFi network (DRAFT, untested)

I could not test this on a Pi. Run the steps one at a time and tell me where a check fails.

Topology (routed, two subnets):

    5 AI decks --WiFi 2.4 GHz--> Pi wlan0 (192.168.50.1/24)
    laptop  --Ethernet (USB adapter)--> Pi eth0 (192.168.51.1/24)

Decks get 192.168.50.11-.15 (fixed leases by MAC). The laptop reaches them through the Pi.
The laptop's other Ethernet port stays on the OptiTrack/Motive network.

## 1. Flash and first boot
1. Raspberry Pi Imager -> Raspberry Pi OS Lite (64-bit). In the settings: hostname `swarm-pi`, user + password, enable SSH, set WiFi country to RO (needed before the AP will transmit).
2. Boot the Pi on a temporary wired connection or monitor, `ssh <user>@swarm-pi.local`.
3. `nmcli -v` and `sudo nmcli device` should work. (NetworkManager is the default on current Raspberry Pi OS. If it is not, tell me and I will write the hostapd/dnsmasq version.)

## 2. Access point on wlan0 (WPA2, fixed channel 6)
    sudo nmcli con add type wifi ifname wlan0 con-name swarm-ap autoconnect no ssid SWARM-NET \
      802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
      ipv4.method shared ipv4.addresses 192.168.50.1/24 \
      wifi-sec.key-mgmt wpa-psk wifi-sec.psk 'CHOOSE-A-PASSPHRASE' \
      wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp
    sudo nmcli con up swarm-ap

This cuts your SSH session if you are connected over WiFi (hotspot). Then join the laptop's WiFi to SWARM-NET and `ssh <user>@192.168.50.1`.
autoconnect is OFF on purpose: if the AP misbehaves, a reboot returns the Pi to the hotspot.

Check: a phone or the laptop sees SWARM-NET and gets an address in 192.168.50.x.

Once it is proven (stays up 30 min), make it permanent:

    sudo nmcli con modify swarm-ap connection.autoconnect yes connection.autoconnect-priority 100
    sudo nmcli con modify <hotspot-connection-name> connection.autoconnect no
    (or delete the hotspot and fake-WiFi connections: sudo nmcli con delete "<name>")

## 3. Ethernet to the laptop on eth0
    sudo nmcli con add type ethernet ifname eth0 con-name swarm-lan autoconnect yes \
      ipv4.method shared ipv4.addresses 192.168.51.1/24
    sudo nmcli con up swarm-lan

Check: with the cable plugged into the laptop's USB-Ethernet adapter, Windows gets 192.168.51.x and `ping 192.168.51.1` works.

## 4. Laptop route to the decks (Windows, admin PowerShell)
    route add 192.168.50.0 mask 255.255.255.0 192.168.51.1
Check: from the laptop `ping 192.168.50.1` works, and a phone on SWARM-NET answers a ping from the laptop (try its IP).
If it does not, check `sysctl net.ipv4.ip_forward` on the Pi (should be 1) and tell me the output.

## 5. Fixed leases for the decks (after you know each deck's MAC)
Create /etc/NetworkManager/dnsmasq-shared.d/decks.conf:

    dhcp-host=AA:BB:CC:DD:EE:01,192.168.50.12
    dhcp-host=AA:BB:CC:DD:EE:02,192.168.50.14
    ... one line per deck (use the real MACs and the body-id based IPs: .1<id> style or as in config/drones.yaml)

then `sudo nmcli con down swarm-ap && sudo nmcli con up swarm-ap`.

## 6. Throughput and stability
- Fix the AP on channel 6, 20 MHz, WPA2-PSK only. The ESP32 on the deck is 2.4 GHz only.
- `iperf3 -s` on the Pi, `iperf3 -c 192.168.51.1` from the laptop (wired leg), and a phone iperf client on SWARM-NET (WiFi leg). Note the numbers.
- Leave it running 30 min with a phone connected. No disconnects = gate passed.


## Login (verified 2026-11-10)
User is lowercase `swarm-pi` (Linux user names are case-sensitive; `Swarm-Pi@...` is refused).
`ssh -o PreferredAuthentications=password swarm-pi@192.168.50.1` from SWARM-NET. Reading the DHCP lease file needs sudo:
`sudo cat /var/lib/NetworkManager/dnsmasq-wlan0.leases`
