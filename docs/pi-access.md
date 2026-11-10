# Raspberry Pi access (SWARM-NET access point)

| Item | Value |
|---|---|
| Device / hostname | `Swarm-Pi` (prompt shows `swarm-pi@Swarm-Pi`), mDNS name `swarm-pi.local` |
| User | `swarm-pi` (all lowercase: Linux user names are case-sensitive, `Swarm-Pi@...` is refused) |
| Pi password | in [CREDENTIALS.md](../CREDENTIALS.md) |
| WiFi network | `SWARM-NET`, WPA2, channel 6, Pi address `192.168.50.1` |
| SWARM-NET passphrase | in [CREDENTIALS.md](../CREDENTIALS.md). Also compiled into the deck station image (`wifi_credentials.h`). |
| sudo | Same password as the login. sudo remembers it for a few minutes, so it may not ask twice. |

## How to connect (try in this order)

1. **By name** (works when the laptop and the Pi are on the same network and Windows resolves `.local`):
   ```powershell
   ssh -o PreferredAuthentications=password swarm-pi@swarm-pi.local
   ```
   Always add `-o PreferredAuthentications=password`. Without it, ssh first tries key logins and can fail with
   `Permission denied (publickey,password)` before ever asking for the password.
2. **By address on SWARM-NET** (most reliable; use it whenever the laptop is on SWARM-NET):
   ```powershell
   ssh -o PreferredAuthentications=password swarm-pi@192.168.50.1
   ```
3. **By address on another network** (e.g. the phone hotspot used at first boot): find the Pi's address in the
   phone's hotspot client list, or on the laptop with `arp -a`, then `ssh -o PreferredAuthentications=password swarm-pi@<address>`.
   (Once the AP is running, the Pi's WiFi is busy being SWARM-NET, so it is normally reached on SWARM-NET.)
4. **Cable** (planned, docs/pi-ap-setup.md section 3): Pi `eth0` = `192.168.51.1`.

## Common problems

| Message | Fix |
|---|---|
| `Permission denied (publickey,password)` with the right password | User name must be lowercase `swarm-pi`; keep `-o PreferredAuthentications=password` |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` (after re-flashing the Pi's SD card) | `ssh-keygen -R swarm-pi.local` and `ssh-keygen -R 192.168.50.1`, then connect again |
| `Could not resolve hostname swarm-pi.local` | Use the address instead (step 2 or 3) |
| `Connection timed out` | Laptop is not on SWARM-NET (or the Pi is off); check the WiFi icon |
| `client_loop: send disconnect: Connection reset` | Normal when the laptop leaves SWARM-NET or the AP restarts |
| `cat ...dnsmasq-wlan0.leases: Permission denied` | Prefix with `sudo` |
| `iw: command not found` in a one-line ssh command | Use `/usr/sbin/iw` |

## Useful commands on the Pi
```bash
sudo cat /var/lib/NetworkManager/dnsmasq-wlan0.leases        # which device got which address
/usr/sbin/iw dev wlan0 station dump | grep Station             # who is connected now
journalctl --since '-15min' --no-pager | grep -i <deck MAC>    # a deck's connect / disconnect history
vcgencmd get_throttled                                         # power: throttled=0x0 is good
nmcli con show                                                 # network profiles (swarm-ap = SWARM-NET)
passwd                                                         # change the Pi password
sudo nmcli con modify swarm-ap wifi-sec.psk '<new>' && sudo nmcli con up swarm-ap   # change the SWARM-NET passphrase
```
After changing the SWARM-NET passphrase, every converted deck needs a rebuilt station image with the new key
(`wifi_credentials.h`, docs/speedrun.md Part C) and a `gap8-fw` reflash, otherwise the decks cannot join.
